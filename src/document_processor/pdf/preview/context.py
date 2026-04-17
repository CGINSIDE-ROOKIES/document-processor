"""Preview context collection helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..meta import PdfBoundingBox, coerce_bbox, coerce_float, coerce_int
from .analyze import (
    _build_visual_block_candidates,
    _detect_pdfium_split_regions,
    _extract_pdfium_visual_primitives,
)
from .models import PdfLayoutRegion, PdfPreviewContext, PdfPreviewTableContext


def build_pdf_preview_context(
    raw_document: dict[str, Any],
    *,
    pdf_path: str | Path | None = None,
    page_numbers: list[int] | set[int] | tuple[int, ...] | None = None,
) -> PdfPreviewContext:
    """Extract preview-only layout/table hints from raw ODL JSON."""
    context = PdfPreviewContext(
        layout_regions=_layout_regions_from_raw(raw_document.get("layout regions")),
    )
    _collect_table_preview_context(raw_document.get("kids"), context.tables)
    if pdf_path is not None:
        _augment_layout_regions_with_pdfium(
            context,
            pdf_path=Path(pdf_path),
            target_pages=set(page_numbers) if page_numbers is not None else None,
        )
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


def _augment_layout_regions_with_pdfium(
    context: PdfPreviewContext,
    *,
    pdf_path: Path,
    target_pages: set[int] | None = None,
) -> None:
    try:
        import pypdfium2 as pdfium
    except Exception:
        return

    by_page: dict[int, list[PdfLayoutRegion]] = {}
    for region in context.layout_regions:
        by_page.setdefault(region.page_number, []).append(region)

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        for page_index in range(len(doc)):
            page_number = page_index + 1
            if target_pages is not None and page_number not in target_pages:
                continue
            page_primitives = _extract_pdfium_visual_primitives(doc[page_index], page_number=page_number)
            context.visual_block_candidates.extend(_build_visual_block_candidates(page_primitives))
            page_regions = by_page.get(page_number, [])
            if any(region.region_type in {"left", "right"} for region in page_regions):
                continue

            split_regions = _detect_pdfium_split_regions(doc[page_index], page_number=page_number)
            if not split_regions:
                continue

            context.layout_regions.extend(split_regions)
            by_page.setdefault(page_number, []).extend(split_regions)
    finally:
        doc.close()

__all__ = [
    "build_pdf_preview_context",
    "_layout_regions_from_raw",
    "_collect_table_preview_context",
    "_table_preview_context_from_node",
    "_float_list",
    "_line_art_boxes",
    "_augment_layout_regions_with_pdfium",
]
