#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(REPO_ROOT / "src"))

from document_processor.env_utils import load_dotenv_file

DEFAULT_REVIEW_ROOT = Path("out/pdf-review/text-pdf")
DEFAULT_MODEL = "gpt-4.1"
DEFAULT_OUTPUT_PATH = Path("out/pdf-review/text-pdf/vlm-findings.jsonl")
API_URL = "https://api.openai.com/v1/responses"

PROMPT = """You are reviewing PDF-to-HTML rendering fidelity.

You will receive two page images:
1. The original PDF page
2. The rendered HTML page screenshot

Compare them visually as a whole page, not by OCR token matching.

Your priority order is:
1. Structural loss
2. Page geometry mismatch
3. Layout collapse
4. Secondary styling differences

Be strict about structure first.
If the HTML page loses boxes, callouts, tables, badges, sidebars, page bands,
grouping, or region layout, that matters more than small font differences.
Judge the rendered page against the original page frame first: page size, usable
content area, and whether the overall composition was flattened into a taller
single-flow page.

You must explicitly check:
- whether the overall page size/proportion looks preserved
- whether the rendered page became unnaturally longer or shorter than the source page
- whether parallel regions were collapsed into one vertical flow
- whether tables, boxed notices, labels, badges, and separators were lost
- whether right/center alignment was lost
- whether list structure or caption attachment was lost
- whether heading hierarchy was flattened
- whether spacing/indentation changed enough to alter structure

Treat these as high-severity when they happen:
- table structure loss
- boxed notice / callout loss
- section badge / label loss
- page-length expansion or shrinkage caused by layout collapse
- multi-column or region collapse
- major alignment collapse
- page frame preserved poorly even if most text is present

Treat these as lower-severity:
- small font differences
- minor line wrapping differences that do not change structure
- small spacing drift with preserved grouping

Return STRICT JSON with this schema:
{
  "status": "match" | "minor_mismatch" | "major_mismatch",
  "page_size_fidelity": "match" | "minor_mismatch" | "major_mismatch",
  "page_length_fidelity": "match" | "minor_mismatch" | "major_mismatch",
  "likely_stage": "render_only" | "preview_or_layout" | "raw_or_structure" | "unclear",
  "summary": "short Korean sentence",
  "findings": [
    {
      "severity": "high" | "medium" | "low",
      "category": "free short label",
      "description": "Korean sentence describing what was lost or distorted"
    }
  ]
}

Rules:
- Do not mention OCR confidence.
- Do not output markdown.
- Keep findings grounded in visible page differences only.
- Prefer finding fewer, stronger structural issues over many tiny visual notes.
- If page height or overall page proportion is visibly different, mention it explicitly.
- If side-by-side or boxed content was flattened into vertical flow, prefer `major_mismatch`.
- If pages are mostly faithful, return status=match with an empty findings list or only low-severity notes.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare original PDF page images against rendered HTML screenshots with "
            "an OpenAI vision model and emit per-page JSON findings."
        )
    )
    parser.add_argument(
        "--review-root",
        type=Path,
        default=DEFAULT_REVIEW_ROOT,
        help=f"Root directory containing vlm-manifest.jsonl (default: {DEFAULT_REVIEW_ROOT})",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Optional explicit manifest path. Defaults to <review-root>/vlm-manifest.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output JSONL path (default: {DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenAI model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--image-detail",
        choices=("low", "high", "auto"),
        default="low",
        help="Image detail level sent to the model (default: low)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit on the number of page pairs to compare.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="Optional delay between requests.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=450,
        help="Maximum response tokens per page comparison (default: 450)",
    )
    return parser.parse_args()


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def _read_image_data_url(path: Path) -> str:
    resolved_path = path
    if path.suffix.lower() == ".bmp":
        png_path = path.with_suffix(".png")
        if png_path.exists():
            resolved_path = png_path

    suffix = resolved_path.suffix.lower()
    if suffix == ".png":
        mime_type = "image/png"
    elif suffix in {".jpg", ".jpeg"}:
        mime_type = "image/jpeg"
    elif suffix == ".gif":
        mime_type = "image/gif"
    elif suffix == ".webp":
        mime_type = "image/webp"
    else:
        raise ValueError(f"Unsupported image input for OpenAI API: {resolved_path}")

    encoded = base64.b64encode(resolved_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _responses_payload(
    *,
    model: str,
    original_path: Path,
    rendered_path: Path,
    image_detail: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "temperature": 0,
        "max_output_tokens": max_output_tokens,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": PROMPT},
                    {
                        "type": "input_text",
                        "text": "Image 1: original PDF page",
                    },
                    {
                        "type": "input_image",
                        "image_url": _read_image_data_url(original_path),
                        "detail": image_detail,
                    },
                    {
                        "type": "input_text",
                        "text": "Image 2: rendered HTML screenshot",
                    },
                    {
                        "type": "input_image",
                        "image_url": _read_image_data_url(rendered_path),
                        "detail": image_detail,
                    },
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "render_fidelity_review",
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["match", "minor_mismatch", "major_mismatch"],
                        },
                        "page_size_fidelity": {
                            "type": "string",
                            "enum": ["match", "minor_mismatch", "major_mismatch"],
                        },
                        "page_length_fidelity": {
                            "type": "string",
                            "enum": ["match", "minor_mismatch", "major_mismatch"],
                        },
                        "likely_stage": {
                            "type": "string",
                            "enum": ["render_only", "preview_or_layout", "raw_or_structure", "unclear"],
                        },
                        "summary": {"type": "string"},
                        "findings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "severity": {
                                        "type": "string",
                                        "enum": ["high", "medium", "low"],
                                    },
                                    "category": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["severity", "category", "description"],
                            },
                        },
                    },
                    "required": [
                        "status",
                        "page_size_fidelity",
                        "page_length_fidelity",
                        "likely_stage",
                        "summary",
                        "findings",
                    ],
                },
            }
        },
    }


def _call_openai(*, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"HTTP {exc.code}: {body}") from exc


def _extract_response_json(response_json: dict[str, Any]) -> dict[str, Any]:
    if isinstance(response_json.get("output_parsed"), dict):
        return response_json["output_parsed"]

    output = response_json.get("output")
    if not isinstance(output, list):
        raise ValueError("Unexpected Responses API payload: missing output list")
    for item in output:
        if not isinstance(item, dict):
            continue
        contents = item.get("content")
        if not isinstance(contents, list):
            continue
        for content_item in contents:
            if not isinstance(content_item, dict):
                continue
            text_value = content_item.get("text")
            if isinstance(text_value, str):
                return json.loads(text_value)
    raise ValueError("Could not extract JSON result from Responses API payload")


def main() -> int:
    load_dotenv_file(REPO_ROOT / ".env")
    args = parse_args()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set.")

    review_root = args.review_root.expanduser().resolve()
    manifest_path = (
        args.manifest.expanduser().resolve()
        if args.manifest is not None
        else review_root / "vlm-manifest.jsonl"
    )
    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = _load_manifest(manifest_path)
    if args.limit is not None:
        manifest = manifest[: args.limit]
    if not manifest:
        raise SystemExit(f"No page pairs found in manifest: {manifest_path}")

    with output_path.open("w", encoding="utf-8") as fh:
        for index, record in enumerate(manifest, start=1):
            original_path = review_root / str(record["source_image"])
            rendered_path = review_root / str(record["rendered_page_screenshot"])
            payload = _responses_payload(
                model=args.model,
                original_path=original_path,
                rendered_path=rendered_path,
                image_detail=args.image_detail,
                max_output_tokens=args.max_output_tokens,
            )
            try:
                response_json = _call_openai(api_key=api_key, payload=payload)
                findings = _extract_response_json(response_json)
                status = "ok"
                error = None
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                findings = None
                status = "error"
                error = str(exc)

            output_record = {
                **record,
                "vlm_status": status,
                "vlm_findings": findings,
                "vlm_error": error,
            }
            fh.write(json.dumps(output_record, ensure_ascii=False) + "\n")
            print(f"[{index}/{len(manifest)}] {record['document_id']} page {record['page_number']} -> {status}")
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)

    print(f"[done] findings written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
