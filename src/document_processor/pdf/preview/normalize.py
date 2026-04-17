"""PDF preview normalization helpers."""

from __future__ import annotations

from typing import Any

from ...models import DocIR, ImageIR, PageInfo, ParagraphIR, RunIR, TableCellIR, TableIR
from ...style_types import CellStyleInfo, TableStyleInfo
from ..enhancement import enrich_pdf_table_backgrounds, enrich_pdf_table_borders
from ..meta import PdfBoundingBox
from .models import (
    PdfLayoutRegion,
    PdfPreviewContext,
    PdfPreviewTableContext,
    PdfPreviewVisualBlockCandidate,
    _AssignedCandidate,
    _AssignedCandidateGroup,
    _CANDIDATE_ASSIGN_TOLERANCE_PT,
    _COLUMN_BAND_CENTER_OFFSET_RATIO,
    _COLUMN_BAND_GUTTER_MIN_WIDTH_PT,
    _COLUMN_BAND_GUTTER_MIN_WIDTH_RATIO,
    _COLUMN_BAND_MIN_HEIGHT_PT,
    _COLUMN_BAND_SPLIT_MERGE_TOLERANCE_PT,
    _IMAGE_STRIP_MAX_CENTER_DELTA_RATIO,
    _IMAGE_STRIP_MAX_GAP_PT,
    _IMAGE_STRIP_MAX_HEIGHT_RATIO,
    _IMAGE_STRIP_MIN_GROUP_SIZE,
    _IMAGE_STRIP_MIN_SPAN_OVERLAP_RATIO,
    _LAYOUT_TABLE_ALIGNMENT_OVERLAP_RATIO,
    _LAYOUT_TABLE_BOUNDARY_TOLERANCE_PT,
    _LAYOUT_TABLE_GROUP_GAP_TOLERANCE_PT,
    _LOGICAL_PAGE_NUMBER_FOOTER_TOP_RATIO,
    _LOGICAL_PAGE_NUMBER_MAX_WIDTH_RATIO,
    _LOGICAL_PAGE_NUMBER_TEXT_RE,
    _LogicalPage,
    _LogicalPageComposition,
    _PreviewCompositionEntry,
    _PreviewRenderNode,
)
from .shared import (
    _bbox_area,
    _bbox_center,
    _bbox_contains,
    _bbox_from_bounds,
    _bbox_intersection,
    _bbox_touches_or_near,
    _shared_bbox_distance,
    _shared_page_content_margins,
    _union_box_bounds,
)

def _paragraph_region_id(paragraph: ParagraphIR) -> str | None:
    if paragraph.meta is None:
        return None
    return getattr(paragraph.meta, "layout_region_id", None)


def _paragraph_bbox(paragraph: ParagraphIR) -> PdfBoundingBox | None:
    return getattr(paragraph, "bbox", None) or getattr(paragraph.meta, "bounding_box", None)


def _page_bbox(page: PageInfo) -> PdfBoundingBox | None:
    if page.width_pt is None or page.height_pt is None:
        return None
    return PdfBoundingBox(
        left_pt=0.0,
        bottom_pt=0.0,
        right_pt=page.width_pt,
        top_pt=page.height_pt,
    )


def _bbox_vertical_overlap(left: PdfBoundingBox, right: PdfBoundingBox) -> float:
    return max(0.0, min(left.top_pt, right.top_pt) - max(left.bottom_pt, right.bottom_pt))


def _bbox_region_type(
    bbox: PdfBoundingBox | None,
    *,
    page: PageInfo,
    page_regions: list[PdfLayoutRegion],
    explicit_region_type: str | None = None,
) -> str:
    has_side_regions = any(item.region_type in {"left", "right"} for item in page_regions)
    if explicit_region_type is not None and (
        explicit_region_type not in {"main", "full"} or not has_side_regions
    ):
        return explicit_region_type
    if bbox is None:
        return explicit_region_type or "main"

    left_regions = [item for item in page_regions if item.region_type == "left" and item.bounding_box is not None]
    right_regions = [item for item in page_regions if item.region_type == "right" and item.bounding_box is not None]
    overlapping_left_regions = [
        item
        for item in left_regions
        if item.bounding_box is not None and _bbox_vertical_overlap(item.bounding_box, bbox) > 0.0
    ]
    overlapping_right_regions = [
        item
        for item in right_regions
        if item.bounding_box is not None and _bbox_vertical_overlap(item.bounding_box, bbox) > 0.0
    ]
    if overlapping_left_regions and overlapping_right_regions:
        left_regions = overlapping_left_regions
        right_regions = overlapping_right_regions
    if not left_regions or not right_regions:
        return explicit_region_type or "main"

    left_edge = max(item.bounding_box.right_pt for item in left_regions if item.bounding_box is not None)
    right_edge = min(item.bounding_box.left_pt for item in right_regions if item.bounding_box is not None)
    spans_gutter = bbox.left_pt <= left_edge and bbox.right_pt >= right_edge
    if page.width_pt is not None and (bbox.right_pt - bbox.left_pt) >= page.width_pt * 0.70:
        spans_gutter = True

    if explicit_region_type == "full":
        if spans_gutter:
            return "full"
    elif spans_gutter:
        return "full"

    bbox_center_x = (bbox.left_pt + bbox.right_pt) / 2.0
    split_x = (left_edge + right_edge) / 2.0
    return "left" if bbox_center_x <= split_x else "right"


def _paragraph_region_type(
    paragraph: ParagraphIR,
    *,
    page: PageInfo,
    page_regions: list[PdfLayoutRegion],
    region_by_id: dict[str, PdfLayoutRegion],
) -> str:
    region = region_by_id.get(_paragraph_region_id(paragraph) or "")
    bbox = _paragraph_bbox(paragraph)
    return _bbox_region_type(
        bbox,
        page=page,
        page_regions=page_regions,
        explicit_region_type=region.region_type if region is not None else None,
    )


def _union_bboxes(boxes: list[PdfBoundingBox]) -> PdfBoundingBox | None:
    return _bbox_from_bounds(
        _union_box_bounds(
            [
                (box.left_pt, box.bottom_pt, box.right_pt, box.top_pt)
                for box in boxes
            ]
        )
    )


def _paragraph_union_bbox(paragraphs: list[ParagraphIR]) -> PdfBoundingBox | None:
    boxes = [bbox for paragraph in paragraphs if (bbox := _paragraph_bbox(paragraph)) is not None]
    return _union_bboxes(boxes)


def _build_logical_pages_for_page(
    page: PageInfo,
    page_regions: list[PdfLayoutRegion],
    *,
    page_paragraphs: list[ParagraphIR] | None = None,
    starting_page_number: int = 1,
) -> list[_LogicalPage]:
    split_x = _spread_split_x(page, page_regions, page_paragraphs=page_paragraphs or [])
    if split_x is not None and page.height_pt is not None and page.width_pt is not None:
        left_regions = [region for region in page_regions if region.region_type == "left" and region.bounding_box is not None]
        right_regions = [region for region in page_regions if region.region_type == "right" and region.bounding_box is not None]
        left_region = max(left_regions, key=lambda region: _bbox_area(region.bounding_box) if region.bounding_box is not None else 0.0)
        right_region = max(right_regions, key=lambda region: _bbox_area(region.bounding_box) if region.bounding_box is not None else 0.0)
        left_bbox = PdfBoundingBox(
            left_pt=0.0,
            bottom_pt=0.0,
            right_pt=split_x,
            top_pt=page.height_pt,
        )
        right_bbox = PdfBoundingBox(
            left_pt=split_x,
            bottom_pt=0.0,
            right_pt=page.width_pt,
            top_pt=page.height_pt,
        )
        target_width_pt = page.height_pt
        left_width = max(left_bbox.right_pt - left_bbox.left_pt, 1.0)
        right_width = max(right_bbox.right_pt - right_bbox.left_pt, 1.0)
        left_scale_factor = target_width_pt / left_width
        right_scale_factor = target_width_pt / right_width
        return [
            _LogicalPage(
                page_number=starting_page_number,
                physical_page_number=page.page_number,
                logical_page_type="left",
                bounding_box=left_bbox,
                source_region_ids=[left_region.region_id],
                scale_factor=left_scale_factor,
                target_width_pt=target_width_pt,
                target_height_pt=page.height_pt * left_scale_factor,
            ),
            _LogicalPage(
                page_number=starting_page_number + 1,
                physical_page_number=page.page_number,
                logical_page_type="right",
                bounding_box=right_bbox,
                source_region_ids=[right_region.region_id],
                scale_factor=right_scale_factor,
                target_width_pt=target_width_pt,
                target_height_pt=page.height_pt * right_scale_factor,
            ),
        ]

    page_bbox = _page_bbox(page)
    if page_bbox is None:
        return []
    source_region_ids = [
        region.region_id
        for region in page_regions
        if region.region_type not in {"left", "right"}
    ]
    return [
        _LogicalPage(
            page_number=starting_page_number,
            physical_page_number=page.page_number,
            logical_page_type="single",
            bounding_box=page_bbox,
            source_region_ids=source_region_ids,
        )
    ]


def _region_split_x(
    page: PageInfo,
    page_regions: list[PdfLayoutRegion],
) -> float | None:
    if page.width_pt is None or page.height_pt is None:
        return None
    if page.width_pt <= page.height_pt:
        return None

    left_regions = [region for region in page_regions if region.region_type == "left" and region.bounding_box is not None]
    right_regions = [region for region in page_regions if region.region_type == "right" and region.bounding_box is not None]
    if not left_regions or not right_regions:
        return None

    left_edge = max(region.bounding_box.right_pt for region in left_regions if region.bounding_box is not None)
    right_edge = min(region.bounding_box.left_pt for region in right_regions if region.bounding_box is not None)
    gutter_width = right_edge - left_edge
    if gutter_width <= 0.0:
        return None

    split_x = (left_edge + right_edge) / 2.0
    if split_x <= page.width_pt * 0.20 or split_x >= page.width_pt * 0.80:
        return None
    if gutter_width < max(page.width_pt * 0.02, 8.0):
        return None
    return split_x


def _footer_page_number_candidates(
    page: PageInfo,
    page_paragraphs: list[ParagraphIR],
) -> list[tuple[int, PdfBoundingBox]]:
    if page.width_pt is None or page.height_pt is None:
        return []

    candidates: list[tuple[int, PdfBoundingBox]] = []
    for paragraph in page_paragraphs:
        bbox = _paragraph_bbox(paragraph)
        text = paragraph.text.strip()
        if bbox is None or not text:
            continue
        if bbox.top_pt > page.height_pt * _LOGICAL_PAGE_NUMBER_FOOTER_TOP_RATIO:
            continue
        if (bbox.right_pt - bbox.left_pt) > page.width_pt * _LOGICAL_PAGE_NUMBER_MAX_WIDTH_RATIO:
            continue
        match = _LOGICAL_PAGE_NUMBER_TEXT_RE.match(text)
        if match is None:
            continue
        candidates.append((int(match.group(1)), bbox))
    return candidates


def _has_footer_page_number_pair(
    page: PageInfo,
    page_paragraphs: list[ParagraphIR],
    *,
    split_x: float,
) -> bool:
    left_candidates: list[tuple[int, PdfBoundingBox]] = []
    right_candidates: list[tuple[int, PdfBoundingBox]] = []
    for page_number, bbox in _footer_page_number_candidates(page, page_paragraphs):
        center_x, _center_y = _bbox_center(bbox)
        if center_x < split_x:
            left_candidates.append((page_number, bbox))
        elif center_x > split_x:
            right_candidates.append((page_number, bbox))

    if not left_candidates or not right_candidates:
        return False

    for left_number, _left_bbox in left_candidates:
        for right_number, _right_bbox in right_candidates:
            if right_number == left_number + 1:
                return True
    return False


def _spread_split_x(
    page: PageInfo,
    page_regions: list[PdfLayoutRegion],
    *,
    page_paragraphs: list[ParagraphIR],
) -> float | None:
    split_x = _region_split_x(page, page_regions)
    if split_x is None:
        return None
    if not _has_footer_page_number_pair(page, page_paragraphs, split_x=split_x):
        return None
    return split_x


def _score_logical_page_for_bbox(logical_page: _LogicalPage, bbox: PdfBoundingBox) -> tuple[int, float, float]:
    center_x, center_y = _bbox_center(bbox)
    center_inside = (
        logical_page.bounding_box.left_pt <= center_x <= logical_page.bounding_box.right_pt
        and logical_page.bounding_box.bottom_pt <= center_y <= logical_page.bounding_box.top_pt
    )
    overlap = _bbox_intersection(logical_page.bounding_box, bbox)
    overlap_ratio = 0.0 if overlap is None else _bbox_area(overlap) / max(_bbox_area(bbox), 1.0)
    bbox_center_x, bbox_center_y = _bbox_center(bbox)
    page_center_x, page_center_y = _bbox_center(logical_page.bounding_box)
    center_distance = abs(bbox_center_x - page_center_x) + abs(bbox_center_y - page_center_y)
    return (1 if center_inside else 0, overlap_ratio, -center_distance)


def _best_logical_page_for_bbox(
    logical_pages: list[_LogicalPage],
    *,
    bbox: PdfBoundingBox | None,
    explicit_region_type: str | None,
) -> _LogicalPage | None:
    if not logical_pages:
        return None
    if len(logical_pages) == 1:
        return logical_pages[0]
    if explicit_region_type in {"left", "right"}:
        for logical_page in logical_pages:
            if logical_page.logical_page_type == explicit_region_type:
                return logical_page
    if bbox is None:
        return logical_pages[0]

    best_page: _LogicalPage | None = None
    best_score: tuple[int, float, float] | None = None
    for logical_page in logical_pages:
        score = _score_logical_page_for_bbox(logical_page, bbox)
        if best_score is None or score > best_score:
            best_page = logical_page
            best_score = score
    return best_page


def _is_image_only_paragraph(paragraph: ParagraphIR) -> bool:
    return (
        not paragraph.text.strip()
        and not paragraph.runs
        and not paragraph.tables
        and len(paragraph.images) == 1
    )


def _image_strip_paragraphs_can_merge(left: ParagraphIR, right: ParagraphIR) -> bool:
    if not _is_image_only_paragraph(left) or not _is_image_only_paragraph(right):
        return False

    left_bbox = _paragraph_bbox(left)
    right_bbox = _paragraph_bbox(right)
    if left_bbox is None or right_bbox is None:
        return False

    left_width = max(left_bbox.right_pt - left_bbox.left_pt, 0.0)
    right_width = max(right_bbox.right_pt - right_bbox.left_pt, 0.0)
    left_height = max(left_bbox.top_pt - left_bbox.bottom_pt, 0.0)
    right_height = max(right_bbox.top_pt - right_bbox.bottom_pt, 0.0)
    if left_width <= 0.0 or right_width <= 0.0 or left_height <= 0.0 or right_height <= 0.0:
        return False

    if left_height > left_width * _IMAGE_STRIP_MAX_HEIGHT_RATIO:
        return False
    if right_height > right_width * _IMAGE_STRIP_MAX_HEIGHT_RATIO:
        return False

    overlap_ratio = _span_overlap_ratio(
        left_bbox.left_pt,
        left_bbox.right_pt,
        right_bbox.left_pt,
        right_bbox.right_pt,
    )
    if overlap_ratio < _IMAGE_STRIP_MIN_SPAN_OVERLAP_RATIO:
        return False

    left_center_x = (left_bbox.left_pt + left_bbox.right_pt) / 2.0
    right_center_x = (right_bbox.left_pt + right_bbox.right_pt) / 2.0
    max_center_delta = max(max(left_width, right_width) * _IMAGE_STRIP_MAX_CENTER_DELTA_RATIO, 4.0)
    if abs(left_center_x - right_center_x) > max_center_delta:
        return False

    upper, lower = (left_bbox, right_bbox) if left_bbox.top_pt >= right_bbox.top_pt else (right_bbox, left_bbox)
    vertical_gap = max(upper.bottom_pt - lower.top_pt, 0.0)
    if vertical_gap > _IMAGE_STRIP_MAX_GAP_PT:
        return False

    return True


def _merged_image_strip_paragraph(paragraphs: list[ParagraphIR]) -> ParagraphIR:
    merged = paragraphs[0].model_copy(deep=True)
    merged.unit_id = f"{paragraphs[0].unit_id}.image-stack"
    merged.content = [
        image.model_copy(deep=True)
        for paragraph in paragraphs
        for image in paragraph.images
    ]
    merged_bbox = _paragraph_union_bbox(paragraphs)
    merged.bbox = merged_bbox
    if merged.meta is not None and hasattr(merged.meta, "bounding_box"):
        merged.meta.bounding_box = merged_bbox
    merged.recompute_text()
    return merged


def _collapse_image_strip_paragraphs(paragraphs: list[ParagraphIR]) -> list[ParagraphIR]:
    collapsed: list[ParagraphIR] = []
    current_group: list[ParagraphIR] = []

    def flush_group() -> None:
        nonlocal current_group
        if not current_group:
            return
        if len(current_group) >= _IMAGE_STRIP_MIN_GROUP_SIZE:
            collapsed.append(_merged_image_strip_paragraph(current_group))
        else:
            collapsed.extend(current_group)
        current_group = []

    for paragraph in paragraphs:
        if not current_group:
            current_group = [paragraph]
            continue
        if _image_strip_paragraphs_can_merge(current_group[-1], paragraph):
            current_group.append(paragraph)
            continue
        flush_group()
        current_group = [paragraph]

    flush_group()
    return collapsed


def _is_footer_page_number_paragraph(page: PageInfo, paragraph: ParagraphIR) -> bool:
    bbox = _paragraph_bbox(paragraph)
    text = paragraph.text.strip()
    if bbox is None or not text or page.width_pt is None or page.height_pt is None:
        return False
    if bbox.top_pt > page.height_pt * _LOGICAL_PAGE_NUMBER_FOOTER_TOP_RATIO:
        return False
    if (bbox.right_pt - bbox.left_pt) > page.width_pt * _LOGICAL_PAGE_NUMBER_MAX_WIDTH_RATIO:
        return False
    return _LOGICAL_PAGE_NUMBER_TEXT_RE.match(text) is not None


def _column_band_split_x(
    page: PageInfo,
    slice_boxes: list[PdfBoundingBox],
) -> float | None:
    if page.width_pt is None or len(slice_boxes) < 2:
        return None

    center_x = page.width_pt / 2.0
    center_offset = page.width_pt * _COLUMN_BAND_CENTER_OFFSET_RATIO
    left_boxes = [
        box
        for box in slice_boxes
        if ((box.left_pt + box.right_pt) / 2.0) < center_x - center_offset
    ]
    right_boxes = [
        box
        for box in slice_boxes
        if ((box.left_pt + box.right_pt) / 2.0) > center_x + center_offset
    ]
    if not left_boxes or not right_boxes:
        return None

    strip_half_width = max(page.width_pt * 0.01, 4.0)
    if any(
        box.right_pt >= center_x - strip_half_width and box.left_pt <= center_x + strip_half_width
        for box in slice_boxes
    ):
        return None

    left_edge = max(box.right_pt for box in left_boxes)
    right_edge = min(box.left_pt for box in right_boxes)
    gutter_width = right_edge - left_edge
    if gutter_width < max(page.width_pt * _COLUMN_BAND_GUTTER_MIN_WIDTH_RATIO, _COLUMN_BAND_GUTTER_MIN_WIDTH_PT):
        return None

    return (left_edge + right_edge) / 2.0


def _detect_intra_page_column_regions(
    page: PageInfo,
    page_paragraphs: list[ParagraphIR],
) -> list[PdfLayoutRegion]:
    boxes = [
        bbox
        for paragraph in page_paragraphs
        if not _is_footer_page_number_paragraph(page, paragraph)
        if (bbox := _paragraph_bbox(paragraph)) is not None
    ]
    if page.width_pt is None or page.height_pt is None or len(boxes) < 2:
        return []

    boundaries = sorted({value for box in boxes for value in (box.bottom_pt, box.top_pt)})
    if len(boundaries) < 2:
        return []

    bands: list[dict[str, Any]] = []
    active_band: dict[str, Any] | None = None

    for bottom, top in zip(boundaries, boundaries[1:]):
        if top - bottom <= 0.0:
            continue
        midpoint = (bottom + top) / 2.0
        slice_boxes = [box for box in boxes if box.bottom_pt < midpoint < box.top_pt]
        split_x = _column_band_split_x(page, slice_boxes)
        if split_x is None:
            if active_band is not None:
                bands.append(active_band)
                active_band = None
            continue

        left_boxes = [box for box in slice_boxes if ((box.left_pt + box.right_pt) / 2.0) <= split_x]
        right_boxes = [box for box in slice_boxes if ((box.left_pt + box.right_pt) / 2.0) > split_x]
        if not left_boxes or not right_boxes:
            if active_band is not None:
                bands.append(active_band)
                active_band = None
            continue

        if (
            active_band is not None
            and abs(active_band["split_x"] - split_x) <= _COLUMN_BAND_SPLIT_MERGE_TOLERANCE_PT
            and abs(active_band["top"] - bottom) <= 1.0
        ):
            active_band["top"] = top
            active_band["left_boxes"].extend(left_boxes)
            active_band["right_boxes"].extend(right_boxes)
            continue

        if active_band is not None:
            bands.append(active_band)
        active_band = {
            "bottom": bottom,
            "top": top,
            "split_x": split_x,
            "left_boxes": list(left_boxes),
            "right_boxes": list(right_boxes),
        }

    if active_band is not None:
        bands.append(active_band)

    regions: list[PdfLayoutRegion] = []
    for band_index, band in enumerate(sorted(bands, key=lambda item: item["top"], reverse=True), start=1):
        if (band["top"] - band["bottom"]) < _COLUMN_BAND_MIN_HEIGHT_PT:
            continue
        left_bbox = _union_bboxes(band["left_boxes"])
        right_bbox = _union_bboxes(band["right_boxes"])
        if left_bbox is None or right_bbox is None:
            continue
        regions.extend(
            [
                PdfLayoutRegion(
                    region_id=f"p{page.page_number}-band{band_index}-left",
                    region_type="left",
                    page_number=page.page_number,
                    bounding_box=PdfBoundingBox(
                        left_pt=left_bbox.left_pt,
                        bottom_pt=band["bottom"],
                        right_pt=left_bbox.right_pt,
                        top_pt=band["top"],
                    ),
                ),
                PdfLayoutRegion(
                    region_id=f"p{page.page_number}-band{band_index}-right",
                    region_type="right",
                    page_number=page.page_number,
                    bounding_box=PdfBoundingBox(
                        left_pt=right_bbox.left_pt,
                        bottom_pt=band["bottom"],
                        right_pt=right_bbox.right_pt,
                        top_pt=band["top"],
                    ),
                ),
            ]
        )

    return regions


def _rebase_bbox(bbox: PdfBoundingBox | None, *, origin_bbox: PdfBoundingBox) -> PdfBoundingBox | None:
    if bbox is None:
        return None
    return PdfBoundingBox(
        left_pt=bbox.left_pt - origin_bbox.left_pt,
        bottom_pt=bbox.bottom_pt - origin_bbox.bottom_pt,
        right_pt=bbox.right_pt - origin_bbox.left_pt,
        top_pt=bbox.top_pt - origin_bbox.bottom_pt,
    )


def _scale_value(value: float | None, *, scale_factor: float) -> float | None:
    if value is None:
        return None
    return value * scale_factor


def _scale_bbox(bbox: PdfBoundingBox | None, *, scale_factor: float) -> PdfBoundingBox | None:
    if bbox is None or scale_factor == 1.0:
        return bbox
    return PdfBoundingBox(
        left_pt=bbox.left_pt * scale_factor,
        bottom_pt=bbox.bottom_pt * scale_factor,
        right_pt=bbox.right_pt * scale_factor,
        top_pt=bbox.top_pt * scale_factor,
    )


def _rebase_meta_bbox(meta: Any, *, origin_bbox: PdfBoundingBox, scale_factor: float = 1.0) -> Any:
    if meta is None or not hasattr(meta, "bounding_box"):
        return meta
    rebased_meta = meta.model_copy(deep=True) if hasattr(meta, "model_copy") else meta
    rebased_meta.bounding_box = _scale_bbox(
        _rebase_bbox(getattr(meta, "bounding_box", None), origin_bbox=origin_bbox),
        scale_factor=scale_factor,
    )
    return rebased_meta


def _scale_para_style(para_style: Any, *, scale_factor: float) -> Any:
    if para_style is None or scale_factor == 1.0:
        return para_style
    clone = para_style.model_copy(deep=True) if hasattr(para_style, "model_copy") else para_style
    for field_name in ("left_indent_pt", "right_indent_pt", "first_line_indent_pt", "hanging_indent_pt"):
        value = getattr(clone, field_name, None)
        if value is not None:
            setattr(clone, field_name, value * scale_factor)
    return clone


def _scale_run_style(run_style: Any, *, scale_factor: float) -> Any:
    if run_style is None or scale_factor == 1.0:
        return run_style
    clone = run_style.model_copy(deep=True) if hasattr(run_style, "model_copy") else run_style
    if getattr(clone, "size_pt", None) is not None:
        clone.size_pt = clone.size_pt * scale_factor
    return clone


def _scale_cell_style(cell_style: Any, *, scale_factor: float) -> Any:
    if cell_style is None or scale_factor == 1.0:
        return cell_style
    clone = cell_style.model_copy(deep=True) if hasattr(cell_style, "model_copy") else cell_style
    if getattr(clone, "width_pt", None) is not None:
        clone.width_pt = clone.width_pt * scale_factor
    if getattr(clone, "height_pt", None) is not None:
        clone.height_pt = clone.height_pt * scale_factor
    return clone


def _scale_table_style(table_style: Any, *, scale_factor: float) -> Any:
    if table_style is None or scale_factor == 1.0:
        return table_style
    clone = table_style.model_copy(deep=True) if hasattr(table_style, "model_copy") else table_style
    if getattr(clone, "width_pt", None) is not None:
        clone.width_pt = clone.width_pt * scale_factor
    if getattr(clone, "height_pt", None) is not None:
        clone.height_pt = clone.height_pt * scale_factor
    return clone


def _rebase_table_for_logical_page(table: TableIR, *, origin_bbox: PdfBoundingBox, scale_factor: float = 1.0) -> TableIR:
    clone = table.model_copy(deep=True)
    clone.bbox = _scale_bbox(_rebase_bbox(table.bbox, origin_bbox=origin_bbox), scale_factor=scale_factor)
    clone.meta = _rebase_meta_bbox(table.meta, origin_bbox=origin_bbox, scale_factor=scale_factor)
    clone.table_style = _scale_table_style(clone.table_style, scale_factor=scale_factor)
    for cell in clone.cells:
        cell.bbox = _scale_bbox(_rebase_bbox(cell.bbox, origin_bbox=origin_bbox), scale_factor=scale_factor)
        cell.meta = _rebase_meta_bbox(cell.meta, origin_bbox=origin_bbox, scale_factor=scale_factor)
        cell.cell_style = _scale_cell_style(cell.cell_style, scale_factor=scale_factor)
        for paragraph in cell.paragraphs:
            rebased = _rebase_paragraph_for_logical_page(
                paragraph,
                origin_bbox=origin_bbox,
                scale_factor=scale_factor,
            )
            paragraph.unit_id = rebased.unit_id
            paragraph.text = rebased.text
            paragraph.page_number = rebased.page_number
            paragraph.bbox = rebased.bbox
            paragraph.meta = rebased.meta
            paragraph.para_style = rebased.para_style
            paragraph.content = rebased.content
    return clone


def _rebase_paragraph_content_node(node: Any, *, origin_bbox: PdfBoundingBox, scale_factor: float = 1.0) -> Any:
    if isinstance(node, RunIR):
        clone = node.model_copy(deep=True)
        clone.bbox = _scale_bbox(_rebase_bbox(node.bbox, origin_bbox=origin_bbox), scale_factor=scale_factor)
        clone.meta = _rebase_meta_bbox(node.meta, origin_bbox=origin_bbox, scale_factor=scale_factor)
        clone.run_style = _scale_run_style(clone.run_style, scale_factor=scale_factor)
        return clone
    if isinstance(node, ImageIR):
        clone = node.model_copy(deep=True)
        clone.bbox = _scale_bbox(_rebase_bbox(node.bbox, origin_bbox=origin_bbox), scale_factor=scale_factor)
        clone.display_width_pt = _scale_value(clone.display_width_pt, scale_factor=scale_factor)
        clone.display_height_pt = _scale_value(clone.display_height_pt, scale_factor=scale_factor)
        return clone
    if isinstance(node, TableIR):
        return _rebase_table_for_logical_page(node, origin_bbox=origin_bbox, scale_factor=scale_factor)
    return node


def _rebase_paragraph_for_logical_page(
    paragraph: ParagraphIR,
    *,
    origin_bbox: PdfBoundingBox,
    scale_factor: float = 1.0,
) -> ParagraphIR:
    clone = paragraph.model_copy(deep=True)
    clone.bbox = _scale_bbox(_rebase_bbox(_paragraph_bbox(paragraph), origin_bbox=origin_bbox), scale_factor=scale_factor)
    clone.meta = _rebase_meta_bbox(paragraph.meta, origin_bbox=origin_bbox, scale_factor=scale_factor)
    clone.para_style = _scale_para_style(paragraph.para_style, scale_factor=scale_factor)
    clone.content = [
        _rebase_paragraph_content_node(node, origin_bbox=origin_bbox, scale_factor=scale_factor)
        for node in paragraph.content
    ]
    clone.recompute_text()
    return clone


def _rebase_candidate_for_logical_page(
    candidate: PdfPreviewVisualBlockCandidate,
    *,
    logical_page: _LogicalPage,
) -> PdfPreviewVisualBlockCandidate:
    clone = candidate.model_copy(deep=True)
    clone.page_number = logical_page.page_number
    clone.bounding_box = (
        _scale_bbox(
            _rebase_bbox(candidate.bounding_box, origin_bbox=logical_page.bounding_box),
            scale_factor=logical_page.scale_factor,
        )
        or candidate.bounding_box
    )
    clone.child_cells = [
        rebased
        for cell_bbox in candidate.child_cells
        if (
            rebased := _scale_bbox(
                _rebase_bbox(cell_bbox, origin_bbox=logical_page.bounding_box),
                scale_factor=logical_page.scale_factor,
            )
        ) is not None
    ]
    return clone


def _logical_page_page_info(logical_page: _LogicalPage, *, source_page: PageInfo) -> PageInfo:
    bbox = logical_page.bounding_box
    margin_left = _scale_value(source_page.margin_left_pt, scale_factor=logical_page.scale_factor)
    margin_right = _scale_value(source_page.margin_right_pt, scale_factor=logical_page.scale_factor)
    margin_top = _scale_value(source_page.margin_top_pt, scale_factor=logical_page.scale_factor)
    margin_bottom = _scale_value(source_page.margin_bottom_pt, scale_factor=logical_page.scale_factor)
    return PageInfo(
        page_number=logical_page.page_number,
        width_pt=logical_page.target_width_pt or max(bbox.right_pt - bbox.left_pt, 0.0),
        height_pt=logical_page.target_height_pt or max(bbox.top_pt - bbox.bottom_pt, 0.0),
        margin_left_pt=margin_left,
        margin_right_pt=margin_right,
        margin_top_pt=margin_top,
        margin_bottom_pt=margin_bottom,
    )


def _flow_regions_for_logical_page(
    page: PageInfo,
    page_regions: list[PdfLayoutRegion],
    *,
    logical_pages: list[_LogicalPage],
    page_paragraphs: list[ParagraphIR],
) -> list[PdfLayoutRegion]:
    if len(logical_pages) != 1:
        return []
    band_regions = _detect_intra_page_column_regions(page, page_paragraphs)
    if band_regions:
        return band_regions
    return [region for region in page_regions if region.region_type in {"left", "right"}]


def _logical_page_paragraphs(
    page: PageInfo,
    page_paragraphs: list[ParagraphIR],
    *,
    page_regions: list[PdfLayoutRegion],
    logical_pages: list[_LogicalPage],
    logical_page: _LogicalPage,
) -> list[ParagraphIR]:
    region_by_id = {region.region_id: region for region in page_regions}
    paragraphs: list[ParagraphIR] = []
    for paragraph in page_paragraphs:
        region_type = _paragraph_region_type(
            paragraph,
            page=page,
            page_regions=page_regions,
            region_by_id=region_by_id,
        )
        target_page = _best_logical_page_for_bbox(
            logical_pages,
            bbox=_paragraph_bbox(paragraph),
            explicit_region_type=region_type,
        )
        if target_page is None or target_page.page_number != logical_page.page_number:
            continue
        rebased = _rebase_paragraph_for_logical_page(
            paragraph,
            origin_bbox=logical_page.bounding_box,
            scale_factor=logical_page.scale_factor,
        )
        rebased.page_number = logical_page.page_number
        paragraphs.append(rebased)
    return paragraphs


def _logical_page_preview_context(
    page: PageInfo,
    preview_context: PdfPreviewContext,
    *,
    page_regions: list[PdfLayoutRegion],
    logical_pages: list[_LogicalPage],
    logical_page: _LogicalPage,
) -> PdfPreviewContext:
    local_candidates: list[PdfPreviewVisualBlockCandidate] = []
    for candidate in preview_context.visual_block_candidates:
        if candidate.page_number != page.page_number:
            continue
        region_type = _bbox_region_type(
            candidate.bounding_box,
            page=page,
            page_regions=page_regions,
            explicit_region_type=None,
        )
        target_page = _best_logical_page_for_bbox(
            logical_pages,
            bbox=candidate.bounding_box,
            explicit_region_type=region_type,
        )
        if target_page is None or target_page.page_number != logical_page.page_number:
            continue
        local_candidates.append(
            _rebase_candidate_for_logical_page(
                candidate,
                logical_page=logical_page,
            )
        )
    return PdfPreviewContext(
        layout_regions=[],
        tables=[],
        visual_block_candidates=local_candidates,
    )


def _bbox_order_key(
    bbox: PdfBoundingBox | None,
    *,
    fallback_index: int,
    subindex: int = 0,
) -> tuple[float, float, int, int]:
    if bbox is None:
        return (1_000_000.0, 1_000_000.0, fallback_index, subindex)
    return (-bbox.top_pt, bbox.left_pt, fallback_index, subindex)


def _paragraph_offsets_from_page(
    paragraph: ParagraphIR,
    *,
    page: PageInfo,
) -> tuple[float | None, float | None]:
    bbox = _paragraph_bbox(paragraph)
    return _bbox_offsets_from_page(bbox, page=page)


def _bbox_offsets_from_page(
    bbox: PdfBoundingBox | None,
    *,
    page: PageInfo,
) -> tuple[float | None, float | None]:
    if bbox is None or page.height_pt is None:
        return None, None
    top_offset = max(page.height_pt - bbox.top_pt, 0.0)
    bottom_offset = max(page.height_pt - bbox.bottom_pt, top_offset)
    return top_offset, bottom_offset


def _bbox_assignment_score(candidate_bbox: PdfBoundingBox, node_bbox: PdfBoundingBox) -> tuple[int, float, float]:
    if _bbox_contains(
        candidate_bbox,
        node_bbox,
        tolerance_pt=_CANDIDATE_ASSIGN_TOLERANCE_PT,
    ):
        candidate_area = _bbox_area(candidate_bbox)
        node_area = _bbox_area(node_bbox)
        area_ratio = 1.0 if candidate_area <= 0.0 else min(node_area / candidate_area, 1.0)
        return (3, area_ratio, -candidate_area)

    center_x, center_y = _bbox_center(node_bbox)
    if (
        candidate_bbox.left_pt - _CANDIDATE_ASSIGN_TOLERANCE_PT <= center_x <= candidate_bbox.right_pt + _CANDIDATE_ASSIGN_TOLERANCE_PT
        and candidate_bbox.bottom_pt - _CANDIDATE_ASSIGN_TOLERANCE_PT <= center_y <= candidate_bbox.top_pt + _CANDIDATE_ASSIGN_TOLERANCE_PT
    ):
        overlap = _bbox_intersection(candidate_bbox, node_bbox)
        overlap_area = _bbox_area(overlap) if overlap is not None else 0.0
        node_area = _bbox_area(node_bbox)
        overlap_ratio = 0.0 if node_area <= 0.0 else overlap_area / node_area
        return (2, overlap_ratio, -_bbox_area(candidate_bbox))

    overlap = _bbox_intersection(candidate_bbox, node_bbox)
    if overlap is None:
        return (0, 0.0, 0.0)
    node_area = _bbox_area(node_bbox)
    overlap_ratio = 0.0 if node_area <= 0.0 else _bbox_area(overlap) / node_area
    return (1, overlap_ratio, -_bbox_area(candidate_bbox))


def _best_candidate_for_node(
    node_bbox: PdfBoundingBox,
    candidates: list[PdfPreviewVisualBlockCandidate],
) -> PdfPreviewVisualBlockCandidate | None:
    best_candidate: PdfPreviewVisualBlockCandidate | None = None
    best_score: tuple[int, float, float] | None = None
    for candidate in candidates:
        score = _bbox_assignment_score(candidate.bounding_box, node_bbox)
        if score[0] <= 0:
            continue
        if best_score is None or score > best_score:
            best_candidate = candidate
            best_score = score
    return best_candidate


def _compose_logical_page(
    page: PageInfo,
    page_paragraphs: list[ParagraphIR],
    *,
    page_regions: list[PdfLayoutRegion],
    preview_context: PdfPreviewContext,
) -> _LogicalPageComposition:
    assigned_candidates, assigned_paragraph_ids, assigned_child_ids = _assign_page_nodes_to_candidates(
        page,
        page_paragraphs,
        page_regions=page_regions,
        preview_context=preview_context,
    )
    promoted_table_paragraphs, promoted_candidate_ids = _promote_assigned_candidates_to_layout_tables(
        assigned_candidates,
        page=page,
    )
    flow_paragraphs = _filter_page_flow_paragraphs(
        page_paragraphs,
        assigned_paragraph_ids=assigned_paragraph_ids,
        assigned_child_ids=assigned_child_ids,
    )
    flow_paragraphs.extend(promoted_table_paragraphs)
    entries = _build_preview_entries(
        page,
        flow_paragraphs,
        page_regions=page_regions,
    )
    ordered_paragraphs = _materialize_flow_paragraphs(
        page,
        entries,
        page_regions=page_regions,
    )
    return _LogicalPageComposition(
        ordered_paragraphs=ordered_paragraphs,
        assigned_candidates=assigned_candidates,
        promoted_candidate_ids=promoted_candidate_ids,
    )


def _normalize_pdf_doc_for_flow(
    doc_ir: DocIR,
    *,
    preview_context: PdfPreviewContext | None,
) -> DocIR:
    if preview_context is None or not doc_ir.pages:
        return doc_ir

    paragraphs_by_page: dict[int, list[ParagraphIR]] = {}
    unpaged: list[ParagraphIR] = []
    for paragraph in doc_ir.paragraphs:
        if paragraph.page_number is None:
            unpaged.append(paragraph)
            continue
        paragraphs_by_page.setdefault(paragraph.page_number, []).append(paragraph)

    normalized_pages: list[PageInfo] = []
    normalized_paragraphs: list[ParagraphIR] = []
    residual_candidates: list[PdfPreviewVisualBlockCandidate] = []
    next_logical_page_number = 1

    for page in doc_ir.pages:
        page_regions = [region for region in preview_context.layout_regions if region.page_number == page.page_number]
        logical_pages = _build_logical_pages_for_page(
            page,
            page_regions,
            page_paragraphs=paragraphs_by_page.get(page.page_number, []),
            starting_page_number=next_logical_page_number,
        )
        next_logical_page_number += max(len(logical_pages), 1)

        if not logical_pages:
            normalized_pages.append(page.model_copy(deep=True))
            normalized_paragraphs.extend(paragraphs_by_page.get(page.page_number, []))
            continue

        physical_page_paragraphs = paragraphs_by_page.get(page.page_number, [])
        for logical_page in logical_pages:
            logical_page_info = _logical_page_page_info(logical_page, source_page=page)
            logical_page_paragraphs = _logical_page_paragraphs(
                page,
                physical_page_paragraphs,
                page_regions=page_regions,
                logical_pages=logical_pages,
                logical_page=logical_page,
            )
            logical_page_paragraphs = _collapse_image_strip_paragraphs(logical_page_paragraphs)
            flow_regions = _flow_regions_for_logical_page(
                logical_page_info,
                page_regions,
                logical_pages=logical_pages,
                page_paragraphs=logical_page_paragraphs,
            )
            logical_preview_context = _logical_page_preview_context(
                page,
                preview_context,
                page_regions=page_regions,
                logical_pages=logical_pages,
                logical_page=logical_page,
            )
            composition = _compose_logical_page(
                logical_page_info,
                logical_page_paragraphs,
                page_regions=flow_regions,
                preview_context=logical_preview_context,
            )
            normalized_pages.append(logical_page_info)
            normalized_paragraphs.extend(composition.ordered_paragraphs)
            residual_candidates.extend(
                assigned_candidate.candidate
                for assigned_candidate in composition.assigned_candidates
                if id(assigned_candidate.candidate) not in composition.promoted_candidate_ids
            )

    doc_ir.pages = normalized_pages
    doc_ir.paragraphs = normalized_paragraphs + unpaged
    preview_context.layout_regions = []
    preview_context.tables = []
    preview_context.visual_block_candidates = residual_candidates
    return doc_ir


def _build_preview_entries(
    page: PageInfo,
    page_paragraphs: list[ParagraphIR],
    *,
    page_regions: list[PdfLayoutRegion],
) -> list[_PreviewCompositionEntry]:
    region_by_id = {region.region_id: region for region in page_regions}
    entries: list[_PreviewCompositionEntry] = []
    for fallback_index, paragraph in enumerate(page_paragraphs):
        top_offset, bottom_offset = _paragraph_offsets_from_page(paragraph, page=page)
        paragraph_bbox = _paragraph_bbox(paragraph)
        order_key = _bbox_order_key(paragraph_bbox, fallback_index=fallback_index)
        entries.append(
            _PreviewCompositionEntry(
                item_type="paragraph",
                bounding_box=paragraph_bbox,
                paragraph=paragraph,
                region_type=_paragraph_region_type(
                    paragraph,
                    page=page,
                    page_regions=page_regions,
                    region_by_id=region_by_id,
                ),
                top_offset_pt=top_offset,
                bottom_offset_pt=bottom_offset,
                order_key=order_key,
            )
        )

    entries.sort(key=lambda entry: entry.order_key)
    return entries


def _primary_region_bbox(
    page_regions: list[PdfLayoutRegion],
    *,
    region_type: str,
) -> PdfBoundingBox | None:
    matching = [
        region.bounding_box
        for region in page_regions
        if region.region_type == region_type and region.bounding_box is not None
    ]
    if not matching:
        return None
    return max(matching, key=_bbox_area)


def _column_band_cell_style(width_pt: float | None) -> CellStyleInfo:
    return CellStyleInfo(
        width_pt=width_pt,
        vertical_align="top",
    )


def _build_column_band_paragraph(
    page: PageInfo,
    *,
    band_index: int,
    left_paragraphs: list[ParagraphIR],
    right_paragraphs: list[ParagraphIR],
    left_region_bbox: PdfBoundingBox | None,
    right_region_bbox: PdfBoundingBox | None,
) -> ParagraphIR:
    margin_top, margin_right, margin_bottom, margin_left = _shared_page_content_margins(page)
    content_width = None
    if page.width_pt is not None:
        content_width = max(page.width_pt - margin_left - margin_right, 0.0)

    left_width = None
    right_width = None
    gutter_width = 18.0
    if left_region_bbox is not None:
        left_width = max(left_region_bbox.right_pt - left_region_bbox.left_pt, 0.0)
    if right_region_bbox is not None:
        right_width = max(right_region_bbox.right_pt - right_region_bbox.left_pt, 0.0)
    if left_region_bbox is not None and right_region_bbox is not None:
        gutter_width = max(right_region_bbox.left_pt - left_region_bbox.right_pt, 18.0)

    if content_width is not None and left_width is None and right_width is None:
        gutter_width = max(content_width * 0.06, 18.0)
        column_width = max((content_width - gutter_width) / 2.0, 0.0)
        left_width = column_width
        right_width = column_width
    elif content_width is not None:
        if left_width is None and right_width is not None:
            left_width = max(content_width - right_width - gutter_width, 0.0)
        elif right_width is None and left_width is not None:
            right_width = max(content_width - left_width - gutter_width, 0.0)

    cells = [
        TableCellIR(
            unit_id=f"pdf-preview.p{page.page_number}.column-band.{band_index}.cell.1",
            row_index=1,
            col_index=1,
            cell_style=_column_band_cell_style(left_width),
            paragraphs=[paragraph.model_copy(deep=True) for paragraph in left_paragraphs],
        ),
        TableCellIR(
            unit_id=f"pdf-preview.p{page.page_number}.column-band.{band_index}.cell.2",
            row_index=1,
            col_index=2,
            cell_style=_column_band_cell_style(gutter_width),
            paragraphs=[],
        ),
        TableCellIR(
            unit_id=f"pdf-preview.p{page.page_number}.column-band.{band_index}.cell.3",
            row_index=1,
            col_index=3,
            cell_style=_column_band_cell_style(right_width),
            paragraphs=[paragraph.model_copy(deep=True) for paragraph in right_paragraphs],
        ),
    ]
    for cell in cells:
        cell.recompute_text()

    table = TableIR(
        unit_id=f"pdf-preview.p{page.page_number}.column-band.{band_index}",
        row_count=1,
        col_count=3,
        table_style=TableStyleInfo(
            row_count=1,
            col_count=3,
            width_pt=content_width,
            preview_grid=False,
        ),
        cells=cells,
    )
    paragraph = ParagraphIR(
        unit_id=f"{table.unit_id}.paragraph",
        page_number=page.page_number,
        text="",
        content=[table],
    )
    paragraph.recompute_text()
    return paragraph


def _materialize_flow_paragraphs(
    page: PageInfo,
    entries: list[_PreviewCompositionEntry],
    *,
    page_regions: list[PdfLayoutRegion],
) -> list[ParagraphIR]:
    ordered_paragraphs = [entry.paragraph for entry in entries if entry.paragraph is not None]
    has_side_regions = any(region.region_type in {"left", "right"} for region in page_regions)
    if not has_side_regions:
        return ordered_paragraphs

    materialized: list[ParagraphIR] = []
    left_band: list[ParagraphIR] = []
    right_band: list[ParagraphIR] = []
    band_index = 0

    def flush_band() -> None:
        nonlocal band_index, left_band, right_band
        if not left_band and not right_band:
            return
        band_index += 1
        left_region_bbox = _paragraph_union_bbox(left_band) or _primary_region_bbox(page_regions, region_type="left")
        right_region_bbox = _paragraph_union_bbox(right_band) or _primary_region_bbox(page_regions, region_type="right")
        materialized.append(
            _build_column_band_paragraph(
                page,
                band_index=band_index,
                left_paragraphs=left_band,
                right_paragraphs=right_band,
                left_region_bbox=left_region_bbox,
                right_region_bbox=right_region_bbox,
            )
        )
        left_band = []
        right_band = []

    for entry in entries:
        if entry.paragraph is None:
            continue
        if entry.region_type == "left":
            left_band.append(entry.paragraph)
            continue
        if entry.region_type == "right":
            right_band.append(entry.paragraph)
            continue
        flush_band()
        materialized.append(entry.paragraph)

    flush_band()
    return materialized


def _span_overlap_ratio(
    left_start: float,
    left_end: float,
    right_start: float,
    right_end: float,
) -> float:
    overlap = max(min(left_end, right_end) - max(left_start, right_start), 0.0)
    shorter_span = min(max(left_end - left_start, 0.0), max(right_end - right_start, 0.0))
    if overlap <= 0.0 or shorter_span <= 0.0:
        return 0.0
    return overlap / shorter_span


def _candidate_boxes_belong_to_same_group(
    left: _AssignedCandidate,
    right: _AssignedCandidate,
) -> bool:
    if left.region_type != right.region_type:
        return False

    left_bbox = left.candidate.bounding_box
    right_bbox = right.candidate.bounding_box
    if _bbox_intersection(left_bbox, right_bbox) is not None:
        return True
    if _bbox_contains(left_bbox, right_bbox, tolerance_pt=_CANDIDATE_ASSIGN_TOLERANCE_PT):
        return True
    if _bbox_contains(right_bbox, left_bbox, tolerance_pt=_CANDIDATE_ASSIGN_TOLERANCE_PT):
        return True
    if _bbox_touches_or_near(left_bbox, right_bbox, tolerance_pt=_CANDIDATE_ASSIGN_TOLERANCE_PT):
        return True

    horizontal_alignment = _span_overlap_ratio(
        left_bbox.left_pt,
        left_bbox.right_pt,
        right_bbox.left_pt,
        right_bbox.right_pt,
    )
    vertical_alignment = _span_overlap_ratio(
        left_bbox.bottom_pt,
        left_bbox.top_pt,
        right_bbox.bottom_pt,
        right_bbox.top_pt,
    )
    horizontal_gap = max(left_bbox.left_pt - right_bbox.right_pt, right_bbox.left_pt - left_bbox.right_pt, 0.0)
    vertical_gap = max(left_bbox.bottom_pt - right_bbox.top_pt, right_bbox.bottom_pt - left_bbox.top_pt, 0.0)
    return (
        horizontal_alignment >= _LAYOUT_TABLE_ALIGNMENT_OVERLAP_RATIO
        and vertical_gap <= _LAYOUT_TABLE_GROUP_GAP_TOLERANCE_PT
    ) or (
        vertical_alignment >= _LAYOUT_TABLE_ALIGNMENT_OVERLAP_RATIO
        and horizontal_gap <= _LAYOUT_TABLE_GROUP_GAP_TOLERANCE_PT
    )


def _build_candidate_groups(
    assigned_candidates: list[_AssignedCandidate],
    *,
    page: PageInfo,
) -> list[_AssignedCandidateGroup]:
    if not assigned_candidates:
        return []

    ordered = sorted(assigned_candidates, key=lambda item: item.order_key)
    index_by_id = {id(candidate): index for index, candidate in enumerate(ordered)}
    groups: list[_AssignedCandidateGroup] = []
    seen: set[int] = set()

    for root in ordered:
        root_id = id(root)
        if root_id in seen:
            continue
        stack = [root]
        members: list[_AssignedCandidate] = []
        while stack:
            current = stack.pop()
            current_id = id(current)
            if current_id in seen:
                continue
            seen.add(current_id)
            members.append(current)
            for other in ordered:
                other_id = id(other)
                if other_id in seen:
                    continue
                if _candidate_boxes_belong_to_same_group(current, other):
                    stack.append(other)

        member_bboxes = [member.candidate.bounding_box for member in members]
        group_bbox = PdfBoundingBox(
            left_pt=min(bbox.left_pt for bbox in member_bboxes),
            bottom_pt=min(bbox.bottom_pt for bbox in member_bboxes),
            right_pt=max(bbox.right_pt for bbox in member_bboxes),
            top_pt=max(bbox.top_pt for bbox in member_bboxes),
        )
        top_offset, bottom_offset = _bbox_offsets_from_page(group_bbox, page=page)
        groups.append(
            _AssignedCandidateGroup(
                candidates=sorted(
                    members,
                    key=lambda item: _bbox_order_key(
                        item.candidate.bounding_box,
                        fallback_index=index_by_id[id(item)],
                    ),
                ),
                region_type=members[0].region_type,
                top_offset_pt=top_offset,
                bottom_offset_pt=bottom_offset,
                order_key=_bbox_order_key(group_bbox, fallback_index=len(groups)),
                bounding_box=group_bbox,
            )
        )

    groups.sort(key=lambda group: group.order_key)
    return groups


def _content_node_bbox(node: Any) -> PdfBoundingBox | None:
    return getattr(node, "bbox", None)


def _collect_page_render_nodes(
    page_paragraphs: list[ParagraphIR],
) -> tuple[list[_PreviewRenderNode], list[_PreviewRenderNode], list[_PreviewRenderNode], list[_PreviewRenderNode]]:
    paragraph_nodes: list[_PreviewRenderNode] = []
    table_nodes: list[_PreviewRenderNode] = []
    image_nodes: list[_PreviewRenderNode] = []
    run_nodes: list[_PreviewRenderNode] = []

    for fallback_index, paragraph in enumerate(page_paragraphs):
        paragraph_bbox = _paragraph_bbox(paragraph)
        paragraph_key = _bbox_order_key(paragraph_bbox, fallback_index=fallback_index)
        if paragraph_bbox is not None:
            paragraph_nodes.append(
                _PreviewRenderNode(
                    kind="paragraph",
                    unit_id=paragraph.unit_id,
                    bbox=paragraph_bbox,
                    order_key=paragraph_key,
                    parent_paragraph_id=paragraph.unit_id,
                    parent_para_style=paragraph.para_style,
                    paragraph=paragraph,
                )
            )

        for content_index, node in enumerate(paragraph.content, start=1):
            node_bbox = _content_node_bbox(node)
            if node_bbox is None:
                continue
            node_key = _bbox_order_key(node_bbox, fallback_index=fallback_index, subindex=content_index)
            common_kwargs = {
                "unit_id": getattr(node, "unit_id", f"{paragraph.unit_id}.c{content_index}"),
                "bbox": node_bbox,
                "order_key": node_key,
                "parent_paragraph_id": paragraph.unit_id,
                "parent_para_style": paragraph.para_style,
            }
            if isinstance(node, TableIR):
                table_nodes.append(_PreviewRenderNode(kind="table", table=node, **common_kwargs))
            elif isinstance(node, ImageIR):
                image_nodes.append(_PreviewRenderNode(kind="image", image=node, **common_kwargs))
            elif isinstance(node, RunIR):
                run_nodes.append(_PreviewRenderNode(kind="run", run=node, **common_kwargs))

    return paragraph_nodes, table_nodes, image_nodes, run_nodes


def _page_box_candidates(
    preview_context: PdfPreviewContext,
    *,
    page_number: int,
) -> list[PdfPreviewVisualBlockCandidate]:
    return [
        candidate
        for candidate in preview_context.visual_block_candidates
        if candidate.page_number == page_number and candidate.candidate_type in {"axis_box", "open_frame"}
    ]


def _candidate_matches_table_bbox(
    candidate_bbox: PdfBoundingBox,
    table_bbox: PdfBoundingBox,
) -> bool:
    intersection = _bbox_intersection(candidate_bbox, table_bbox)
    if intersection is None:
        return False

    candidate_area = _bbox_area(candidate_bbox)
    table_area = _bbox_area(table_bbox)
    intersection_area = _bbox_area(intersection)
    if candidate_area <= 0.0 or table_area <= 0.0 or intersection_area <= 0.0:
        return False

    candidate_overlap = intersection_area / candidate_area
    table_overlap = intersection_area / table_area
    return (
        candidate_overlap >= 0.82
        and table_overlap >= 0.82
        and _shared_bbox_distance(candidate_bbox, table_bbox) <= 12.0
    )


def _assign_page_nodes_to_candidates(
    page: PageInfo,
    page_paragraphs: list[ParagraphIR],
    *,
    page_regions: list[PdfLayoutRegion],
    preview_context: PdfPreviewContext,
) -> tuple[list[_AssignedCandidate], set[str], set[str]]:
    paragraph_nodes, table_nodes, image_nodes, run_nodes = _collect_page_render_nodes(page_paragraphs)
    table_bboxes = [table_node.bbox for table_node in table_nodes]
    candidates = [
        candidate
        for candidate in _page_box_candidates(preview_context, page_number=page.page_number)
        if not any(_candidate_matches_table_bbox(candidate.bounding_box, table_bbox) for table_bbox in table_bboxes)
    ]
    if not candidates:
        return [], set(), set()

    assigned_candidates: list[_AssignedCandidate] = []
    for candidate_index, candidate in enumerate(
        sorted(candidates, key=lambda item: (item.bounding_box.top_pt, item.bounding_box.left_pt))
    ):
        bbox = candidate.bounding_box
        top_offset, bottom_offset = _bbox_offsets_from_page(bbox, page=page)
        assigned_candidates.append(
            _AssignedCandidate(
                candidate=candidate,
                region_type=_bbox_region_type(
                    bbox,
                    page=page,
                    page_regions=page_regions,
                    explicit_region_type=None,
                ),
                top_offset_pt=top_offset,
                bottom_offset_pt=bottom_offset,
                order_key=_bbox_order_key(bbox, fallback_index=candidate_index),
                paragraph_nodes=[],
                table_nodes=[],
                image_nodes=[],
                run_nodes=[],
            )
        )

    candidate_lookup = {id(candidate.candidate): candidate for candidate in assigned_candidates}
    assigned_paragraph_ids: set[str] = set()
    assigned_child_ids: set[str] = set()

    for node in sorted(paragraph_nodes, key=lambda item: item.order_key):
        candidate = _best_candidate_for_node(node.bbox, candidates)
        if candidate is None or node.paragraph is None:
            continue
        assigned = candidate_lookup[id(candidate)]
        assigned.paragraph_nodes.append(node)
        assigned.order_key = min(assigned.order_key, node.order_key)
        assigned_paragraph_ids.add(node.unit_id)

    for nodes, target_attr in (
        (table_nodes, "table_nodes"),
        (image_nodes, "image_nodes"),
        (run_nodes, "run_nodes"),
    ):
        for node in sorted(nodes, key=lambda item: item.order_key):
            if node.parent_paragraph_id in assigned_paragraph_ids:
                continue
            candidate = _best_candidate_for_node(node.bbox, candidates)
            if candidate is None:
                continue
            assigned = candidate_lookup[id(candidate)]
            getattr(assigned, target_attr).append(node)
            assigned.order_key = min(assigned.order_key, node.order_key)
            assigned_child_ids.add(node.unit_id)

    assigned_candidates = [
        assigned_candidate
        for assigned_candidate in assigned_candidates
        if _assigned_candidate_has_content(assigned_candidate)
    ]
    return assigned_candidates, assigned_paragraph_ids, assigned_child_ids


def _assigned_candidate_has_content(assigned_candidate: _AssignedCandidate) -> bool:
    return bool(
        assigned_candidate.paragraph_nodes
        or assigned_candidate.table_nodes
        or assigned_candidate.image_nodes
        or assigned_candidate.run_nodes
    )


def _filter_page_flow_paragraphs(
    page_paragraphs: list[ParagraphIR],
    *,
    assigned_paragraph_ids: set[str],
    assigned_child_ids: set[str],
) -> list[ParagraphIR]:
    filtered: list[ParagraphIR] = []
    for paragraph in page_paragraphs:
        if paragraph.unit_id in assigned_paragraph_ids:
            continue
        if not assigned_child_ids:
            filtered.append(paragraph)
            continue
        remaining_content = [
            node
            for node in paragraph.content
            if getattr(node, "unit_id", "") not in assigned_child_ids
        ]
        if len(remaining_content) == len(paragraph.content):
            filtered.append(paragraph)
            continue
        clone = paragraph.model_copy(deep=True)
        clone.content = remaining_content
        clone.recompute_text()
        if clone.content or clone.text.strip():
            filtered.append(clone)
    return filtered


def _cluster_boundary_values(
    values: list[float],
    *,
    descending: bool,
) -> list[float]:
    if not values:
        return []

    clustered: list[list[float]] = []
    for value in sorted(values, reverse=descending):
        if not clustered or abs(value - clustered[-1][-1]) > _LAYOUT_TABLE_BOUNDARY_TOLERANCE_PT:
            clustered.append([value])
            continue
        clustered[-1].append(value)

    representatives = [sum(cluster) / len(cluster) for cluster in clustered]
    return sorted(representatives, reverse=descending)


def _nearest_boundary_index(boundaries: list[float], value: float) -> int:
    return min(range(len(boundaries)), key=lambda index: abs(boundaries[index] - value))


def _auxiliary_nodes_to_paragraphs(
    nodes: list[_PreviewRenderNode],
) -> list[tuple[tuple[float, float, int, int], ParagraphIR]]:
    if not nodes:
        return []

    grouped: dict[str, list[_PreviewRenderNode]] = {}
    group_order: dict[str, tuple[float, float, int, int]] = {}
    group_para_style: dict[str, Any] = {}
    group_bbox: dict[str, PdfBoundingBox] = {}
    for node in nodes:
        group_key = node.parent_paragraph_id or node.unit_id
        grouped.setdefault(group_key, []).append(node)
        group_order[group_key] = min(group_order.get(group_key, node.order_key), node.order_key)
        if group_key not in group_para_style:
            group_para_style[group_key] = node.parent_para_style
        group_bbox[group_key] = (
            node.bbox
            if group_key not in group_bbox
            else PdfBoundingBox(
                left_pt=min(group_bbox[group_key].left_pt, node.bbox.left_pt),
                bottom_pt=min(group_bbox[group_key].bottom_pt, node.bbox.bottom_pt),
                right_pt=max(group_bbox[group_key].right_pt, node.bbox.right_pt),
                top_pt=max(group_bbox[group_key].top_pt, node.bbox.top_pt),
            )
        )

    paragraphs: list[tuple[tuple[float, float, int, int], ParagraphIR]] = []
    for group_key, group_nodes in grouped.items():
        content_nodes: list[Any] = []
        for node in sorted(group_nodes, key=lambda item: item.order_key):
            if node.table is not None:
                content_nodes.append(node.table.model_copy(deep=True))
            elif node.image is not None:
                content_nodes.append(node.image.model_copy(deep=True))
            elif node.run is not None:
                content_nodes.append(node.run.model_copy(deep=True))
        if not content_nodes:
            continue
        paragraph = ParagraphIR(
            unit_id=f"{group_key}.layout-table",
            text="",
            bbox=group_bbox.get(group_key),
            para_style=group_para_style.get(group_key),
            content=content_nodes,
        )
        paragraph.recompute_text()
        paragraphs.append((group_order[group_key], paragraph))
    return paragraphs


def _assigned_candidate_cell_paragraphs(assigned_candidate: _AssignedCandidate) -> list[ParagraphIR]:
    content_blocks: list[tuple[tuple[float, float, int, int], ParagraphIR]] = []
    for paragraph_node in sorted(assigned_candidate.paragraph_nodes, key=lambda item: item.order_key):
        if paragraph_node.paragraph is None:
            continue
        content_blocks.append((paragraph_node.order_key, paragraph_node.paragraph.model_copy(deep=True)))

    auxiliary_nodes = sorted(
        assigned_candidate.table_nodes + assigned_candidate.image_nodes + assigned_candidate.run_nodes,
        key=lambda node: node.order_key,
    )
    content_blocks.extend(_auxiliary_nodes_to_paragraphs(auxiliary_nodes))
    content_blocks.sort(key=lambda item: item[0])
    return [paragraph for _, paragraph in content_blocks]


def _assigned_candidate_real_table_unit_ids(
    assigned_candidate: _AssignedCandidate,
) -> set[str]:
    table_unit_ids: set[str] = set()

    for paragraph_node in assigned_candidate.paragraph_nodes:
        if paragraph_node.paragraph is None:
            continue
        for content_node in paragraph_node.paragraph.content:
            if isinstance(content_node, TableIR):
                table_unit_ids.add(content_node.unit_id)

    for table_node in assigned_candidate.table_nodes:
        if table_node.table is not None:
            table_unit_ids.add(table_node.table.unit_id)

    return table_unit_ids


def _group_has_many_real_tables(
    assigned_candidate_group: _AssignedCandidateGroup,
) -> bool:
    table_unit_ids: set[str] = set()
    for assigned_candidate in assigned_candidate_group.candidates:
        table_unit_ids.update(_assigned_candidate_real_table_unit_ids(assigned_candidate))
        if len(table_unit_ids) >= 3:
            return True
    return False


def _layout_table_cell_style(
    bbox: PdfBoundingBox,
    *,
    colspan: int,
    rowspan: int,
) -> CellStyleInfo:
    return CellStyleInfo(
        width_pt=max(bbox.right_pt - bbox.left_pt, 0.0),
        border_top="1px solid #4a4f57",
        border_bottom="1px solid #4a4f57",
        border_left="1px solid #4a4f57",
        border_right="1px solid #4a4f57",
        colspan=max(colspan, 1),
        rowspan=max(rowspan, 1),
    )


def _build_layout_table_paragraph_for_group(
    assigned_candidate_group: _AssignedCandidateGroup,
    *,
    page_number: int,
    group_index: int,
) -> ParagraphIR | None:
    assigned_candidates = assigned_candidate_group.candidates
    if not assigned_candidates:
        return None

    group_bbox = assigned_candidate_group.bounding_box
    x_boundaries = _cluster_boundary_values(
        [group_bbox.left_pt, group_bbox.right_pt]
        + [candidate.candidate.bounding_box.left_pt for candidate in assigned_candidates]
        + [candidate.candidate.bounding_box.right_pt for candidate in assigned_candidates],
        descending=False,
    )
    y_boundaries = _cluster_boundary_values(
        [group_bbox.top_pt, group_bbox.bottom_pt]
        + [candidate.candidate.bounding_box.top_pt for candidate in assigned_candidates]
        + [candidate.candidate.bounding_box.bottom_pt for candidate in assigned_candidates],
        descending=True,
    )
    if len(x_boundaries) < 2 or len(y_boundaries) < 2:
        return None

    cells: list[TableCellIR] = []
    for candidate_index, assigned_candidate in enumerate(assigned_candidates, start=1):
        bbox = assigned_candidate.candidate.bounding_box
        left_index = _nearest_boundary_index(x_boundaries, bbox.left_pt)
        right_index = _nearest_boundary_index(x_boundaries, bbox.right_pt)
        top_index = _nearest_boundary_index(y_boundaries, bbox.top_pt)
        bottom_index = _nearest_boundary_index(y_boundaries, bbox.bottom_pt)

        colspan = max(right_index - left_index, 1)
        rowspan = max(bottom_index - top_index, 1)
        cell = TableCellIR(
            unit_id=f"pdf-preview.p{page_number}.layout-table.{group_index}.cell.{candidate_index}",
            row_index=top_index + 1,
            col_index=left_index + 1,
            bbox=bbox,
            cell_style=_layout_table_cell_style(
                bbox,
                colspan=colspan,
                rowspan=rowspan,
            ),
            paragraphs=_assigned_candidate_cell_paragraphs(assigned_candidate),
        )
        cell.recompute_text()
        cells.append(cell)

    table = TableIR(
        unit_id=f"pdf-preview.p{page_number}.layout-table.{group_index}",
        row_count=max(len(y_boundaries) - 1, 1),
        col_count=max(len(x_boundaries) - 1, 1),
        bbox=group_bbox,
        table_style=TableStyleInfo(
            row_count=max(len(y_boundaries) - 1, 1),
            col_count=max(len(x_boundaries) - 1, 1),
            width_pt=max(group_bbox.right_pt - group_bbox.left_pt, 0.0),
            preview_grid=len(x_boundaries) > 2 or len(y_boundaries) > 2,
        ),
        cells=cells,
    )
    paragraph = ParagraphIR(
        unit_id=f"{table.unit_id}.paragraph",
        text="",
        page_number=page_number,
        bbox=group_bbox,
        content=[table],
    )
    paragraph.recompute_text()
    return paragraph


def _promote_assigned_candidates_to_layout_tables(
    assigned_candidates: list[_AssignedCandidate],
    *,
    page: PageInfo,
) -> tuple[list[ParagraphIR], set[int]]:
    if not assigned_candidates:
        return [], set()

    paragraphs: list[ParagraphIR] = []
    promoted_candidate_ids: set[int] = set()
    for group_index, assigned_candidate_group in enumerate(_build_candidate_groups(assigned_candidates, page=page), start=1):
        if _group_has_many_real_tables(assigned_candidate_group):
            continue
        paragraph = _build_layout_table_paragraph_for_group(
            assigned_candidate_group,
            page_number=page.page_number,
            group_index=group_index,
        )
        if paragraph is None:
            continue
        paragraphs.append(paragraph)
        promoted_candidate_ids.update(id(assigned_candidate.candidate) for assigned_candidate in assigned_candidate_group.candidates)

    return paragraphs, promoted_candidate_ids


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
        if _shared_bbox_distance(candidate.bounding_box, bounding_box) <= 4.0:
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
