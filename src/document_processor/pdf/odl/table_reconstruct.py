"""Dotted-rule cell-split preprocessor for ODL raw tables.

ODL parses solid grid lines well but misses dotted/dashed grid lines inside
tables. This module detects dotted rules via pdfium visual primitives
(`segmented_horizontal_rule` / `segmented_vertical_rule`) and rewrites the
raw ODL table so its `rows`/`cells`/`grid boundaries` include the extra
splits.

Detection is per-cell: a dotted rule is treated as a split only inside
cells whose interior it actually crosses. This matters for merged cells —
a horizontal dotted rule that sits inside the right-hand detail columns
but does not extend across the left-hand category cell must split the
detail rows while leaving the category cell intact with a larger rowspan.

Rebuild preserves merged cells: cells not crossed by any new boundary stay
as one sub-cell with an expanded rowspan/colspan, while cells crossed by
new boundaries produce multiple sub-cells with paragraphs distributed by
bbox center.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Iterable

from ..meta import PdfBoundingBox, coerce_bbox, coerce_float, coerce_int
from ..preview.analyze import extract_pdfium_table_rule_primitives
from ..preview.models import PdfPreviewVisualPrimitive

_AXIS_MERGE_TOL_PT = 2.0
_INTERIOR_PAD_PT = 2.0
_CELL_COVERAGE_RATIO = 0.7
_BBOX_PAD_PT = 1.0


def preprocess_dotted_rule_splits(
    raw_document: dict[str, Any],
    *,
    pdf_path: str | Path,
    page_numbers: Iterable[int] | None = None,
) -> None:
    """Mutate `raw_document` in place so tables include dotted-rule splits."""
    resolved_pdf_path = Path(pdf_path).expanduser()
    if not resolved_pdf_path.exists():
        return
    tables_by_page = _collect_table_nodes_by_page(raw_document)
    if page_numbers is not None:
        wanted = {int(p) for p in page_numbers}
        tables_by_page = {p: t for p, t in tables_by_page.items() if p in wanted}
    if not tables_by_page:
        return
    try:
        import pypdfium2 as pdfium
    except Exception:
        return
    try:
        document = pdfium.PdfDocument(str(resolved_pdf_path))
    except Exception:
        return
    try:
        page_count = _document_page_count(document)
        for page_number, tables in tables_by_page.items():
            if page_number <= 0 or page_number > page_count:
                continue
            primitives = extract_pdfium_table_rule_primitives(
                document[page_number - 1],
                page_number=page_number,
            )
            dotted_h = [p for p in primitives if p.object_type == "segmented_horizontal_rule"]
            dotted_v = [p for p in primitives if p.object_type == "segmented_vertical_rule"]
            if not dotted_h and not dotted_v:
                continue
            # A dotted rule belongs to the innermost enclosing table. When
            # processing one table, strip rules that fall inside any nested
            # table on the same page — those belong to that nested table's
            # own turn in this loop.
            table_bboxes = [
                (t, coerce_bbox(t.get("bounding box"))) for t in tables
            ]
            # Process innermost tables first: a shallow copy of an already-
            # rewritten nested table will carry the new ``rows`` reference
            # into the outer table's rebuilt cells. Sorting by enclosing-
            # depth (most enclosed → deepest → processed first) keeps this
            # invariant without relying on depth-first visit order.
            ordered_tables = sorted(
                tables,
                key=lambda t: -_enclosing_depth(t, table_bboxes),
            )
            for table in ordered_tables:
                outer_bbox = coerce_bbox(table.get("bounding box"))
                nested_bboxes = [
                    other_bbox
                    for other, other_bbox in table_bboxes
                    if other is not table
                    and other_bbox is not None
                    and outer_bbox is not None
                    and _bbox_encloses(outer_bbox, other_bbox)
                ]
                table_dotted_h = _rules_outside(dotted_h, nested_bboxes)
                table_dotted_v = _rules_outside(dotted_v, nested_bboxes)
                _apply_dotted_splits(table, table_dotted_h, table_dotted_v)
    finally:
        document.close()


def _enclosing_depth(
    table: dict[str, Any],
    table_bboxes: list[tuple[dict[str, Any], PdfBoundingBox | None]],
) -> int:
    self_bbox = coerce_bbox(table.get("bounding box"))
    if self_bbox is None:
        return 0
    return sum(
        1
        for other, other_bbox in table_bboxes
        if other is not table
        and other_bbox is not None
        and _bbox_encloses(other_bbox, self_bbox)
    )


def _bbox_encloses(outer: PdfBoundingBox, inner: PdfBoundingBox, pad: float = 1.0) -> bool:
    return (
        inner.left_pt >= outer.left_pt - pad
        and inner.right_pt <= outer.right_pt + pad
        and inner.bottom_pt >= outer.bottom_pt - pad
        and inner.top_pt <= outer.top_pt + pad
    )


def _rules_outside(
    rules: list[PdfPreviewVisualPrimitive],
    exclude_bboxes: list[PdfBoundingBox],
) -> list[PdfPreviewVisualPrimitive]:
    if not exclude_bboxes:
        return rules
    kept: list[PdfPreviewVisualPrimitive] = []
    for rule in rules:
        b = rule.bounding_box
        cx = (b.left_pt + b.right_pt) / 2.0
        cy = (b.bottom_pt + b.top_pt) / 2.0
        if any(
            ex.left_pt <= cx <= ex.right_pt and ex.bottom_pt <= cy <= ex.top_pt
            for ex in exclude_bboxes
        ):
            continue
        kept.append(rule)
    return kept


def _collect_table_nodes_by_page(root: Any) -> dict[int, list[dict[str, Any]]]:
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

    visit(root)
    return grouped


def _document_page_count(document: Any) -> int:
    page_count = getattr(document, "page_count", None)
    if isinstance(page_count, int) and page_count > 0:
        return page_count
    try:
        return len(document)
    except TypeError:
        return 0


def _apply_dotted_splits(
    table: dict[str, Any],
    dotted_h: list[PdfPreviewVisualPrimitive],
    dotted_v: list[PdfPreviewVisualPrimitive],
) -> None:
    table_bbox = coerce_bbox(table.get("bounding box"))
    if table_bbox is None:
        return

    raw_cells: list[dict[str, Any]] = []
    for row in table.get("rows", []) or []:
        if not isinstance(row, dict):
            continue
        for cell in row.get("cells", []) or []:
            if isinstance(cell, dict) and coerce_bbox(cell.get("bounding box")) is not None:
                raw_cells.append(cell)
    if not raw_cells:
        return

    existing_ys = _boundary_values(table.get("grid row boundaries"))
    existing_xs = _boundary_values(table.get("grid column boundaries"))
    if len(existing_ys) < 2 or len(existing_xs) < 2:
        existing_ys, existing_xs = _reconstruct_boundaries_from_cells(raw_cells)
    if len(existing_ys) < 2 or len(existing_xs) < 2:
        return

    # Pass 1 — vertical splits per cell. A vertical dotted rule defines a
    # new column divider inside a cell when its x-axis value sits strictly
    # interior to the cell and its extent covers most of the cell's height.
    cell_x_splits: dict[int, list[float]] = {}
    for idx, cell in enumerate(raw_cells):
        bbox = coerce_bbox(cell["bounding box"])
        if bbox is None:
            continue
        cell_x_splits[idx] = _cell_split_points(bbox, dotted_v, axis="x")

    # Pass 2 — horizontal splits per (cell, x sub-band). A horizontal rule
    # only splits the sub-column(s) it actually crosses: a rule confined to
    # the right sub-column of a cell must not split the left sub-column
    # (which typically carries a rowspan label).
    cell_x_bands = _compute_cell_x_bands(raw_cells, cell_x_splits)
    sub_rect_y_splits: dict[tuple[int, int], list[float]] = {}
    for idx, cell in enumerate(raw_cells):
        bbox = coerce_bbox(cell["bounding box"])
        if bbox is None:
            continue
        for band_idx, (x_lo, x_hi) in enumerate(cell_x_bands.get(idx, [])):
            sub_bbox = PdfBoundingBox(
                left_pt=x_lo,
                bottom_pt=bbox.bottom_pt,
                right_pt=x_hi,
                top_pt=bbox.top_pt,
            )
            sub_rect_y_splits[(idx, band_idx)] = _cell_split_points(
                sub_bbox, dotted_h, axis="y"
            )

    # Aggregate into global boundary sets. Every detected split — regardless
    # of which sub-rect produced it — contributes to the shared row/column
    # grid, but rebuild below uses each sub-rect's own splits when deciding
    # how to cut that sub-rect.
    all_x_splits = _collect_new_boundaries(cell_x_splits, existing=existing_xs)
    aggregated_ys: list[float] = []
    for ys in sub_rect_y_splits.values():
        aggregated_ys.extend(ys)
    aggregated_ys = _dedupe_close(sorted(aggregated_ys), _AXIS_MERGE_TOL_PT)
    all_y_splits = [
        y
        for y in aggregated_ys
        if not any(abs(y - e) <= _INTERIOR_PAD_PT for e in existing_ys)
    ]
    if not all_y_splits and not all_x_splits:
        return

    merged_ys = _dedupe_close(sorted(existing_ys + all_y_splits), _AXIS_MERGE_TOL_PT)
    merged_xs = _dedupe_close(sorted(existing_xs + all_x_splits), _AXIS_MERGE_TOL_PT)

    cell_x_splits = {
        idx: _snap_values(splits, merged_xs) for idx, splits in cell_x_splits.items()
    }
    # Recompute x-bands after snapping (snap may collapse close values).
    cell_x_bands = _compute_cell_x_bands(raw_cells, cell_x_splits)
    sub_rect_y_splits = {
        key: _snap_values(ys, merged_ys) for key, ys in sub_rect_y_splits.items()
    }

    # Pre-split leaves whose bbox straddles sub-band boundaries when their
    # content decomposes into exactly as many logical units as the number of
    # new sub-bands crossing the leaf. This recovers rows that ODL merged
    # into a single leaf (e.g., "④ ... § 1개사 내외" collapsed into one
    # list_item content + bbox).
    unit_separators = _learn_unit_separators(raw_cells)
    for idx, cell in enumerate(raw_cells):
        cell_y_splits_union = sorted({
            y
            for (cell_idx, _band_idx), ys in sub_rect_y_splits.items()
            if cell_idx == idx
            for y in ys
        })
        if not cell_y_splits_union or not unit_separators:
            continue
        _presplit_merged_leaves(cell, cell_y_splits_union, unit_separators)

    new_rows = _rebuild_rows(
        raw_cells,
        cell_x_bands=cell_x_bands,
        sub_rect_y_splits=sub_rect_y_splits,
        ys=merged_ys,
        xs=merged_xs,
    )
    if not new_rows:
        return

    table["rows"] = new_rows
    # ODL's convention for `grid row boundaries` is descending (top-down).
    table["grid row boundaries"] = list(reversed(merged_ys))
    table["grid column boundaries"] = list(merged_xs)
    table["number of rows"] = max(len(merged_ys) - 1, 0)
    table["number of columns"] = max(len(merged_xs) - 1, 0)


def _boundary_values(raw: Any) -> list[float]:
    if not isinstance(raw, list):
        return []
    values: list[float] = []
    for item in raw:
        f = coerce_float(item)
        if f is not None:
            values.append(f)
    values.sort()
    return _dedupe_close(values, _AXIS_MERGE_TOL_PT)


def _reconstruct_boundaries_from_cells(
    cells: list[dict[str, Any]],
) -> tuple[list[float], list[float]]:
    ys: list[float] = []
    xs: list[float] = []
    for cell in cells:
        bbox = coerce_bbox(cell.get("bounding box"))
        if bbox is None:
            continue
        ys.extend([bbox.bottom_pt, bbox.top_pt])
        xs.extend([bbox.left_pt, bbox.right_pt])
    return (
        _dedupe_close(sorted(ys), _AXIS_MERGE_TOL_PT),
        _dedupe_close(sorted(xs), _AXIS_MERGE_TOL_PT),
    )


def _cell_split_points(
    bbox: PdfBoundingBox,
    rules: list[PdfPreviewVisualPrimitive],
    *,
    axis: str,
) -> list[float]:
    """Return dotted-rule axis values that crack this cell's interior."""
    if not rules:
        return []
    points: list[float] = []
    for rule in rules:
        rb = rule.bounding_box
        if axis == "y":
            rule_axis = (rb.top_pt + rb.bottom_pt) / 2.0
            rule_lo, rule_hi = rb.left_pt, rb.right_pt
            cell_axis_lo, cell_axis_hi = bbox.bottom_pt, bbox.top_pt
            cell_span_lo, cell_span_hi = bbox.left_pt, bbox.right_pt
        else:
            rule_axis = (rb.left_pt + rb.right_pt) / 2.0
            rule_lo, rule_hi = rb.bottom_pt, rb.top_pt
            cell_axis_lo, cell_axis_hi = bbox.left_pt, bbox.right_pt
            cell_span_lo, cell_span_hi = bbox.bottom_pt, bbox.top_pt
        if not (
            cell_axis_lo + _INTERIOR_PAD_PT < rule_axis < cell_axis_hi - _INTERIOR_PAD_PT
        ):
            continue
        cell_span = cell_span_hi - cell_span_lo
        if cell_span <= 0:
            continue
        overlap = min(rule_hi, cell_span_hi) - max(rule_lo, cell_span_lo)
        if overlap >= _CELL_COVERAGE_RATIO * cell_span:
            points.append(rule_axis)
    return _dedupe_close(sorted(points), _AXIS_MERGE_TOL_PT)


def _learn_unit_separators(nodes: list[dict[str, Any]]) -> set[str]:
    """Discover paragraph-leading bullet-like characters used in these nodes.

    A character qualifies as a unit separator if it appears as the first non-
    whitespace character of some leaf's ``content``, is followed by whitespace,
    and belongs to a Unicode punctuation / symbol / other-number category
    (catches ``§`` ``▪`` ``•`` ``※`` ``①②③`` etc. while excluding letters and
    plain ASCII digits). Learning from the document itself avoids hard-coding
    a fixed bullet set.
    """
    seps: set[str] = set()

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        content = node.get("content")
        if isinstance(content, str):
            stripped = content.lstrip()
            if len(stripped) >= 2 and stripped[1].isspace():
                ch = stripped[0]
                if _is_unit_marker(ch):
                    seps.add(ch)
        for key in _CHILD_KEYS:
            items = node.get(key)
            if isinstance(items, list):
                for item in items:
                    visit(item)

    for n in nodes:
        visit(n)
    return seps


def _is_unit_marker(ch: str) -> bool:
    if ch.isalpha():
        return False
    if ch.isascii() and ch.isdigit():
        return False
    cat = unicodedata.category(ch)
    # S* Symbol (math, other, modifier), P* Punctuation, No "Other Number"
    # (covers circled digits like ①). Nd (decimal digit) is handled by the
    # isascii-isdigit check above, but non-ASCII decimal digits are also
    # excluded via that check.
    return bool(cat) and (cat[0] == "S" or cat[0] == "P" or cat == "No")


def _split_content_into_units(content: str, separators: set[str]) -> list[str]:
    """Split content at ``<whitespace><separator>`` boundaries.

    The very first character of the content — the leaf's own leading bullet —
    is never treated as a split point; only mid-text separators produce unit
    boundaries.
    """
    if not content or not separators:
        return [content] if content else []
    units: list[str] = []
    start = 0
    i = 1
    while i < len(content):
        if content[i] in separators and content[i - 1].isspace():
            units.append(content[start:i].strip())
            start = i
        i += 1
    units.append(content[start:].strip())
    return [u for u in units if u]


def _presplit_merged_leaves(
    cell: dict[str, Any],
    y_splits: list[float],
    separators: set[str],
) -> None:
    """Walk ``cell``'s tree and split leaves whose bbox crosses ``y_splits``
    when their content decomposes into exactly as many units as the crossed
    sub-bands. Synthetic sub-leaves receive proportional bboxes so the
    downstream distribution can place each unit in its rightful sub-band.
    """

    def process(parent: dict[str, Any]) -> None:
        for key in _CHILD_KEYS:
            items = parent.get(key)
            if not isinstance(items, list):
                continue
            new_items: list[Any] = []
            for item in items:
                if not isinstance(item, dict):
                    new_items.append(item)
                    continue
                process(item)
                has_children = any(
                    isinstance(item.get(k), list) and item.get(k) for k in _CHILD_KEYS
                )
                if has_children:
                    new_items.append(item)
                    continue
                split = _try_split_merged_leaf(item, y_splits, separators)
                if split:
                    new_items.extend(split)
                else:
                    new_items.append(item)
            parent[key] = new_items

    process(cell)


def _try_split_merged_leaf(
    leaf: dict[str, Any],
    y_splits: list[float],
    separators: set[str],
) -> list[dict[str, Any]] | None:
    bbox = coerce_bbox(leaf.get("bounding box"))
    if bbox is None:
        return None
    content = leaf.get("content")
    if not isinstance(content, str) or not content:
        return None
    interior_ys = sorted(
        y
        for y in y_splits
        if bbox.bottom_pt + _INTERIOR_PAD_PT < y < bbox.top_pt - _INTERIOR_PAD_PT
    )
    if not interior_ys:
        return None
    units = _split_content_into_units(content, separators)
    if len(units) != len(interior_ys) + 1:
        return None

    y_cuts = [bbox.bottom_pt, *interior_ys, bbox.top_pt]
    # Content order: first unit is visually at the TOP (highest y), so it
    # maps to the topmost y-band (y_cuts[-2..-1]). Reverse the list so
    # index i aligns with y_cuts[i..i+1] (bottom-up traversal).
    units_bottom_to_top = list(reversed(units))
    out: list[dict[str, Any]] = []
    for i, unit in enumerate(units_bottom_to_top):
        new_leaf = dict(leaf)
        new_leaf["bounding box"] = [
            bbox.left_pt,
            y_cuts[i],
            bbox.right_pt,
            y_cuts[i + 1],
        ]
        new_leaf["content"] = unit
        out.append(new_leaf)
    return out


def _collect_new_boundaries(
    cell_splits: dict[int, list[float]],
    *,
    existing: list[float],
) -> list[float]:
    aggregated: list[float] = []
    for splits in cell_splits.values():
        aggregated.extend(splits)
    if not aggregated:
        return []
    aggregated = _dedupe_close(sorted(aggregated), _AXIS_MERGE_TOL_PT)
    return [
        value
        for value in aggregated
        if not any(abs(value - e) <= _INTERIOR_PAD_PT for e in existing)
    ]


def _dedupe_close(sorted_values: list[float], tol: float) -> list[float]:
    if not sorted_values:
        return []
    out = [sorted_values[0]]
    for v in sorted_values[1:]:
        if v - out[-1] > tol:
            out.append(v)
    return out


def _snap_values(values: list[float], boundaries: list[float]) -> list[float]:
    """Replace each value with the nearest boundary within tolerance."""
    snapped: list[float] = []
    for v in values:
        best = min(boundaries, key=lambda b: abs(b - v))
        if abs(best - v) <= _AXIS_MERGE_TOL_PT:
            snapped.append(best)
    return _dedupe_close(sorted(snapped), _AXIS_MERGE_TOL_PT)


def _find_boundary_index(value: float, boundaries: list[float]) -> int | None:
    for i, b in enumerate(boundaries):
        if abs(value - b) <= _AXIS_MERGE_TOL_PT:
            return i
    return None


def _compute_cell_x_bands(
    raw_cells: list[dict[str, Any]],
    cell_x_splits: dict[int, list[float]],
) -> dict[int, list[tuple[float, float]]]:
    bands_by_cell: dict[int, list[tuple[float, float]]] = {}
    for idx, cell in enumerate(raw_cells):
        bbox = coerce_bbox(cell.get("bounding box"))
        if bbox is None:
            continue
        x_cuts = sorted({bbox.left_pt, bbox.right_pt, *cell_x_splits.get(idx, [])})
        x_cuts = _dedupe_close(x_cuts, _AXIS_MERGE_TOL_PT)
        bands_by_cell[idx] = [
            (x_cuts[i], x_cuts[i + 1]) for i in range(len(x_cuts) - 1)
        ]
    return bands_by_cell


def _rebuild_rows(
    original_cells: list[dict[str, Any]],
    *,
    cell_x_bands: dict[int, list[tuple[float, float]]],
    sub_rect_y_splits: dict[tuple[int, int], list[float]],
    ys: list[float],
    xs: list[float],
) -> list[dict[str, Any]]:
    """Rebuild ``rows`` using each sub-column's independent horizontal splits.

    Each original cell is partitioned by ``cell_x_bands`` into one or more
    sub-columns (x-bands). Each sub-column is cut only by its own y-splits
    recorded in ``sub_rect_y_splits``; a sub-column with no splits stays as
    a single sub-cell whose rowspan spans every merged row it covers.
    """
    ny = len(ys) - 1
    if ny <= 0 or len(xs) < 2:
        return []

    flat_cells: list[dict[str, Any]] = []

    for idx, cell in enumerate(original_cells):
        bbox = coerce_bbox(cell.get("bounding box"))
        if bbox is None:
            continue
        bands = cell_x_bands.get(idx, [(bbox.left_pt, bbox.right_pt)])
        for band_idx, (x_lo, x_hi) in enumerate(bands):
            y_splits = sub_rect_y_splits.get((idx, band_idx), [])
            y_cuts = sorted({bbox.bottom_pt, bbox.top_pt, *y_splits})
            y_cuts = _dedupe_close(y_cuts, _AXIS_MERGE_TOL_PT)

            lo_col = _find_boundary_index(x_lo, xs)
            hi_col = _find_boundary_index(x_hi, xs)
            if lo_col is None or hi_col is None or hi_col <= lo_col:
                continue
            colspan = hi_col - lo_col
            col_number = lo_col + 1

            for yi in range(len(y_cuts) - 1):
                y_lo, y_hi = y_cuts[yi], y_cuts[yi + 1]
                if y_hi - y_lo < _AXIS_MERGE_TOL_PT:
                    continue
                lo_band = _find_boundary_index(y_lo, ys)
                hi_band = _find_boundary_index(y_hi, ys)
                if lo_band is None or hi_band is None or hi_band <= lo_band:
                    continue
                rowspan = hi_band - lo_band
                row_number = ny - (hi_band - 1)

                sub_bbox = PdfBoundingBox(
                    left_pt=x_lo, bottom_pt=y_lo, right_pt=x_hi, top_pt=y_hi
                )
                flat_cells.append(
                    _build_sub_cell(
                        source=cell,
                        sub_bbox=sub_bbox,
                        row_number=row_number,
                        col_number=col_number,
                        rowspan=rowspan,
                        colspan=colspan,
                    )
                )

    if not flat_cells:
        return []

    rows_by_number: dict[int, list[dict[str, Any]]] = {}
    for sub_cell in flat_cells:
        rows_by_number.setdefault(sub_cell["row number"], []).append(sub_cell)

    new_rows: list[dict[str, Any]] = []
    for row_number in sorted(rows_by_number):
        row_cells = sorted(rows_by_number[row_number], key=lambda c: c["column number"])
        new_rows.append(
            {"type": "table row", "row number": row_number, "cells": row_cells}
        )
    return new_rows


def _build_sub_cell(
    source: dict[str, Any],
    sub_bbox: PdfBoundingBox,
    *,
    row_number: int,
    col_number: int,
    rowspan: int,
    colspan: int,
) -> dict[str, Any]:
    sub_bbox_list = [
        sub_bbox.left_pt,
        sub_bbox.bottom_pt,
        sub_bbox.right_pt,
        sub_bbox.top_pt,
    ]
    cell = dict(source)
    cell["row number"] = row_number
    cell["column number"] = col_number
    cell["row span"] = rowspan
    cell["column span"] = colspan
    cell["bounding box"] = sub_bbox_list
    cell["kids"] = _distribute_children(source.get("kids"), sub_bbox)
    if "paragraphs" in source:
        cell["paragraphs"] = _distribute_children(source.get("paragraphs"), sub_bbox)
    return cell


def _distribute_children(
    children: Any,
    sub_bbox: PdfBoundingBox,
) -> list[Any]:
    """Return the subset of children that belongs inside ``sub_bbox``.

    Every dict child is passed through ``_node_restricted_to``, which does
    span-level pruning when the node carries leaf spans and falls back to a
    bbox-center test otherwise. This handles ODL outputs where visually
    distinct labels (e.g., ``선정규모`` + ``협력기업``) sit in one node with
    multiple stacked spans, regardless of whether that node's ``type`` is
    ``paragraph``, ``heading``, etc.
    """
    if not isinstance(children, list):
        return []
    kept: list[Any] = []
    for child in children:
        if not isinstance(child, dict):
            kept.append(child)
            continue
        restricted = _node_restricted_to(child, sub_bbox)
        if restricted is not None:
            kept.append(restricted)
    return kept


_LEAF_TEXT_TYPES = frozenset({"span", "text chunk", "run"})
_CHILD_KEYS = ("kids", "spans", "runs", "list items")


def _node_restricted_to(
    node: dict[str, Any],
    sub_bbox: PdfBoundingBox,
) -> dict[str, Any] | None:
    """Return a shallow copy of ``node`` restricted to descendants in ``sub_bbox``.

    General ODL-tree pruning: recurses through every known container key
    (``kids``/``spans``/``runs``/``list items``) so any hierarchy — paragraph
    with stacked spans, list holding list items, heading grouping labels —
    is narrowed at the finest level that still preserves the node shape.
    Leaf-like nodes (explicit leaf types or nodes without child collections)
    fall back to a bbox-center match.
    """
    if node.get("type") in _LEAF_TEXT_TYPES:
        bbox = coerce_bbox(node.get("bounding box")) or coerce_bbox(node.get("bbox"))
        if bbox is None:
            return None
        return dict(node) if _bbox_center_in(bbox, sub_bbox) else None

    child_collections: list[tuple[str, list[Any]]] = []
    for key in _CHILD_KEYS:
        items = node.get(key)
        if isinstance(items, list) and items:
            child_collections.append((key, items))

    if not child_collections:
        node_bbox = coerce_bbox(node.get("bounding box"))
        if node_bbox is None:
            return dict(node)
        return dict(node) if _bbox_center_in(node_bbox, sub_bbox) else None

    new_node = dict(node)
    kept_any = False
    for key, items in child_collections:
        new_items: list[Any] = []
        for item in items:
            if isinstance(item, dict):
                restricted = _node_restricted_to(item, sub_bbox)
                if restricted is not None:
                    new_items.append(restricted)
                    kept_any = True
            else:
                new_items.append(item)
        new_node[key] = new_items

    if not kept_any:
        return None

    # Rebuild bbox from surviving leaf descendants so parent cells can rely
    # on consistent bbox information post-pruning.
    leaf_bboxes = _collect_leaf_bboxes(new_node)
    if leaf_bboxes:
        new_node["bounding box"] = [
            min(b.left_pt for b in leaf_bboxes),
            min(b.bottom_pt for b in leaf_bboxes),
            max(b.right_pt for b in leaf_bboxes),
            max(b.top_pt for b in leaf_bboxes),
        ]

    # For nodes that carry text as a concatenated ``content`` string (paragraph,
    # heading, …), rebuild it from the surviving direct leaf spans so the
    # downstream adapter's text extraction stays in sync with the retained
    # spans. Nodes without direct leaf spans (list, list item, …) keep their
    # original content field.
    direct_spans = [
        item
        for key in ("spans", "runs")
        for item in new_node.get(key) or []
        if isinstance(item, dict) and item.get("type") in _LEAF_TEXT_TYPES
    ]
    if direct_spans and "content" in new_node:
        new_node["content"] = "".join(
            s.get("content", "")
            for s in direct_spans
            if isinstance(s.get("content"), str)
        )

    # Keep list metadata consistent.
    if isinstance(new_node.get("list items"), list):
        new_node["number of list items"] = len(new_node["list items"])

    return new_node


def _collect_leaf_bboxes(node: dict[str, Any]) -> list[PdfBoundingBox]:
    bboxes: list[PdfBoundingBox] = []

    def visit(current: Any) -> None:
        if not isinstance(current, dict):
            return
        has_children = any(
            isinstance(current.get(k), list) and current.get(k) for k in _CHILD_KEYS
        )
        if current.get("type") in _LEAF_TEXT_TYPES or not has_children:
            bbox = coerce_bbox(current.get("bounding box")) or coerce_bbox(current.get("bbox"))
            if bbox is not None:
                bboxes.append(bbox)
            return
        for key in _CHILD_KEYS:
            items = current.get(key)
            if isinstance(items, list):
                for item in items:
                    visit(item)

    for key in _CHILD_KEYS:
        items = node.get(key)
        if isinstance(items, list):
            for item in items:
                visit(item)
    return bboxes


def _bbox_center_in(bbox: PdfBoundingBox, sub_bbox: PdfBoundingBox) -> bool:
    cx = (bbox.left_pt + bbox.right_pt) / 2.0
    cy = (bbox.bottom_pt + bbox.top_pt) / 2.0
    # Strict half-open partitioning: a center lying on any sub-cell boundary
    # is assigned to exactly one neighbor (the lower / left one). No padding
    # on either side — when adjacent sub-cells share a boundary, padding on
    # one side's boundary would let a boundary-hugging center match both
    # and end up duplicated.
    return (
        sub_bbox.left_pt <= cx < sub_bbox.right_pt
        and sub_bbox.bottom_pt <= cy < sub_bbox.top_pt
    )


__all__ = ["preprocess_dotted_rule_splits"]
