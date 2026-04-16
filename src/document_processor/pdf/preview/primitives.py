"""PDFium visual primitive extraction helpers."""

from __future__ import annotations

from ..meta import PdfBoundingBox
from .models import (
    PdfPreviewVisualPrimitive,
    _VISUAL_MIN_LINE_SEGMENT_PT,
    _VISUAL_SEGMENTED_AXIS_TOLERANCE_PT,
    _VISUAL_SEGMENTED_GAP_TOLERANCE_PT,
    _VISUAL_SEGMENTED_MAX_FRAGMENT_PT,
    _VISUAL_SEGMENTED_MIN_PARTS,
    _VISUAL_SEGMENTED_MIN_SPAN_PT,
    _VISUAL_TOUCH_TOLERANCE_PT,
)
from .shared import (
    _bbox_from_bounds,
    _has_visible_stroke,
    _primitive_bbox_line_orientation,
    _primitive_line_axis_center,
    _primitive_line_span,
    _primitive_line_span_range,
    _primitive_size,
    _union_visual_primitive_bboxes,
)


def _extract_pdfium_visual_primitives(
    page,  # noqa: ANN001
    *,
    page_number: int,
    raw_module=None,  # noqa: ANN001
) -> list[PdfPreviewVisualPrimitive]:
    raw = raw_module
    if raw is None:
        try:
            import pypdfium2.raw as raw
        except Exception:
            return []

    page_width = page.get_width() or 0.0
    page_height = page.get_height() or 0.0
    primitives: list[PdfPreviewVisualPrimitive] = []
    for draw_order, obj in enumerate(page.get_objects()):
        bounds = obj.get_bounds()
        if bounds is None:
            continue

        bbox = _bbox_from_bounds(bounds)
        if bbox is None:
            continue

        object_type = _pdfium_object_type_name(raw, obj.raw)
        if object_type != "path":
            continue

        primitives.append(
            PdfPreviewVisualPrimitive(
                page_number=page_number,
                draw_order=draw_order,
                object_type=object_type,
                bounding_box=bbox,
                fill_color=_pdfium_color(raw, obj.raw, getter=raw.FPDFPageObj_GetFillColor),
                stroke_color=_pdfium_color(raw, obj.raw, getter=raw.FPDFPageObj_GetStrokeColor),
                stroke_width_pt=_pdfium_stroke_width(raw, obj.raw),
                has_fill=_pdfium_has_fill(raw, obj.raw),
                has_stroke=_pdfium_has_stroke(raw, obj.raw),
                is_axis_aligned_box=object_type == "path" and _pdfium_is_axis_aligned_box(raw, obj.raw),
            )
        )

    segmented_primitives = _build_segmented_rule_primitives(
        primitives,
        page_width=page_width,
        page_height=page_height,
    )
    primitives.extend(segmented_primitives)
    primitives.extend(_build_axis_box_edge_primitives(primitives))
    for primitive in primitives:
        primitive.candidate_roles = _candidate_roles_for_visual_primitive(
            primitive,
            page_width=page_width,
            page_height=page_height,
        )

    return [
        primitive
        for primitive in primitives
        if primitive.candidate_roles and not (primitive.object_type == "path" and primitive.is_axis_aligned_box)
    ]


def _build_segmented_rule_primitives(
    primitives: list[PdfPreviewVisualPrimitive],
    *,
    page_width: float,
    page_height: float,
) -> list[PdfPreviewVisualPrimitive]:
    buckets: dict[tuple[int, str, str, int], list[PdfPreviewVisualPrimitive]] = {}
    for primitive in primitives:
        if not _has_visible_stroke(primitive):
            continue
        orientation = _primitive_bbox_line_orientation(
            primitive,
            page_width=page_width,
            page_height=page_height,
            min_length_pt=0.0,
        )
        if orientation is None:
            continue
        line_span = _primitive_line_span(primitive, orientation)
        if line_span <= 0.0 or line_span > _VISUAL_SEGMENTED_MAX_FRAGMENT_PT:
            continue
        axis_value = _primitive_line_axis_center(primitive, orientation)
        bucket_key = (
            primitive.page_number,
            orientation,
            primitive.stroke_color or "",
            round(axis_value / _VISUAL_SEGMENTED_AXIS_TOLERANCE_PT),
        )
        buckets.setdefault(bucket_key, []).append(primitive)

    synthetic_primitives: list[PdfPreviewVisualPrimitive] = []
    next_draw_order = max((primitive.draw_order for primitive in primitives), default=-1) + 1
    for (page_number, orientation, stroke_color, _axis_bucket), group in buckets.items():
        group.sort(key=lambda item: _primitive_line_span_range(item, orientation)[0])
        run: list[PdfPreviewVisualPrimitive] = []
        for primitive in group:
            if not run:
                run = [primitive]
                continue
            if _segmented_rule_can_extend(run[-1], primitive, orientation):
                run.append(primitive)
                continue
            synthetic = _build_segmented_rule_primitive(
                run,
                page_number=page_number,
                orientation=orientation,
                stroke_color=stroke_color,
                draw_order=next_draw_order,
            )
            if synthetic is not None:
                synthetic_primitives.append(synthetic)
                next_draw_order += 1
            run = [primitive]

        synthetic = _build_segmented_rule_primitive(
            run,
            page_number=page_number,
            orientation=orientation,
            stroke_color=stroke_color,
            draw_order=next_draw_order,
        )
        if synthetic is not None:
            synthetic_primitives.append(synthetic)
            next_draw_order += 1

    return synthetic_primitives


def _build_axis_box_edge_primitives(
    primitives: list[PdfPreviewVisualPrimitive],
) -> list[PdfPreviewVisualPrimitive]:
    synthetic_primitives: list[PdfPreviewVisualPrimitive] = []
    next_draw_order = max((primitive.draw_order for primitive in primitives), default=-1) + 1
    for primitive in primitives:
        if not primitive.is_axis_aligned_box or not _has_visible_stroke(primitive):
            continue

        bbox = primitive.bounding_box
        stroke_width = max(primitive.stroke_width_pt or 1.0, 1.0)
        half_stroke = stroke_width / 2.0
        synthetic_primitives.extend(
            [
                PdfPreviewVisualPrimitive(
                    page_number=primitive.page_number,
                    draw_order=next_draw_order,
                    object_type="axis_box_edge_horizontal",
                    bounding_box=PdfBoundingBox(
                        left_pt=bbox.left_pt,
                        bottom_pt=bbox.top_pt - half_stroke,
                        right_pt=bbox.right_pt,
                        top_pt=bbox.top_pt + half_stroke,
                    ),
                    fill_color=None,
                    stroke_color=primitive.stroke_color,
                    stroke_width_pt=stroke_width,
                    has_fill=False,
                    has_stroke=True,
                    is_axis_aligned_box=False,
                ),
                PdfPreviewVisualPrimitive(
                    page_number=primitive.page_number,
                    draw_order=next_draw_order + 1,
                    object_type="axis_box_edge_horizontal",
                    bounding_box=PdfBoundingBox(
                        left_pt=bbox.left_pt,
                        bottom_pt=bbox.bottom_pt - half_stroke,
                        right_pt=bbox.right_pt,
                        top_pt=bbox.bottom_pt + half_stroke,
                    ),
                    fill_color=None,
                    stroke_color=primitive.stroke_color,
                    stroke_width_pt=stroke_width,
                    has_fill=False,
                    has_stroke=True,
                    is_axis_aligned_box=False,
                ),
                PdfPreviewVisualPrimitive(
                    page_number=primitive.page_number,
                    draw_order=next_draw_order + 2,
                    object_type="axis_box_edge_vertical",
                    bounding_box=PdfBoundingBox(
                        left_pt=bbox.left_pt - half_stroke,
                        bottom_pt=bbox.bottom_pt,
                        right_pt=bbox.left_pt + half_stroke,
                        top_pt=bbox.top_pt,
                    ),
                    fill_color=None,
                    stroke_color=primitive.stroke_color,
                    stroke_width_pt=stroke_width,
                    has_fill=False,
                    has_stroke=True,
                    is_axis_aligned_box=False,
                ),
                PdfPreviewVisualPrimitive(
                    page_number=primitive.page_number,
                    draw_order=next_draw_order + 3,
                    object_type="axis_box_edge_vertical",
                    bounding_box=PdfBoundingBox(
                        left_pt=bbox.right_pt - half_stroke,
                        bottom_pt=bbox.bottom_pt,
                        right_pt=bbox.right_pt + half_stroke,
                        top_pt=bbox.top_pt,
                    ),
                    fill_color=None,
                    stroke_color=primitive.stroke_color,
                    stroke_width_pt=stroke_width,
                    has_fill=False,
                    has_stroke=True,
                    is_axis_aligned_box=False,
                ),
            ]
        )
        next_draw_order += 4
    return synthetic_primitives


def _segmented_rule_can_extend(
    left: PdfPreviewVisualPrimitive,
    right: PdfPreviewVisualPrimitive,
    orientation: str,
) -> bool:
    if left.page_number != right.page_number:
        return False
    if left.stroke_color != right.stroke_color:
        return False
    if orientation == "horizontal":
        left_axis = (left.bounding_box.top_pt + left.bounding_box.bottom_pt) / 2.0
        right_axis = (right.bounding_box.top_pt + right.bounding_box.bottom_pt) / 2.0
    else:
        left_axis = (left.bounding_box.left_pt + left.bounding_box.right_pt) / 2.0
        right_axis = (right.bounding_box.left_pt + right.bounding_box.right_pt) / 2.0
    if abs(left_axis - right_axis) > _VISUAL_SEGMENTED_AXIS_TOLERANCE_PT:
        return False

    _, left_end = _primitive_line_span_range(left, orientation)
    right_start, _ = _primitive_line_span_range(right, orientation)
    gap = right_start - left_end
    return gap <= _VISUAL_SEGMENTED_GAP_TOLERANCE_PT


def _build_segmented_rule_primitive(
    run: list[PdfPreviewVisualPrimitive],
    *,
    page_number: int,
    orientation: str,
    stroke_color: str,
    draw_order: int,
) -> PdfPreviewVisualPrimitive | None:
    if len(run) < _VISUAL_SEGMENTED_MIN_PARTS:
        return None

    starts_ends = [_primitive_line_span_range(item, orientation) for item in run]
    start = min(item[0] for item in starts_ends)
    end = max(item[1] for item in starts_ends)
    span = end - start
    if span < _VISUAL_SEGMENTED_MIN_SPAN_PT:
        return None

    gaps = [
        max(current_start - previous_end, 0.0)
        for (_, previous_end), (current_start, _) in zip(starts_ends, starts_ends[1:])
    ]
    has_visible_gap = any(gap >= _VISUAL_TOUCH_TOLERANCE_PT for gap in gaps)
    if not has_visible_gap and len(run) < 5:
        return None

    bbox = _union_visual_primitive_bboxes(run)
    if bbox is None:
        return None

    return PdfPreviewVisualPrimitive(
        page_number=page_number,
        draw_order=draw_order,
        object_type=f"segmented_{orientation}_rule",
        bounding_box=bbox,
        fill_color=None,
        stroke_color=stroke_color,
        stroke_width_pt=max((item.stroke_width_pt or 0.0) for item in run) or None,
        has_fill=False,
        has_stroke=True,
        is_axis_aligned_box=False,
    )


def _pdfium_object_type_name(raw, obj_raw) -> str:  # noqa: ANN001
    object_type = raw.FPDFPageObj_GetType(obj_raw)
    if object_type == raw.FPDF_PAGEOBJ_PATH:
        return "path"
    if object_type == raw.FPDF_PAGEOBJ_SHADING:
        return "shading"
    if object_type == raw.FPDF_PAGEOBJ_IMAGE:
        return "image"
    if object_type == raw.FPDF_PAGEOBJ_TEXT:
        return "text"
    return "unknown"


def _pdfium_color(raw, obj_raw, *, getter) -> str | None:  # noqa: ANN001
    from ctypes import c_uint

    red = c_uint()
    green = c_uint()
    blue = c_uint()
    alpha = c_uint()
    if not getter(obj_raw, red, green, blue, alpha):
        return None
    return f"#{red.value:02x}{green.value:02x}{blue.value:02x}{alpha.value:02x}"


def _pdfium_stroke_width(raw, obj_raw) -> float | None:  # noqa: ANN001
    from ctypes import c_float

    width = c_float()
    if not raw.FPDFPageObj_GetStrokeWidth(obj_raw, width):
        return None
    return float(width.value)


def _pdfium_has_fill(raw, obj_raw) -> bool:  # noqa: ANN001
    from ctypes import c_int

    if not hasattr(raw, "FPDFPath_GetDrawMode"):
        return False
    fill_mode = c_int()
    stroke = c_int()
    if not raw.FPDFPath_GetDrawMode(obj_raw, fill_mode, stroke):
        return False
    return fill_mode.value != getattr(raw, "FPDF_FILLMODE_NONE", 0)


def _pdfium_has_stroke(raw, obj_raw) -> bool:  # noqa: ANN001
    from ctypes import c_int

    if not hasattr(raw, "FPDFPath_GetDrawMode"):
        return False
    fill_mode = c_int()
    stroke = c_int()
    if not raw.FPDFPath_GetDrawMode(obj_raw, fill_mode, stroke):
        return False
    return bool(stroke.value)


def _pdfium_is_axis_aligned_box(raw, obj_raw) -> bool:  # noqa: ANN001
    points = _pdfium_path_points(raw, obj_raw)
    if len(points) < 4:
        return False

    unique_points = {(round(x, 3), round(y, 3)) for x, y in points}
    if len(unique_points) != 4:
        return False

    xs = {point[0] for point in unique_points}
    ys = {point[1] for point in unique_points}
    return len(xs) == 2 and len(ys) == 2


def _pdfium_path_points(raw, obj_raw) -> list[tuple[float, float]]:  # noqa: ANN001
    from ctypes import c_float

    segment_count = raw.FPDFPath_CountSegments(obj_raw)
    if segment_count <= 0:
        return []

    points: list[tuple[float, float]] = []
    for segment_index in range(segment_count):
        segment = raw.FPDFPath_GetPathSegment(obj_raw, segment_index)
        if not segment:
            continue
        segment_type = raw.FPDFPathSegment_GetType(segment)
        if segment_type not in (raw.FPDF_SEGMENT_MOVETO, raw.FPDF_SEGMENT_LINETO):
            return []
        x = c_float()
        y = c_float()
        if not raw.FPDFPathSegment_GetPoint(segment, x, y):
            continue
        points.append((float(x.value), float(y.value)))
    return points


def _candidate_roles_for_visual_primitive(
    primitive: PdfPreviewVisualPrimitive,
    *,
    page_width: float,
    page_height: float,
) -> list[str]:
    roles: list[str] = []
    width, height = _primitive_size(primitive)
    if width <= 0.0 or height <= 0.0:
        return roles
    has_visible_stroke = _has_visible_stroke(primitive)
    is_segmented_horizontal = primitive.object_type == "segmented_horizontal_rule"
    is_segmented_vertical = primitive.object_type == "segmented_vertical_rule"

    narrow_width = max(page_width * 0.03, 10.0)
    narrow_height = max(page_height * 0.03, 10.0)

    is_vertical_segment = is_segmented_vertical or (
        has_visible_stroke
        and width <= narrow_width
        and height > width
        and height > _VISUAL_MIN_LINE_SEGMENT_PT
    )
    is_horizontal_segment = is_segmented_horizontal or (
        has_visible_stroke
        and height <= narrow_height
        and width > height
        and width > _VISUAL_MIN_LINE_SEGMENT_PT
    )
    if is_vertical_segment:
        roles.append("vertical_line_segment")
    if is_horizontal_segment:
        roles.append("horizontal_line_segment")
    if is_segmented_vertical:
        roles.append("segmented_vertical_rule")
    if is_segmented_horizontal:
        roles.append("segmented_horizontal_rule")

    is_long_vertical_rule = (
        not is_segmented_vertical and has_visible_stroke and height >= page_height * 0.70 and width <= narrow_width
    )
    is_long_horizontal_rule = (
        not is_segmented_horizontal and has_visible_stroke and width >= page_width * 0.70 and height <= narrow_height
    )
    if is_long_vertical_rule:
        roles.append("long_vertical_rule")
    if is_long_horizontal_rule:
        roles.append("long_horizontal_rule")

    return roles


__all__ = [
    "_extract_pdfium_visual_primitives",
    "_build_segmented_rule_primitives",
    "_build_axis_box_edge_primitives",
    "_segmented_rule_can_extend",
    "_build_segmented_rule_primitive",
    "_pdfium_object_type_name",
    "_pdfium_color",
    "_pdfium_stroke_width",
    "_pdfium_has_fill",
    "_pdfium_has_stroke",
    "_pdfium_is_axis_aligned_box",
    "_pdfium_path_points",
    "_candidate_roles_for_visual_primitive",
]
