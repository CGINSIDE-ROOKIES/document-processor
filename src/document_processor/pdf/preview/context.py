"""PDF raw 결과에서 preview sidecar context를 수집하는 코드.

ODL raw JSON은 DocIR 본문 구조와 별도로 layout region/table grid 정보를 담고
있다. 이 파일은 그 정보를 `PdfPreviewContext`로 옮기고, pdfium으로 시각
primitive를 추가 수집해서 normalize 단계가 쓸 수 있게 만든다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..meta import PdfBoundingBox, coerce_bbox, coerce_float, coerce_int
from .analyze import _build_visual_block_candidates, _extract_pdfium_visual_primitives
from .models import PdfLayoutRegion, PdfPreviewContext, PdfPreviewTableContext, PdfPreviewVisualBlockCandidate

_SUPPORTED_LAYOUT_REGION_TYPES = {
    "main",
    "left-page",
    "right-page",
    "left-column",
    "right-column",
}


def build_pdf_preview_context(
    raw_document: dict[str, Any],
) -> PdfPreviewContext:
    """ODL raw JSON에서 layout region과 table grid context를 뽑는다."""
    context = PdfPreviewContext(
        layout_regions=_layout_regions_from_raw(raw_document.get("layout regions")),
    )
    _collect_table_preview_context(raw_document.get("kids"), context.tables)
    return context


def collect_pdfium_visual_block_candidates(
    *,
    pdf_path: str | Path,
    page_numbers: list[int] | set[int] | tuple[int, ...] | None = None,
) -> list[PdfPreviewVisualBlockCandidate]:
    """pdfium 시각 primitive를 읽어 layout-table 승격 후보를 만든다."""
    try:
        import pypdfium2 as pdfium
    except Exception:
        return []

    target_pages = set(page_numbers) if page_numbers is not None else None
    candidates: list[PdfPreviewVisualBlockCandidate] = []
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
    except Exception:
        return []
    try:
        for page_index in range(len(doc)):
            page_number = page_index + 1
            if target_pages is not None and page_number not in target_pages:
                continue
            try:
                page_primitives = _extract_pdfium_visual_primitives(doc[page_index], page_number=page_number)
            except Exception:
                continue
            candidates.extend(_build_visual_block_candidates(page_primitives))
    finally:
        doc.close()
    return candidates


def _layout_regions_from_raw(value: Any) -> list[PdfLayoutRegion]:
    """raw `layout regions` 목록을 preview 모델로 변환한다."""
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
        if region_type not in _SUPPORTED_LAYOUT_REGION_TYPES:
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
    """raw tree를 순회하면서 table node의 grid 정보를 모은다."""
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
        grid_row_boundaries=_float_list(node.get("grid row boundaries")),
        grid_column_boundaries=_float_list(node.get("grid column boundaries")),
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


__all__ = [
    "build_pdf_preview_context",
    "collect_pdfium_visual_block_candidates",
    "_layout_regions_from_raw",
    "_collect_table_preview_context",
    "_table_preview_context_from_node",
    "_float_list",
]
