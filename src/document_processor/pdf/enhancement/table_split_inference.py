"""Infer conservative text-bearing table cell splits from PDF line primitives."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ...models import BoundingBox, DocIR, ParagraphIR, TableCellIR, TableIR
from ...style_types import CellStyleInfo
from ..preview.analyze import extract_pdfium_table_rule_primitives
from ..preview.models import PdfPreviewVisualPrimitive

_AXIS_CLUSTER_TOLERANCE_PT = 2.0
_OUTER_BORDER_TOLERANCE_PT = 4.0
_MIN_RULE_SPAN_RATIO = 0.45


def enrich_pdf_table_splits(
    doc_ir: DocIR,
    *,
    pdf_path: str | Path | None = None,
) -> DocIR:
    if (doc_ir.source_doc_type or "").lower() != "pdf":
        return doc_ir

    resolved_pdf_path = Path(pdf_path or doc_ir.source_path or "").expanduser()
    if not resolved_pdf_path.exists():
        return doc_ir

    tables_by_page = _collect_tables_by_page(doc_ir)
    if not tables_by_page:
        return doc_ir

    primitives_by_page = _extract_rule_primitives_for_pages(
        resolved_pdf_path,
        page_numbers=set(tables_by_page),
    )
    for page_number, tables in tables_by_page.items():
        page_primitives = primitives_by_page.get(page_number, [])
        for table in tables:
            _split_table_from_primitives(table, page_primitives)
    return doc_ir


def _extract_rule_primitives_for_pages(
    pdf_path: Path,
    *,
    page_numbers: set[int],
) -> dict[int, list[PdfPreviewVisualPrimitive]]:
    try:
        import pypdfium2 as pdfium
    except Exception:
        return {}

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return {
            page_number: extract_pdfium_table_rule_primitives(
                doc[page_number - 1],
                page_number=page_number,
            )
            for page_number in sorted(page_numbers)
        }
    finally:
        doc.close()


def _collect_tables_by_page(doc_ir: DocIR) -> dict[int, list[TableIR]]:
    grouped: dict[int, list[TableIR]] = {}
    for paragraph in doc_ir.paragraphs:
        for table in paragraph.tables:
            table_meta = getattr(table, "meta", None)
            page_number = getattr(table_meta, "page_number", None) or paragraph.page_number
            if page_number is None or table.bbox is None:
                continue
            grouped.setdefault(page_number, []).append(table)
    return grouped


def _split_table_from_primitives(
    table: TableIR,
    primitives: list[PdfPreviewVisualPrimitive],
) -> None:
    if not table.cells or table.bbox is None:
        return

    x_axes = _axes_for_table(table, primitives, orientation="vertical")
    y_axes = _axes_for_table(table, primitives, orientation="horizontal")
    if not x_axes and not y_axes:
        return

    global_x = _cluster_axes(
        [
            table.bbox.left_pt,
            table.bbox.right_pt,
            *x_axes,
            *[cell.bbox.left_pt for cell in table.cells if cell.bbox is not None],
            *[cell.bbox.right_pt for cell in table.cells if cell.bbox is not None],
        ]
    )
    global_y = _cluster_axes(
        [
            table.bbox.bottom_pt,
            table.bbox.top_pt,
            *y_axes,
            *[cell.bbox.bottom_pt for cell in table.cells if cell.bbox is not None],
            *[cell.bbox.top_pt for cell in table.cells if cell.bbox is not None],
        ]
    )

    replacement: list[TableCellIR] = []
    for cell in table.cells:
        split_cells = _materialize_split_cells(cell, global_x, global_y)
        if split_cells is None:
            replacement.append(_remap_unsplit_cell(cell, global_x, global_y))
            continue
        replacement.extend(split_cells)

    replacement.sort(key=lambda item: (item.row_index, item.col_index, item.unit_id))
    table.cells = replacement
    table.row_count = max(
        cell.row_index + ((cell.cell_style.rowspan - 1) if cell.cell_style is not None else 0)
        for cell in replacement
    )
    table.col_count = max(
        cell.col_index + ((cell.cell_style.colspan - 1) if cell.cell_style is not None else 0)
        for cell in replacement
    )


def _axes_for_table(
    table: TableIR,
    primitives: list[PdfPreviewVisualPrimitive],
    *,
    orientation: str,
) -> list[float]:
    bbox = table.bbox
    table_span = max(
        (bbox.top_pt - bbox.bottom_pt) if orientation == "vertical" else (bbox.right_pt - bbox.left_pt),
        0.0,
    )
    if table_span <= 0.0:
        return []

    axes: list[float] = []
    for primitive in primitives:
        if orientation == "vertical" and "vertical_line_segment" not in primitive.candidate_roles:
            continue
        if orientation == "horizontal" and "horizontal_line_segment" not in primitive.candidate_roles:
            continue

        primitive_bbox = primitive.bounding_box
        axis = (
            (primitive_bbox.left_pt + primitive_bbox.right_pt) / 2.0
            if orientation == "vertical"
            else (primitive_bbox.bottom_pt + primitive_bbox.top_pt) / 2.0
        )
        lower = bbox.left_pt if orientation == "vertical" else bbox.bottom_pt
        upper = bbox.right_pt if orientation == "vertical" else bbox.top_pt
        if axis <= lower + _OUTER_BORDER_TOLERANCE_PT:
            continue
        if axis >= upper - _OUTER_BORDER_TOLERANCE_PT:
            continue

        overlap = (
            min(primitive_bbox.top_pt, bbox.top_pt) - max(primitive_bbox.bottom_pt, bbox.bottom_pt)
            if orientation == "vertical"
            else min(primitive_bbox.right_pt, bbox.right_pt) - max(primitive_bbox.left_pt, bbox.left_pt)
        )
        if overlap <= 0.0 or overlap / table_span < _MIN_RULE_SPAN_RATIO:
            continue
        axes.append(axis)
    return _cluster_axes(axes)


def _cluster_axes(values: list[float]) -> list[float]:
    if not values:
        return []

    values.sort()
    clusters: list[list[float]] = [[values[0]]]
    for value in values[1:]:
        if abs(value - clusters[-1][-1]) <= _AXIS_CLUSTER_TOLERANCE_PT:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _materialize_split_cells(
    cell: TableCellIR,
    global_x: list[float],
    global_y: list[float],
) -> list[TableCellIR] | None:
    cell_bbox = cell.bbox
    if cell_bbox is None:
        return None

    x_boundaries = _cluster_axes(
        [
            cell_bbox.left_pt,
            *[value for value in global_x if cell_bbox.left_pt < value < cell_bbox.right_pt],
            cell_bbox.right_pt,
        ]
    )
    y_boundaries = _cluster_axes(
        [
            cell_bbox.bottom_pt,
            *[value for value in global_y if cell_bbox.bottom_pt < value < cell_bbox.top_pt],
            cell_bbox.top_pt,
        ]
    )
    if len(x_boundaries) <= 2 and len(y_boundaries) <= 2:
        return None

    paragraph_boxes = [(paragraph, _paragraph_bbox(paragraph)) for paragraph in cell.paragraphs]
    paragraph_boxes = [(paragraph, bbox) for paragraph, bbox in paragraph_boxes if bbox is not None]
    if len(paragraph_boxes) < 2:
        return None

    x_pairs = list(zip(x_boundaries, x_boundaries[1:]))
    y_pairs = list(reversed(list(zip(y_boundaries, y_boundaries[1:]))))
    buckets: dict[tuple[int, int], list[ParagraphIR]] = {}
    for paragraph, bbox in paragraph_boxes:
        center_x = (bbox.left_pt + bbox.right_pt) / 2.0
        center_y = (bbox.bottom_pt + bbox.top_pt) / 2.0
        col_index = next((index for index, (left, right) in enumerate(x_pairs, start=1) if left <= center_x <= right), None)
        row_index = next((index for index, (bottom, top) in enumerate(y_pairs, start=1) if bottom <= center_y <= top), None)
        if row_index is None or col_index is None:
            continue
        buckets.setdefault((row_index, col_index), []).append(paragraph)

    if len(buckets) < 2:
        return None

    cells: list[TableCellIR] = []
    for (row_index, col_index), paragraphs in sorted(buckets.items()):
        left, right = x_pairs[col_index - 1]
        bottom, top = y_pairs[row_index - 1]
        bbox = BoundingBox(
            left_pt=left,
            bottom_pt=bottom,
            right_pt=right,
            top_pt=top,
        )
        style = deepcopy(cell.cell_style) if cell.cell_style is not None else CellStyleInfo()
        style.rowspan = 1
        style.colspan = 1
        meta = cell.meta.model_copy(deep=True) if cell.meta is not None else None
        if meta is not None:
            meta.bounding_box = bbox
        cell_ir = TableCellIR(
            unit_id=f"{cell.unit_id}.r{row_index}.c{col_index}",
            row_index=_row_index_from_boundaries(global_y, bottom, top),
            col_index=_col_index_from_boundaries(global_x, left, right),
            bbox=bbox,
            cell_style=style,
            paragraphs=paragraphs,
            meta=meta,
        )
        cell_ir.recompute_text()
        cells.append(cell_ir)
    return cells


def _remap_unsplit_cell(
    cell: TableCellIR,
    global_x: list[float],
    global_y: list[float],
) -> TableCellIR:
    cell_bbox = cell.bbox
    if cell_bbox is None:
        return cell

    remapped = cell.model_copy(deep=True)
    remapped.row_index = _row_index_from_boundaries(global_y, cell_bbox.bottom_pt, cell_bbox.top_pt)
    remapped.col_index = _col_index_from_boundaries(global_x, cell_bbox.left_pt, cell_bbox.right_pt)
    if remapped.cell_style is not None:
        remapped.cell_style.rowspan = _span_from_boundaries(global_y, cell_bbox.bottom_pt, cell_bbox.top_pt)
        remapped.cell_style.colspan = _span_from_boundaries(global_x, cell_bbox.left_pt, cell_bbox.right_pt)
    return remapped


def _row_index_from_boundaries(
    boundaries: list[float],
    bottom: float,
    top: float,
) -> int:
    return _interval_index(list(reversed(list(zip(boundaries, boundaries[1:])))), bottom, top)


def _col_index_from_boundaries(
    boundaries: list[float],
    left: float,
    right: float,
) -> int:
    return _interval_index(list(zip(boundaries, boundaries[1:])), left, right)


def _interval_index(
    intervals: list[tuple[float, float]],
    start: float,
    end: float,
) -> int:
    for index, (interval_start, interval_end) in enumerate(intervals, start=1):
        if (
            abs(interval_start - start) <= _AXIS_CLUSTER_TOLERANCE_PT
            and abs(interval_end - end) <= _AXIS_CLUSTER_TOLERANCE_PT
        ):
            return index
    return 1


def _span_from_boundaries(
    boundaries: list[float],
    start: float,
    end: float,
) -> int:
    intervals = [
        (interval_start, interval_end)
        for interval_start, interval_end in zip(boundaries, boundaries[1:])
        if interval_start >= start - _AXIS_CLUSTER_TOLERANCE_PT
        and interval_end <= end + _AXIS_CLUSTER_TOLERANCE_PT
    ]
    return max(1, len(intervals))


def _paragraph_bbox(paragraph: ParagraphIR) -> BoundingBox | None:
    if paragraph.bbox is not None:
        return paragraph.bbox

    boxes = [run.bbox for run in paragraph.runs if run.bbox is not None]
    if not boxes:
        return None
    return BoundingBox(
        left_pt=min(box.left_pt for box in boxes),
        bottom_pt=min(box.bottom_pt for box in boxes),
        right_pt=max(box.right_pt for box in boxes),
        top_pt=max(box.top_pt for box in boxes),
    )


__all__ = ["enrich_pdf_table_splits"]
