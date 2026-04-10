"""PDF HTML preview implementation.

This module owns the whole PDF preview path in one place:

1. Build preview-only sidecar metadata from raw ODL JSON.
2. Apply render-only PDF preparation to a canonical DocIR.
3. Render HTML using the shared exporter plus the preview sidecar.

Compatibility shim modules still exist for older import paths, but the actual
implementation should live here so the PDF HTML flow is easy to follow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..html_exporter import (
    _page_content_style,
    _page_style,
    _render_html_document_shell,
    _render_paragraph,
    render_html_document,
)
from ..models import DocIR, PageInfo, ParagraphIR
from ..style_types import CellStyleInfo
from .enhancement import enrich_pdf_table_backgrounds, enrich_pdf_table_borders
from .meta import PdfBoundingBox, coerce_bbox, coerce_float, coerce_int


# ---------------------------------------------------------------------------
# Preview sidecar models and extraction
# ---------------------------------------------------------------------------


class PdfLayoutRegion(BaseModel):
    region_id: str
    region_type: str
    page_number: int
    bounding_box: PdfBoundingBox | None = None


class PdfPreviewTableContext(BaseModel):
    page_number: int | None = None
    bounding_box: PdfBoundingBox | None = None
    layout_region_id: str | None = None
    reading_order_index: int | None = None
    grid_row_boundaries: list[float] = Field(default_factory=list)
    grid_column_boundaries: list[float] = Field(default_factory=list)
    serialized_cell_count: int | None = None
    logical_cell_count: int | None = None
    covered_logical_cell_count: int | None = None
    non_empty_cell_count: int | None = None
    empty_cell_count: int | None = None
    spanning_cell_count: int | None = None
    line_art_boxes: list[PdfBoundingBox] = Field(default_factory=list)


class PdfPreviewContext(BaseModel):
    layout_regions: list[PdfLayoutRegion] = Field(default_factory=list)
    tables: list[PdfPreviewTableContext] = Field(default_factory=list)


def build_pdf_preview_context(raw_document: dict[str, Any]) -> PdfPreviewContext:
    """Extract preview-only layout/table hints from raw ODL JSON."""
    context = PdfPreviewContext(
        layout_regions=_layout_regions_from_raw(raw_document.get("layout regions")),
    )
    _collect_table_preview_context(raw_document.get("kids"), context.tables)
    return context


def _layout_regions_from_raw(value: Any) -> list[PdfLayoutRegion]:
    if not isinstance(value, list):
        return []

    regions: list[PdfLayoutRegion] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        region_id = item.get("region id")
        region_type = item.get("region type")
        page_number = coerce_int(item.get("page number"))
        if not isinstance(region_id, str) or not isinstance(region_type, str) or page_number is None:
            continue
        regions.append(
            PdfLayoutRegion(
                region_id=region_id,
                region_type=region_type,
                page_number=page_number,
                bounding_box=coerce_bbox(item.get("bounding box")),
            )
        )
    return regions


def _collect_table_preview_context(value: Any, sink: list[PdfPreviewTableContext]) -> None:
    if isinstance(value, dict):
        if value.get("type") == "table":
            sink.append(_table_preview_context_from_node(value))
        for child in value.values():
            _collect_table_preview_context(child, sink)
        return
    if isinstance(value, list):
        for child in value:
            _collect_table_preview_context(child, sink)


def _table_preview_context_from_node(node: dict[str, Any]) -> PdfPreviewTableContext:
    return PdfPreviewTableContext(
        page_number=coerce_int(node.get("page number")),
        bounding_box=coerce_bbox(node.get("bounding box")),
        layout_region_id=node.get("layout region id")
        if isinstance(node.get("layout region id"), str)
        else None,
        reading_order_index=coerce_int(node.get("reading order index")),
        grid_row_boundaries=_float_list(node.get("grid row boundaries")),
        grid_column_boundaries=_float_list(node.get("grid column boundaries")),
        serialized_cell_count=coerce_int(node.get("serialized cell count")),
        logical_cell_count=coerce_int(node.get("logical cell count")),
        covered_logical_cell_count=coerce_int(node.get("covered logical cell count")),
        non_empty_cell_count=coerce_int(node.get("non-empty cell count")),
        empty_cell_count=coerce_int(node.get("empty cell count")),
        spanning_cell_count=coerce_int(node.get("spanning cell count")),
        line_art_boxes=_line_art_boxes(node.get("line arts")),
    )


def _float_list(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    result: list[float] = []
    for item in value:
        number = coerce_float(item)
        if number is None:
            continue
        result.append(number)
    return result


def _line_art_boxes(value: Any) -> list[PdfBoundingBox]:
    if not isinstance(value, list):
        return []
    boxes: list[PdfBoundingBox] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        bbox = coerce_bbox(item.get("bounding box"))
        if bbox is not None:
            boxes.append(bbox)
    return boxes


# ---------------------------------------------------------------------------
# Render-only DocIR preparation
# ---------------------------------------------------------------------------


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
    _prepare_pdf_caption_groups(doc_ir)
    _prepare_pdf_list_groups(doc_ir)
    return doc_ir


def _prepare_pdf_caption_groups(doc_ir: DocIR) -> DocIR:
    """Placeholder for future caption-to-table/image grouping."""
    return doc_ir


def _prepare_pdf_list_groups(doc_ir: DocIR) -> DocIR:
    """Placeholder for future list reconstruction from ODL list metadata."""
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
            table_context = _match_preview_table_context(table_contexts, paragraph.page_number, table.meta)
            if table_context is None:
                continue
            _apply_table_context(table, table_context)

    return doc_ir


def _match_preview_table_context(
    candidates: list[PdfPreviewTableContext],
    paragraph_page_number: int | None,
    table_meta,
) -> PdfPreviewTableContext | None:
    page_number = getattr(table_meta, "page_number", None) or paragraph_page_number
    layout_region_id = getattr(table_meta, "layout_region_id", None)
    reading_order_index = getattr(table_meta, "reading_order_index", None)
    bounding_box = getattr(table_meta, "bounding_box", None)

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


def _bbox_distance(left, right) -> float:
    return abs(left.left_pt - right.left_pt) + abs(left.bottom_pt - right.bottom_pt) + abs(
        left.right_pt - right.right_pt
    ) + abs(left.top_pt - right.top_pt)


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


# ---------------------------------------------------------------------------
# Preview rendering entrypoints
# ---------------------------------------------------------------------------


def _paragraph_region_id(paragraph: ParagraphIR) -> str | None:
    if paragraph.meta is None:
        return None
    return getattr(paragraph.meta, "layout_region_id", None)


def _paragraph_reading_order(paragraph: ParagraphIR, fallback_index: int) -> tuple[int, int]:
    if paragraph.meta is None:
        return (fallback_index + 1_000_000, fallback_index)
    reading_order_index = getattr(paragraph.meta, "reading_order_index", None)
    if reading_order_index is None:
        return (fallback_index + 1_000_000, fallback_index)
    return (reading_order_index, fallback_index)


def _render_preview_body(doc_ir: DocIR, *, preview_context: PdfPreviewContext) -> str:
    if not doc_ir.pages:
        return "\n\n".join(_render_paragraph(doc_ir, paragraph) for paragraph in doc_ir.paragraphs)

    paragraphs_by_page: dict[int, list[ParagraphIR]] = {}
    unpaged: list[ParagraphIR] = []

    for paragraph in doc_ir.paragraphs:
        if paragraph.page_number is None:
            unpaged.append(paragraph)
            continue
        paragraphs_by_page.setdefault(paragraph.page_number, []).append(paragraph)

    parts: list[str] = []
    for page in doc_ir.pages:
        page_paragraphs = paragraphs_by_page.get(page.page_number, [])
        content_html = _render_preview_page_content(
            doc_ir,
            page,
            page_paragraphs,
            preview_context=preview_context,
        )
        parts.append(
            f'<section class="document-page" data-page-number="{page.page_number}" style="{_page_style(page)}">'
            f'<div class="document-page__content" style="{_page_content_style(page)}">{content_html or "&nbsp;"}</div>'
            "</section>"
        )

    if unpaged:
        parts.append(
            '<section class="document-unpaged">'
            + "\n\n".join(_render_paragraph(doc_ir, paragraph) for paragraph in unpaged)
            + "</section>"
        )

    return "\n".join(parts)


def _render_preview_page_content(
    doc_ir: DocIR,
    page: PageInfo,
    page_paragraphs: list[ParagraphIR],
    *,
    preview_context: PdfPreviewContext,
) -> str:
    if not page_paragraphs:
        return ""

    page_regions = [region for region in preview_context.layout_regions if region.page_number == page.page_number]
    if not page_regions:
        return "\n\n".join(_render_paragraph(doc_ir, paragraph) for paragraph in page_paragraphs)

    region_by_id = {region.region_id: region for region in page_regions}
    ordered_paragraphs = [
        paragraph
        for _, paragraph in sorted(
            enumerate(page_paragraphs),
            key=lambda pair: _paragraph_reading_order(pair[1], pair[0]),
        )
    ]

    if not any(region.region_type in {"left", "right"} for region in page_regions):
        return "\n\n".join(_render_paragraph(doc_ir, paragraph) for paragraph in ordered_paragraphs)

    parts: list[str] = []
    index = 0
    while index < len(ordered_paragraphs):
        paragraph = ordered_paragraphs[index]
        region = region_by_id.get(_paragraph_region_id(paragraph) or "")
        region_type = region.region_type if region is not None else "main"

        if region_type in {"left", "right"}:
            left_items: list[ParagraphIR] = []
            right_items: list[ParagraphIR] = []
            left_region: PdfLayoutRegion | None = None
            right_region: PdfLayoutRegion | None = None
            while index < len(ordered_paragraphs):
                current = ordered_paragraphs[index]
                current_region = region_by_id.get(_paragraph_region_id(current) or "")
                current_type = current_region.region_type if current_region is not None else "main"
                if current_type not in {"left", "right"}:
                    break
                if current_type == "left":
                    left_items.append(current)
                    left_region = current_region
                else:
                    right_items.append(current)
                    right_region = current_region
                index += 1
            parts.append(
                _render_two_column_band(
                    doc_ir,
                    left_items,
                    right_items,
                    left_region=left_region,
                    right_region=right_region,
                )
            )
            continue

        single_region_items: list[ParagraphIR] = []
        while index < len(ordered_paragraphs):
            current = ordered_paragraphs[index]
            current_region = region_by_id.get(_paragraph_region_id(current) or "")
            current_type = current_region.region_type if current_region is not None else "main"
            if current_type in {"left", "right"}:
                break
            single_region_items.append(current)
            index += 1
        parts.append(
            '<div class="document-region document-region--single">'
            + "\n\n".join(_render_paragraph(doc_ir, paragraph) for paragraph in single_region_items)
            + "</div>"
        )

    return "\n".join(part for part in parts if part)


def _render_two_column_band(
    doc_ir: DocIR,
    left_items: list[ParagraphIR],
    right_items: list[ParagraphIR],
    *,
    left_region: PdfLayoutRegion | None,
    right_region: PdfLayoutRegion | None,
) -> str:
    left_html = "\n\n".join(_render_paragraph(doc_ir, paragraph) for paragraph in left_items) or "&nbsp;"
    right_html = "\n\n".join(_render_paragraph(doc_ir, paragraph) for paragraph in right_items) or "&nbsp;"

    left_width = _region_width(left_region)
    right_width = _region_width(right_region)
    total_width = left_width + right_width
    if total_width > 0:
        left_ratio = left_width / total_width * 100.0
        right_ratio = right_width / total_width * 100.0
        band_style = (
            "display:grid;"
            f"grid-template-columns:{left_ratio:.2f}% {right_ratio:.2f}%;"
            "gap:24pt;"
            "align-items:start;"
            "margin:0 0 12px 0"
        )
    else:
        band_style = "display:grid;grid-template-columns:1fr 1fr;gap:24pt;align-items:start;margin:0 0 12px 0"

    return (
        f'<div class="document-region-band document-region-band--columns" style="{band_style}">'
        f'<div class="document-region document-region--left">{left_html}</div>'
        f'<div class="document-region document-region--right">{right_html}</div>'
        "</div>"
    )


def _region_width(region: PdfLayoutRegion | None) -> float:
    if region is None or region.bounding_box is None:
        return 0.0
    return max(region.bounding_box.right_pt - region.bounding_box.left_pt, 0.0)


def render_pdf_html(
    path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    title: str | None = None,
    doc_id: str | None = None,
    doc_cls: type[DocIR] | None = None,
    **doc_kwargs: Any,
) -> str:
    """Public PDF -> HTML preview entrypoint."""
    from .pipeline import _parse_pdf_to_doc_ir_with_preview

    doc_ir, preview_context = _parse_pdf_to_doc_ir_with_preview(
        path,
        config=config,
        doc_id=doc_id,
        doc_cls=doc_cls,
        **doc_kwargs,
    )
    return render_pdf_preview_html(doc_ir, preview_context=preview_context, title=title)


def render_pdf_preview_html(
    doc_ir: DocIR,
    *,
    preview_context: PdfPreviewContext,
    title: str | None = None,
) -> str:
    """Internal helper for callers that already hold preview sidecar data."""
    prepared_doc = doc_ir.model_copy(deep=True)
    prepare_pdf_for_html(prepared_doc, preview_context=preview_context)
    if not preview_context.layout_regions:
        return render_html_document(prepared_doc, title=title)
    body = _render_preview_body(prepared_doc, preview_context=preview_context)
    resolved_title = title or prepared_doc.doc_id or "Document"
    return _render_html_document_shell(title=resolved_title, body=body)


def render_pdf_preview_html_from_file(
    path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    title: str | None = None,
    doc_id: str | None = None,
    doc_cls: type[DocIR] | None = None,
    **doc_kwargs: Any,
) -> str:
    """Backward-compatible alias around :func:`render_pdf_html`."""
    return render_pdf_html(
        path,
        config=config,
        title=title,
        doc_id=doc_id,
        doc_cls=doc_cls,
        **doc_kwargs,
    )


__all__ = [
    "PdfLayoutRegion",
    "PdfPreviewContext",
    "PdfPreviewTableContext",
    "build_pdf_preview_context",
    "prepare_pdf_for_html",
    "render_pdf_html",
    "render_pdf_preview_html",
    "render_pdf_preview_html_from_file",
]
