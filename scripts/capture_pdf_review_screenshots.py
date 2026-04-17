#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

DEFAULT_REVIEW_ROOT = Path("out/pdf-review/text-pdf")
DEFAULT_WINDOW_WIDTH = 1600
DEFAULT_WINDOW_HEIGHT = 2400
DEFAULT_CHROME_PATH = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture screenshot PNGs for rendered PDF review pages using headless Chrome "
            "and emit a page-pair manifest for VLM comparison."
        )
    )
    parser.add_argument(
        "--review-root",
        type=Path,
        default=DEFAULT_REVIEW_ROOT,
        help=f"Root directory produced by render_pdf_review_batch.py (default: {DEFAULT_REVIEW_ROOT})",
    )
    parser.add_argument(
        "--chrome-path",
        type=Path,
        default=DEFAULT_CHROME_PATH,
        help=f"Path to the Chrome binary used for headless screenshots (default: {DEFAULT_CHROME_PATH})",
    )
    parser.add_argument(
        "--window-width",
        type=int,
        default=DEFAULT_WINDOW_WIDTH,
        help=f"Headless Chrome viewport width (default: {DEFAULT_WINDOW_WIDTH})",
    )
    parser.add_argument(
        "--window-height",
        type=int,
        default=DEFAULT_WINDOW_HEIGHT,
        help=f"Headless Chrome viewport height (default: {DEFAULT_WINDOW_HEIGHT})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit on the number of rendered page HTML files to capture.",
    )
    return parser.parse_args()


def _document_dirs(review_root: Path) -> list[Path]:
    return sorted(path for path in review_root.iterdir() if path.is_dir())


def _page_html_paths(document_dir: Path) -> list[Path]:
    rendered_dir = document_dir / "rendered-pages"
    if not rendered_dir.exists():
        return []
    return sorted(rendered_dir.glob("page-*.html"))


def _source_image_path(document_dir: Path, page_html_path: Path) -> Path:
    page_suffix = page_html_path.stem.replace("page-", "")
    return document_dir / "review" / "source-pages" / f"page-{page_suffix}.bmp"


def _screenshot_path(document_dir: Path, page_html_path: Path) -> Path:
    target_dir = document_dir / "rendered-page-images"
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"{page_html_path.stem}.png"


def _capture_html_screenshot(
    *,
    chrome_path: Path,
    html_path: Path,
    screenshot_path: Path,
    window_width: int,
    window_height: int,
) -> None:
    file_url = html_path.resolve().as_uri()
    command = [
        str(chrome_path),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--window-size={window_width},{window_height}",
        f"--screenshot={screenshot_path}",
        file_url,
    ]
    subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )


def _manifest_record(
    *,
    review_root: Path,
    document_dir: Path,
    page_html_path: Path,
    source_image_path: Path,
    screenshot_path: Path,
) -> dict[str, object]:
    page_number = int(page_html_path.stem.split("-")[-1])
    return {
        "document_id": document_dir.name,
        "pdf_name": (document_dir / "source.pdf").name,
        "page_number": page_number,
        "source_pdf": str((document_dir / "source.pdf").relative_to(review_root)),
        "source_image": str(source_image_path.relative_to(review_root)),
        "rendered_page_html": str(page_html_path.relative_to(review_root)),
        "rendered_page_screenshot": str(screenshot_path.relative_to(review_root)),
    }


def main() -> int:
    args = parse_args()
    review_root = args.review_root.expanduser().resolve()
    chrome_path = args.chrome_path.expanduser().resolve()

    if not review_root.exists():
        raise SystemExit(f"Review root does not exist: {review_root}")
    if not chrome_path.exists():
        raise SystemExit(f"Chrome binary does not exist: {chrome_path}")

    manifest_records: list[dict[str, object]] = []
    captured = 0
    for document_dir in _document_dirs(review_root):
        for page_html_path in _page_html_paths(document_dir):
            if args.limit is not None and captured >= args.limit:
                break
            source_image_path = _source_image_path(document_dir, page_html_path)
            if not source_image_path.exists():
                continue
            screenshot_path = _screenshot_path(document_dir, page_html_path)
            _capture_html_screenshot(
                chrome_path=chrome_path,
                html_path=page_html_path,
                screenshot_path=screenshot_path,
                window_width=args.window_width,
                window_height=args.window_height,
            )
            manifest_records.append(
                _manifest_record(
                    review_root=review_root,
                    document_dir=document_dir,
                    page_html_path=page_html_path,
                    source_image_path=source_image_path,
                    screenshot_path=screenshot_path,
                )
            )
            captured += 1
            print(f"[ok] {page_html_path} -> {screenshot_path}")
        if args.limit is not None and captured >= args.limit:
            break

    manifest_path = review_root / "vlm-manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8") as fh:
        for record in manifest_records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[done] screenshots={captured} manifest={manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
