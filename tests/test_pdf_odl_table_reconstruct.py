from __future__ import annotations

import sys
import unittest
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor.pdf.meta import PdfBoundingBox
from document_processor.pdf.odl.table_reconstruct import _apply_dotted_splits
from document_processor.pdf.preview.models import PdfPreviewVisualPrimitive


def _dotted_primitive(
    *,
    orientation: str,
    left: float,
    bottom: float,
    right: float,
    top: float,
) -> PdfPreviewVisualPrimitive:
    return PdfPreviewVisualPrimitive(
        page_number=1,
        draw_order=1,
        object_type=f"segmented_{orientation}_rule",
        bounding_box=PdfBoundingBox(
            left_pt=left, bottom_pt=bottom, right_pt=right, top_pt=top
        ),
        stroke_color="#000000ff",
        stroke_width_pt=1.0,
        has_stroke=True,
        candidate_roles=[f"segmented_{orientation}_rule"],
    )


def _paragraph(text: str, *, left: float, bottom: float, right: float, top: float) -> dict:
    return {
        "type": "paragraph",
        "page number": 1,
        "content": text,
        "bounding box": [left, bottom, right, top],
        "spans": [
            {
                "type": "text chunk",
                "content": text,
                "bounding box": [left, bottom, right, top],
            }
        ],
    }


def _single_cell_table(
    *,
    paragraphs: list[dict],
    table_bbox: tuple[float, float, float, float] = (10.0, 10.0, 110.0, 90.0),
) -> dict:
    left, bottom, right, top = table_bbox
    return {
        "type": "table",
        "page number": 1,
        "bounding box": list(table_bbox),
        "number of rows": 1,
        "number of columns": 1,
        "grid row boundaries": [top, bottom],
        "grid column boundaries": [left, right],
        "rows": [
            {
                "type": "table row",
                "row number": 1,
                "cells": [
                    {
                        "type": "table cell",
                        "page number": 1,
                        "row number": 1,
                        "column number": 1,
                        "row span": 1,
                        "column span": 1,
                        "bounding box": list(table_bbox),
                        "has top border": True,
                        "has bottom border": True,
                        "has left border": True,
                        "has right border": True,
                        "kids": list(paragraphs),
                        "paragraphs": list(paragraphs),
                    }
                ],
            }
        ],
    }


class DottedRuleSplitTests(unittest.TestCase):
    def test_no_dotted_rules_leaves_table_unchanged(self) -> None:
        table = _single_cell_table(
            paragraphs=[_paragraph("Only", left=14.0, bottom=20.0, right=108.0, top=80.0)]
        )
        snapshot = {key: table[key] for key in ("number of rows", "number of columns", "rows")}
        _apply_dotted_splits(table, dotted_h=[], dotted_v=[])
        self.assertEqual(table["number of rows"], snapshot["number of rows"])
        self.assertEqual(table["number of columns"], snapshot["number of columns"])
        self.assertEqual(table["rows"], snapshot["rows"])

    def test_horizontal_dotted_rule_splits_cell_into_two_rows(self) -> None:
        table = _single_cell_table(
            paragraphs=[
                _paragraph("Top", left=14.0, bottom=58.0, right=108.0, top=82.0),
                _paragraph("Bottom", left=14.0, bottom=18.0, right=108.0, top=42.0),
            ]
        )
        dotted = _dotted_primitive(orientation="horizontal", left=10.0, bottom=49.5, right=110.0, top=50.5)
        _apply_dotted_splits(table, dotted_h=[dotted], dotted_v=[])
        self.assertEqual(table["number of rows"], 2)
        self.assertEqual(table["number of columns"], 1)
        rows = table["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["row number"], 1)  # top row
        self.assertEqual(rows[1]["row number"], 2)
        top_cell = rows[0]["cells"][0]
        bottom_cell = rows[1]["cells"][0]
        self.assertEqual(top_cell["paragraphs"][0]["content"], "Top")
        self.assertEqual(bottom_cell["paragraphs"][0]["content"], "Bottom")

    def test_vertical_dotted_rule_splits_cell_into_two_columns(self) -> None:
        table = _single_cell_table(
            paragraphs=[
                _paragraph("Left", left=14.0, bottom=40.0, right=55.0, top=60.0),
                _paragraph("Right", left=65.0, bottom=40.0, right=106.0, top=60.0),
            ]
        )
        dotted = _dotted_primitive(orientation="vertical", left=59.5, bottom=10.0, right=60.5, top=90.0)
        _apply_dotted_splits(table, dotted_h=[], dotted_v=[dotted])
        self.assertEqual(table["number of rows"], 1)
        self.assertEqual(table["number of columns"], 2)
        cells = table["rows"][0]["cells"]
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[0]["paragraphs"][0]["content"], "Left")
        self.assertEqual(cells[1]["paragraphs"][0]["content"], "Right")

    def test_dotted_rule_along_existing_boundary_is_ignored(self) -> None:
        table = _single_cell_table(
            paragraphs=[_paragraph("Only", left=14.0, bottom=20.0, right=108.0, top=80.0)]
        )
        # A dotted rule at the bottom boundary of the table — must not spawn a new row.
        dotted = _dotted_primitive(orientation="horizontal", left=10.0, bottom=9.5, right=110.0, top=10.5)
        _apply_dotted_splits(table, dotted_h=[dotted], dotted_v=[])
        self.assertEqual(table["number of rows"], 1)

    def test_partial_width_dotted_rule_is_ignored(self) -> None:
        table = _single_cell_table(
            paragraphs=[_paragraph("Only", left=14.0, bottom=20.0, right=108.0, top=80.0)]
        )
        # Rule only covers 40pt of the 100pt table width — below the 90% threshold.
        dotted = _dotted_primitive(orientation="horizontal", left=30.0, bottom=49.5, right=70.0, top=50.5)
        _apply_dotted_splits(table, dotted_h=[dotted], dotted_v=[])
        self.assertEqual(table["number of rows"], 1)

    def test_merged_cell_table_is_skipped(self) -> None:
        table = _single_cell_table(
            paragraphs=[_paragraph("Only", left=14.0, bottom=20.0, right=108.0, top=80.0)]
        )
        table["rows"][0]["cells"][0]["row span"] = 2  # no longer "simple"
        dotted = _dotted_primitive(orientation="horizontal", left=10.0, bottom=49.5, right=110.0, top=50.5)
        _apply_dotted_splits(table, dotted_h=[dotted], dotted_v=[])
        self.assertEqual(table["number of rows"], 1)


class DottedRuleSplitAdapterIntegrationTests(unittest.TestCase):
    """End-to-end: preprocessed raw table flows through adapter correctly."""

    def test_split_table_converts_to_two_row_table_ir(self) -> None:
        from document_processor.pdf.odl import adapter as odl_adapter

        table = _single_cell_table(
            paragraphs=[
                _paragraph("Top", left=14.0, bottom=58.0, right=108.0, top=82.0),
                _paragraph("Bottom", left=14.0, bottom=18.0, right=108.0, top=42.0),
            ]
        )
        dotted = _dotted_primitive(orientation="horizontal", left=10.0, bottom=49.5, right=110.0, top=50.5)
        _apply_dotted_splits(table, dotted_h=[dotted], dotted_v=[])
        table_ir = odl_adapter._table_node_to_ir(table, unit_id="u", assets={})
        self.assertEqual(table_ir.row_count, 2)
        self.assertEqual(table_ir.col_count, 1)
        cells_by_row = {cell.row_index: cell for cell in table_ir.cells}
        self.assertEqual(cells_by_row[1].text.strip(), "Top")
        self.assertEqual(cells_by_row[2].text.strip(), "Bottom")


if __name__ == "__main__":
    unittest.main()
