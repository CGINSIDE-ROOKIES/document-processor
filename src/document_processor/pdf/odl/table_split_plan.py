"""Raw ODL table split plan generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..meta import PdfBoundingBox, coerce_bbox, coerce_int
from ..preview.analyze import extract_pdfium_table_rule_primitives
from ..preview.models import PdfPreviewVisualPrimitive

_OUTER_BORDER_TOLERANCE_PT = 4.0
_MIN_RULE_SPAN_RATIO = 0.45
_BOUNDARY_DEDUPE_TOLERANCE_PT = 2.0


@dataclass(frozen=True)
class CellKey:
    row_index: int
    col_index: int
    rowspan: int
    colspan: int


@dataclass(frozen=True)
class TableNodeKey:
    page_number: int
    reading_order_index: int | None
    left_pt: float
    bottom_pt: float
    right_pt: float
    top_pt: float


@dataclass(frozen=True)
class BoundaryEvent:
    source_index: int
    axis_pt: float
    supporting_cells: frozenset[CellKey]

    @property
    def source_row(self) -> int:
        return self.source_index

    @property
    def source_col(self) -> int:
        return self.source_index


@dataclass(frozen=True)
class CellSplitPlan:
    orientation: str
    axis_pt: float


@dataclass
class TableSplitPlan:
    table_key: TableNodeKey
    row_events: list[BoundaryEvent] = field(default_factory=list)
    column_events: list[BoundaryEvent] = field(default_factory=list)
    cell_splits: dict[CellKey, CellSplitPlan] = field(default_factory=dict)


def table_node_key(node: dict[str, Any]) -> TableNodeKey:
    bbox = coerce_bbox(node.get("bounding box")) or PdfBoundingBox(
        left_pt=0.0,
        bottom_pt=0.0,
        right_pt=0.0,
        top_pt=0.0,
    )
    return TableNodeKey(
        page_number=coerce_int(node.get("page number")) or 0,
        reading_order_index=coerce_int(node.get("reading order index")),
        left_pt=bbox.left_pt,
        bottom_pt=bbox.bottom_pt,
        right_pt=bbox.right_pt,
        top_pt=bbox.top_pt,
    )


def build_table_split_plan_for_table_node(
    node: dict[str, Any],
    *,
    primitives: list[PdfPreviewVisualPrimitive],
) -> TableSplitPlan | None:
    table_bbox = coerce_bbox(node.get("bounding box"))
    page_number = coerce_int(node.get("page number"))
    if table_bbox is None or page_number is None:
        return None

    proposals: dict[CellKey, tuple[str, BoundaryEvent]] = {}
    ambiguous_cells: set[CellKey] = set()

    for primitive in _segmented_primitives_within_table_bbox(table_bbox, primitives):
        if primitive.page_number != page_number:
            continue
        orientation = _primitive_orientation(primitive)
        for cell in _iter_raw_cells(node):
            proposal = _proposal_for_crossed_cell(
                cell,
                primitive=primitive,
                orientation=orientation,
            )
            if proposal is None:
                continue
            cell_key, boundary_event = proposal
            if cell_key in ambiguous_cells:
                continue
            existing = proposals.get(cell_key)
            if existing is None:
                proposals[cell_key] = (orientation, boundary_event)
                continue
            existing_orientation, existing_event = existing
            if (
                existing_orientation == orientation
                and abs(existing_event.axis_pt - boundary_event.axis_pt)
                <= _BOUNDARY_DEDUPE_TOLERANCE_PT
            ):
                continue
            proposals.pop(cell_key, None)
            ambiguous_cells.add(cell_key)

    row_events: list[BoundaryEvent] = []
    column_events: list[BoundaryEvent] = []
    cell_splits: dict[CellKey, CellSplitPlan] = {}
    for cell_key, (orientation, boundary_event) in proposals.items():
        cell_splits[cell_key] = CellSplitPlan(
            orientation=orientation,
            axis_pt=boundary_event.axis_pt,
        )
        if orientation == "horizontal":
            row_events.append(boundary_event)
        else:
            column_events.append(boundary_event)
    row_events = _dedupe_boundary_events(row_events)
    column_events = _dedupe_boundary_events(column_events)
    if not row_events and not column_events:
        return None

    return TableSplitPlan(
        table_key=table_node_key(node),
        row_events=row_events,
        column_events=column_events,
        cell_splits=cell_splits,
    )


def build_table_split_plans(
    raw_document: dict[str, Any],
    *,
    pdf_path: str | Path,
    page_numbers: Iterable[int] | None = None,
) -> dict[TableNodeKey, TableSplitPlan]:
    resolved_pdf_path = Path(pdf_path).expanduser()
    if not resolved_pdf_path.exists():
        return {}

    tables_by_page = _collect_table_nodes_by_page(raw_document)
    if page_numbers is not None:
        requested_pages = {int(page_number) for page_number in page_numbers}
        tables_by_page = {
            page_number: tables
            for page_number, tables in tables_by_page.items()
            if page_number in requested_pages
        }
    if not tables_by_page:
        return {}

    try:
        import pypdfium2 as pdfium
    except Exception:
        return {}

    try:
        document = pdfium.PdfDocument(str(resolved_pdf_path))
    except Exception:
        return {}
    try:
        page_count = _document_page_count(document)
        plans: dict[TableNodeKey, TableSplitPlan] = {}
        for page_number, tables in tables_by_page.items():
            if page_number <= 0 or page_number > page_count:
                continue
            primitives = extract_pdfium_table_rule_primitives(
                document[page_number - 1],
                page_number=page_number,
            )
            for table_node in tables:
                plan = build_table_split_plan_for_table_node(table_node, primitives=primitives)
                if plan is not None:
                    plans[plan.table_key] = plan
        return plans
    finally:
        document.close()


def _iter_raw_cells(node: dict[str, Any]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for row in node.get("rows", []) or []:
        for cell in row.get("cells", []) or []:
            if isinstance(cell, dict):
                cells.append(cell)
    return cells


def _segmented_primitives_within_table_bbox(
    table_bbox: PdfBoundingBox,
    primitives: list[PdfPreviewVisualPrimitive],
) -> list[PdfPreviewVisualPrimitive]:
    filtered: list[PdfPreviewVisualPrimitive] = []
    table_width = max(table_bbox.right_pt - table_bbox.left_pt, 0.0)
    table_height = max(table_bbox.top_pt - table_bbox.bottom_pt, 0.0)
    for primitive in primitives:
        if not _primitive_is_segmented_rule(primitive):
            continue
        bbox = primitive.bounding_box
        if not _bbox_within(bbox, table_bbox):
            continue
        orientation = _primitive_orientation(primitive)
        if orientation == "horizontal":
            axis_pt = (bbox.bottom_pt + bbox.top_pt) / 2.0
            if axis_pt <= table_bbox.bottom_pt + _OUTER_BORDER_TOLERANCE_PT:
                continue
            if axis_pt >= table_bbox.top_pt - _OUTER_BORDER_TOLERANCE_PT:
                continue
            if table_width <= 0.0:
                continue
            span_ratio = (bbox.right_pt - bbox.left_pt) / table_width
        else:
            axis_pt = (bbox.left_pt + bbox.right_pt) / 2.0
            if axis_pt <= table_bbox.left_pt + _OUTER_BORDER_TOLERANCE_PT:
                continue
            if axis_pt >= table_bbox.right_pt - _OUTER_BORDER_TOLERANCE_PT:
                continue
            if table_height <= 0.0:
                continue
            span_ratio = (bbox.top_pt - bbox.bottom_pt) / table_height
        if span_ratio < _MIN_RULE_SPAN_RATIO:
            continue
        filtered.append(primitive)
    return sorted(filtered, key=lambda item: item.draw_order)


def _primitive_orientation(primitive: PdfPreviewVisualPrimitive) -> str:
    roles = set(primitive.candidate_roles)
    if "segmented_horizontal_rule" in roles:
        return "horizontal"
    if "segmented_vertical_rule" in roles:
        return "vertical"
    if primitive.object_type == "segmented_horizontal_rule":
        return "horizontal"
    if primitive.object_type == "segmented_vertical_rule":
        return "vertical"
    bbox = primitive.bounding_box
    width = max(bbox.right_pt - bbox.left_pt, 0.0)
    height = max(bbox.top_pt - bbox.bottom_pt, 0.0)
    return "horizontal" if width >= height else "vertical"


def _proposal_for_crossed_cell(
    cell: dict[str, Any],
    *,
    primitive: PdfPreviewVisualPrimitive,
    orientation: str,
) -> tuple[CellKey, BoundaryEvent] | None:
    cell_bbox = coerce_bbox(cell.get("bounding box"))
    if cell_bbox is None:
        return None

    axis_pt = (
        (primitive.bounding_box.bottom_pt + primitive.bounding_box.top_pt) / 2.0
        if orientation == "horizontal"
        else (primitive.bounding_box.left_pt + primitive.bounding_box.right_pt) / 2.0
    )
    if orientation == "horizontal":
        if not (cell_bbox.bottom_pt < axis_pt < cell_bbox.top_pt):
            return None
    else:
        if not (cell_bbox.left_pt < axis_pt < cell_bbox.right_pt):
            return None

    text_boxes = _cell_text_boxes(cell)
    before, after = _split_text_boxes(text_boxes, axis_pt=axis_pt, orientation=orientation)
    if not before or not after:
        return None

    cell_key = CellKey(
        row_index=coerce_int(cell.get("row number")) or 1,
        col_index=coerce_int(cell.get("column number")) or 1,
        rowspan=max(coerce_int(cell.get("row span")) or 1, 1),
        colspan=max(coerce_int(cell.get("column span")) or 1, 1),
    )
    can_split = (orientation == "horizontal" and cell_key.rowspan == 1) or (
        orientation == "vertical" and cell_key.colspan == 1
    )
    if not can_split:
        return None

    event = BoundaryEvent(
        source_index=cell_key.row_index if orientation == "horizontal" else cell_key.col_index,
        axis_pt=axis_pt,
        supporting_cells=frozenset({cell_key}),
    )
    return cell_key, event


def _split_text_boxes(
    text_boxes: list[PdfBoundingBox],
    *,
    axis_pt: float,
    orientation: str,
) -> tuple[list[PdfBoundingBox], list[PdfBoundingBox]]:
    before: list[PdfBoundingBox] = []
    after: list[PdfBoundingBox] = []
    for bbox in text_boxes:
        if orientation == "horizontal":
            center = (bbox.bottom_pt + bbox.top_pt) / 2.0
        else:
            center = (bbox.left_pt + bbox.right_pt) / 2.0
        if center < axis_pt:
            before.append(bbox)
        else:
            after.append(bbox)
    return before, after


def _dedupe_boundary_events(events: list[BoundaryEvent]) -> list[BoundaryEvent]:
    if not events:
        return []

    merged: list[BoundaryEvent] = []
    for event in sorted(events, key=lambda item: (item.source_index, item.axis_pt)):
        if not merged:
            merged.append(event)
            continue
        previous = merged[-1]
        if (
            previous.source_index == event.source_index
            and abs(previous.axis_pt - event.axis_pt) <= _BOUNDARY_DEDUPE_TOLERANCE_PT
        ):
            merged[-1] = BoundaryEvent(
                source_index=previous.source_index,
                axis_pt=(previous.axis_pt + event.axis_pt) / 2.0,
                supporting_cells=previous.supporting_cells | event.supporting_cells,
            )
            continue
        merged.append(event)
    return merged


def _collect_table_nodes_by_page(raw_document: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "table":
                page_number = coerce_int(node.get("page number"))
                if page_number is not None:
                    grouped.setdefault(page_number, []).append(node)
            for value in node.values():
                visit(value)
            return
        if isinstance(node, list):
            for item in node:
                visit(item)

    visit(raw_document)
    return grouped


def _cell_text_boxes(cell: dict[str, Any]) -> list[PdfBoundingBox]:
    boxes: list[PdfBoundingBox] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            bbox = coerce_bbox(value.get("bounding box"))
            content = value.get("content")
            if bbox is not None and isinstance(content, str) and content.strip():
                boxes.append(bbox)
            for child in value.values():
                visit(child)
            return
        if isinstance(value, list):
            for item in value:
                visit(item)

    visit(cell.get("kids", []))
    return boxes


def _bbox_within(inner: PdfBoundingBox, outer: PdfBoundingBox) -> bool:
    return (
        inner.left_pt >= outer.left_pt
        and inner.right_pt <= outer.right_pt
        and inner.bottom_pt >= outer.bottom_pt
        and inner.top_pt <= outer.top_pt
    )


def _primitive_is_segmented_rule(primitive: PdfPreviewVisualPrimitive) -> bool:
    roles = set(primitive.candidate_roles)
    return (
        "segmented_horizontal_rule" in roles
        or "segmented_vertical_rule" in roles
        or primitive.object_type in {"segmented_horizontal_rule", "segmented_vertical_rule"}
    )


def _document_page_count(document: Any) -> int:
    page_count = getattr(document, "page_count", None)
    if isinstance(page_count, int) and page_count > 0:
        return page_count
    try:
        return len(document)
    except TypeError:
        return 0


__all__ = [
    "BoundaryEvent",
    "CellKey",
    "CellSplitPlan",
    "TableNodeKey",
    "TableSplitPlan",
    "build_table_split_plan_for_table_node",
    "build_table_split_plans",
    "table_node_key",
]
