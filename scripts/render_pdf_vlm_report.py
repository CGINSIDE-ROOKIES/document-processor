#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_REVIEW_ROOT = Path("out/pdf-review/text-pdf")
DEFAULT_INPUT_PATH = DEFAULT_REVIEW_ROOT / "vlm-findings-high.jsonl"
DEFAULT_HTML_PATH = DEFAULT_REVIEW_ROOT / "vlm-findings-high-report.html"
DEFAULT_CSV_PATH = DEFAULT_REVIEW_ROOT / "vlm-findings-high-pages.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a human-readable HTML/CSV report from VLM PDF fidelity JSONL findings."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_PATH,
        help=f"Input JSONL findings path (default: {DEFAULT_INPUT_PATH})",
    )
    parser.add_argument(
        "--html-output",
        type=Path,
        default=DEFAULT_HTML_PATH,
        help=f"Output HTML report path (default: {DEFAULT_HTML_PATH})",
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"Output CSV path (default: {DEFAULT_CSV_PATH})",
    )
    parser.add_argument(
        "--review-root",
        type=Path,
        default=DEFAULT_REVIEW_ROOT,
        help=f"Review root for relative links (default: {DEFAULT_REVIEW_ROOT})",
    )
    return parser.parse_args()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _severity_rank(record: dict[str, Any]) -> tuple[int, int, int]:
    findings = record.get("vlm_findings") or {}
    status = findings.get("status")
    page_size = findings.get("page_size_fidelity")
    page_length = findings.get("page_length_fidelity")
    status_rank = {"major_mismatch": 0, "minor_mismatch": 1, "match": 2}.get(status, 3)
    size_rank = {"major_mismatch": 0, "minor_mismatch": 1, "match": 2}.get(page_size, 3)
    length_rank = {"major_mismatch": 0, "minor_mismatch": 1, "match": 2}.get(page_length, 3)
    return status_rank, size_rank, length_rank


def _flatten_findings(findings: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in findings:
        severity = item.get("severity", "")
        category = item.get("category", "")
        description = item.get("description", "")
        parts.append(f"[{severity}] {category}: {description}".strip())
    return " | ".join(parts)


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "document_id",
                "page_number",
                "vlm_status",
                "status",
                "page_size_fidelity",
                "page_length_fidelity",
                "likely_stage",
                "summary",
                "findings",
                "review_page",
            ]
        )
        for record in records:
            findings = record.get("vlm_findings") or {}
            writer.writerow(
                [
                    record.get("document_id"),
                    record.get("page_number"),
                    record.get("vlm_status"),
                    findings.get("status", ""),
                    findings.get("page_size_fidelity", ""),
                    findings.get("page_length_fidelity", ""),
                    findings.get("likely_stage", ""),
                    findings.get("summary", ""),
                    _flatten_findings(findings.get("findings", [])),
                    f"{record.get('document_id')}/review/index.html",
                ]
            )


def _status_badge(label: str) -> str:
    safe = html.escape(label or "-")
    cls = (
        "major" if label == "major_mismatch" else
        "minor" if label == "minor_mismatch" else
        "match" if label == "match" else
        "neutral"
    )
    return f'<span class="badge {cls}">{safe}</span>'


def _write_html(path: Path, records: list[dict[str, Any]], *, input_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    by_document: dict[str, list[dict[str, Any]]] = defaultdict(list)
    status_counter: Counter[str] = Counter()
    page_size_counter: Counter[str] = Counter()
    page_length_counter: Counter[str] = Counter()
    stage_counter: Counter[str] = Counter()
    category_counter: Counter[str] = Counter()

    for record in records:
        by_document[str(record["document_id"])].append(record)
        findings = record.get("vlm_findings") or {}
        status_counter[str(findings.get("status"))] += 1
        page_size_counter[str(findings.get("page_size_fidelity"))] += 1
        page_length_counter[str(findings.get("page_length_fidelity"))] += 1
        stage_counter[str(findings.get("likely_stage"))] += 1
        for item in findings.get("findings", []):
            category_counter[str(item.get("category"))] += 1

    top_categories = "".join(
        f"<li><strong>{html.escape(category)}</strong>: {count}</li>"
        for category, count in category_counter.most_common(15)
    )

    document_sections: list[str] = []
    for document_id, doc_records in sorted(by_document.items()):
        doc_records.sort(key=lambda r: (_severity_rank(r), r["page_number"]))
        review_index_rel = Path(document_id) / "review" / "index.html"
        page_rows: list[str] = []
        for record in doc_records:
            findings = record.get("vlm_findings") or {}
            source_image_rel = Path(str(record["source_image"])).with_suffix(".png")
            rendered_image_rel = Path(str(record["rendered_page_screenshot"]))
            page_rows.append(
                "<tr>"
                f"<td>{record['page_number']}</td>"
                f"<td>{_status_badge(str(findings.get('status')))}</td>"
                f"<td>{_status_badge(str(findings.get('page_size_fidelity')))}</td>"
                f"<td>{_status_badge(str(findings.get('page_length_fidelity')))}</td>"
                f"<td>{html.escape(str(findings.get('likely_stage', '')))}</td>"
                f"<td>{html.escape(str(findings.get('summary', '')))}</td>"
                f"<td><a href='{html.escape(str(source_image_rel))}'>원본</a> / "
                f"<a href='{html.escape(str(rendered_image_rel))}'>렌더</a> / "
                f"<a href='{html.escape(str(review_index_rel))}'>리뷰</a></td>"
                "</tr>"
            )

        document_sections.append(
            "<section class='doc-section'>"
            f"<h2>{html.escape(document_id)}</h2>"
            f"<p><a href='{html.escape(str(review_index_rel))}'>문서 review 페이지 열기</a></p>"
            "<table>"
            "<thead><tr>"
            "<th>Page</th><th>Status</th><th>Page Size</th><th>Page Length</th><th>Likely Stage</th><th>Summary</th><th>Links</th>"
            "</tr></thead>"
            f"<tbody>{''.join(page_rows)}</tbody>"
            "</table>"
            "</section>"
        )

    html_text = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PDF VLM Findings Report</title>
<style>
body {{
  margin: 0;
  padding: 24px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #f4f5f7;
  color: #111827;
}}
h1, h2 {{ margin: 0 0 12px 0; }}
.summary-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin: 20px 0 28px 0;
}}
.card {{
  background: #fff;
  border: 1px solid #d1d5db;
  border-radius: 12px;
  padding: 16px;
}}
.badge {{
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}}
.badge.major {{ background: #fee2e2; color: #991b1b; }}
.badge.minor {{ background: #fef3c7; color: #92400e; }}
.badge.match {{ background: #dcfce7; color: #166534; }}
.badge.neutral {{ background: #e5e7eb; color: #374151; }}
.doc-section {{
  background: #fff;
  border: 1px solid #d1d5db;
  border-radius: 12px;
  padding: 18px;
  margin: 18px 0;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  margin-top: 10px;
  font-size: 14px;
}}
th, td {{
  text-align: left;
  vertical-align: top;
  padding: 10px 8px;
  border-top: 1px solid #e5e7eb;
}}
th {{
  background: #f9fafb;
  border-top: none;
}}
ul {{ margin: 8px 0 0 18px; }}
a {{ color: #1d4ed8; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>PDF VLM Findings Report</h1>
<p>입력: {html.escape(str(input_path))}</p>
<div class="summary-grid">
  <div class="card"><strong>Total pages</strong><div>{len(records)}</div></div>
  <div class="card"><strong>Status</strong><div>{html.escape(str(dict(status_counter)))}</div></div>
  <div class="card"><strong>Page size</strong><div>{html.escape(str(dict(page_size_counter)))}</div></div>
  <div class="card"><strong>Page length</strong><div>{html.escape(str(dict(page_length_counter)))}</div></div>
  <div class="card"><strong>Likely stage</strong><div>{html.escape(str(dict(stage_counter)))}</div></div>
</div>
<section class="card">
  <h2>Top categories</h2>
  <ul>{top_categories}</ul>
</section>
{''.join(document_sections)}
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    html_output = args.html_output.expanduser().resolve()
    csv_output = args.csv_output.expanduser().resolve()
    records = _load_jsonl(input_path)
    records.sort(key=lambda r: (str(r["document_id"]), _severity_rank(r), r["page_number"]))

    _write_csv(csv_output, records)
    _write_html(html_output, records, input_path=input_path)

    print(f"[done] html report: {html_output}")
    print(f"[done] csv report: {csv_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
