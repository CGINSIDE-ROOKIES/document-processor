"""PDF preview preparation helpers."""

from __future__ import annotations

from ...models import DocIR
from ...style_types import CellStyleInfo
from ..enhancement import enrich_pdf_table_backgrounds, enrich_pdf_table_borders
from .compose import _normalize_pdf_doc_for_flow
from .models import PdfPreviewContext, PdfPreviewTableContext
from .shared import _shared_bbox_distance


def _bbox_distance(left, right) -> float:
    return _shared_bbox_distance(left, right)


def prepare_pdf_for_html(
    doc_ir: DocIR,
    *,
    preview_context: PdfPreviewContext | None = None,
) -> DocIR:
    if (doc_ir.source_doc_type or "").lower() != "pdf":
        return doc_ir

    _apply_preview_table_geometry(doc_ir, preview_context=preview_context)

    # Raster-based refinement stays here so the shared HTML renderer remains
    # unaware of PDF-specific extraction quirks.
    enrich_pdf_table_borders(doc_ir)
    enrich_pdf_table_backgrounds(doc_ir)
    _normalize_pdf_doc_for_flow(doc_ir, preview_context=preview_context)
    return doc_ir


def _apply_preview_table_geometry(
    doc_ir: DocIR,
    *,
    preview_context: PdfPreviewContext | None,
) -> DocIR:
    if preview_context is None or not preview_context.tables:
        return doc_ir

    table_contexts = [
        table_context
        for table_context in preview_context.tables
        if table_context.grid_row_boundaries or table_context.grid_column_boundaries
    ]
    if not table_contexts:
        return doc_ir

    for paragraph in doc_ir.paragraphs:
        for table in paragraph.tables:
            table_context = _match_preview_table_context(table_contexts, paragraph.page_number, table)
            if table_context is None:
                continue
            _apply_table_context(table, table_context)

    return doc_ir


def _match_preview_table_context(
    candidates: list[PdfPreviewTableContext],
    paragraph_page_number: int | None,
    table,
) -> PdfPreviewTableContext | None:
    table_meta = getattr(table, "meta", None)
    page_number = getattr(table_meta, "page_number", None) or paragraph_page_number
    layout_region_id = getattr(table_meta, "layout_region_id", None)
    reading_order_index = getattr(table_meta, "reading_order_index", None)
    bounding_box = getattr(table, "bbox", None) or getattr(table_meta, "bounding_box", None)

    if page_number is None:
        return None

    exact_key_matches = [
        candidate
        for candidate in candidates
        if candidate.page_number == page_number
        and candidate.layout_region_id == layout_region_id
        and candidate.reading_order_index == reading_order_index
    ]
    if exact_key_matches:
        return exact_key_matches[0]

    if bounding_box is None:
        return None

    for candidate in candidates:
        if candidate.page_number != page_number or candidate.bounding_box is None:
            continue
        if _bbox_distance(candidate.bounding_box, bounding_box) <= 4.0:
            return candidate
    return None


def _apply_table_context(table, table_context: PdfPreviewTableContext) -> None:
    if table.table_style is not None:
        if table.table_style.width_pt is None and table_context.grid_column_boundaries:
            table.table_style.width_pt = _span_extent(table_context.grid_column_boundaries, 1, table.col_count)
        if table.table_style.height_pt is None and table_context.grid_row_boundaries:
            table.table_style.height_pt = _span_extent(table_context.grid_row_boundaries, 1, table.row_count)

    for cell in table.cells:
        if cell.cell_style is None:
            cell.cell_style = CellStyleInfo()

        colspan = max(cell.cell_style.colspan, 1)
        rowspan = max(cell.cell_style.rowspan, 1)

        if cell.cell_style.width_pt is None and table_context.grid_column_boundaries:
            width_pt = _span_extent(table_context.grid_column_boundaries, cell.col_index, colspan)
            if width_pt is not None:
                cell.cell_style.width_pt = width_pt
        if cell.cell_style.height_pt is None and table_context.grid_row_boundaries:
            height_pt = _span_extent(table_context.grid_row_boundaries, cell.row_index, rowspan)
            if height_pt is not None:
                cell.cell_style.height_pt = height_pt


def _span_extent(boundaries: list[float], start_index_1based: int, span: int) -> float | None:
    start_index = start_index_1based - 1
    end_index = start_index + span
    if start_index < 0 or end_index >= len(boundaries):
        return None
    return abs(boundaries[end_index] - boundaries[start_index])


__all__ = ["enrich_pdf_table_backgrounds", "enrich_pdf_table_borders", "prepare_pdf_for_html"]
