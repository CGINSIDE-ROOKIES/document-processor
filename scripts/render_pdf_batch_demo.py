#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from document_processor import DocIR  # noqa: E402

DEFAULT_INPUT_DIR = Path("/Users/yoonseo/Developer/External/RAGBuilder-test/Dataset/temp-test")
DEFAULT_OUTPUT_DIR = REPO_ROOT / "out" / "pdf-render" / "temp-test-demo"


@dataclass
class RenderResult:
    source_path: Path
    html_path: Path
    page_count: int
    paragraph_count: int
    asset_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render every PDF in a directory to HTML and optionally open the results in Google Chrome."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"Directory containing PDF files (default: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory where HTML files will be written (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the generated HTML files in Google Chrome after rendering.",
    )
    parser.add_argument(
        "--chrome-app",
        default="Google Chrome",
        help='macOS app name passed to `open -a` when --open is used (default: "Google Chrome")',
    )
    return parser.parse_args()


def find_pdf_files(input_dir: Path) -> list[Path]:
    return sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")


def render_pdf_to_html(pdf_path: Path, output_dir: Path) -> RenderResult:
    doc = DocIR.from_file(pdf_path, doc_type="pdf")
    html = doc.to_html(title=pdf_path.stem)

    html_path = output_dir / f"{pdf_path.stem}.html"
    html_path.write_text(html, encoding="utf-8")

    return RenderResult(
        source_path=pdf_path,
        html_path=html_path,
        page_count=len(doc.pages),
        paragraph_count=len(doc.paragraphs),
        asset_count=len(doc.assets),
    )


def open_in_chrome(paths: list[Path], chrome_app: str) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("--open currently expects macOS because it uses `open -a`.")
    if not paths:
        return
    subprocess.run(["open", "-a", chrome_app, *(str(path) for path in paths)], check=True)


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    pdf_files = find_pdf_files(input_dir)
    if not pdf_files:
        raise SystemExit(f"No PDF files found in: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[RenderResult] = []
    for pdf_path in pdf_files:
        result = render_pdf_to_html(pdf_path, output_dir)
        results.append(result)
        print(
            f"[ok] {pdf_path.name} -> {result.html_path} "
            f"(pages={result.page_count}, paragraphs={result.paragraph_count}, assets={result.asset_count})"
        )

    if args.open:
        open_in_chrome([result.html_path for result in results], args.chrome_app)
        print(f"[open] Opened {len(results)} HTML files in {args.chrome_app}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
