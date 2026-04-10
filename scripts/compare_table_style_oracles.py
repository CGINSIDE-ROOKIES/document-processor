#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from document_processor import DocIR  # noqa: E402
from document_processor.render_prep import prepare_doc_ir_for_html  # noqa: E402

SIDES = ("top", "bottom", "left", "right")
DEFAULT_OUTPUT_DIR = REPO_ROOT / "out" / "table-style-oracle"
DEFAULT_PAIRS: tuple[tuple[str, Path, Path], ...] = (
    (
        "modu_ax_llm",
        Path("/Users/yoonseo/Developer/External/RAGBuilder-test/dataset-2/모두의_챌린지_AX_-_LLM_분야_참여기업_모집공고.pdf"),
        Path("/Users/yoonseo/Developer/External/RAGBuilder-test/dataset-2/모두의_챌린지_AX_-_LLM_분야_참여기업_모집공고.hwpx"),
    ),
    (
        "2026_jeontongsijang",
        Path("/Users/yoonseo/Developer/External/RAGBuilder-test/dataset-2/2026년_전통시장_육성사업(백년시장)_모집공고.pdf"),
        Path("/Users/yoonseo/Developer/External/RAGBuilder-test/dataset-2/2026년_전통시장_육성사업(백년시장)_모집공고(수정).hwpx"),
    ),
)


@dataclass
class CellSummary:
    row: int
    col: int
    rowspan: int
    colspan: int
    text: str
    has_text: bool
    background: str | None
    borders: dict[str, str | None]


@dataclass
class TableSummary:
    index: int
    unit_id: str
    row_count: int
    col_count: int
    logical_cell_count: int
    covered_logical_cell_count: int
    non_empty_cell_count: int
    spanning_cell_count: int
    text_fingerprint: str
    span_fingerprint: str
    cells_by_position: dict[tuple[int, int], CellSummary]


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip().lower()


def normalize_style_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", str(value)).strip().lower()
    return normalized or None


def iter_tables_from_doc(doc: DocIR) -> list[Any]:
    tables: list[Any] = []

    def visit_paragraph(paragraph: Any) -> None:
        for node in getattr(paragraph, "content", []):
            if type(node).__name__ == "TableIR":
                tables.append(node)
                for cell in getattr(node, "cells", []):
                    for child_paragraph in getattr(cell, "paragraphs", []):
                        visit_paragraph(child_paragraph)

    for paragraph in doc.paragraphs:
        visit_paragraph(paragraph)
    return tables


def summarize_table(table: Any, index: int) -> TableSummary:
    cells_by_position: dict[tuple[int, int], CellSummary] = {}
    max_row = max(getattr(table, "row_count", 0), 0)
    max_col = max(getattr(table, "col_count", 0), 0)
    covered_logical = 0
    non_empty = 0
    spanning = 0

    for cell in sorted(getattr(table, "cells", []), key=lambda c: (c.row_index, c.col_index)):
        key = (cell.row_index, cell.col_index)
        if key in cells_by_position:
            continue

        cell_style = getattr(cell, "cell_style", None)
        rowspan = getattr(cell_style, "rowspan", 1) if cell_style else 1
        colspan = getattr(cell_style, "colspan", 1) if cell_style else 1
        background = normalize_style_value(getattr(cell_style, "background", None) if cell_style else None)
        borders = {
            side: normalize_style_value(getattr(cell_style, f"border_{side}", None) if cell_style else None)
            for side in SIDES
        }
        text = normalize_text(getattr(cell, "text", ""))
        has_text = bool(text)

        if has_text:
            non_empty += 1
        if rowspan > 1 or colspan > 1:
            spanning += 1

        covered_logical += max(rowspan, 1) * max(colspan, 1)
        max_row = max(max_row, cell.row_index + max(rowspan, 1))
        max_col = max(max_col, cell.col_index + max(colspan, 1))

        cells_by_position[key] = CellSummary(
            row=cell.row_index,
            col=cell.col_index,
            rowspan=max(rowspan, 1),
            colspan=max(colspan, 1),
            text=text,
            has_text=has_text,
            background=background,
            borders=borders,
        )

    entries = []
    span_entries = []
    for key in sorted(cells_by_position):
        cell = cells_by_position[key]
        entries.append(f"{cell.row}:{cell.col}:{cell.rowspan}x{cell.colspan}:{cell.text}")
        span_entries.append(f"{cell.row}:{cell.col}:{cell.rowspan}x{cell.colspan}")

    logical_cell_count = max_row * max_col
    return TableSummary(
        index=index,
        unit_id=getattr(table, "unit_id", f"table-{index}"),
        row_count=max_row,
        col_count=max_col,
        logical_cell_count=logical_cell_count,
        covered_logical_cell_count=covered_logical,
        non_empty_cell_count=non_empty,
        spanning_cell_count=spanning,
        text_fingerprint="\n".join(entries),
        span_fingerprint="\n".join(span_entries),
        cells_by_position=cells_by_position,
    )


def extract_table_summaries(doc: DocIR) -> list[TableSummary]:
    return [summarize_table(table, index) for index, table in enumerate(iter_tables_from_doc(doc))]


def ratio_from_strings(left: str, right: str) -> float:
    if not left and not right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def score_table_match(oracle: TableSummary, candidate: TableSummary) -> float:
    row_ratio = 1.0 - abs(oracle.row_count - candidate.row_count) / max(oracle.row_count, candidate.row_count, 1)
    col_ratio = 1.0 - abs(oracle.col_count - candidate.col_count) / max(oracle.col_count, candidate.col_count, 1)
    logical_ratio = 1.0 - abs(oracle.logical_cell_count - candidate.logical_cell_count) / max(
        oracle.logical_cell_count, candidate.logical_cell_count, 1
    )
    text_ratio = ratio_from_strings(oracle.text_fingerprint, candidate.text_fingerprint)
    span_ratio = ratio_from_strings(oracle.span_fingerprint, candidate.span_fingerprint)
    non_empty_ratio = 1.0 - abs(oracle.non_empty_cell_count - candidate.non_empty_cell_count) / max(
        oracle.non_empty_cell_count, candidate.non_empty_cell_count, 1
    )
    return (
        row_ratio * 25.0
        + col_ratio * 25.0
        + logical_ratio * 10.0
        + text_ratio * 25.0
        + span_ratio * 10.0
        + non_empty_ratio * 5.0
    )


def greedy_match_tables(oracle_tables: list[TableSummary], pdf_tables: list[TableSummary], threshold: float = 55.0) -> list[dict[str, Any]]:
    candidates: list[tuple[float, int, int]] = []
    for oracle_index, oracle_table in enumerate(oracle_tables):
        for pdf_index, pdf_table in enumerate(pdf_tables):
            candidates.append((score_table_match(oracle_table, pdf_table), oracle_index, pdf_index))
    candidates.sort(reverse=True)

    matched_oracle: set[int] = set()
    matched_pdf: set[int] = set()
    matches: list[dict[str, Any]] = []

    for score, oracle_index, pdf_index in candidates:
        if score < threshold or oracle_index in matched_oracle or pdf_index in matched_pdf:
            continue
        matched_oracle.add(oracle_index)
        matched_pdf.add(pdf_index)
        matches.append(
            {
                "oracle_index": oracle_index,
                "pdf_index": pdf_index,
                "score": round(score, 3),
            }
        )
    matches.sort(key=lambda item: item["oracle_index"])
    return matches


def empty_presence_counter() -> Counter:
    return Counter(tp=0, fp=0, fn=0, tn=0)


def update_presence(counter: Counter, oracle_has: bool, pdf_has: bool) -> None:
    if oracle_has and pdf_has:
        counter["tp"] += 1
    elif oracle_has and not pdf_has:
        counter["fn"] += 1
    elif not oracle_has and pdf_has:
        counter["fp"] += 1
    else:
        counter["tn"] += 1


def summarize_presence(counter: Counter) -> dict[str, Any]:
    tp = counter["tp"]
    fp = counter["fp"]
    fn = counter["fn"]
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = None
    if precision is not None and recall is not None and (precision + recall):
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": counter["tn"],
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
    }


def compare_tables(oracle: TableSummary, pdf: TableSummary, score: float) -> dict[str, Any]:
    all_positions = sorted(set(oracle.cells_by_position) | set(pdf.cells_by_position))
    background_presence = empty_presence_counter()
    border_presence = {side: empty_presence_counter() for side in SIDES}
    span_exact = 0
    occupancy_exact = 0
    background_exact = 0
    compared_cells = len(all_positions)
    mismatch_samples: list[dict[str, Any]] = []

    for row, col in all_positions:
        oracle_cell = oracle.cells_by_position.get((row, col))
        pdf_cell = pdf.cells_by_position.get((row, col))

        oracle_span = (
            (oracle_cell.rowspan, oracle_cell.colspan) if oracle_cell else (0, 0)
        )
        pdf_span = (
            (pdf_cell.rowspan, pdf_cell.colspan) if pdf_cell else (0, 0)
        )
        if oracle_span == pdf_span:
            span_exact += 1

        oracle_has_text = oracle_cell.has_text if oracle_cell else False
        pdf_has_text = pdf_cell.has_text if pdf_cell else False
        if oracle_has_text == pdf_has_text:
            occupancy_exact += 1

        oracle_background = oracle_cell.background if oracle_cell else None
        pdf_background = pdf_cell.background if pdf_cell else None
        update_presence(background_presence, bool(oracle_background), bool(pdf_background))
        if oracle_background and pdf_background and oracle_background == pdf_background:
            background_exact += 1

        for side in SIDES:
            oracle_border = oracle_cell.borders[side] if oracle_cell else None
            pdf_border = pdf_cell.borders[side] if pdf_cell else None
            update_presence(border_presence[side], bool(oracle_border), bool(pdf_border))

        cell_mismatch = (
            oracle_span != pdf_span
            or oracle_has_text != pdf_has_text
            or bool(oracle_background) != bool(pdf_background)
            or any(
                bool(oracle_cell.borders[side] if oracle_cell else None)
                != bool(pdf_cell.borders[side] if pdf_cell else None)
                for side in SIDES
            )
        )
        if cell_mismatch and len(mismatch_samples) < 8:
            mismatch_samples.append(
                {
                    "row": row,
                    "col": col,
                    "oracle": {
                        "span": oracle_span,
                        "text": oracle_cell.text if oracle_cell else "",
                        "background": oracle_background,
                        "borders": oracle_cell.borders if oracle_cell else {side: None for side in SIDES},
                    },
                    "pdf": {
                        "span": pdf_span,
                        "text": pdf_cell.text if pdf_cell else "",
                        "background": pdf_background,
                        "borders": pdf_cell.borders if pdf_cell else {side: None for side in SIDES},
                    },
                }
            )

    return {
        "oracle_index": oracle.index,
        "pdf_index": pdf.index,
        "oracle_unit_id": oracle.unit_id,
        "pdf_unit_id": pdf.unit_id,
        "score": round(score, 3),
        "shape": {
            "oracle_rows": oracle.row_count,
            "oracle_cols": oracle.col_count,
            "pdf_rows": pdf.row_count,
            "pdf_cols": pdf.col_count,
        },
        "cell_counts": {
            "oracle_logical": oracle.logical_cell_count,
            "pdf_logical": pdf.logical_cell_count,
            "oracle_non_empty": oracle.non_empty_cell_count,
            "pdf_non_empty": pdf.non_empty_cell_count,
        },
        "metrics": {
            "compared_cells": compared_cells,
            "span_exact_ratio": round(span_exact / compared_cells, 4) if compared_cells else None,
            "occupancy_exact_ratio": round(occupancy_exact / compared_cells, 4) if compared_cells else None,
            "background_presence": summarize_presence(background_presence),
            "background_exact_match_count": background_exact,
            "border_presence": {side: summarize_presence(counter) for side, counter in border_presence.items()},
        },
        "mismatch_samples": mismatch_samples,
    }


def aggregate_pair_metrics(table_comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    total_cells = sum(item["metrics"]["compared_cells"] for item in table_comparisons)
    background_counter = empty_presence_counter()
    border_counters = {side: empty_presence_counter() for side in SIDES}
    span_exact_sum = 0.0
    occupancy_exact_sum = 0.0
    background_exact_sum = 0

    for item in table_comparisons:
        metrics = item["metrics"]
        compared_cells = metrics["compared_cells"]
        span_exact_sum += (metrics["span_exact_ratio"] or 0.0) * compared_cells
        occupancy_exact_sum += (metrics["occupancy_exact_ratio"] or 0.0) * compared_cells
        background_exact_sum += metrics["background_exact_match_count"]

        for key in ("tp", "fp", "fn", "tn"):
            background_counter[key] += metrics["background_presence"][key]
        for side in SIDES:
            for key in ("tp", "fp", "fn", "tn"):
                border_counters[side][key] += metrics["border_presence"][side][key]

    return {
        "matched_tables": len(table_comparisons),
        "compared_cells": total_cells,
        "avg_match_score": round(
            sum(item["score"] for item in table_comparisons) / len(table_comparisons), 3
        ) if table_comparisons else None,
        "span_exact_ratio": round(span_exact_sum / total_cells, 4) if total_cells else None,
        "occupancy_exact_ratio": round(occupancy_exact_sum / total_cells, 4) if total_cells else None,
        "background_presence": summarize_presence(background_counter),
        "background_exact_match_count": background_exact_sum,
        "border_presence": {side: summarize_presence(counter) for side, counter in border_counters.items()},
    }


def build_pair_report(label: str, pdf_path: Path, oracle_path: Path) -> dict[str, Any]:
    pdf_doc = DocIR.from_file(pdf_path, doc_type="pdf")
    prepare_doc_ir_for_html(pdf_doc)
    oracle_doc = DocIR.from_file(oracle_path, doc_type=oracle_path.suffix.lower().lstrip("."))
    pdf_tables = extract_table_summaries(pdf_doc)
    oracle_tables = extract_table_summaries(oracle_doc)
    matches = greedy_match_tables(oracle_tables, pdf_tables)

    table_comparisons = [
        compare_tables(oracle_tables[match["oracle_index"]], pdf_tables[match["pdf_index"]], match["score"])
        for match in matches
    ]

    matched_oracle = {match["oracle_index"] for match in matches}
    matched_pdf = {match["pdf_index"] for match in matches}

    return {
        "label": label,
        "pdf_path": str(pdf_path),
        "oracle_path": str(oracle_path),
        "pdf_tables": len(pdf_tables),
        "oracle_tables": len(oracle_tables),
        "unmatched_pdf_tables": [table.index for table in pdf_tables if table.index not in matched_pdf],
        "unmatched_oracle_tables": [table.index for table in oracle_tables if table.index not in matched_oracle],
        "aggregate": aggregate_pair_metrics(table_comparisons),
        "table_comparisons": table_comparisons,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# Table Style Oracle Report", ""]
    for pair in report["pairs"]:
        aggregate = pair["aggregate"]
        lines.extend(
            [
                f"## {pair['label']}",
                "",
                f"- PDF: `{pair['pdf_path']}`",
                f"- Oracle: `{pair['oracle_path']}`",
                f"- Matched tables: `{aggregate['matched_tables']}` / oracle `{pair['oracle_tables']}` / pdf `{pair['pdf_tables']}`",
                f"- Compared cells: `{aggregate['compared_cells']}`",
                f"- Avg match score: `{aggregate['avg_match_score']}`",
                f"- Span exact ratio: `{aggregate['span_exact_ratio']}`",
                f"- Occupancy exact ratio: `{aggregate['occupancy_exact_ratio']}`",
                "",
                "### Background Presence",
                "",
                f"- precision: `{aggregate['background_presence']['precision']}`",
                f"- recall: `{aggregate['background_presence']['recall']}`",
                f"- f1: `{aggregate['background_presence']['f1']}`",
                f"- tp/fp/fn: `{aggregate['background_presence']['tp']}/{aggregate['background_presence']['fp']}/{aggregate['background_presence']['fn']}`",
                "",
                "### Border Presence",
                "",
            ]
        )
        for side in SIDES:
            border = aggregate["border_presence"][side]
            lines.append(
                f"- {side}: precision `{border['precision']}`, recall `{border['recall']}`, f1 `{border['f1']}`, tp/fp/fn `{border['tp']}/{border['fp']}/{border['fn']}`"
            )
        lines.extend(["", "### Sample Table Matches", ""])
        for comparison in pair["table_comparisons"][:5]:
            metrics = comparison["metrics"]
            lines.append(
                f"- oracle `{comparison['oracle_index']}` ↔ pdf `{comparison['pdf_index']}` "
                f"(score `{comparison['score']}`, span `{metrics['span_exact_ratio']}`, "
                f"occupancy `{metrics['occupancy_exact_ratio']}`, "
                f"background f1 `{metrics['background_presence']['f1']}`)"
            )
            for mismatch in comparison["mismatch_samples"][:2]:
                lines.append(
                    f"  - cell ({mismatch['row']}, {mismatch['col']}): "
                    f"oracle bg `{mismatch['oracle']['background']}` / pdf bg `{mismatch['pdf']['background']}`; "
                    f"oracle span `{mismatch['oracle']['span']}` / pdf span `{mismatch['pdf']['span']}`"
                )
        lines.extend(
            [
                "",
                f"- unmatched oracle tables: `{pair['unmatched_oracle_tables']}`",
                f"- unmatched pdf tables: `{pair['unmatched_pdf_tables']}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare PDF table-style recovery against HWP/HWPX oracle tables built from DocIR."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for markdown/json reports.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    pair_reports = [build_pair_report(label, pdf_path, oracle_path) for label, pdf_path, oracle_path in DEFAULT_PAIRS]
    report = {"pairs": pair_reports}

    json_path = args.output_dir / "table_style_oracle_report.json"
    markdown_path = args.output_dir / "table_style_oracle_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    print(f"json={json_path}")
    print(f"markdown={markdown_path}")
    for pair in pair_reports:
        aggregate = pair["aggregate"]
        print(
            f"{pair['label']}: matched_tables={aggregate['matched_tables']} compared_cells={aggregate['compared_cells']} "
            f"span_exact={aggregate['span_exact_ratio']} occupancy_exact={aggregate['occupancy_exact_ratio']} "
            f"background_f1={aggregate['background_presence']['f1']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
