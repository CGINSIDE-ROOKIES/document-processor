"""Internal helper functions shared across preview submodules."""

from __future__ import annotations

from ...models import PageInfo
from ..meta import PdfBoundingBox
from .models import (
    PdfPreviewVisualPrimitive,
    _VISUAL_BOUNDARY_SUPPRESSION_TOLERANCE_PT,
    _VISUAL_BOX_SEED_MIN_SIZE_PT,
    _VISUAL_FRAME_MIN_SIZE_PT,
    _VISUAL_LINE_JOIN_TOLERANCE_PT,
    _VISUAL_TOUCH_TOLERANCE_PT,
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


def _shared_bbox_distance(left: PdfBoundingBox, right: PdfBoundingBox) -> float:
    return abs(left.left_pt - right.left_pt) + abs(left.bottom_pt - right.bottom_pt) + abs(
        left.right_pt - right.right_pt
    ) + abs(left.top_pt - right.top_pt)


def _shared_page_content_margins(page: PageInfo) -> tuple[float, float, float, float]:
    return (
        page.margin_top_pt if page.margin_top_pt is not None else 48.0,
        page.margin_right_pt if page.margin_right_pt is not None else 42.0,
        page.margin_bottom_pt if page.margin_bottom_pt is not None else 48.0,
        page.margin_left_pt if page.margin_left_pt is not None else 42.0,
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


__all__ = [
    "_has_visible_stroke",
    "_primitive_size",
    "_primitive_bbox_line_orientation",
    "_primitive_line_span",
    "_primitive_line_span_range",
    "_primitive_line_axis_center",
    "_bbox_touches_or_near",
    "_bbox_contains",
    "_line_primitives_belong_to_same_frame",
    "_is_open_frame_component",
    "_horizontal_line_matches_box_boundary",
    "_vertical_line_matches_box_boundary",
    "_dedupe_seed_bboxes",
    "_primitive_belongs_to_axis_box",
    "_union_visual_primitive_bboxes",
    "_primitive_is_long_rule",
    "_line_like_bbox_orientation",
    "_bbox_overlap_ratio",
    "_primitive_line_orientation",
    "_primitive_line_endpoints",
    "_point_distance",
    "_point_bucket_keys",
    "_pdfium_text_boxes",
    "_has_central_vertical_gutter",
    "_merge_intervals",
    "_longest_interval_gap",
    "_union_box_bounds",
    "_bbox_from_bounds",
    "_bbox_center",
    "_bbox_area",
    "_bbox_intersection",
]
