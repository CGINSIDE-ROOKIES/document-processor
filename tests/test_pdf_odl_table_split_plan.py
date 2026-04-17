from __future__ import annotations

from pathlib import Path
import sys
import unittest

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor.pdf.meta import PdfBoundingBox
from document_processor.pdf.odl.table_split_plan import (
    CellKey,
    TableNodeKey,
    build_table_split_plan_for_table_node,
)
from document_processor.pdf.preview.models import PdfPreviewVisualPrimitive


def _text_box(text: str, *, left: float, bottom: float, right: float, top: float) -> dict[str, object]:
    return {
        "type": "paragraph",
        "content": text,
        "page number": 1,
        "bounding box": [left, bottom, right, top],
    }


def _table_node() -> dict[str, object]:
    return {
        "type": "table",
        "page number": 1,
        "reading order index": 7,
        "bounding box": [10.0, 10.0, 110.0, 90.0],
        "number of rows": 2,
        "number of columns": 2,
        "rows": [
            {
                "cells": [
                    {
                        "type": "table cell",
                        "row number": 1,
                        "column number": 1,
                        "row span": 1,
                        "column span": 1,
                        "bounding box": [10.0, 50.0, 60.0, 90.0],
                        "kids": [
                            _text_box("Top", left=14.0, bottom=68.0, right=58.0, top=86.0),
                            _text_box("Bottom", left=14.0, bottom=52.0, right=58.0, top=66.0),
                        ],
                    },
                    {
                        "type": "table cell",
                        "row number": 1,
                        "column number": 2,
                        "row span": 2,
                        "column span": 1,
                        "bounding box": [60.0, 10.0, 110.0, 90.0],
                        "kids": [_text_box("Merged", left=66.0, bottom=40.0, right=104.0, top=60.0)],
                    },
                ]
            },
            {
                "cells": [
                    {
                        "type": "table cell",
                        "row number": 2,
                        "column number": 1,
                        "row span": 1,
                        "column span": 1,
                        "bounding box": [10.0, 10.0, 60.0, 50.0],
                        "kids": [_text_box("Tail", left=14.0, bottom=18.0, right=58.0, top=42.0)],
                    },
                ]
            },
        ],
    }


def _horizontal_rule(y_pt: float) -> PdfPreviewVisualPrimitive:
    return PdfPreviewVisualPrimitive(
        page_number=1,
        draw_order=1,
        object_type="segmented_horizontal_rule",
        bounding_box=PdfBoundingBox(left_pt=12.0, bottom_pt=y_pt - 0.5, right_pt=88.0, top_pt=y_pt + 0.5),
        stroke_color="#000000ff",
        stroke_width_pt=1.0,
        has_stroke=True,
        candidate_roles=["horizontal_line_segment", "segmented_horizontal_rule"],
    )


def _vertical_rule(x_pt: float) -> PdfPreviewVisualPrimitive:
    return PdfPreviewVisualPrimitive(
        page_number=1,
        draw_order=1,
        object_type="segmented_vertical_rule",
        bounding_box=PdfBoundingBox(left_pt=x_pt - 0.5, bottom_pt=12.0, right_pt=x_pt + 0.5, top_pt=88.0),
        stroke_color="#000000ff",
        stroke_width_pt=1.0,
        has_stroke=True,
        candidate_roles=["vertical_line_segment", "segmented_vertical_rule"],
    )


class TableSplitPlanTests(unittest.TestCase):
    def test_build_table_split_plan_for_table_node_creates_row_event_for_text_bearing_horizontal_rule(self) -> None:
        plan = build_table_split_plan_for_table_node(_table_node(), primitives=[_horizontal_rule(67.0)])

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual([(event.source_row, round(event.axis_pt, 1)) for event in plan.row_events], [(1, 67.0)])
        self.assertEqual(plan.column_events, [])
        self.assertIn(CellKey(row_index=1, col_index=1, rowspan=1, colspan=1), plan.cell_splits)

    def test_build_table_split_plan_for_table_node_ignores_rule_outside_table_bbox(self) -> None:
        plan = build_table_split_plan_for_table_node(_table_node(), primitives=[_horizontal_rule(95.0)])
        self.assertIsNone(plan)

    def test_build_table_split_plan_for_table_node_does_not_split_when_text_only_on_one_side(self) -> None:
        node = _table_node()
        node["rows"][0]["cells"][0]["kids"] = [
            _text_box("Only top", left=14.0, bottom=68.0, right=58.0, top=86.0)
        ]
        plan = build_table_split_plan_for_table_node(node, primitives=[_horizontal_rule(67.0)])
        self.assertIsNone(plan)

    def test_build_table_split_plan_for_table_node_creates_column_event_for_text_bearing_vertical_rule(self) -> None:
        node = _table_node()
        node["rows"][1]["cells"][0]["kids"] = [
            _text_box("Left", left=14.0, bottom=14.0, right=30.0, top=42.0),
            _text_box("Right", left=40.0, bottom=14.0, right=56.0, top=42.0),
        ]
        plan = build_table_split_plan_for_table_node(node, primitives=[_vertical_rule(35.0)])

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual([(event.source_col, round(event.axis_pt, 1)) for event in plan.column_events], [(1, 35.0)])
