#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from document_processor import DocIR  # noqa: E402

DEFAULT_OUTPUT_DIR = REPO_ROOT / "out" / "text-fidelity-oracle"
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
class TextStats:
    top_level_paragraphs: int = 0
    top_level_paragraphs_with_newline: int = 0
    top_level_paragraphs_with_double_space: int = 0
    cell_paragraphs: int = 0
    cell_paragraphs_with_newline: int = 0
    cell_paragraphs_with_double_space: int = 0
    top_level_runs: int = 0
    top_level_runs_with_font_size: int = 0
    top_level_runs_with_font_family: int = 0
    cell_runs: int = 0
    cell_runs_with_font_size: int = 0
    cell_runs_with_font_family: int = 0
    header_footer_paragraphs: int = 0


def _update_text_stats(stats: TextStats, text: str, *, in_cell: bool) -> None:
    if in_cell:
        stats.cell_paragraphs += 1
        if "\n" in text:
            stats.cell_paragraphs_with_newline += 1
        if "  " in text:
            stats.cell_paragraphs_with_double_space += 1
        return

    stats.top_level_paragraphs += 1
    if "\n" in text:
        stats.top_level_paragraphs_with_newline += 1
    if "  " in text:
        stats.top_level_paragraphs_with_double_space += 1


def _update_run_stats(stats: TextStats, paragraph, *, in_cell: bool) -> None:
    runs = paragraph.runs
    target_total = "cell_runs" if in_cell else "top_level_runs"
    target_font_size = "cell_runs_with_font_size" if in_cell else "top_level_runs_with_font_size"
    target_font_family = "cell_runs_with_font_family" if in_cell else "top_level_runs_with_font_family"
    setattr(stats, target_total, getattr(stats, target_total) + len(runs))
    setattr(
        stats,
        target_font_size,
        getattr(stats, target_font_size)
        + sum(1 for run in runs if run.run_style is not None and run.run_style.size_pt is not None),
    )
    setattr(
        stats,
        target_font_family,
        getattr(stats, target_font_family)
        + sum(1 for run in runs if run.run_style is not None and run.run_style.font_family),
    )


def collect_text_stats(doc: DocIR) -> TextStats:
    stats = TextStats()

    def visit_paragraph(paragraph, *, in_cell: bool) -> None:
        _update_text_stats(stats, paragraph.text, in_cell=in_cell)
        _update_run_stats(stats, paragraph, in_cell=in_cell)
        if not in_cell and getattr(paragraph.meta, "source_type", None) in {"header", "footer"}:
            stats.header_footer_paragraphs += 1

        for table in paragraph.tables:
            for cell in table.cells:
                for cell_paragraph in cell.paragraphs:
                    visit_paragraph(cell_paragraph, in_cell=True)

    for paragraph in doc.paragraphs:
        visit_paragraph(paragraph, in_cell=False)

    return stats


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _format_ratio(numerator: int, denominator: int) -> str:
    ratio = _ratio(numerator, denominator)
    return "-" if ratio is None else f"{ratio:.4f}"


def _parse_pdf(pdf_path: Path, *, keep_line_breaks: bool = False) -> DocIR:
    config = {"odl": {"keep_line_breaks": True}} if keep_line_breaks else None
    return DocIR.from_file(pdf_path, doc_type="pdf", config=config)


def _parse_pdf_preview_fidelity(pdf_path: Path) -> DocIR:
    config = {"odl": {"keep_line_breaks": True, "preserve_whitespace": True}}
    return DocIR.from_file(pdf_path, doc_type="pdf", config=config)


def _render_markdown(report: dict[str, dict[str, dict[str, int]]]) -> str:
    lines = ["# Text Fidelity Oracle Report", ""]
    metrics = [
        "top_level_paragraphs",
        "top_level_paragraphs_with_newline",
        "top_level_paragraphs_with_double_space",
        "cell_paragraphs",
        "cell_paragraphs_with_newline",
        "cell_paragraphs_with_double_space",
        "top_level_runs",
        "top_level_runs_with_font_size",
        "top_level_runs_with_font_family",
        "cell_runs",
        "cell_runs_with_font_size",
        "cell_runs_with_font_family",
        "header_footer_paragraphs",
    ]

    for label, variants in report.items():
        lines.append(f"## {label}")
        lines.append("")
        lines.append("| Metric | HWPX | PDF canonical | PDF keep-line-breaks | PDF preview fidelity | canonical/HWPX | keep-lines/HWPX | preview/HWPX |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        hwpx = variants["hwpx"]
        canonical = variants["pdf_canonical"]
        keep_lines = variants["pdf_keep_line_breaks"]
        preview = variants["pdf_preview_fidelity"]
        for metric in metrics:
            lines.append(
                "| "
                + metric.replace("_", " ")
                + f" | {hwpx[metric]} | {canonical[metric]} | {keep_lines[metric]} | {preview[metric]} | "
                + _format_ratio(canonical[metric], hwpx[metric])
                + " | "
                + _format_ratio(keep_lines[metric], hwpx[metric])
                + " | "
                + _format_ratio(preview[metric], hwpx[metric])
                + " |"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--odl-jar", type=Path, default=None)
    args = parser.parse_args()

    if args.odl_jar is not None:
        os.environ["DOCUMENT_PROCESSOR_ODL_JAR"] = str(args.odl_jar)

    report: dict[str, dict[str, dict[str, int]]] = {}
    for label, pdf_path, hwpx_path in DEFAULT_PAIRS:
        hwpx_doc = DocIR.from_file(hwpx_path, doc_type="hwpx")
        pdf_doc = _parse_pdf(pdf_path, keep_line_breaks=False)
        pdf_keep_lines_doc = _parse_pdf(pdf_path, keep_line_breaks=True)
        pdf_preview_doc = _parse_pdf_preview_fidelity(pdf_path)
        report[label] = {
            "hwpx": asdict(collect_text_stats(hwpx_doc)),
            "pdf_canonical": asdict(collect_text_stats(pdf_doc)),
            "pdf_keep_line_breaks": asdict(collect_text_stats(pdf_keep_lines_doc)),
            "pdf_preview_fidelity": asdict(collect_text_stats(pdf_preview_doc)),
        }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "text_fidelity_oracle_report.json"
    md_path = args.output_dir / "text_fidelity_oracle_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    print(json_path)
    print(md_path)


if __name__ == "__main__":
    main()
