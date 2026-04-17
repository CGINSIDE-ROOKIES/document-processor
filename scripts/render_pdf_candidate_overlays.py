from __future__ import annotations

import argparse
from collections import Counter
from html import escape
from pathlib import Path

import pypdfium2 as pdfium

from document_processor.pdf.preview import _build_visual_block_candidates, _extract_pdfium_visual_primitives


TYPE_COLORS = {
    "axis_box": "#ef4444",
    "open_frame": "#f59e0b",
    "semantic_line": "#2563eb",
    "long_rule": "#10b981",
}


def _render_document_overlays(doc_dir: Path) -> Path:
    source_pdf = doc_dir / "source.pdf"
    source_png_dir = doc_dir / "review" / "source-pages-png"
    source_bmp_dir = doc_dir / "review" / "source-pages"
    output_dir = doc_dir / "review" / "candidate-overlays"
    output_dir.mkdir(parents=True, exist_ok=True)

    with pdfium.PdfDocument(str(source_pdf)) as pdf:
        index_rows: list[str] = []
        for page_index in range(len(pdf)):
            page_number = page_index + 1
            page = pdf[page_index]
            page_width = page.get_width() or 1.0
            page_height = page.get_height() or 1.0
            primitives = _extract_pdfium_visual_primitives(page, page_number=page_number)
            page_candidates = _build_visual_block_candidates(primitives)
            role_counts = Counter(item.candidate_type for item in page_candidates)
            source_image = source_png_dir / f"page-{page_number:03d}.png"
            if not source_image.exists():
                source_image = source_bmp_dir / f"page-{page_number:03d}.bmp"
            page_html = _render_page_overlay(
                source_image=source_image,
                page_number=page_number,
                page_width=page_width,
                page_height=page_height,
                primitives=primitives,
                candidates=page_candidates,
            )
            page_path = output_dir / f"page-{page_number:03d}.html"
            page_path.write_text(page_html, encoding="utf-8")
            counts_text = ", ".join(f"{key}:{value}" for key, value in sorted(role_counts.items())) or "-"
            index_rows.append(
                "<tr>"
                f"<td>{page_number}</td>"
                f"<td>{len(page_candidates)}</td>"
                f"<td>{escape(counts_text)}</td>"
                f'<td><a href="{escape(page_path.name)}">overlay</a></td>'
                f'<td><a href="../{escape(source_image.parent.name)}/{escape(source_image.name)}">source image</a></td>'
                "</tr>"
            )

    index_html = _render_index(doc_dir.name, index_rows)
    index_path = output_dir / "index.html"
    index_path.write_text(index_html, encoding="utf-8")
    return index_path


def _render_page_overlay(
    *,
    source_image: Path,
    page_number: int,
    page_width: float,
    page_height: float,
    primitives,
    candidates,
) -> str:
    primitive_by_draw_order = {primitive.draw_order: primitive for primitive in primitives}
    boxes: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        color = TYPE_COLORS.get(candidate.candidate_type, "#ff2d20")
        if candidate.candidate_type == "semantic_line":
            for draw_order in candidate.primitive_draw_orders:
                primitive = primitive_by_draw_order.get(draw_order)
                if primitive is None:
                    continue
                bbox = primitive.bounding_box
                left = bbox.left_pt / page_width * 100.0
                width = (bbox.right_pt - bbox.left_pt) / page_width * 100.0
                top = (page_height - bbox.top_pt) / page_height * 100.0
                height = (bbox.top_pt - bbox.bottom_pt) / page_height * 100.0
                boxes.append(
                    f'<div class="line" style="left:{left:.4f}%;top:{top:.4f}%;width:{width:.4f}%;height:{height:.4f}%;'
                    f'background:{color};"></div>'
                )
            continue

        bbox = candidate.bounding_box
        left = bbox.left_pt / page_width * 100.0
        width = (bbox.right_pt - bbox.left_pt) / page_width * 100.0
        top = (page_height - bbox.top_pt) / page_height * 100.0
        height = (bbox.top_pt - bbox.bottom_pt) / page_height * 100.0
        boxes.append(
            f'<div class="box" style="left:{left:.4f}%;top:{top:.4f}%;width:{width:.4f}%;height:{height:.4f}%;'
            f'border-color:{color};border-width:3px;"></div>'
        )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>Candidate Overlay p{page_number}</title>
<style>
  body {{
    margin: 0;
    padding: 16px;
    background: #f3f3f3;
    font-family: monospace;
  }}
  .wrap {{
    position: relative;
    display: inline-block;
    box-shadow: 0 2px 10px rgba(0,0,0,.12);
  }}
  .wrap img {{
    display: block;
    max-width: min(96vw, 1400px);
    height: auto;
  }}
  .box {{
    position: absolute;
    border: 3px solid #ff2d20;
    box-sizing: border-box;
    opacity: 0.9;
    pointer-events: none;
  }}
  .line {{
    position: absolute;
    box-sizing: border-box;
    opacity: 0.9;
    pointer-events: none;
  }}
  .legend {{
    margin-bottom: 12px;
    line-height: 1.6;
  }}
</style>
</head>
<body>
  <div class="legend">
    <div>page {page_number}</div>
    <div>candidates: {len(candidates)}</div>
  </div>
  <div class="wrap">
    <img src="../{escape(source_image.parent.name)}/{escape(source_image.name)}" alt="page {page_number}">
    {''.join(boxes)}
  </div>
</body>
</html>
"""


def _render_index(doc_name: str, rows: list[str]) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>Candidate Overlays {escape(doc_name)}</title>
<style>
  body {{
    margin: 24px;
    font-family: sans-serif;
    color: #111;
  }}
  table {{
    border-collapse: collapse;
    width: 100%;
    max-width: 960px;
  }}
  th, td {{
    border: 1px solid #ddd;
    padding: 8px 10px;
    text-align: left;
    vertical-align: top;
  }}
  th {{
    background: #f5f5f5;
  }}
</style>
</head>
<body>
  <h1>{escape(doc_name)} candidate overlays</h1>
  <table>
    <thead>
      <tr>
        <th>page</th>
        <th>candidate count</th>
        <th>types</th>
        <th>overlay</th>
        <th>source</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("doc_dirs", nargs="+", type=Path)
    args = parser.parse_args()

    for doc_dir in args.doc_dirs:
        index_path = _render_document_overlays(doc_dir.resolve())
        print(index_path)


if __name__ == "__main__":
    main()
