#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import pypdfium2 as pdfium  # noqa: E402

from document_processor import DocIR  # noqa: E402
from document_processor.html_exporter import _render_html_document_shell  # noqa: E402

DEFAULT_INPUT_DIR = Path("/Users/yoonseo/Developer/External/RAGBuilder-test/Dataset/Text pdf")
DEFAULT_OUTPUT_DIR = REPO_ROOT / "out" / "pdf-review" / "text-pdf"

_PAGE_SECTION_RE = re.compile(
    r'(<section class="document-page"[^>]*>.*?</section>)',
    flags=re.DOTALL,
)


@dataclass(slots=True)
class PageArtifact:
    page_number: int
    source_image_path: Path
    rendered_page_path: Path


@dataclass(slots=True)
class DocumentArtifact:
    source_pdf_path: Path
    output_dir: Path
    full_html_path: Path
    review_html_path: Path
    page_count: int
    paragraph_count: int
    asset_count: int
    page_artifacts: list[PageArtifact]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render every PDF in a dataset to HTML, rasterize original PDF pages, "
            "and build side-by-side review pages."
        )
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"Directory containing source PDFs (default: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory where review artifacts will be written (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=110,
        help="Rasterization DPI for source PDF page images (default: 110)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit on the number of PDFs to process.",
    )
    return parser.parse_args()


def find_pdf_files(input_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    )


def _safe_slug(pdf_path: Path, index: int) -> str:
    digest = hashlib.sha1(str(pdf_path).encode("utf-8")).hexdigest()[:8]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", pdf_path.stem).strip("-") or f"pdf-{index:03d}"
    return f"{index:03d}-{stem[:48]}-{digest}"


def _extract_page_sections(full_html: str) -> list[str]:
    return _PAGE_SECTION_RE.findall(full_html)


def _page_html_document(*, title: str, section_html: str) -> str:
    return _render_html_document_shell(title=title, body=section_html)


def _write_bmp(path: Path, *, width: int, height: int, stride: int, pixels: bytes) -> None:
    row_raw = width * 3
    row_padded = (row_raw + 3) & ~3
    pixel_array_size = row_padded * height
    file_size = 14 + 40 + pixel_array_size

    file_header = struct.pack(
        "<2sIHHI",
        b"BM",
        file_size,
        0,
        0,
        14 + 40,
    )
    info_header = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        -height,
        1,
        24,
        0,
        pixel_array_size,
        2835,
        2835,
        0,
        0,
    )

    with path.open("wb") as fh:
        fh.write(file_header)
        fh.write(info_header)
        if row_padded == row_raw:
            fh.write(pixels[: stride * height])
            return

        padding = b"\x00" * (row_padded - row_raw)
        for row_index in range(height):
            start = row_index * stride
            fh.write(pixels[start : start + row_raw])
            fh.write(padding)


def _render_pdf_pages_to_bmp(
    pdf_path: Path,
    *,
    output_dir: Path,
    dpi: int,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    scale = dpi / 72.0
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        image_paths: list[Path] = []
        for page_index in range(len(doc)):
            page = doc[page_index]
            bitmap = page.render(scale=scale)
            try:
                image_path = output_dir / f"page-{page_index + 1:03d}.bmp"
                _write_bmp(
                    image_path,
                    width=bitmap.width,
                    height=bitmap.height,
                    stride=bitmap.stride,
                    pixels=bytes(bitmap.buffer),
                )
                image_paths.append(image_path)
            finally:
                bitmap.close()
        return image_paths
    finally:
        doc.close()


def _build_review_html(
    artifact: DocumentArtifact,
    *,
    title: str,
) -> str:
    page_cards: list[str] = []
    for page_artifact in artifact.page_artifacts:
        page_cards.append(
            f"""
<section class="page-card">
  <div class="page-card__header">
    <h2>Page {page_artifact.page_number}</h2>
    <div class="page-card__links">
      <a href="{page_artifact.source_image_path.name}" target="_blank" rel="noreferrer">source image</a>
      <a href="../full.html" target="_blank" rel="noreferrer">full html</a>
      <a href="../source.pdf" target="_blank" rel="noreferrer">source pdf</a>
    </div>
  </div>
  <div class="page-card__grid">
    <div class="page-card__pane">
      <div class="page-card__label">Original PDF</div>
      <img src="{page_artifact.source_image_path.name}" alt="Original PDF page {page_artifact.page_number}" />
    </div>
    <div class="page-card__pane">
      <div class="page-card__label">Rendered HTML</div>
      <iframe src="../rendered-pages/{page_artifact.rendered_page_path.name}" title="Rendered page {page_artifact.page_number}"></iframe>
    </div>
  </div>
</section>
"""
        )

    body = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{
    margin: 0;
    font-family: system-ui, sans-serif;
    background: #eef1f5;
    color: #111;
  }}
  .page {{
    max-width: 1800px;
    margin: 0 auto;
    padding: 24px;
  }}
  .topbar {{
    background: #fff;
    border: 1px solid #d7dde7;
    padding: 18px 20px;
    margin-bottom: 20px;
  }}
  .topbar h1 {{
    margin: 0 0 8px 0;
    font-size: 24px;
  }}
  .topbar p {{
    margin: 4px 0;
  }}
  .topbar a {{
    color: #1b4ea3;
  }}
  .page-card {{
    background: #fff;
    border: 1px solid #d7dde7;
    margin-bottom: 18px;
    padding: 16px;
  }}
  .page-card__header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 16px;
    margin-bottom: 12px;
  }}
  .page-card__header h2 {{
    margin: 0;
    font-size: 18px;
  }}
  .page-card__links {{
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    font-size: 14px;
  }}
  .page-card__grid {{
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 16px;
  }}
  .page-card__pane {{
    min-width: 0;
  }}
  .page-card__label {{
    font-weight: 600;
    margin-bottom: 8px;
  }}
  .page-card img {{
    width: 100%;
    display: block;
    background: #fff;
    border: 1px solid #d7dde7;
  }}
  .page-card iframe {{
    width: 100%;
    min-height: 1200px;
    border: 1px solid #d7dde7;
    background: #fff;
  }}
  @media (max-width: 1100px) {{
    .page-card__grid {{
      grid-template-columns: 1fr;
    }}
  }}
</style>
</head>
<body>
  <main class="page">
    <section class="topbar">
      <h1>{title}</h1>
      <p>Source PDF: <a href="../source.pdf" target="_blank" rel="noreferrer">{artifact.source_pdf_path.name}</a></p>
      <p>Pages: {artifact.page_count} | Paragraphs: {artifact.paragraph_count} | Assets: {artifact.asset_count}</p>
      <p>Compare the original page image against the rendered HTML page iframe and note which visual/structural features were not reflected.</p>
    </section>
    {''.join(page_cards)}
  </main>
</body>
</html>
"""
    return body


def _build_global_index(artifacts: list[DocumentArtifact], *, input_dir: Path) -> str:
    rows = []
    for artifact in artifacts:
        rel_dir = artifact.output_dir.name
        rows.append(
            f"""
<tr>
  <td>{artifact.source_pdf_path.name}</td>
  <td>{artifact.page_count}</td>
  <td>{artifact.paragraph_count}</td>
  <td>{artifact.asset_count}</td>
  <td><a href="{rel_dir}/review/index.html">review</a></td>
  <td><a href="{rel_dir}/full.html">rendered html</a></td>
  <td><a href="{rel_dir}/source.pdf">source pdf</a></td>
</tr>
"""
        )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PDF Review Index</title>
<style>
  body {{
    margin: 0;
    font-family: system-ui, sans-serif;
    background: #eef1f5;
    color: #111;
  }}
  main {{
    max-width: 1400px;
    margin: 0 auto;
    padding: 24px;
  }}
  .panel {{
    background: #fff;
    border: 1px solid #d7dde7;
    padding: 18px 20px;
    margin-bottom: 20px;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: #fff;
  }}
  th, td {{
    border: 1px solid #d7dde7;
    padding: 10px 12px;
    text-align: left;
    vertical-align: top;
  }}
  th {{
    background: #f5f7fb;
  }}
</style>
</head>
<body>
  <main>
    <section class="panel">
      <h1>PDF Batch Review</h1>
      <p>Input directory: {input_dir}</p>
      <p>Documents: {len(artifacts)} | Total pages: {sum(a.page_count for a in artifacts)}</p>
    </section>
    <section class="panel">
      <table>
        <thead>
          <tr>
            <th>PDF</th>
            <th>Pages</th>
            <th>Paragraphs</th>
            <th>Assets</th>
            <th>Review</th>
            <th>Rendered HTML</th>
            <th>Source PDF</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


def render_one_pdf(pdf_path: Path, *, output_root: Path, dpi: int, index: int) -> DocumentArtifact:
    slug = _safe_slug(pdf_path, index)
    doc_dir = output_root / slug
    rendered_pages_dir = doc_dir / "rendered-pages"
    source_pages_dir = doc_dir / "review" / "source-pages"
    review_dir = doc_dir / "review"

    rendered_pages_dir.mkdir(parents=True, exist_ok=True)
    source_pages_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)

    doc = DocIR.from_file(pdf_path, doc_type="pdf")
    full_html = doc.to_html(title=pdf_path.stem)
    full_html_path = doc_dir / "full.html"
    full_html_path.write_text(full_html, encoding="utf-8")

    source_pdf_copy = doc_dir / "source.pdf"
    if not source_pdf_copy.exists():
        source_pdf_copy.write_bytes(pdf_path.read_bytes())

    page_sections = _extract_page_sections(full_html)
    rendered_page_paths: list[Path] = []
    for page_index, section_html in enumerate(page_sections, start=1):
        rendered_page_path = rendered_pages_dir / f"page-{page_index:03d}.html"
        rendered_page_path.write_text(
            _page_html_document(title=f"{pdf_path.stem} - page {page_index}", section_html=section_html),
            encoding="utf-8",
        )
        rendered_page_paths.append(rendered_page_path)

    source_page_paths = _render_pdf_pages_to_bmp(pdf_path, output_dir=source_pages_dir, dpi=dpi)

    page_artifacts: list[PageArtifact] = []
    page_count = max(len(source_page_paths), len(rendered_page_paths), len(doc.pages))
    for page_number in range(1, page_count + 1):
        source_image_path = source_pages_dir / f"page-{page_number:03d}.bmp"
        rendered_page_path = rendered_pages_dir / f"page-{page_number:03d}.html"
        if not source_image_path.exists() or not rendered_page_path.exists():
            continue
        page_artifacts.append(
            PageArtifact(
                page_number=page_number,
                source_image_path=source_image_path.relative_to(review_dir),
                rendered_page_path=rendered_page_path.relative_to(doc_dir),
            )
        )

    artifact = DocumentArtifact(
        source_pdf_path=pdf_path,
        output_dir=doc_dir,
        full_html_path=full_html_path,
        review_html_path=review_dir / "index.html",
        page_count=len(doc.pages) or len(source_page_paths),
        paragraph_count=len(doc.paragraphs),
        asset_count=len(doc.assets),
        page_artifacts=page_artifacts,
    )
    artifact.review_html_path.write_text(
        _build_review_html(artifact, title=pdf_path.stem),
        encoding="utf-8",
    )
    return artifact


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    pdf_files = find_pdf_files(input_dir)
    if args.limit is not None:
        pdf_files = pdf_files[: args.limit]
    if not pdf_files:
        raise SystemExit(f"No PDF files found in: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[DocumentArtifact] = []
    for index, pdf_path in enumerate(pdf_files, start=1):
        artifact = render_one_pdf(pdf_path, output_root=output_dir, dpi=args.dpi, index=index)
        artifacts.append(artifact)
        print(
            f"[ok] {pdf_path.name} -> {artifact.review_html_path} "
            f"(pages={artifact.page_count}, paragraphs={artifact.paragraph_count}, assets={artifact.asset_count})"
        )

    (output_dir / "index.html").write_text(
        _build_global_index(artifacts, input_dir=input_dir),
        encoding="utf-8",
    )
    print(f"[done] review index: {output_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
