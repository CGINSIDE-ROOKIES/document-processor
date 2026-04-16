"""Layout normalization helpers for PDF preview."""

from __future__ import annotations

from typing import Any

from ...models import ImageIR, PageInfo, ParagraphIR, RunIR, TableIR
from ..meta import PdfBoundingBox
from .models import (
    PdfLayoutRegion,
    PdfPreviewContext,
    PdfPreviewVisualBlockCandidate,
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
    _LOGICAL_PAGE_NUMBER_FOOTER_TOP_RATIO,
    _LOGICAL_PAGE_NUMBER_MAX_WIDTH_RATIO,
    _LOGICAL_PAGE_NUMBER_TEXT_RE,
    _LogicalPage,
)
from .shared import _bbox_area, _bbox_center, _bbox_from_bounds, _bbox_intersection, _union_box_bounds


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
        return [
            _LogicalPage(
                page_number=starting_page_number,
                physical_page_number=page.page_number,
                logical_page_type="left",
                bounding_box=PdfBoundingBox(
                    left_pt=0.0,
                    bottom_pt=0.0,
                    right_pt=split_x,
                    top_pt=page.height_pt,
                ),
                source_region_ids=[left_region.region_id],
            ),
            _LogicalPage(
                page_number=starting_page_number + 1,
                physical_page_number=page.page_number,
                logical_page_type="right",
                bounding_box=PdfBoundingBox(
                    left_pt=split_x,
                    bottom_pt=0.0,
                    right_pt=page.width_pt,
                    top_pt=page.height_pt,
                ),
                source_region_ids=[right_region.region_id],
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


def _rebase_meta_bbox(meta: Any, *, origin_bbox: PdfBoundingBox) -> Any:
    if meta is None or not hasattr(meta, "bounding_box"):
        return meta
    rebased_meta = meta.model_copy(deep=True) if hasattr(meta, "model_copy") else meta
    rebased_meta.bounding_box = _rebase_bbox(getattr(meta, "bounding_box", None), origin_bbox=origin_bbox)
    return rebased_meta


def _rebase_table_for_logical_page(table: TableIR, *, origin_bbox: PdfBoundingBox) -> TableIR:
    clone = table.model_copy(deep=True)
    clone.bbox = _rebase_bbox(table.bbox, origin_bbox=origin_bbox)
    clone.meta = _rebase_meta_bbox(table.meta, origin_bbox=origin_bbox)
    for cell in clone.cells:
        cell.bbox = _rebase_bbox(cell.bbox, origin_bbox=origin_bbox)
        cell.meta = _rebase_meta_bbox(cell.meta, origin_bbox=origin_bbox)
        for paragraph in cell.paragraphs:
            rebased = _rebase_paragraph_for_logical_page(paragraph, origin_bbox=origin_bbox)
            paragraph.unit_id = rebased.unit_id
            paragraph.text = rebased.text
            paragraph.page_number = rebased.page_number
            paragraph.bbox = rebased.bbox
            paragraph.meta = rebased.meta
            paragraph.para_style = rebased.para_style
            paragraph.content = rebased.content
    return clone


def _rebase_paragraph_content_node(node: Any, *, origin_bbox: PdfBoundingBox) -> Any:
    if isinstance(node, RunIR):
        clone = node.model_copy(deep=True)
        clone.bbox = _rebase_bbox(node.bbox, origin_bbox=origin_bbox)
        clone.meta = _rebase_meta_bbox(node.meta, origin_bbox=origin_bbox)
        return clone
    if isinstance(node, ImageIR):
        clone = node.model_copy(deep=True)
        clone.bbox = _rebase_bbox(node.bbox, origin_bbox=origin_bbox)
        return clone
    if isinstance(node, TableIR):
        return _rebase_table_for_logical_page(node, origin_bbox=origin_bbox)
    return node


def _rebase_paragraph_for_logical_page(paragraph: ParagraphIR, *, origin_bbox: PdfBoundingBox) -> ParagraphIR:
    clone = paragraph.model_copy(deep=True)
    clone.bbox = _rebase_bbox(_paragraph_bbox(paragraph), origin_bbox=origin_bbox)
    clone.meta = _rebase_meta_bbox(paragraph.meta, origin_bbox=origin_bbox)
    clone.content = [
        _rebase_paragraph_content_node(node, origin_bbox=origin_bbox)
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
    clone.bounding_box = _rebase_bbox(candidate.bounding_box, origin_bbox=logical_page.bounding_box) or candidate.bounding_box
    clone.child_cells = [
        rebased
        for cell_bbox in candidate.child_cells
        if (rebased := _rebase_bbox(cell_bbox, origin_bbox=logical_page.bounding_box)) is not None
    ]
    return clone


def _logical_page_page_info(logical_page: _LogicalPage, *, source_page: PageInfo) -> PageInfo:
    bbox = logical_page.bounding_box
    return PageInfo(
        page_number=logical_page.page_number,
        width_pt=max(bbox.right_pt - bbox.left_pt, 0.0),
        height_pt=max(bbox.top_pt - bbox.bottom_pt, 0.0),
        margin_left_pt=source_page.margin_left_pt,
        margin_right_pt=source_page.margin_right_pt,
        margin_top_pt=source_page.margin_top_pt,
        margin_bottom_pt=source_page.margin_bottom_pt,
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
        rebased = _rebase_paragraph_for_logical_page(paragraph, origin_bbox=logical_page.bounding_box)
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


__all__ = [
    "_best_logical_page_for_bbox",
    "_build_logical_pages_for_page",
    "_collapse_image_strip_paragraphs",
    "_column_band_split_x",
    "_detect_intra_page_column_regions",
    "_flow_regions_for_logical_page",
    "_footer_page_number_candidates",
    "_has_footer_page_number_pair",
    "_image_strip_paragraphs_can_merge",
    "_is_image_only_paragraph",
    "_logical_page_page_info",
    "_logical_page_paragraphs",
    "_logical_page_preview_context",
    "_merged_image_strip_paragraph",
    "_rebase_bbox",
    "_rebase_candidate_for_logical_page",
    "_rebase_meta_bbox",
    "_rebase_paragraph_content_node",
    "_rebase_paragraph_for_logical_page",
    "_rebase_table_for_logical_page",
    "_region_split_x",
    "_score_logical_page_for_bbox",
    "_spread_split_x",
]
