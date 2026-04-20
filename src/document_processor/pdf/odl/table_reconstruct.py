"""Visual-first table grid reconstruction.

Replaces the previous `table_split_plan` post-correction approach. Instead of
trusting ODL's row/col structure and patching splits onto it, we rebuild the
table grid directly from detected line primitives:

  1. For each ODL table bbox, collect horizontal/vertical line primitives
     (solid segments, segmented rules, axis-box edges) inside the bbox.
  2. Derive row boundaries from unique h-line y-coords, col boundaries from
     unique v-line x-coords (with proximity-merge to kill noise).
  3. Detect merged cells by checking whether each (i,j) cell's bottom/right
     border is actually drawn. Missing borders mean merge with neighbor.
  4. BFS connected merge directions into rectangular merge groups.

The adapter then maps each ODL raw cell's paragraphs into the merge group
whose grid-rect contains the cell's center, preserving ODL's text/reading
order while using the visually-faithful grid as ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..meta import PdfBoundingBox, coerce_bbox, coerce_int
from ..preview.models import PdfPreviewVisualPrimitive


_COORD_MERGE_TOLERANCE_PT = 2.0
_BORDER_AXIS_TOLERANCE_PT = 2.0
_BORDER_SNAP_TOLERANCE_PT = 3.0
_MIN_EDGE_BAND_PT = 4.0
_BORDER_COVERAGE_RATIO = 0.30


@dataclass(frozen=True)
class TableNodeKey:
    page_number: int
    reading_order_index: int | None
    left_pt: float
    bottom_pt: float
    right_pt: float
    top_pt: float


@dataclass(frozen=True)
class MergeGroup:
    """A rectangular group of grid cells that share one logical cell."""

    min_row: int  # 0-based, inclusive
    min_col: int
    max_row: int  # inclusive
    max_col: int

    @property
    def rowspan(self) -> int:
        return self.max_row - self.min_row + 1

    @property
    def colspan(self) -> int:
        return self.max_col - self.min_col + 1


@dataclass
class TableGrid:
    table_key: TableNodeKey
    h_y: list[float] = field(default_factory=list)  # row boundaries (PDF pt, bottom-to-top not required)
    v_x: list[float] = field(default_factory=list)  # col boundaries
    merge_groups: list[MergeGroup] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return max(len(self.h_y) - 1, 0)

    @property
    def col_count(self) -> int:
        return max(len(self.v_x) - 1, 0)

    def group_bbox(self, group: MergeGroup) -> PdfBoundingBox:
        left = self.v_x[group.min_col]
        right = self.v_x[group.max_col + 1]
        bottom = self.h_y[group.min_row]
        top = self.h_y[group.max_row + 1]
        return PdfBoundingBox(
            left_pt=left, bottom_pt=bottom, right_pt=right, top_pt=top
        )


def table_node_key(node: dict[str, Any]) -> TableNodeKey:
    bbox = coerce_bbox(node.get("bounding box")) or PdfBoundingBox(0.0, 0.0, 0.0, 0.0)
    return TableNodeKey(
        page_number=coerce_int(node.get("page number")) or 0,
        reading_order_index=coerce_int(node.get("reading order index")),
        left_pt=bbox.left_pt,
        bottom_pt=bbox.bottom_pt,
        right_pt=bbox.right_pt,
        top_pt=bbox.top_pt,
    )


def _union_bboxes(boxes: Iterable[PdfBoundingBox]) -> PdfBoundingBox | None:
    collected = list(boxes)
    if not collected:
        return None
    return PdfBoundingBox(
        left_pt=min(box.left_pt for box in collected),
        bottom_pt=min(box.bottom_pt for box in collected),
        right_pt=max(box.right_pt for box in collected),
        top_pt=max(box.top_pt for box in collected),
    )


def _descendant_bboxes(node: dict[str, Any]) -> list[PdfBoundingBox]:
    bboxes: list[PdfBoundingBox] = []

    def visit(current: Any) -> None:
        if not isinstance(current, dict):
            return
        bbox = coerce_bbox(current.get("bounding box")) or coerce_bbox(current.get("bbox"))
        if bbox is not None:
            bboxes.append(bbox)
        for key in ("kids", "spans", "runs"):
            items = current.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                visit(item)

    for key in ("kids", "spans", "runs"):
        items = node.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            visit(item)
    return bboxes


def _effective_bbox_from_descendants(
    node: dict[str, Any],
    fallback_bbox: PdfBoundingBox | None = None,
) -> PdfBoundingBox | None:
    bbox = coerce_bbox(node.get("bounding box")) or coerce_bbox(node.get("bbox"))
    if bbox is not None:
        return bbox

    descendant_bbox = _union_bboxes(_descendant_bboxes(node))
    if descendant_bbox is not None:
        return descendant_bbox

    return fallback_bbox


def _iter_table_fragments(raw_cell: dict[str, Any]) -> Iterable[dict[str, Any]]:
    def visit(node: Any) -> Iterable[dict[str, Any]]:
        if not isinstance(node, dict):
            return
        if node.get("type") == "paragraph":
            yield node
        kids = node.get("kids")
        if isinstance(kids, list):
            for kid in kids:
                yield from visit(kid)

    return visit(raw_cell)


def _find_group_for_bbox_center(bbox: PdfBoundingBox, grid: TableGrid) -> MergeGroup | None:
    center_x = (bbox.left_pt + bbox.right_pt) / 2.0
    center_y = (bbox.bottom_pt + bbox.top_pt) / 2.0

    matches: list[MergeGroup] = []
    for group in grid.merge_groups:
        group_bbox = grid.group_bbox(group)
        if (
            group_bbox.left_pt <= center_x <= group_bbox.right_pt
            and group_bbox.bottom_pt <= center_y <= group_bbox.top_pt
        ):
            matches.append(group)
            if len(matches) > 1:
                return None

    return matches[0] if matches else None


def assign_fragments_to_groups(
    raw_cells: list[dict[str, Any]],
    grid: TableGrid,
) -> dict[MergeGroup, list[dict[str, Any]]]:
    mapping: dict[MergeGroup, list[dict[str, Any]]] = {}

    for raw_cell in raw_cells:
        cell_bbox = coerce_bbox(raw_cell.get("bounding box"))
        for fragment in _iter_table_fragments(raw_cell):
            fragment_bbox = _effective_bbox_from_descendants(fragment, fallback_bbox=cell_bbox)
            if fragment_bbox is None:
                return {}
            group = _find_group_for_bbox_center(fragment_bbox, grid)
            if group is None:
                return {}
            mapping.setdefault(group, []).append(fragment)

    return mapping


# ---------- line collection ----------


def _is_horizontal_primitive(prim: PdfPreviewVisualPrimitive) -> bool:
    roles = set(prim.candidate_roles or ())
    return bool(
        {"horizontal_line_segment", "segmented_horizontal_rule", "long_horizontal_rule"} & roles
    ) or prim.object_type in {"segmented_horizontal_rule", "axis_box_edge_horizontal"}


def _is_vertical_primitive(prim: PdfPreviewVisualPrimitive) -> bool:
    roles = set(prim.candidate_roles or ())
    return bool(
        {"vertical_line_segment", "segmented_vertical_rule", "long_vertical_rule"} & roles
    ) or prim.object_type in {"segmented_vertical_rule", "axis_box_edge_vertical"}


def _bbox_inside(inner: PdfBoundingBox, outer: PdfBoundingBox, pad: float = 1.0) -> bool:
    return (
        inner.left_pt >= outer.left_pt - pad
        and inner.right_pt <= outer.right_pt + pad
        and inner.bottom_pt >= outer.bottom_pt - pad
        and inner.top_pt <= outer.top_pt + pad
    )


def _snap_axis_to_table_border(axis: float, lower: float, upper: float) -> float:
    if abs(axis - lower) <= _BORDER_SNAP_TOLERANCE_PT:
        return lower
    if abs(axis - upper) <= _BORDER_SNAP_TOLERANCE_PT:
        return upper
    return axis


def _suppress_narrow_edge_band(boundaries: list[float]) -> list[float]:
    if len(boundaries) < 3:
        return boundaries

    compact = list(boundaries)
    while len(compact) >= 3:
        lower_band = compact[1] - compact[0]
        upper_band = compact[-1] - compact[-2]
        if lower_band < _MIN_EDGE_BAND_PT:
            del compact[1]
            continue
        if upper_band < _MIN_EDGE_BAND_PT:
            del compact[-2]
            continue
        break
    return compact


def collect_lines(
    primitives: list[PdfPreviewVisualPrimitive],
    table_bbox: PdfBoundingBox,
) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """Return (h_lines, v_lines) inside the table bbox.

    h_lines: list of (axis_y, x_start, x_end)
    v_lines: list of (axis_x, y_start, y_end)
    The table's outer border is appended so the grid always closes.
    """
    h_lines: list[tuple[float, float, float]] = []
    v_lines: list[tuple[float, float, float]] = []

    for prim in primitives:
        bbox = prim.bounding_box
        if not _bbox_inside(bbox, table_bbox):
            continue
        is_h = _is_horizontal_primitive(prim)
        is_v = _is_vertical_primitive(prim)
        if is_h:
            y_axis = (bbox.top_pt + bbox.bottom_pt) / 2.0
            y_axis = _snap_axis_to_table_border(y_axis, table_bbox.bottom_pt, table_bbox.top_pt)
            h_lines.append((y_axis, bbox.left_pt, bbox.right_pt))
        if is_v:
            x_axis = (bbox.left_pt + bbox.right_pt) / 2.0
            x_axis = _snap_axis_to_table_border(x_axis, table_bbox.left_pt, table_bbox.right_pt)
            v_lines.append((x_axis, bbox.bottom_pt, bbox.top_pt))

    # Outer borders of the table itself.
    h_lines.append((table_bbox.top_pt, table_bbox.left_pt, table_bbox.right_pt))
    h_lines.append((table_bbox.bottom_pt, table_bbox.left_pt, table_bbox.right_pt))
    v_lines.append((table_bbox.left_pt, table_bbox.bottom_pt, table_bbox.top_pt))
    v_lines.append((table_bbox.right_pt, table_bbox.bottom_pt, table_bbox.top_pt))

    return h_lines, v_lines


# ---------- grid construction ----------


def _merge_close_coords(values: Iterable[float], tol: float) -> list[float]:
    sorted_values = sorted(values)
    if not sorted_values:
        return []
    groups: list[list[float]] = [[sorted_values[0]]]
    for v in sorted_values[1:]:
        if v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]


def _detect_merge_matrix(
    h_lines: list[tuple[float, float, float]],
    v_lines: list[tuple[float, float, float]],
    h_y: list[float],
    v_x: list[float],
) -> list[list[int]]:
    """Return merge flags per cell: 0=none, 1=merge-down, 2=merge-right, 3=both."""
    rows = len(h_y) - 1
    cols = len(v_x) - 1
    if rows <= 0 or cols <= 0:
        return []

    h_segs: dict[int, list[tuple[float, float]]] = {i: [] for i in range(len(h_y))}
    for y, x0, x1 in h_lines:
        for i, grid_y in enumerate(h_y):
            if abs(y - grid_y) <= _BORDER_AXIS_TOLERANCE_PT:
                h_segs[i].append((x0, x1))
                break

    v_segs: dict[int, list[tuple[float, float]]] = {j: [] for j in range(len(v_x))}
    for x, y0, y1 in v_lines:
        for j, grid_x in enumerate(v_x):
            if abs(x - grid_x) <= _BORDER_AXIS_TOLERANCE_PT:
                v_segs[j].append((y0, y1))
                break

    def covers(segments: list[tuple[float, float]], lo: float, hi: float) -> bool:
        span = hi - lo
        if span <= 0:
            return False
        for s0, s1 in segments:
            if s1 < lo + _BORDER_AXIS_TOLERANCE_PT or s0 > hi - _BORDER_AXIS_TOLERANCE_PT:
                continue
            overlap = min(s1, hi) - max(s0, lo)
            if overlap >= span * _BORDER_COVERAGE_RATIO:
                return True
        return False

    matrix = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            left_x, right_x = v_x[j], v_x[j + 1]
            bot_y, top_y = h_y[i], h_y[i + 1]
            if i < rows - 1:
                if not covers(h_segs.get(i + 1, []), left_x, right_x):
                    matrix[i][j] = 1
            if j < cols - 1:
                if not covers(v_segs.get(j + 1, []), bot_y, top_y):
                    matrix[i][j] = 3 if matrix[i][j] == 1 else 2
    return matrix


def _build_merge_groups(merge_matrix: list[list[int]]) -> list[MergeGroup] | None:
    if not merge_matrix:
        return []
    rows = len(merge_matrix)
    cols = len(merge_matrix[0])
    visited = [[False] * cols for _ in range(rows)]
    groups: list[MergeGroup] = []

    for i in range(rows):
        for j in range(cols):
            if visited[i][j]:
                continue
            queue = [(i, j)]
            visited[i][j] = True
            min_r = max_r = i
            min_c = max_c = j
            component_cells: list[tuple[int, int]] = []
            while queue:
                r, c = queue.pop(0)
                component_cells.append((r, c))
                min_r, max_r = min(min_r, r), max(max_r, r)
                min_c, max_c = min(min_c, c), max(max_c, c)
                flag = merge_matrix[r][c]
                if r < rows - 1 and flag in (1, 3) and not visited[r + 1][c]:
                    visited[r + 1][c] = True
                    queue.append((r + 1, c))
                if c < cols - 1 and flag in (2, 3) and not visited[r][c + 1]:
                    visited[r][c + 1] = True
                    queue.append((r, c + 1))
            expected_cells = (max_r - min_r + 1) * (max_c - min_c + 1)
            if len(component_cells) != expected_cells:
                return None
            groups.append(
                MergeGroup(min_row=min_r, min_col=min_c, max_row=max_r, max_col=max_c)
            )
    return groups


def reconstruct_table_grid(
    node: dict[str, Any],
    primitives: list[PdfPreviewVisualPrimitive],
) -> TableGrid | None:
    """Reconstruct the grid of a single ODL table node from visual primitives.

    Returns None when the bbox is missing or the grid collapses to 0 rows/cols.
    """
    table_bbox = coerce_bbox(node.get("bounding box"))
    if table_bbox is None:
        return None

    page_number = coerce_int(node.get("page number"))
    if page_number is None:
        return None
    primitives = [p for p in primitives if p.page_number == page_number]

    h_lines, v_lines = collect_lines(primitives, table_bbox)
    h_y = _merge_close_coords((y for y, _, _ in h_lines), _COORD_MERGE_TOLERANCE_PT)
    v_x = _merge_close_coords((x for x, _, _ in v_lines), _COORD_MERGE_TOLERANCE_PT)
    h_y = _suppress_narrow_edge_band(h_y)
    v_x = _suppress_narrow_edge_band(v_x)

    if len(h_y) < 2 or len(v_x) < 2:
        return None

    matrix = _detect_merge_matrix(h_lines, v_lines, h_y, v_x)
    groups = _build_merge_groups(matrix)
    if not groups:
        return None

    return TableGrid(
        table_key=table_node_key(node),
        h_y=h_y,
        v_x=v_x,
        merge_groups=groups,
    )


__all__ = [
    "assign_fragments_to_groups",
    "MergeGroup",
    "TableGrid",
    "TableNodeKey",
    "collect_lines",
    "reconstruct_table_grid",
    "table_node_key",
]
