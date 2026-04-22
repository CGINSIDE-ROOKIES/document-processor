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
            for table in tables:
                _apply_dotted_splits(table, dotted_h, dotted_v)
    finally:
        document.close()


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

    A paragraph whose overall bbox fits inside ``sub_bbox`` is kept whole.
    A paragraph whose spans straddle ``sub_bbox`` is replaced with a sub
    paragraph that only carries the spans fitting inside — this matters for
    cells where ODL merged many visual rows into one paragraph.
    """
    if not isinstance(children, list):
        return []
    kept: list[Any] = []
    for child in children:
        if not isinstance(child, dict):
            kept.append(child)
            continue
        child_bbox = coerce_bbox(child.get("bounding box"))
        if child_bbox is None:
            kept.append(child)
            continue
        if child.get("type") == "paragraph":
            sub_paragraph = _paragraph_restricted_to(child, sub_bbox)
            if sub_paragraph is not None:
                kept.append(sub_paragraph)
            continue
        if _bbox_center_in(child_bbox, sub_bbox):
            kept.append(child)
    return kept


def _paragraph_restricted_to(
    paragraph: dict[str, Any],
    sub_bbox: PdfBoundingBox,
) -> dict[str, Any] | None:
    """Keep paragraph spans whose bbox center lies in ``sub_bbox``.

    Returns ``None`` when no spans qualify; returns the original paragraph
    (shallow copied) when every span qualifies; otherwise returns a pruned
    paragraph with ``spans``/``content``/``bounding box`` rebuilt from the
    retained spans.
    """
    spans = _iter_leaf_spans(paragraph)
    if not spans:
        pbbox = coerce_bbox(paragraph.get("bounding box"))
        if pbbox is not None and _bbox_center_in(pbbox, sub_bbox):
            return dict(paragraph)
        return None

    kept_spans = [s for s in spans if _span_center_in(s, sub_bbox)]
    if not kept_spans:
        return None
    if len(kept_spans) == len(spans):
        return dict(paragraph)
    return _build_sub_paragraph(paragraph, kept_spans)


def _iter_leaf_spans(paragraph: dict[str, Any]) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") in {"span", "text chunk", "run"}:
            leaves.append(node)
            return
        for key in ("kids", "spans", "runs"):
            items = node.get(key)
            if isinstance(items, list):
                for item in items:
                    visit(item)

    for key in ("kids", "spans", "runs"):
        items = paragraph.get(key)
        if isinstance(items, list):
            for item in items:
                visit(item)
    return leaves


def _span_center_in(span: dict[str, Any], sub_bbox: PdfBoundingBox) -> bool:
    bbox = coerce_bbox(span.get("bounding box")) or coerce_bbox(span.get("bbox"))
    if bbox is None:
        return False
    return _bbox_center_in(bbox, sub_bbox)


def _bbox_center_in(bbox: PdfBoundingBox, sub_bbox: PdfBoundingBox) -> bool:
    cx = (bbox.left_pt + bbox.right_pt) / 2.0
    cy = (bbox.bottom_pt + bbox.top_pt) / 2.0
    # Half-open interval on top/right so a center lying exactly on an interior
    # split boundary is assigned to a single sub-cell (the one below / to the
    # left), avoiding duplication.
    return (
        sub_bbox.left_pt - _BBOX_PAD_PT <= cx < sub_bbox.right_pt + _BBOX_PAD_PT
        and sub_bbox.bottom_pt - _BBOX_PAD_PT <= cy < sub_bbox.top_pt + _BBOX_PAD_PT
    )


def _build_sub_paragraph(
    paragraph: dict[str, Any],
    spans: list[dict[str, Any]],
) -> dict[str, Any]:
    sub = dict(paragraph)
    sub["spans"] = list(spans)
    sub["content"] = "".join(
        span.get("content", "")
        for span in spans
        if isinstance(span.get("content"), str)
    )
    if "kids" in sub:
        sub["kids"] = []
    bboxes = [
        bbox
        for span in spans
        if (bbox := coerce_bbox(span.get("bounding box"))) is not None
    ]
    if bboxes:
        union_bbox = PdfBoundingBox(
            left_pt=min(b.left_pt for b in bboxes),
            bottom_pt=min(b.bottom_pt for b in bboxes),
            right_pt=max(b.right_pt for b in bboxes),
            top_pt=max(b.top_pt for b in bboxes),
        )
        sub["bounding box"] = [
            union_bbox.left_pt,
            union_bbox.bottom_pt,
            union_bbox.right_pt,
            union_bbox.top_pt,
        ]
    return sub


__all__ = ["preprocess_dotted_rule_splits"]
