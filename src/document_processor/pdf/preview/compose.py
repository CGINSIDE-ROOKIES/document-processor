"""Flow composition and layout-table promotion helpers for PDF preview."""

from __future__ import annotations

from typing import Any

from ...models import DocIR, ImageIR, PageInfo, ParagraphIR, RunIR, TableCellIR, TableIR
from ...style_types import CellStyleInfo, TableStyleInfo
from ..meta import PdfBoundingBox
from .layout import (
    _build_logical_pages_for_page,
    _collapse_image_strip_paragraphs,
    _bbox_region_type,
    _flow_regions_for_logical_page,
    _logical_page_page_info,
    _logical_page_paragraphs,
    _logical_page_preview_context,
    _paragraph_bbox,
    _paragraph_region_type,
    _paragraph_union_bbox,
)
from .models import (
    PdfLayoutRegion,
    PdfPreviewContext,
    PdfPreviewVisualBlockCandidate,
    _AssignedCandidate,
    _AssignedCandidateGroup,
    _CANDIDATE_ASSIGN_TOLERANCE_PT,
    _LAYOUT_TABLE_ALIGNMENT_OVERLAP_RATIO,
    _LAYOUT_TABLE_BOUNDARY_TOLERANCE_PT,
    _LAYOUT_TABLE_GROUP_GAP_TOLERANCE_PT,
    _LogicalPageComposition,
    _PreviewCompositionEntry,
    _PreviewRenderNode,
)
from .shared import (
    _bbox_area,
    _bbox_center,
    _bbox_contains,
    _bbox_intersection,
    _bbox_touches_or_near,
    _shared_bbox_distance,
    _shared_page_content_margins,
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
