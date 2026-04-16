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

from ...html_exporter import (
    _page_content_style,
    _page_style,
    _render_html_document_shell,
    _render_paragraph,
    render_html_document,
)
from ...models import DocIR, ImageIR, PageInfo, ParagraphIR, RunIR, TableCellIR, TableIR
from ...style_types import CellStyleInfo, TableStyleInfo
from ..enhancement import enrich_pdf_table_backgrounds, enrich_pdf_table_borders
from ..meta import PdfBoundingBox, coerce_bbox, coerce_float, coerce_int
from .candidates import (
    _build_axis_box_candidates_from_component,
    _build_non_box_line_candidates,
    _build_visual_block_candidates,
    _component_has_box_outline,
    _connected_line_components,
    _dedupe_line_primitives_for_graph,
    _dedupe_visual_block_candidates,
    _find_axis_box_seed_bboxes_from_component,
    _line_primitives_are_graph_duplicates,
    _semantic_line_matches_structure_boundary,
    _suppress_boundary_semantic_lines,
)
from .context import (
    _augment_layout_regions_with_pdfium,
    _collect_table_preview_context,
    _detect_pdfium_split_regions,
    _float_list,
    _layout_regions_from_raw,
    _line_art_boxes,
    _table_preview_context_from_node,
    build_pdf_preview_context,
)
from .layout import (
    _best_logical_page_for_bbox,
    _bbox_region_type,
    _build_logical_pages_for_page,
    _collapse_image_strip_paragraphs,
    _column_band_split_x,
    _detect_intra_page_column_regions,
    _flow_regions_for_logical_page,
    _footer_page_number_candidates,
    _has_footer_page_number_pair,
    _image_strip_paragraphs_can_merge,
    _is_image_only_paragraph,
    _logical_page_page_info,
    _logical_page_paragraphs,
    _logical_page_preview_context,
    _merged_image_strip_paragraph,
    _paragraph_bbox,
    _paragraph_union_bbox,
    _paragraph_region_type,
    _rebase_bbox,
    _rebase_candidate_for_logical_page,
    _rebase_meta_bbox,
    _rebase_paragraph_content_node,
    _rebase_paragraph_for_logical_page,
    _rebase_table_for_logical_page,
    _region_split_x,
    _score_logical_page_for_bbox,
    _spread_split_x,
)
from .compose import (
    _assigned_candidate_cell_paragraphs,
    _assign_page_nodes_to_candidates,
    _auxiliary_nodes_to_paragraphs,
    _bbox_assignment_score,
    _bbox_distance,
    _bbox_offsets_from_page,
    _build_candidate_groups,
    _build_column_band_paragraph,
    _build_layout_table_paragraph_for_group,
    _build_preview_entries,
    _best_candidate_for_node,
    _candidate_boxes_belong_to_same_group,
    _candidate_matches_table_bbox,
    _cluster_boundary_values,
    _column_band_cell_style,
    _compose_logical_page,
    _content_node_bbox,
    _filter_page_flow_paragraphs,
    _layout_table_cell_style,
    _materialize_flow_paragraphs,
    _nearest_boundary_index,
    _normalize_pdf_doc_for_flow,
    _page_box_candidates,
    _page_content_margins,
    _page_long_rule_candidates,
    _paragraph_offsets_from_page,
    _paragraph_reading_order,
    _preview_entry_sort_key,
    _preview_region_rank,
    _primary_region_bbox,
    _promote_assigned_candidates_to_layout_tables,
    _span_overlap_ratio,
)
from .models import (
    PdfLayoutRegion,
    PdfPreviewContext,
    PdfPreviewTableContext,
    PdfPreviewVisualBlockCandidate,
    PdfPreviewVisualPrimitive,
    _AssignedCandidate,
    _AssignedCandidateGroup,
    _CANDIDATE_ASSIGN_TOLERANCE_PT,
    _LAYOUT_TABLE_ALIGNMENT_OVERLAP_RATIO,
    _LAYOUT_TABLE_BOUNDARY_TOLERANCE_PT,
    _LAYOUT_TABLE_GROUP_GAP_TOLERANCE_PT,
    _LogicalPage,
    _LogicalPageComposition,
    _PreviewCompositionEntry,
    _PreviewRenderNode,
    _VISUAL_BOUNDARY_SUPPRESSION_OVERLAP_RATIO,
    _VISUAL_BOUNDARY_SUPPRESSION_TOLERANCE_PT,
    _VISUAL_BOX_SEED_MIN_SIZE_PT,
    _VISUAL_DIVIDER_SPAN_RATIO,
    _VISUAL_FRAME_MIN_SIZE_PT,
    _VISUAL_LINE_JOIN_TOLERANCE_PT,
    _VISUAL_MIN_LINE_SEGMENT_PT,
    _VISUAL_OPEN_FRAME_PRIMITIVE_LIMIT,
    _VISUAL_SEGMENTED_AXIS_TOLERANCE_PT,
    _VISUAL_SEGMENTED_GAP_TOLERANCE_PT,
    _VISUAL_SEGMENTED_MAX_FRAGMENT_PT,
    _VISUAL_SEGMENTED_MIN_PARTS,
    _VISUAL_SEGMENTED_MIN_SPAN_PT,
    _VISUAL_TOUCH_TOLERANCE_PT,
)
from .primitives import (
    _build_axis_box_edge_primitives,
    _build_segmented_rule_primitive,
    _build_segmented_rule_primitives,
    _candidate_roles_for_visual_primitive,
    _extract_pdfium_visual_primitives,
    _pdfium_color,
    _pdfium_has_fill,
    _pdfium_has_stroke,
    _pdfium_is_axis_aligned_box,
    _pdfium_object_type_name,
    _pdfium_path_points,
    _pdfium_stroke_width,
    _segmented_rule_can_extend,
)


def _has_visible_stroke(primitive: PdfPreviewVisualPrimitive) -> bool:
    if not primitive.has_stroke:
        return False
    if primitive.stroke_color is None:
        return False
    rgba = primitive.stroke_color.removeprefix("#")
    if len(rgba) != 8:
        return True
    try:
        red = int(rgba[0:2], 16)
        green = int(rgba[2:4], 16)
        blue = int(rgba[4:6], 16)
        alpha = int(rgba[6:8], 16)
    except ValueError:
        return True
    if alpha < 16:
        return False
    if red >= 245 and green >= 245 and blue >= 245:
        return False
    return True


def _primitive_size(primitive: PdfPreviewVisualPrimitive) -> tuple[float, float]:
    bbox = primitive.bounding_box
    return (
        max(bbox.right_pt - bbox.left_pt, 0.0),
        max(bbox.top_pt - bbox.bottom_pt, 0.0),
    )


def _primitive_bbox_line_orientation(
    primitive: PdfPreviewVisualPrimitive,
    *,
    page_width: float,
    page_height: float,
    min_length_pt: float,
) -> str | None:
    width, height = _primitive_size(primitive)
    if width <= 0.0 or height <= 0.0:
        return None
    narrow_width = max(page_width * 0.03, 10.0)
    narrow_height = max(page_height * 0.03, 10.0)
    if width <= narrow_width and height > width and height > min_length_pt:
        return "vertical"
    if height <= narrow_height and width > height and width > min_length_pt:
        return "horizontal"
    return None


def _primitive_line_span(primitive: PdfPreviewVisualPrimitive, orientation: str) -> float:
    start, end = _primitive_line_span_range(primitive, orientation)
    return max(end - start, 0.0)


def _primitive_line_span_range(
    primitive: PdfPreviewVisualPrimitive,
    orientation: str,
) -> tuple[float, float]:
    bbox = primitive.bounding_box
    if orientation == "horizontal":
        return bbox.left_pt, bbox.right_pt
    return bbox.bottom_pt, bbox.top_pt


def _primitive_line_axis_center(primitive: PdfPreviewVisualPrimitive, orientation: str) -> float:
    bbox = primitive.bounding_box
    if orientation == "horizontal":
        return (bbox.top_pt + bbox.bottom_pt) / 2.0
    return (bbox.left_pt + bbox.right_pt) / 2.0


def _bbox_touches_or_near(left: PdfBoundingBox, right: PdfBoundingBox, *, tolerance_pt: float) -> bool:
    horizontal_gap = max(left.left_pt - right.right_pt, right.left_pt - left.right_pt, 0.0)
    vertical_gap = max(left.bottom_pt - right.top_pt, right.bottom_pt - left.top_pt, 0.0)
    return horizontal_gap <= tolerance_pt and vertical_gap <= tolerance_pt


def _bbox_contains(container: PdfBoundingBox, item: PdfBoundingBox, *, tolerance_pt: float) -> bool:
    return (
        container.left_pt - tolerance_pt <= item.left_pt
        and container.bottom_pt - tolerance_pt <= item.bottom_pt
        and container.right_pt + tolerance_pt >= item.right_pt
        and container.top_pt + tolerance_pt >= item.top_pt
    )


def _line_primitives_belong_to_same_frame(
    left: PdfPreviewVisualPrimitive,
    right: PdfPreviewVisualPrimitive,
) -> bool:
    left_orientation = _primitive_line_orientation(left)
    right_orientation = _primitive_line_orientation(right)
    if left_orientation is None or right_orientation is None:
        return False

    left_endpoints = _primitive_line_endpoints(left)
    right_endpoints = _primitive_line_endpoints(right)
    if not left_endpoints or not right_endpoints:
        return False

    if left_orientation != right_orientation:
        return any(
            _point_distance(left_point, right_point) <= _VISUAL_LINE_JOIN_TOLERANCE_PT
            for left_point in left_endpoints
            for right_point in right_endpoints
        )

    if left_orientation == "horizontal":
        same_axis = abs(left_endpoints[0][1] - right_endpoints[0][1]) <= _VISUAL_LINE_JOIN_TOLERANCE_PT
    else:
        same_axis = abs(left_endpoints[0][0] - right_endpoints[0][0]) <= _VISUAL_LINE_JOIN_TOLERANCE_PT
    if not same_axis:
        return False

    return any(
        _point_distance(left_point, right_point) <= _VISUAL_LINE_JOIN_TOLERANCE_PT
        for left_point in left_endpoints
        for right_point in right_endpoints
    )


def _is_open_frame_component(component: list[PdfPreviewVisualPrimitive]) -> bool:
    if len(component) < 3:
        return False

    orientations = {_primitive_line_orientation(primitive) for primitive in component}
    if "horizontal" not in orientations or "vertical" not in orientations:
        return False

    bbox = _union_visual_primitive_bboxes(component)
    if bbox is None:
        return False
    width = bbox.right_pt - bbox.left_pt
    height = bbox.top_pt - bbox.bottom_pt
    return width >= _VISUAL_FRAME_MIN_SIZE_PT and height >= _VISUAL_FRAME_MIN_SIZE_PT


def _horizontal_line_matches_box_boundary(
    primitive: PdfPreviewVisualPrimitive,
    *,
    left_x: float,
    right_x: float,
) -> bool:
    if _primitive_line_orientation(primitive) != "horizontal":
        return False
    if right_x - left_x < _VISUAL_BOX_SEED_MIN_SIZE_PT:
        return False
    line_left, line_right = _primitive_line_span_range(primitive, "horizontal")
    if line_left > left_x + _VISUAL_TOUCH_TOLERANCE_PT:
        return False
    if line_right < right_x - _VISUAL_TOUCH_TOLERANCE_PT:
        return False

    span = line_right - line_left
    if abs(line_left - left_x) <= _VISUAL_TOUCH_TOLERANCE_PT and abs(line_right - right_x) <= _VISUAL_TOUCH_TOLERANCE_PT:
        return True
    return span <= (right_x - left_x) * 1.35


def _vertical_line_matches_box_boundary(
    primitive: PdfPreviewVisualPrimitive,
    *,
    x: float,
    bottom_y: float,
    top_y: float,
) -> bool:
    if _primitive_line_orientation(primitive) != "vertical":
        return False
    x_center = _primitive_line_axis_center(primitive, "vertical")
    if abs(x_center - x) > _VISUAL_TOUCH_TOLERANCE_PT:
        return False
    line_bottom, line_top = _primitive_line_span_range(primitive, "vertical")
    return line_bottom <= bottom_y + _VISUAL_TOUCH_TOLERANCE_PT and line_top >= top_y - _VISUAL_TOUCH_TOLERANCE_PT


def _dedupe_seed_bboxes(seed_bboxes: list[PdfBoundingBox]) -> list[PdfBoundingBox]:
    if not seed_bboxes:
        return []

    kept: list[PdfBoundingBox] = []
    for candidate in sorted(
        seed_bboxes,
        key=lambda item: (
            item.top_pt,
            item.left_pt,
            (item.right_pt - item.left_pt) * (item.top_pt - item.bottom_pt),
        ),
    ):
        if any(
            _bbox_overlap_ratio(existing, candidate) >= 0.95
            or _bbox_contains(existing, candidate, tolerance_pt=_VISUAL_TOUCH_TOLERANCE_PT)
            for existing in kept
        ):
            continue
        kept.append(candidate)
    return kept


def _primitive_belongs_to_axis_box(
    primitive: PdfPreviewVisualPrimitive,
    axis_box_bbox: PdfBoundingBox,
) -> bool:
    if _bbox_contains(axis_box_bbox, primitive.bounding_box, tolerance_pt=_VISUAL_TOUCH_TOLERANCE_PT):
        return True

    orientation = _primitive_line_orientation(primitive)
    bbox = primitive.bounding_box
    if orientation == "vertical":
        x_center = (bbox.left_pt + bbox.right_pt) / 2.0
        y_overlap = not (
            bbox.top_pt < axis_box_bbox.bottom_pt - _VISUAL_TOUCH_TOLERANCE_PT
            or bbox.bottom_pt > axis_box_bbox.top_pt + _VISUAL_TOUCH_TOLERANCE_PT
        )
        return y_overlap and (
            abs(x_center - axis_box_bbox.left_pt) <= _VISUAL_TOUCH_TOLERANCE_PT
            or abs(x_center - axis_box_bbox.right_pt) <= _VISUAL_TOUCH_TOLERANCE_PT
        )
    if orientation == "horizontal":
        y_center = (bbox.top_pt + bbox.bottom_pt) / 2.0
        overlap_width = min(axis_box_bbox.right_pt, bbox.right_pt) - max(axis_box_bbox.left_pt, bbox.left_pt)
        box_width = max(axis_box_bbox.right_pt - axis_box_bbox.left_pt, 0.0)
        if box_width <= 0.0:
            return False
        return (
            overlap_width >= box_width * 0.70
            and axis_box_bbox.bottom_pt - _VISUAL_TOUCH_TOLERANCE_PT <= y_center <= axis_box_bbox.top_pt + _VISUAL_TOUCH_TOLERANCE_PT
        )
    return False


def _union_visual_primitive_bboxes(
    primitives: list[PdfPreviewVisualPrimitive],
) -> PdfBoundingBox | None:
    if not primitives:
        return None
    return PdfBoundingBox(
        left_pt=min(primitive.bounding_box.left_pt for primitive in primitives),
        bottom_pt=min(primitive.bounding_box.bottom_pt for primitive in primitives),
        right_pt=max(primitive.bounding_box.right_pt for primitive in primitives),
        top_pt=max(primitive.bounding_box.top_pt for primitive in primitives),
    )


def _primitive_is_long_rule(primitive: PdfPreviewVisualPrimitive) -> bool:
    roles = set(primitive.candidate_roles)
    return "long_vertical_rule" in roles or "long_horizontal_rule" in roles


def _line_like_bbox_orientation(bbox: PdfBoundingBox) -> str | None:
    width = max(bbox.right_pt - bbox.left_pt, 0.0)
    height = max(bbox.top_pt - bbox.bottom_pt, 0.0)
    if width <= 0.0 or height <= 0.0:
        return None
    if width >= height * 4.0 and height <= _VISUAL_BOUNDARY_SUPPRESSION_TOLERANCE_PT * 2.0:
        return "horizontal"
    if height >= width * 4.0 and width <= _VISUAL_BOUNDARY_SUPPRESSION_TOLERANCE_PT * 2.0:
        return "vertical"
    return None


def _bbox_overlap_ratio(left: PdfBoundingBox, right: PdfBoundingBox) -> float:
    intersection = _bbox_intersection(left, right)
    if intersection is None:
        return 0.0
    intersection_area = _bbox_area(intersection)
    if intersection_area <= 0.0:
        return 0.0
    left_area = _bbox_area(left)
    right_area = _bbox_area(right)
    if left_area <= 0.0 or right_area <= 0.0:
        return 0.0
    return max(intersection_area / left_area, intersection_area / right_area)


def _primitive_line_orientation(primitive: PdfPreviewVisualPrimitive) -> str | None:
    roles = set(primitive.candidate_roles)
    if "horizontal_line_segment" in roles:
        return "horizontal"
    if "vertical_line_segment" in roles:
        return "vertical"
    return None


def _primitive_line_endpoints(
    primitive: PdfPreviewVisualPrimitive,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    bbox = primitive.bounding_box
    orientation = _primitive_line_orientation(primitive)
    if orientation == "horizontal":
        y = (bbox.top_pt + bbox.bottom_pt) / 2.0
        return (bbox.left_pt, y), (bbox.right_pt, y)
    if orientation == "vertical":
        x = (bbox.left_pt + bbox.right_pt) / 2.0
        return (x, bbox.bottom_pt), (x, bbox.top_pt)
    return None


def _point_distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return ((left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2) ** 0.5


def _point_bucket_keys(point: tuple[float, float], *, tolerance_pt: float) -> list[tuple[int, int]]:
    bucket_size = max(tolerance_pt, 1.0)
    base_x = int(point[0] // bucket_size)
    base_y = int(point[1] // bucket_size)
    return [
        (base_x + dx, base_y + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
    ]


def _pdfium_text_boxes(page) -> list[tuple[float, float, float, float]]:  # noqa: ANN001
    textpage = page.get_textpage()
    rect_count = textpage.count_rects()
    boxes: list[tuple[float, float, float, float]] = []
    for rect_index in range(rect_count):
        left, bottom, right, top = textpage.get_rect(rect_index)
        if right <= left or top <= bottom:
            continue
        boxes.append((left, bottom, right, top))
    return boxes


def _has_central_vertical_gutter(
    text_boxes: list[tuple[float, float, float, float]],
    *,
    page_width: float,
) -> bool:
    if not text_boxes:
        return False

    content_bottom = min(box[1] for box in text_boxes)
    content_top = max(box[3] for box in text_boxes)
    content_height = max(content_top - content_bottom, 1.0)

    strip_half_width = max(page_width * 0.01, 4.0)
    center_x = page_width / 2.0
    probe_offsets = (-0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03)

    for offset in probe_offsets:
        x_center = center_x + page_width * offset
        intervals = [
            (box[1], box[3])
            for box in text_boxes
            if box[2] >= x_center - strip_half_width and box[0] <= x_center + strip_half_width
        ]
        if not intervals:
            return True

        merged = _merge_intervals(intervals)
        longest_gap = _longest_interval_gap(merged, start=content_bottom, end=content_top)
        if longest_gap >= content_height * 0.60:
            return True

    return False


def _has_central_vertical_rule(page, *, page_width: float, page_height: float) -> bool:  # noqa: ANN001
    try:
        import pypdfium2.raw as raw
    except Exception:
        return False

    center_x = page_width / 2.0
    for obj in page.get_objects():
        bounds = obj.get_bounds()
        if bounds is None:
            continue
        left, bottom, right, top = bounds
        width = max(0.0, right - left)
        height = max(0.0, top - bottom)
        if width <= 0.0 or height <= 0.0:
            continue
        if raw.FPDFPageObj_GetType(obj.raw) not in (raw.FPDF_PAGEOBJ_PATH, raw.FPDF_PAGEOBJ_SHADING):
            continue
        if height < page_height * 0.60:
            continue
        if width > max(page_width * 0.03, 10.0):
            continue
        if abs(((left + right) / 2.0) - center_x) > page_width * 0.08:
            continue
        return True
    return False


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not intervals:
        return []

    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _longest_interval_gap(
    intervals: list[tuple[float, float]],
    *,
    start: float,
    end: float,
) -> float:
    cursor = start
    longest = 0.0
    for left, right in intervals:
        if left > cursor:
            longest = max(longest, left - cursor)
        cursor = max(cursor, right)
    longest = max(longest, end - cursor)
    return longest


def _union_box_bounds(
    boxes: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    if not boxes:
        return None
    left = min(box[0] for box in boxes)
    bottom = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    top = max(box[3] for box in boxes)
    return (left, bottom, right, top)


def _bbox_from_bounds(bounds: tuple[float, float, float, float] | None) -> PdfBoundingBox | None:
    if bounds is None:
        return None
    return PdfBoundingBox(
        left_pt=bounds[0],
        bottom_pt=bounds[1],
        right_pt=bounds[2],
        top_pt=bounds[3],
    )


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
    _normalize_pdf_doc_for_flow(doc_ir, preview_context=preview_context)
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

def _paragraph_reading_order(paragraph: ParagraphIR, fallback_index: int) -> tuple[int, int]:
    if paragraph.meta is None:
        return (fallback_index + 1_000_000, fallback_index)
    reading_order_index = getattr(paragraph.meta, "reading_order_index", None)
    if reading_order_index is None:
        return (fallback_index + 1_000_000, fallback_index)
    return (reading_order_index, fallback_index)

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


def _bbox_center(bbox: PdfBoundingBox) -> tuple[float, float]:
    return ((bbox.left_pt + bbox.right_pt) / 2.0, (bbox.bottom_pt + bbox.top_pt) / 2.0)


def _bbox_area(bbox: PdfBoundingBox) -> float:
    return max(bbox.right_pt - bbox.left_pt, 0.0) * max(bbox.top_pt - bbox.bottom_pt, 0.0)


def _bbox_intersection(left: PdfBoundingBox, right: PdfBoundingBox) -> PdfBoundingBox | None:
    intersection = PdfBoundingBox(
        left_pt=max(left.left_pt, right.left_pt),
        bottom_pt=max(left.bottom_pt, right.bottom_pt),
        right_pt=min(left.right_pt, right.right_pt),
        top_pt=min(left.top_pt, right.top_pt),
    )
    if intersection.right_pt <= intersection.left_pt or intersection.top_pt <= intersection.bottom_pt:
        return None
    return intersection


def _bbox_contains_node(candidate_bbox: PdfBoundingBox, node_bbox: PdfBoundingBox) -> bool:
    return _bbox_contains(
        candidate_bbox,
        node_bbox,
        tolerance_pt=_CANDIDATE_ASSIGN_TOLERANCE_PT,
    )


def _bbox_assignment_score(candidate_bbox: PdfBoundingBox, node_bbox: PdfBoundingBox) -> tuple[int, float, float]:
    if _bbox_contains_node(candidate_bbox, node_bbox):
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
                _page_long_rule_candidates(logical_preview_context, page_number=logical_page_info.page_number)
            )
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
        order_key = _paragraph_reading_order(paragraph, fallback_index)
        entries.append(
            _PreviewCompositionEntry(
                item_type="paragraph",
                bounding_box=_paragraph_bbox(paragraph),
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

    has_side_regions = any(region.region_type in {"left", "right"} for region in page_regions)
    entries.sort(
        key=lambda entry: _preview_entry_sort_key(entry, has_side_regions=has_side_regions)
    )
    return entries


def _preview_region_rank(region_type: str, *, has_side_regions: bool) -> int:
    if not has_side_regions:
        return 0
    if region_type in {"full", "main"}:
        return 0
    if region_type == "left":
        return 1
    if region_type == "right":
        return 2
    return 3


def _preview_entry_sort_key(
    entry: _PreviewCompositionEntry,
    *,
    has_side_regions: bool,
) -> tuple[int, int, int, float, float, int]:
    top_offset = 1_000_000.0 if entry.top_offset_pt is None else entry.top_offset_pt
    left_offset = 1_000_000.0 if entry.bounding_box is None else entry.bounding_box.left_pt
    explicit_order = entry.order_key[0] < 1_000_000
    region_rank = _preview_region_rank(entry.region_type, has_side_regions=has_side_regions)
    if explicit_order:
        return (
            0,
            entry.order_key[0],
            entry.order_key[1],
            top_offset,
            left_offset,
            region_rank,
        )
    return (
        1,
        region_rank,
        0,
        top_offset,
        left_offset,
        entry.order_key[1],
    )


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
    margin_top, margin_right, margin_bottom, margin_left = _page_content_margins(page)
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

    ordered = sorted(
        assigned_candidates,
        key=lambda item: (
            1_000_000.0 if item.top_offset_pt is None else item.top_offset_pt,
            item.order_key,
        ),
    )
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
                    key=lambda item: (
                        item.candidate.bounding_box.top_pt,
                        item.candidate.bounding_box.left_pt,
                        index_by_id[id(item)],
                    ),
                ),
                region_type=members[0].region_type,
                top_offset_pt=top_offset,
                bottom_offset_pt=bottom_offset,
                order_key=min(member.order_key for member in members),
                bounding_box=group_bbox,
            )
        )

    groups.sort(
        key=lambda group: (
            1_000_000.0 if group.top_offset_pt is None else group.top_offset_pt,
            group.order_key,
        )
    )
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
        paragraph_order = _paragraph_reading_order(paragraph, fallback_index)
        paragraph_key = (paragraph_order[0], paragraph_order[1], 0)
        paragraph_bbox = _paragraph_bbox(paragraph)
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
            node_key = (paragraph_order[0], paragraph_order[1], content_index)
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


def _page_long_rule_candidates(
    preview_context: PdfPreviewContext,
    *,
    page_number: int,
) -> list[PdfPreviewVisualBlockCandidate]:
    return [
        candidate
        for candidate in preview_context.visual_block_candidates
        if candidate.page_number == page_number and candidate.candidate_type == "long_rule"
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
        and _bbox_distance(candidate_bbox, table_bbox) <= 12.0
    )

def _assign_page_nodes_to_candidates(
    page: PageInfo,
    page_paragraphs: list[ParagraphIR],
    *,
    page_regions: list[PdfLayoutRegion],
    preview_context: PdfPreviewContext,
) -> tuple[list[_AssignedCandidate], set[str], set[str]]:
    paragraph_nodes, table_nodes, image_nodes, run_nodes = _collect_page_render_nodes(page_paragraphs)
    candidates = [
        candidate
        for candidate in _page_box_candidates(preview_context, page_number=page.page_number)
        if not any(_candidate_matches_table_bbox(candidate.bounding_box, table_node.bbox) for table_node in table_nodes)
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
                order_key=(1_000_000, candidate_index, 0),
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

    return assigned_candidates, assigned_paragraph_ids, assigned_child_ids


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
) -> list[tuple[tuple[int, int, int], ParagraphIR]]:
    if not nodes:
        return []

    grouped: dict[str, list[_PreviewRenderNode]] = {}
    group_order: dict[str, tuple[int, int, int]] = {}
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

    paragraphs: list[tuple[tuple[int, int, int], ParagraphIR]] = []
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
    content_blocks: list[tuple[tuple[int, int, int], ParagraphIR]] = []
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


from .compose import (
    _assigned_candidate_cell_paragraphs,
    _assign_page_nodes_to_candidates,
    _auxiliary_nodes_to_paragraphs,
    _bbox_assignment_score,
    _bbox_distance,
    _bbox_offsets_from_page,
    _build_candidate_groups,
    _build_column_band_paragraph,
    _build_layout_table_paragraph_for_group,
    _build_preview_entries,
    _best_candidate_for_node,
    _candidate_boxes_belong_to_same_group,
    _candidate_matches_table_bbox,
    _cluster_boundary_values,
    _column_band_cell_style,
    _compose_logical_page,
    _content_node_bbox,
    _filter_page_flow_paragraphs,
    _layout_table_cell_style,
    _materialize_flow_paragraphs,
    _nearest_boundary_index,
    _normalize_pdf_doc_for_flow,
    _page_box_candidates,
    _page_content_margins,
    _page_long_rule_candidates,
    _paragraph_offsets_from_page,
    _paragraph_reading_order,
    _preview_entry_sort_key,
    _preview_region_rank,
    _primary_region_bbox,
    _promote_assigned_candidates_to_layout_tables,
    _span_overlap_ratio,
)


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
    next_logical_page_number = 1
    for page in doc_ir.pages:
        page_paragraphs = paragraphs_by_page.get(page.page_number, [])
        page_regions = [region for region in preview_context.layout_regions if region.page_number == page.page_number]
        logical_pages = _build_logical_pages_for_page(
            page,
            page_regions,
            page_paragraphs=page_paragraphs,
            starting_page_number=next_logical_page_number,
        )
        next_logical_page_number += len(logical_pages)

        for logical_page in logical_pages:
            logical_page_info = _logical_page_page_info(logical_page, source_page=page)
            logical_page_paragraphs = _logical_page_paragraphs(
                page,
                page_paragraphs,
                page_regions=page_regions,
                logical_pages=logical_pages,
                logical_page=logical_page,
            )
            logical_preview_context = _logical_page_preview_context(
                page,
                preview_context,
                page_regions=page_regions,
                logical_pages=logical_pages,
                logical_page=logical_page,
            )
            content_html = _render_preview_page_content(
                doc_ir,
                logical_page_info,
                logical_page_paragraphs,
                preview_context=logical_preview_context,
            )
            parts.append(
                f'<section class="document-page" data-page-number="{logical_page.page_number}" '
                f'data-physical-page-number="{logical_page.physical_page_number}" '
                f'data-logical-page-type="{logical_page.logical_page_type}" '
                f'style="{_page_style(logical_page_info)}">'
                f'<div class="document-page__content" style="{_page_content_style(logical_page_info)}">{content_html or "&nbsp;"}</div>'
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
    long_rule_overlays = _render_long_rule_overlays(
        page,
        _page_long_rule_candidates(preview_context, page_number=page.page_number),
    )
    if not page_paragraphs and not long_rule_overlays:
        return ""

    composition = _compose_logical_page(
        page,
        page_paragraphs,
        page_regions=[],
        preview_context=preview_context,
    )
    candidate_overlays = _render_page_positioned_candidates(
        doc_ir,
        page,
        [
            assigned_candidate
            for assigned_candidate in composition.assigned_candidates
            if id(assigned_candidate.candidate) not in composition.promoted_candidate_ids
        ],
    )
    if not composition.ordered_paragraphs:
        return (
            '<div class="pdf-preview-page-layer" style="position:relative;flex:1 1 auto;height:100%">'
            f"{long_rule_overlays}{candidate_overlays}"
            "</div>"
        )

    flow_html = "\n\n".join(
        _render_paragraph(doc_ir, paragraph)
        for paragraph in composition.ordered_paragraphs
    )
    return (
        '<div class="pdf-preview-page-layer" style="position:relative;flex:1 1 auto;height:100%">'
        f"{long_rule_overlays}{candidate_overlays}"
        '<div class="pdf-preview-page-flow" style="position:relative;z-index:2">'
        f"{flow_html}"
        "</div></div>"
    )


def _page_content_bbox(page: PageInfo) -> PdfBoundingBox | None:
    if page.width_pt is None or page.height_pt is None:
        return None
    margin_top, margin_right, margin_bottom, margin_left = _page_content_margins(page)
    return PdfBoundingBox(
        left_pt=margin_left,
        bottom_pt=margin_bottom,
        right_pt=max(page.width_pt - margin_right, margin_left),
        top_pt=max(page.height_pt - margin_top, margin_bottom),
    )


def _render_page_positioned_candidates(
    doc_ir: DocIR,
    page: PageInfo,
    assigned_candidates: list[_AssignedCandidate],
) -> str:
    if not assigned_candidates:
        return ""

    content_bbox = _page_content_bbox(page)
    if content_bbox is None:
        return ""

    ordered_candidates = sorted(
        assigned_candidates,
        key=lambda item: (
            -_bbox_area(item.candidate.bounding_box),
            item.order_key,
        ),
    )
    parts = [
        _render_positioned_candidate(
            doc_ir,
            assigned_candidate,
            group_bbox=content_bbox,
        )
        for assigned_candidate in ordered_candidates
    ]
    parts = [part for part in parts if part]
    if not parts:
        return ""
    return (
        '<div class="pdf-preview-page-candidates" '
        'style="position:absolute;inset:0;z-index:1;pointer-events:none">'
        + "".join(parts)
        + "</div>"
    )


def _render_preview_entry(doc_ir: DocIR, entry: _PreviewCompositionEntry) -> str:
    if entry.item_type == "paragraph" and entry.paragraph is not None:
        return _render_paragraph(doc_ir, entry.paragraph)
    return ""


def _render_positioned_candidate(
    doc_ir: DocIR,
    assigned_candidate: _AssignedCandidate,
    *,
    group_bbox: PdfBoundingBox,
) -> str:
    candidate = assigned_candidate.candidate
    bbox = candidate.bounding_box
    width_pt = max(bbox.right_pt - bbox.left_pt, 0.0)
    height_pt = max(bbox.top_pt - bbox.bottom_pt, 0.0)
    left_offset_pt = max(bbox.left_pt - group_bbox.left_pt, 0.0)
    top_offset_pt = max(group_bbox.top_pt - bbox.top_pt, 0.0)

    content_blocks: list[tuple[tuple[int, int, int], str]] = []
    for paragraph_node in assigned_candidate.paragraph_nodes:
        if paragraph_node.paragraph is None:
            continue
        content_blocks.append((paragraph_node.order_key, _render_paragraph(doc_ir, paragraph_node.paragraph)))

    auxiliary_nodes = sorted(
        assigned_candidate.table_nodes + assigned_candidate.image_nodes + assigned_candidate.run_nodes,
        key=lambda node: node.order_key,
    )
    content_blocks.extend(_render_auxiliary_nodes(doc_ir, auxiliary_nodes))
    content_blocks.sort(key=lambda item: item[0])
    inner_html = "\n\n".join(html for _, html in content_blocks if html) or "&nbsp;"

    wrapper_styles = [
        "position:absolute",
        "box-sizing:border-box",
        f"left:{left_offset_pt:.1f}pt",
        f"top:{top_offset_pt:.1f}pt",
        "padding:6pt 8pt",
        "border:1px solid #4a4f57",
        "background:transparent",
        f"width:{width_pt:.1f}pt",
    ]
    if height_pt > 0.0:
        wrapper_styles.append(f"min-height:{height_pt:.1f}pt")

    overlay_html = _render_candidate_child_cell_overlays(candidate)
    content_html = (
        '<div class="pdf-preview-candidate__content" '
        'style="position:relative;z-index:1">'
        f"{inner_html}</div>"
    )
    return (
        f'<div class="pdf-preview-candidate pdf-preview-candidate--{candidate.candidate_type}" '
        f'data-candidate-type="{candidate.candidate_type}" '
        f'style="{";".join(wrapper_styles)}">'
        f"{overlay_html}{content_html}</div>"
    )


def _render_auxiliary_nodes(
    doc_ir: DocIR,
    nodes: list[_PreviewRenderNode],
) -> list[tuple[tuple[int, int, int], str]]:
    if not nodes:
        return []

    grouped: dict[str, list[_PreviewRenderNode]] = {}
    group_order: dict[str, tuple[int, int, int]] = {}
    group_para_style: dict[str, Any] = {}
    for node in nodes:
        group_key = node.parent_paragraph_id or node.unit_id
        grouped.setdefault(group_key, []).append(node)
        group_order[group_key] = min(group_order.get(group_key, node.order_key), node.order_key)
        if group_key not in group_para_style:
            group_para_style[group_key] = node.parent_para_style

    rendered: list[tuple[tuple[int, int, int], str]] = []
    for group_key, group_nodes in grouped.items():
        content_nodes: list[Any] = []
        for node in sorted(group_nodes, key=lambda item: item.order_key):
            if node.table is not None:
                content_nodes.append(node.table)
            elif node.image is not None:
                content_nodes.append(node.image)
            elif node.run is not None:
                content_nodes.append(node.run)
        if not content_nodes:
            continue
        wrapper_paragraph = ParagraphIR(
            unit_id=group_key,
            text="",
            bbox=None,
            para_style=group_para_style.get(group_key),
            content=content_nodes,
        )
        wrapper_paragraph.recompute_text()
        rendered.append((group_order[group_key], _render_paragraph(doc_ir, wrapper_paragraph)))
    return rendered


def _render_candidate_child_cell_overlays(candidate: PdfPreviewVisualBlockCandidate) -> str:
    return ""


def _page_content_margins(page: PageInfo) -> tuple[float, float, float, float]:
    return (
        page.margin_top_pt if page.margin_top_pt is not None else 48.0,
        page.margin_right_pt if page.margin_right_pt is not None else 42.0,
        page.margin_bottom_pt if page.margin_bottom_pt is not None else 48.0,
        page.margin_left_pt if page.margin_left_pt is not None else 42.0,
    )


def _render_long_rule_overlays(page: PageInfo, long_rules: list[PdfPreviewVisualBlockCandidate]) -> str:
    if not long_rules:
        return ""
    if page.height_pt is None:
        return ""

    margin_top, _margin_right, _margin_bottom, margin_left = _page_content_margins(page)
    parts: list[str] = []
    for candidate in long_rules:
        bbox = candidate.bounding_box
        left = max(bbox.left_pt - margin_left, 0.0)
        top = max(page.height_pt - bbox.top_pt - margin_top, 0.0)
        width = max(bbox.right_pt - bbox.left_pt, 0.0)
        height = max(bbox.top_pt - bbox.bottom_pt, 0.0)
        if width <= 0.0 or height <= 0.0:
            continue
        parts.append(
            '<div class="pdf-preview-long-rule" '
            f'style="position:absolute;left:{left:.1f}pt;top:{top:.1f}pt;'
            f'width:{width:.1f}pt;height:{height:.1f}pt;'
            'background:#4a4f57;opacity:0.8"></div>'
        )
    if not parts:
        return ""
    return (
        '<div class="pdf-preview-long-rules" '
        'style="position:absolute;inset:0;z-index:0;pointer-events:none">'
        + "".join(parts)
        + "</div>"
    )


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
    from ..pipeline import _parse_pdf_to_doc_ir_with_preview

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
    prepared_context = preview_context.model_copy(deep=True)
    prepare_pdf_for_html(prepared_doc, preview_context=prepared_context)
    if not prepared_context.visual_block_candidates:
        return render_html_document(prepared_doc, title=title)
    body = _render_preview_body(prepared_doc, preview_context=prepared_context)
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
    "PdfPreviewVisualBlockCandidate",
    "PdfPreviewContext",
    "PdfPreviewTableContext",
    "PdfPreviewVisualPrimitive",
    "build_pdf_preview_context",
    "prepare_pdf_for_html",
    "render_pdf_html",
    "render_pdf_preview_html",
    "render_pdf_preview_html_from_file",
]
