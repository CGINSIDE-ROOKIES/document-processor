"""Dotted-rule cell-split preprocessor for ODL raw tables.

ODL parses solid grid lines well but misses dotted/dashed grid lines inside
tables: a cell bounded by solid lines and internally divided by a dotted
rule ends up reported as a single cell. This module detects those dotted
rules via pdfium visual primitives (`segmented_horizontal_rule` /
`segmented_vertical_rule`) and rewrites the raw ODL table in place so that
its `rows`/`cells`/`grid boundaries` reflect the extra splits. Downstream
conversion in `adapter.py` then sees an ODL structure that is already
complete, and no visual-grid reconstruction is needed.

Scope intentionally narrow:
  * Only full-span dotted rules (>= `_FULL_SPAN_RATIO` of the table axis)
    are promoted to new grid boundaries. Partial rules are ignored.
  * Only tables whose cells are all rowspan=colspan=1 are rewritten; the
    adapter handles merged-cell tables via its standard raw-topology path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..meta import PdfBoundingBox, coerce_bbox, coerce_float, coerce_int
from ..preview.analyze import extract_pdfium_table_rule_primitives
from ..preview.models import PdfPreviewVisualPrimitive

_AXIS_MERGE_TOL_PT = 2.0
_BOUNDARY_NEAR_TOL_PT = 2.0
_FULL_SPAN_RATIO = 0.9
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

    existing_ys = _boundary_values(table.get("grid row boundaries"))
    existing_xs = _boundary_values(table.get("grid column boundaries"))
    if len(existing_ys) < 2 or len(existing_xs) < 2:
        return
    if not _cells_are_simple(table):
        return

    new_ys = _interior_axis_values(
        dotted_h, table_bbox, axis="y", existing=existing_ys
    )
    new_xs = _interior_axis_values(
        dotted_v, table_bbox, axis="x", existing=existing_xs
    )
    if not new_ys and not new_xs:
        return

    merged_ys = _dedupe_close(sorted(existing_ys + new_ys), _AXIS_MERGE_TOL_PT)
    merged_xs = _dedupe_close(sorted(existing_xs + new_xs), _AXIS_MERGE_TOL_PT)
    if len(merged_ys) == len(existing_ys) and len(merged_xs) == len(existing_xs):
        return

    _rebuild_rows(table, sorted_xs=merged_xs, sorted_ys=merged_ys)
    # Preserve ODL's top-down (descending-y) ordering for row boundaries.
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
    return values


def _interior_axis_values(
    rules: list[PdfPreviewVisualPrimitive],
    table_bbox: PdfBoundingBox,
    *,
    axis: str,
    existing: list[float],
) -> list[float]:
    """Return dotted-rule axis values that represent new interior grid lines."""
    if not rules:
        return []
    # Rule-length axis: horizontal rules span the table's width, vertical
    # rules span the table's height. Compare like-to-like.
    if axis == "y":
        table_span = table_bbox.right_pt - table_bbox.left_pt
    else:
        table_span = table_bbox.top_pt - table_bbox.bottom_pt
    if table_span <= 0:
        return []

    interior_lo = min(existing) + _BOUNDARY_NEAR_TOL_PT
    interior_hi = max(existing) - _BOUNDARY_NEAR_TOL_PT
    candidates: list[float] = []

    for rule in rules:
        bbox = rule.bounding_box
        if axis == "y":
            if not (
                table_bbox.left_pt - _BBOX_PAD_PT <= bbox.left_pt
                and bbox.right_pt <= table_bbox.right_pt + _BBOX_PAD_PT
            ):
                continue
            rule_span = bbox.right_pt - bbox.left_pt
            if rule_span < _FULL_SPAN_RATIO * table_span:
                continue
            value = (bbox.top_pt + bbox.bottom_pt) / 2.0
        else:
            if not (
                table_bbox.bottom_pt - _BBOX_PAD_PT <= bbox.bottom_pt
                and bbox.top_pt <= table_bbox.top_pt + _BBOX_PAD_PT
            ):
                continue
            rule_span = bbox.top_pt - bbox.bottom_pt
            if rule_span < _FULL_SPAN_RATIO * table_span:
                continue
            value = (bbox.left_pt + bbox.right_pt) / 2.0

        if not (interior_lo < value < interior_hi):
            continue
        if any(abs(value - e) <= _BOUNDARY_NEAR_TOL_PT for e in existing):
            continue
        if any(abs(value - e) <= _BOUNDARY_NEAR_TOL_PT for e in candidates):
            continue
        candidates.append(value)

    return candidates


def _cells_are_simple(table: dict[str, Any]) -> bool:
    for row in table.get("rows", []) or []:
        if not isinstance(row, dict):
            continue
        for cell in row.get("cells", []) or []:
            if not isinstance(cell, dict):
                continue
            rs = coerce_int(cell.get("row span")) or 1
            cs = coerce_int(cell.get("column span")) or 1
            if rs != 1 or cs != 1:
                return False
    return True


def _dedupe_close(sorted_values: list[float], tol: float) -> list[float]:
    if not sorted_values:
        return []
    out = [sorted_values[0]]
    for v in sorted_values[1:]:
        if v - out[-1] > tol:
            out.append(v)
    return out


def _rebuild_rows(
    table: dict[str, Any],
    *,
    sorted_xs: list[float],
    sorted_ys: list[float],
) -> None:
    """Replace ``table['rows']`` with a grid that matches the expanded boundaries.

    Each sub-cell inherits its paragraphs from the original cell whose bbox
    contains the sub-cell's center; paragraphs/kids whose bbox centers fall
    outside the sub-cell's bbox are dropped, so a cell split by a dotted
    rule distributes its text to the correct sub-cell.
    """
    originals: list[dict[str, Any]] = []
    for row in table.get("rows", []) or []:
        if not isinstance(row, dict):
            continue
        for cell in row.get("cells", []) or []:
            if isinstance(cell, dict):
                originals.append(cell)

    ny = len(sorted_ys) - 1
    nx = len(sorted_xs) - 1
    new_rows: list[dict[str, Any]] = []
    for visual_row_idx in range(ny):
        i = ny - 1 - visual_row_idx
        y_lo, y_hi = sorted_ys[i], sorted_ys[i + 1]
        row_number = visual_row_idx + 1
        cells: list[dict[str, Any]] = []
        for j in range(nx):
            x_lo, x_hi = sorted_xs[j], sorted_xs[j + 1]
            col_number = j + 1
            sub_bbox = PdfBoundingBox(
                left_pt=x_lo, bottom_pt=y_lo, right_pt=x_hi, top_pt=y_hi
            )
            source = _find_original_covering(originals, sub_bbox)
            cells.append(_build_sub_cell(source, sub_bbox, row_number, col_number))
        new_rows.append(
            {"type": "table row", "row number": row_number, "cells": cells}
        )
    table["rows"] = new_rows


def _find_original_covering(
    originals: list[dict[str, Any]],
    sub_bbox: PdfBoundingBox,
) -> dict[str, Any] | None:
    cx = (sub_bbox.left_pt + sub_bbox.right_pt) / 2.0
    cy = (sub_bbox.bottom_pt + sub_bbox.top_pt) / 2.0
    for cell in originals:
        bbox = coerce_bbox(cell.get("bounding box"))
        if bbox is None:
            continue
        if (
            bbox.left_pt - _BBOX_PAD_PT <= cx <= bbox.right_pt + _BBOX_PAD_PT
            and bbox.bottom_pt - _BBOX_PAD_PT <= cy <= bbox.top_pt + _BBOX_PAD_PT
        ):
            return cell
    return None


def _build_sub_cell(
    source: dict[str, Any] | None,
    sub_bbox: PdfBoundingBox,
    row_number: int,
    col_number: int,
) -> dict[str, Any]:
    sub_bbox_list = [
        sub_bbox.left_pt,
        sub_bbox.bottom_pt,
        sub_bbox.right_pt,
        sub_bbox.top_pt,
    ]
    if source is None:
        return {
            "type": "table cell",
            "row number": row_number,
            "column number": col_number,
            "row span": 1,
            "column span": 1,
            "bounding box": sub_bbox_list,
            "kids": [],
            "paragraphs": [],
        }
    cell = dict(source)
    cell["row number"] = row_number
    cell["column number"] = col_number
    cell["row span"] = 1
    cell["column span"] = 1
    cell["bounding box"] = sub_bbox_list
    cell["kids"] = _filter_children_by_bbox(source.get("kids"), sub_bbox)
    if "paragraphs" in source:
        cell["paragraphs"] = _filter_children_by_bbox(source.get("paragraphs"), sub_bbox)
    return cell


def _filter_children_by_bbox(
    children: Any,
    sub_bbox: PdfBoundingBox,
) -> list[Any]:
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
        cx = (child_bbox.left_pt + child_bbox.right_pt) / 2.0
        cy = (child_bbox.bottom_pt + child_bbox.top_pt) / 2.0
        if (
            sub_bbox.left_pt - _BBOX_PAD_PT <= cx <= sub_bbox.right_pt + _BBOX_PAD_PT
            and sub_bbox.bottom_pt - _BBOX_PAD_PT <= cy <= sub_bbox.top_pt + _BBOX_PAD_PT
        ):
            kept.append(child)
    return kept


__all__ = ["preprocess_dotted_rule_splits"]
