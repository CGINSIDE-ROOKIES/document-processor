"""Builder utilities for structural document IR."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import DocIR, ParagraphIR, RunIR, TableCellIR, TableIR

if TYPE_CHECKING:
    from .style_types import StyleMap


_LEGACY_NUM_RE = re.compile(r"\d+")
_PARAGRAPH_KEY_RE = re.compile(r"^(s\d+\.p\d+)")


def _legacy_id_sort_key(unit_id: str) -> tuple[tuple[int, ...], str]:
    nums = tuple(int(v) for v in _LEGACY_NUM_RE.findall(unit_id))
    return nums, unit_id


def _safe_para_for_id(
    paragraph_map: dict[str, ParagraphIR],
    paragraph_id: str,
    *,
    style_map: "StyleMap | None",
) -> ParagraphIR:
    paragraph = paragraph_map.get(paragraph_id)
    if paragraph is not None:
        return paragraph

    para_style = style_map.paragraphs.get(paragraph_id) if style_map else None
    paragraph = ParagraphIR(unit_id=paragraph_id, para_style=para_style)
    paragraph_map[paragraph_id] = paragraph
    return paragraph


def _is_token(token: str, prefix: str) -> bool:
    return token.startswith(prefix) and token[len(prefix):].isdigit()


def _get_or_create_table(
    parent,
    table_id: str,
    *,
    style_map: "StyleMap | None",
    table_map: dict[str, TableIR],
) -> TableIR:
    table = table_map.get(table_id)
    if table is not None:
        return table

    table_style = style_map.tables.get(table_id) if style_map else None
    table = TableIR(
        unit_id=table_id,
        row_count=table_style.row_count if table_style else 0,
        col_count=table_style.col_count if table_style else 0,
        table_style=table_style,
    )
    table_map[table_id] = table
    parent.append_content(table)
    return table


def _get_or_create_cell(
    table: TableIR,
    table_id: str,
    row_index: int,
    col_index: int,
    *,
    style_map: "StyleMap | None",
    cell_map: dict[tuple[str, int, int], TableCellIR],
) -> TableCellIR:
    cell_bucket_key = (table_id, row_index, col_index)
    cell = cell_map.get(cell_bucket_key)
    if cell is not None:
        return cell

    cell_id = f"{table_id}.tr{row_index}.tc{col_index}"
    cell_style = style_map.cells.get(cell_id) if style_map else None
    cell = TableCellIR(
        unit_id=cell_id,
        row_index=row_index,
        col_index=col_index,
        cell_style=cell_style,
    )
    cell_map[cell_bucket_key] = cell
    table.cells.append(cell)
    return cell


def _get_or_create_cell_paragraph(
    cell: TableCellIR,
    table_id: str,
    row_index: int,
    col_index: int,
    paragraph_index: int,
    *,
    style_map: "StyleMap | None",
    cell_paragraph_map: dict[tuple[str, int, int, int], ParagraphIR],
) -> ParagraphIR:
    cell_paragraph_bucket_key = (table_id, row_index, col_index, paragraph_index)
    cell_paragraph = cell_paragraph_map.get(cell_paragraph_bucket_key)
    if cell_paragraph is not None:
        return cell_paragraph

    cell_paragraph_id = f"{table_id}.tr{row_index}.tc{col_index}.p{paragraph_index}"
    para_style = style_map.paragraphs.get(cell_paragraph_id) if style_map else None
    cell_paragraph = ParagraphIR(
        unit_id=cell_paragraph_id,
        para_style=para_style,
    )
    cell_paragraph_map[cell_paragraph_bucket_key] = cell_paragraph
    cell.paragraphs.append(cell_paragraph)
    return cell_paragraph


def _attach_run(
    container,
    container_id: str,
    run_token: str,
    text: str,
    *,
    style_map: "StyleMap | None",
) -> None:
    run_id = f"{container_id}.{run_token}"
    run_style = style_map.runs.get(run_id) if style_map else None
    container.append_content(
        RunIR(
            unit_id=run_id,
            text=text,
            run_style=run_style,
        )
    )


def _ingest_table_tokens(
    table: TableIR,
    table_id: str,
    tokens: list[str],
    text: str,
    *,
    style_map: "StyleMap | None",
    table_map: dict[str, TableIR],
    cell_map: dict[tuple[str, int, int], TableCellIR],
    cell_paragraph_map: dict[tuple[str, int, int, int], ParagraphIR],
) -> None:
    if len(tokens) < 3 or not _is_token(tokens[0], "tr") or not _is_token(tokens[1], "tc") or not _is_token(tokens[2], "p"):
        return

    row_index = int(tokens[0][2:])
    col_index = int(tokens[1][2:])
    paragraph_index = int(tokens[2][1:])

    cell = _get_or_create_cell(
        table,
        table_id,
        row_index,
        col_index,
        style_map=style_map,
        cell_map=cell_map,
    )
    cell_paragraph = _get_or_create_cell_paragraph(
        cell,
        table_id,
        row_index,
        col_index,
        paragraph_index,
        style_map=style_map,
        cell_paragraph_map=cell_paragraph_map,
    )
    _ingest_paragraph_like_tokens(
        cell_paragraph,
        cell_paragraph.unit_id,
        tokens[3:],
        text,
        style_map=style_map,
        table_map=table_map,
        cell_map=cell_map,
        cell_paragraph_map=cell_paragraph_map,
        allow_legacy_table_anchor=False,
    )


def _ingest_paragraph_like_tokens(
    container,
    container_id: str,
    tokens: list[str],
    text: str,
    *,
    style_map: "StyleMap | None",
    table_map: dict[str, TableIR],
    cell_map: dict[tuple[str, int, int], TableCellIR],
    cell_paragraph_map: dict[tuple[str, int, int, int], ParagraphIR],
    allow_legacy_table_anchor: bool,
) -> None:
    if not tokens:
        return

    token = tokens[0]
    if _is_token(token, "r"):
        if len(tokens) == 1:
            _attach_run(
                container,
                container_id,
                token,
                text,
                style_map=style_map,
            )
            return

        if allow_legacy_table_anchor and len(tokens) >= 2 and _is_token(tokens[1], "tbl"):
            table_id = f"{container_id}.{token}.{tokens[1]}"
            table = _get_or_create_table(
                container,
                table_id,
                style_map=style_map,
                table_map=table_map,
            )
            _ingest_table_tokens(
                table,
                table_id,
                tokens[2:],
                text,
                style_map=style_map,
                table_map=table_map,
                cell_map=cell_map,
                cell_paragraph_map=cell_paragraph_map,
            )
            return

    if _is_token(token, "tbl"):
        table_id = f"{container_id}.{token}"
        table = _get_or_create_table(
            container,
            table_id,
            style_map=style_map,
            table_map=table_map,
        )
        _ingest_table_tokens(
            table,
            table_id,
            tokens[1:],
            text,
            style_map=style_map,
            table_map=table_map,
            cell_map=cell_map,
            cell_paragraph_map=cell_paragraph_map,
        )


def _finalize_table(
    table: TableIR,
) -> None:
    table.cells.sort(key=lambda cell: (cell.row_index, cell.col_index))

    max_row = 0
    max_col = 0
    for cell in table.cells:
        max_row = max(max_row, cell.row_index)
        max_col = max(max_col, cell.col_index)

        cell.paragraphs.sort(key=lambda cp: _legacy_id_sort_key(cp.unit_id))
        for cell_paragraph in cell.paragraphs:
            cell_paragraph.sort_content(key=lambda node: _legacy_id_sort_key(node.unit_id))
            for nested_table in cell_paragraph.tables:
                _finalize_table(nested_table)
            cell_paragraph.recompute_text()

        cell.recompute_text()

    if table.row_count <= 0:
        table.row_count = max_row
    if table.col_count <= 0:
        table.col_count = max_col


def _infer_source_doc_type(source_path: str | Path | None) -> str | None:
    if source_path is None:
        return None
    suffix = Path(source_path).suffix.lower()
    if suffix.startswith("."):
        suffix = suffix[1:]
    return suffix or None


def apply_style_map_to_doc_ir(doc_ir: "DocIR", style_map: "StyleMap | None") -> "DocIR":
    """Attach styles to an existing structural document IR."""
    if style_map is None:
        return doc_ir

    def _apply_table_styles(table: TableIR) -> None:
        if table.unit_id in style_map.tables:
            table_style = style_map.tables[table.unit_id]
            table.table_style = table_style
            if table.row_count <= 0:
                table.row_count = table_style.row_count
            if table.col_count <= 0:
                table.col_count = table_style.col_count

        for cell in table.cells:
            if cell.unit_id in style_map.cells:
                cell.cell_style = style_map.cells[cell.unit_id]
            for paragraph in cell.paragraphs:
                if paragraph.unit_id in style_map.paragraphs:
                    paragraph.para_style = style_map.paragraphs[paragraph.unit_id]
                for run in paragraph.runs:
                    if run.unit_id in style_map.runs:
                        run.run_style = style_map.runs[run.unit_id]
                for nested_table in paragraph.tables:
                    _apply_table_styles(nested_table)

    for paragraph in doc_ir.paragraphs:
        if paragraph.unit_id in style_map.paragraphs:
            paragraph.para_style = style_map.paragraphs[paragraph.unit_id]
        for run in paragraph.runs:
            if run.unit_id in style_map.runs:
                run.run_style = style_map.runs[run.unit_id]
        for table in paragraph.tables:
            _apply_table_styles(table)

    return doc_ir


def build_doc_ir_from_mapping(
    mapping: dict[str, str],
    *,
    style_map: "StyleMap | None" = None,
    source_path: str | Path | None = None,
    source_doc_type: str | None = None,
    metadata: dict[str, Any] | None = None,
    doc_id: str | None = None,
    doc_cls: type["DocIR"] | None = None,
    **doc_kwargs: Any,
) -> "DocIR":
    """Build document IR from a legacy run-level structural mapping."""

    paragraph_map: dict[str, ParagraphIR] = {}
    table_map: dict[str, TableIR] = {}
    cell_map: dict[tuple[str, int, int], TableCellIR] = {}
    cell_paragraph_map: dict[tuple[str, int, int, int], ParagraphIR] = {}

    sorted_items = sorted(mapping.items(), key=lambda kv: _legacy_id_sort_key(kv[0]))

    for unit_id, text in sorted_items:
        paragraph_match = _PARAGRAPH_KEY_RE.match(unit_id)
        if not paragraph_match:
            continue

        paragraph_id = paragraph_match.group(1)
        paragraph = _safe_para_for_id(paragraph_map, paragraph_id, style_map=style_map)

        _ingest_paragraph_like_tokens(
            paragraph,
            paragraph_id,
            unit_id.split(".")[2:],
            text,
            style_map=style_map,
            table_map=table_map,
            cell_map=cell_map,
            cell_paragraph_map=cell_paragraph_map,
            allow_legacy_table_anchor=True,
        )

    paragraphs = sorted(paragraph_map.values(), key=lambda p: _legacy_id_sort_key(p.unit_id))

    for paragraph in paragraphs:
        paragraph.sort_content(key=lambda node: _legacy_id_sort_key(node.unit_id))

        for table in paragraph.tables:
            _finalize_table(table)

        paragraph.recompute_text()

    resolved_source_path = str(source_path) if source_path is not None else None
    resolved_doc_type = source_doc_type or _infer_source_doc_type(source_path)
    resolved_doc_id = doc_id
    if resolved_doc_id is None and source_path is not None:
        resolved_doc_id = Path(source_path).stem

    resolved_doc_cls = doc_cls or DocIR
    doc_ir = resolved_doc_cls(
        doc_id=resolved_doc_id,
        source_path=resolved_source_path,
        source_doc_type=resolved_doc_type,
        metadata=metadata or {},
        paragraphs=paragraphs,
        **doc_kwargs,
    )
    return apply_style_map_to_doc_ir(doc_ir, style_map)

__all__ = ["apply_style_map_to_doc_ir", "build_doc_ir_from_mapping"]
