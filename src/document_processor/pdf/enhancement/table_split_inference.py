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

    split_axes = _vertical_axes_for_table(table, primitives)
    if not split_axes:
        return

    new_cells: list[TableCellIR] = []
    for cell in table.cells:
        cell_bbox = cell.bbox
        if cell_bbox is None:
            new_cells.append(cell)
            continue

        local_axes = [
            axis
            for axis in split_axes
            if cell_bbox.left_pt + _OUTER_BORDER_TOLERANCE_PT < axis < cell_bbox.right_pt - _OUTER_BORDER_TOLERANCE_PT
        ]
        if not local_axes:
            new_cells.append(cell)
            continue

        split_cells = _materialize_vertical_split_cells(cell, local_axes)
        if split_cells is None:
            new_cells.append(cell)
            continue
        new_cells.extend(split_cells)

    if len(new_cells) == len(table.cells):
        return

    new_cells.sort(key=lambda item: (item.row_index, item.col_index, item.unit_id))
    table.cells = new_cells
    table.row_count = max(cell.row_index for cell in new_cells)
    table.col_count = max(cell.col_index for cell in new_cells)


def _vertical_axes_for_table(
    table: TableIR,
    primitives: list[PdfPreviewVisualPrimitive],
) -> list[float]:
    bbox = table.bbox
    height = max(bbox.top_pt - bbox.bottom_pt, 0.0)
    if height <= 0.0:
        return []

    axes: list[float] = []
    for primitive in primitives:
        if "vertical_line_segment" not in primitive.candidate_roles:
            continue

        primitive_bbox = primitive.bounding_box
        x_center = (primitive_bbox.left_pt + primitive_bbox.right_pt) / 2.0
        if x_center <= bbox.left_pt + _OUTER_BORDER_TOLERANCE_PT:
            continue
        if x_center >= bbox.right_pt - _OUTER_BORDER_TOLERANCE_PT:
            continue

        overlap = min(primitive_bbox.top_pt, bbox.top_pt) - max(primitive_bbox.bottom_pt, bbox.bottom_pt)
        if overlap <= 0.0 or overlap / height < _MIN_RULE_SPAN_RATIO:
            continue
        axes.append(x_center)
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


def _materialize_vertical_split_cells(
    cell: TableCellIR,
    split_axes: list[float],
) -> list[TableCellIR] | None:
    cell_bbox = cell.bbox
    if cell_bbox is None:
        return None

    paragraph_boxes = [(paragraph, _paragraph_bbox(paragraph)) for paragraph in cell.paragraphs]
    paragraph_boxes = [(paragraph, bbox) for paragraph, bbox in paragraph_boxes if bbox is not None]
    if len(paragraph_boxes) < 2:
        return None

    boundaries = [cell_bbox.left_pt, *split_axes, cell_bbox.right_pt]
    buckets: list[list[ParagraphIR]] = [[] for _ in range(len(boundaries) - 1)]
    for paragraph, bbox in paragraph_boxes:
        center_x = (bbox.left_pt + bbox.right_pt) / 2.0
        for index, (left, right) in enumerate(zip(boundaries, boundaries[1:])):
            if left <= center_x <= right:
                buckets[index].append(paragraph)
                break

    non_empty = [(index, bucket) for index, bucket in enumerate(buckets) if bucket]
    if len(non_empty) < 2:
        return None

    split_cells: list[TableCellIR] = []
    for offset, (bucket_index, paragraphs) in enumerate(non_empty):
        left = boundaries[bucket_index]
        right = boundaries[bucket_index + 1]
        bbox = BoundingBox(
            left_pt=left,
            bottom_pt=cell_bbox.bottom_pt,
            right_pt=right,
            top_pt=cell_bbox.top_pt,
        )
        style = deepcopy(cell.cell_style) if cell.cell_style is not None else CellStyleInfo()
        style.rowspan = 1
        style.colspan = 1
        meta = cell.meta.model_copy(deep=True) if cell.meta is not None else None
        if meta is not None:
            meta.bounding_box = bbox
        split_cell = TableCellIR(
            unit_id=f"{cell.unit_id}.split{offset + 1}",
            row_index=cell.row_index,
            col_index=cell.col_index + offset,
            bbox=bbox,
            cell_style=style,
            paragraphs=paragraphs,
            meta=meta,
        )
        split_cell.recompute_text()
        split_cells.append(split_cell)
    return split_cells


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
