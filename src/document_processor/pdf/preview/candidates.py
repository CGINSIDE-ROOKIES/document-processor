"""Visual block candidate graphing helpers."""

from __future__ import annotations

from ..meta import PdfBoundingBox
from .models import (
    PdfPreviewVisualBlockCandidate,
    PdfPreviewVisualPrimitive,
    _VISUAL_BOUNDARY_SUPPRESSION_OVERLAP_RATIO,
    _VISUAL_BOUNDARY_SUPPRESSION_TOLERANCE_PT,
    _VISUAL_BOX_SEED_MIN_SIZE_PT,
    _VISUAL_DIVIDER_SPAN_RATIO,
    _VISUAL_FRAME_MIN_SIZE_PT,
    _VISUAL_LINE_JOIN_TOLERANCE_PT,
    _VISUAL_OPEN_FRAME_PRIMITIVE_LIMIT,
    _VISUAL_TOUCH_TOLERANCE_PT,
)


def _build_visual_block_candidates(
    primitives: list[PdfPreviewVisualPrimitive],
) -> list[PdfPreviewVisualBlockCandidate]:
    from . import _is_open_frame_component, _primitive_is_long_rule

    if not primitives:
        return []

    candidates: list[PdfPreviewVisualBlockCandidate] = []

    line_primitives = [
        primitive
        for primitive in primitives
        if {"horizontal_line_segment", "vertical_line_segment"} & set(primitive.candidate_roles)
    ]
    line_primitives = _dedupe_line_primitives_for_graph(line_primitives)
    if not line_primitives:
        return _dedupe_visual_block_candidates(candidates)

    # Long rules should participate in the same line graph as ordinary line
    # segments. Only when the line set itself is too large do we fall back to
    # a cheap pass, and even then we still let long rules form frames/boxes
    # among themselves before demoting them to standalone long_rule blocks.
    if len(line_primitives) > _VISUAL_OPEN_FRAME_PRIMITIVE_LIMIT:
        long_line_primitives = [
            primitive for primitive in line_primitives if _primitive_is_long_rule(primitive)
        ]
        return _suppress_boundary_semantic_lines(
            _dedupe_visual_block_candidates(_build_non_box_line_candidates(long_line_primitives))
        )

    components = _connected_line_components(line_primitives)
    axis_box_candidates: list[PdfPreviewVisualBlockCandidate] = []
    for component in components:
        if not _is_open_frame_component(component):
            continue
        axis_box_candidates.extend(_build_axis_box_candidates_from_component(component))
    candidates.extend(axis_box_candidates)

    assigned_draw_orders = {
        draw_order
        for candidate in axis_box_candidates
        for draw_order in candidate.primitive_draw_orders
    }
    leftover_lines = [
        primitive
        for primitive in line_primitives
        if primitive.draw_order not in assigned_draw_orders
    ]
    candidates.extend(_build_non_box_line_candidates(leftover_lines))

    return _suppress_boundary_semantic_lines(_dedupe_visual_block_candidates(candidates))


def _connected_line_components(
    line_primitives: list[PdfPreviewVisualPrimitive],
) -> list[list[PdfPreviewVisualPrimitive]]:
    from . import _line_primitives_belong_to_same_frame, _point_bucket_keys, _primitive_line_endpoints

    if not line_primitives:
        return []

    adjacency: dict[int, set[int]] = {index: set() for index in range(len(line_primitives))}
    endpoint_buckets: dict[tuple[int, int], set[int]] = {}
    for index, primitive in enumerate(line_primitives):
        endpoints = _primitive_line_endpoints(primitive)
        if endpoints is None:
            continue
        for endpoint in endpoints:
            for bucket_key in _point_bucket_keys(endpoint, tolerance_pt=_VISUAL_LINE_JOIN_TOLERANCE_PT):
                endpoint_buckets.setdefault(bucket_key, set()).add(index)

    for left_index, left in enumerate(line_primitives):
        endpoints = _primitive_line_endpoints(left)
        if endpoints is None:
            continue
        candidate_indices: set[int] = set()
        for endpoint in endpoints:
            for bucket_key in _point_bucket_keys(endpoint, tolerance_pt=_VISUAL_LINE_JOIN_TOLERANCE_PT):
                candidate_indices.update(endpoint_buckets.get(bucket_key, set()))
        for right_index in candidate_indices:
            if right_index <= left_index:
                continue
            right = line_primitives[right_index]
            if _line_primitives_belong_to_same_frame(left, right):
                adjacency[left_index].add(right_index)
                adjacency[right_index].add(left_index)

    components: list[list[PdfPreviewVisualPrimitive]] = []
    visited: set[int] = set()
    for start_index in range(len(line_primitives)):
        if start_index in visited:
            continue
        stack = [start_index]
        component_indices: list[int] = []
        while stack:
            current_index = stack.pop()
            if current_index in visited:
                continue
            visited.add(current_index)
            component_indices.append(current_index)
            stack.extend(neighbor for neighbor in adjacency[current_index] if neighbor not in visited)
        components.append([line_primitives[index] for index in component_indices])
    return components


def _dedupe_line_primitives_for_graph(
    line_primitives: list[PdfPreviewVisualPrimitive],
) -> list[PdfPreviewVisualPrimitive]:
    if not line_primitives:
        return []

    kept: list[PdfPreviewVisualPrimitive] = []
    for primitive in sorted(
        line_primitives,
        key=lambda item: (
            item.page_number,
            item.draw_order,
            item.bounding_box.left_pt,
            item.bounding_box.bottom_pt,
            item.bounding_box.right_pt,
            item.bounding_box.top_pt,
        ),
    ):
        duplicate_index: int | None = None
        for index, existing in enumerate(kept):
            if _line_primitives_are_graph_duplicates(existing, primitive):
                duplicate_index = index
                break
        if duplicate_index is None:
            kept.append(primitive)
            continue

        merged_roles = sorted(set(kept[duplicate_index].candidate_roles) | set(primitive.candidate_roles))
        kept[duplicate_index] = kept[duplicate_index].model_copy(update={"candidate_roles": merged_roles})
    return kept


def _line_primitives_are_graph_duplicates(
    left: PdfPreviewVisualPrimitive,
    right: PdfPreviewVisualPrimitive,
) -> bool:
    from . import _bbox_overlap_ratio, _primitive_line_orientation, _primitive_line_span_range

    if left.page_number != right.page_number:
        return False
    left_orientation = _primitive_line_orientation(left)
    right_orientation = _primitive_line_orientation(right)
    if left_orientation is None or left_orientation != right_orientation:
        return False
    if _bbox_overlap_ratio(left.bounding_box, right.bounding_box) >= 0.98:
        return True

    if left_orientation == "horizontal":
        left_axis = (left.bounding_box.top_pt + left.bounding_box.bottom_pt) / 2.0
        right_axis = (right.bounding_box.top_pt + right.bounding_box.bottom_pt) / 2.0
    else:
        left_axis = (left.bounding_box.left_pt + left.bounding_box.right_pt) / 2.0
        right_axis = (right.bounding_box.left_pt + right.bounding_box.right_pt) / 2.0
    if abs(left_axis - right_axis) > _VISUAL_TOUCH_TOLERANCE_PT:
        return False

    left_start, left_end = _primitive_line_span_range(left, left_orientation)
    right_start, right_end = _primitive_line_span_range(right, right_orientation)
    left_span = max(left_end - left_start, 0.0)
    right_span = max(right_end - right_start, 0.0)
    overlap = min(left_end, right_end) - max(left_start, right_start)
    if left_span <= 0.0 or right_span <= 0.0 or overlap <= 0.0:
        return False
    span_ratio = min(left_span, right_span) / max(left_span, right_span)
    if span_ratio < 0.98:
        return False
    overlap_ratio = overlap / min(left_span, right_span)
    return overlap_ratio >= 0.98


def _build_axis_box_candidates_from_component(
    component: list[PdfPreviewVisualPrimitive],
) -> list[PdfPreviewVisualBlockCandidate]:
    from . import _primitive_belongs_to_axis_box

    candidates: list[PdfPreviewVisualBlockCandidate] = []
    for seed_bbox in _find_axis_box_seed_bboxes_from_component(component):
        local_members = [
            member
            for member in component
            if _primitive_belongs_to_axis_box(member, seed_bbox)
        ]
        if not local_members:
            continue
        draw_orders = sorted({member.draw_order for member in local_members})
        source_roles = sorted({role for member in local_members for role in member.candidate_roles})
        candidates.append(
            PdfPreviewVisualBlockCandidate(
                page_number=component[0].page_number,
                candidate_type="axis_box",
                bounding_box=seed_bbox,
                primitive_draw_orders=draw_orders,
                source_roles=source_roles,
                child_cells=[],
            )
        )
    return _dedupe_visual_block_candidates(candidates)


def _find_axis_box_seed_bboxes_from_component(
    component: list[PdfPreviewVisualPrimitive],
) -> list[PdfBoundingBox]:
    from . import (
        _dedupe_seed_bboxes,
        _horizontal_line_matches_box_boundary,
        _primitive_line_axis_center,
        _primitive_line_orientation,
        _vertical_line_matches_box_boundary,
    )

    vertical_lines = sorted(
        (
            primitive
            for primitive in component
            if _primitive_line_orientation(primitive) == "vertical"
        ),
        key=lambda item: (
            _primitive_line_axis_center(item, "vertical"),
            item.bounding_box.bottom_pt,
            item.bounding_box.top_pt,
            item.draw_order,
        ),
    )
    horizontal_lines = sorted(
        (
            primitive
            for primitive in component
            if _primitive_line_orientation(primitive) == "horizontal"
        ),
        key=lambda item: (
            _primitive_line_axis_center(item, "horizontal"),
            item.bounding_box.left_pt,
            item.bounding_box.right_pt,
            item.draw_order,
        ),
    )
    if len(vertical_lines) < 2 or len(horizontal_lines) < 2:
        return []

    seed_bboxes: list[PdfBoundingBox] = []

    for left_index, left in enumerate(vertical_lines):
        left_x = _primitive_line_axis_center(left, "vertical")
        for right in vertical_lines[left_index + 1 :]:
            right_x = _primitive_line_axis_center(right, "vertical")
            if right_x - left_x < _VISUAL_BOX_SEED_MIN_SIZE_PT:
                continue

            supporting_horizontals = [
                horizontal
                for horizontal in horizontal_lines
                if _horizontal_line_matches_box_boundary(horizontal, left_x=left_x, right_x=right_x)
            ]
            if len(supporting_horizontals) < 2:
                continue

            for bottom_index, bottom in enumerate(supporting_horizontals):
                bottom_y = _primitive_line_axis_center(bottom, "horizontal")
                for top in supporting_horizontals[bottom_index + 1 :]:
                    top_y = _primitive_line_axis_center(top, "horizontal")
                    if top_y - bottom_y < _VISUAL_BOX_SEED_MIN_SIZE_PT:
                        continue
                    if not _vertical_line_matches_box_boundary(left, x=left_x, bottom_y=bottom_y, top_y=top_y):
                        continue
                    if not _vertical_line_matches_box_boundary(right, x=right_x, bottom_y=bottom_y, top_y=top_y):
                        continue
                    seed_bboxes.append(
                        PdfBoundingBox(
                            left_pt=left_x,
                            bottom_pt=bottom_y,
                            right_pt=right_x,
                            top_pt=top_y,
                        )
                    )

    return _dedupe_seed_bboxes(seed_bboxes)


def _build_non_box_line_candidates(
    line_primitives: list[PdfPreviewVisualPrimitive],
) -> list[PdfPreviewVisualBlockCandidate]:
    from . import _is_open_frame_component, _primitive_is_long_rule, _union_visual_primitive_bboxes

    candidates: list[PdfPreviewVisualBlockCandidate] = []
    for component in _connected_line_components(line_primitives):
        candidate_bbox = _union_visual_primitive_bboxes(component)
        if candidate_bbox is None:
            continue
        candidate_type = "semantic_line"
        if _is_open_frame_component(component):
            candidate_type = "axis_box" if _component_has_box_outline(candidate_bbox, component) else "open_frame"
        elif any(_primitive_is_long_rule(primitive) for primitive in component):
            candidate_type = "long_rule"

        candidates.append(
            PdfPreviewVisualBlockCandidate(
                page_number=component[0].page_number,
                candidate_type=candidate_type,
                bounding_box=candidate_bbox,
                primitive_draw_orders=sorted({primitive.draw_order for primitive in component}),
                source_roles=sorted({role for primitive in component for role in primitive.candidate_roles}),
                child_cells=[],
            )
        )
    return _dedupe_visual_block_candidates(candidates)


def _component_has_box_outline(
    candidate_bbox: PdfBoundingBox,
    component: list[PdfPreviewVisualPrimitive],
) -> bool:
    from . import _primitive_line_orientation

    width = candidate_bbox.right_pt - candidate_bbox.left_pt
    height = candidate_bbox.top_pt - candidate_bbox.bottom_pt
    if width < _VISUAL_FRAME_MIN_SIZE_PT or height < _VISUAL_FRAME_MIN_SIZE_PT:
        return False

    has_top = False
    has_bottom = False
    has_left = False
    has_right = False
    for primitive in component:
        orientation = _primitive_line_orientation(primitive)
        bbox = primitive.bounding_box
        if orientation == "horizontal":
            line_width = bbox.right_pt - bbox.left_pt
            y_center = (bbox.top_pt + bbox.bottom_pt) / 2.0
            if line_width >= width * _VISUAL_DIVIDER_SPAN_RATIO:
                if abs(y_center - candidate_bbox.top_pt) <= _VISUAL_TOUCH_TOLERANCE_PT:
                    has_top = True
                if abs(y_center - candidate_bbox.bottom_pt) <= _VISUAL_TOUCH_TOLERANCE_PT:
                    has_bottom = True
        elif orientation == "vertical":
            line_height = bbox.top_pt - bbox.bottom_pt
            x_center = (bbox.left_pt + bbox.right_pt) / 2.0
            if line_height >= height * _VISUAL_DIVIDER_SPAN_RATIO:
                if abs(x_center - candidate_bbox.left_pt) <= _VISUAL_TOUCH_TOLERANCE_PT:
                    has_left = True
                if abs(x_center - candidate_bbox.right_pt) <= _VISUAL_TOUCH_TOLERANCE_PT:
                    has_right = True
        if has_top and has_bottom and has_left and has_right:
            return True
    return False


def _dedupe_visual_block_candidates(
    candidates: list[PdfPreviewVisualBlockCandidate],
) -> list[PdfPreviewVisualBlockCandidate]:
    from . import _bbox_contains, _bbox_overlap_ratio

    if not candidates:
        return []

    kept: list[PdfPreviewVisualBlockCandidate] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            item.page_number,
            -((item.bounding_box.right_pt - item.bounding_box.left_pt) * (item.bounding_box.top_pt - item.bounding_box.bottom_pt)),
            item.candidate_type,
        ),
    ):
        duplicate = False
        for existing in kept:
            if existing.page_number != candidate.page_number:
                continue
            if _bbox_overlap_ratio(existing.bounding_box, candidate.bounding_box) >= 0.95:
                duplicate = True
                break
            if _bbox_contains(
                existing.bounding_box,
                candidate.bounding_box,
                tolerance_pt=_VISUAL_TOUCH_TOLERANCE_PT,
            ) and existing.candidate_type == candidate.candidate_type:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)

    kept.sort(key=lambda item: (item.page_number, item.bounding_box.top_pt, item.bounding_box.left_pt))
    return kept


def _suppress_boundary_semantic_lines(
    candidates: list[PdfPreviewVisualBlockCandidate],
) -> list[PdfPreviewVisualBlockCandidate]:
    if not candidates:
        return []

    structural_candidates = [
        candidate
        for candidate in candidates
        if candidate.candidate_type in {"axis_box", "open_frame"}
    ]
    if not structural_candidates:
        return candidates

    kept: list[PdfPreviewVisualBlockCandidate] = []
    for candidate in candidates:
        if candidate.candidate_type == "semantic_line" and any(
            _semantic_line_matches_structure_boundary(candidate, structure)
            for structure in structural_candidates
            if structure.page_number == candidate.page_number
        ):
            continue
        kept.append(candidate)
    return kept


def _semantic_line_matches_structure_boundary(
    line_candidate: PdfPreviewVisualBlockCandidate,
    structure_candidate: PdfPreviewVisualBlockCandidate,
) -> bool:
    from . import _line_like_bbox_orientation

    line_bbox = line_candidate.bounding_box
    structure_bbox = structure_candidate.bounding_box
    orientation = _line_like_bbox_orientation(line_bbox)
    if orientation is None:
        return False

    if orientation == "horizontal":
        line_span = max(line_bbox.right_pt - line_bbox.left_pt, 0.0)
        structure_span = max(structure_bbox.right_pt - structure_bbox.left_pt, 0.0)
        overlap = min(line_bbox.right_pt, structure_bbox.right_pt) - max(line_bbox.left_pt, structure_bbox.left_pt)
        if line_span <= 0.0 or structure_span <= 0.0 or overlap <= 0.0:
            return False
        overlap_ratio = overlap / min(line_span, structure_span)
        if overlap_ratio < _VISUAL_BOUNDARY_SUPPRESSION_OVERLAP_RATIO:
            return False
        y_center = (line_bbox.top_pt + line_bbox.bottom_pt) / 2.0
        return (
            abs(y_center - structure_bbox.top_pt) <= _VISUAL_BOUNDARY_SUPPRESSION_TOLERANCE_PT
            or abs(y_center - structure_bbox.bottom_pt) <= _VISUAL_BOUNDARY_SUPPRESSION_TOLERANCE_PT
        )

    line_span = max(line_bbox.top_pt - line_bbox.bottom_pt, 0.0)
    structure_span = max(structure_bbox.top_pt - structure_bbox.bottom_pt, 0.0)
    overlap = min(line_bbox.top_pt, structure_bbox.top_pt) - max(line_bbox.bottom_pt, structure_bbox.bottom_pt)
    if line_span <= 0.0 or structure_span <= 0.0 or overlap <= 0.0:
        return False
    overlap_ratio = overlap / min(line_span, structure_span)
    if overlap_ratio < _VISUAL_BOUNDARY_SUPPRESSION_OVERLAP_RATIO:
        return False
    x_center = (line_bbox.left_pt + line_bbox.right_pt) / 2.0
    return (
        abs(x_center - structure_bbox.left_pt) <= _VISUAL_BOUNDARY_SUPPRESSION_TOLERANCE_PT
        or abs(x_center - structure_bbox.right_pt) <= _VISUAL_BOUNDARY_SUPPRESSION_TOLERANCE_PT
    )


__all__ = [
    "_build_visual_block_candidates",
    "_connected_line_components",
    "_dedupe_line_primitives_for_graph",
    "_line_primitives_are_graph_duplicates",
    "_build_axis_box_candidates_from_component",
    "_find_axis_box_seed_bboxes_from_component",
    "_build_non_box_line_candidates",
    "_component_has_box_outline",
    "_dedupe_visual_block_candidates",
    "_suppress_boundary_semantic_lines",
    "_semantic_line_matches_structure_boundary",
]
