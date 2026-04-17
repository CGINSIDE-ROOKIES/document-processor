#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from render_pdf_candidate_overlays import _render_document_overlays  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render candidate overlay HTML for every reviewed PDF document directory."
    )
    parser.add_argument(
        "--review-root",
        type=Path,
        required=True,
        help="Root directory produced by render_pdf_review_batch.py",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit on number of document directories to process.",
    )
    return parser.parse_args()


def _document_dirs(review_root: Path) -> list[Path]:
    return sorted(path for path in review_root.iterdir() if path.is_dir())


def _build_root_index(rows: list[str]) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Candidate Overlay Index</title>
<style>
  body {{
    margin: 24px;
    font-family: system-ui, sans-serif;
    background: #eef1f5;
    color: #111;
  }}
  .panel {{
    max-width: 1200px;
    margin: 0 auto 20px auto;
    background: #fff;
    border: 1px solid #d7dde7;
    padding: 18px 20px;
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
  <section class="panel">
    <h1>Candidate Overlay Index</h1>
    <p>Review root: {len(rows)} documents</p>
  </section>
  <section class="panel">
    <table>
      <thead>
        <tr>
          <th>Document</th>
          <th>Candidate Overlay</th>
          <th>Review</th>
          <th>Rendered HTML</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
  </section>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    review_root = args.review_root.expanduser().resolve()
    if not review_root.exists():
        raise SystemExit(f"Review root does not exist: {review_root}")

    doc_dirs = _document_dirs(review_root)
    if args.limit is not None:
        doc_dirs = doc_dirs[: args.limit]

    rows: list[str] = []
    for doc_dir in doc_dirs:
        if not (doc_dir / "source.pdf").exists():
            continue
        index_path = _render_document_overlays(doc_dir)
        print(index_path)
        rows.append(
            "<tr>"
            f"<td>{doc_dir.name}</td>"
            f'<td><a href="{index_path.relative_to(review_root)}">candidate overlays</a></td>'
            f'<td><a href="{(doc_dir / "review" / "index.html").relative_to(review_root)}">review</a></td>'
            f'<td><a href="{(doc_dir / "full.html").relative_to(review_root)}">full html</a></td>'
            "</tr>"
        )

    root_index = review_root / "candidate-overlays-index.html"
    root_index.write_text(_build_root_index(rows), encoding="utf-8")
    print(root_index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
