from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor.pdf.meta import PdfBoundingBox
from document_processor.pdf.odl import adapter as odl_adapter
from document_processor.pdf.odl.table_reconstruct import (
    MergeGroup,
    TableGrid,
    collect_lines,
    assign_fragments_to_groups,
    reconstruct_table_grid,
    table_node_key,
)
from document_processor.pdf.preview.models import PdfPreviewVisualPrimitive


def _primitive(*, object_type: str, roles: list[str], left: float, bottom: float, right: float, top: float):
    return PdfPreviewVisualPrimitive(
        page_number=1,
        draw_order=1,
        object_type=object_type,
        bounding_box=PdfBoundingBox(left_pt=left, bottom_pt=bottom, right_pt=right, top_pt=top),
        stroke_color="#000000ff",
        stroke_width_pt=1.0,
        has_stroke=True,
        candidate_roles=roles,
    )


def _table_node() -> dict[str, object]:
    return {
        "type": "table",
        "page number": 1,
        "reading order index": 9,
        "bounding box": [10.0, 10.0, 110.0, 90.0],
    }


def _raw_table_node_with_single_cell() -> dict[str, object]:
    node = _table_node()
    node["number of rows"] = 1
    node["number of columns"] = 1
    node["rows"] = [
        {
            "cells": [
                {
                    "type": "cell",
                    "page number": 1,
                    "row number": 1,
                    "column number": 1,
                    "row span": 1,
                    "column span": 1,
                    "background color": "#ffeeaa",
                    "bounding box": [10.0, 10.0, 110.0, 90.0],
                    "kids": [
                        _paragraph("Bottom", left=14.0, bottom=18.0, right=108.0, top=42.0),
                        _paragraph("Top", left=14.0, bottom=58.0, right=108.0, top=82.0),
                    ],
                }
            ]
        }
    ]
    return node


def _raw_table_node_with_single_cell_sparse_fragments() -> dict[str, object]:
    node = _table_node()
    node["number of rows"] = 1
    node["number of columns"] = 1
    node["rows"] = [
        {
            "cells": [
                {
                    "type": "cell",
                    "page number": 1,
                    "row number": 1,
                    "column number": 1,
                    "row span": 1,
                    "column span": 1,
                    "background color": "#ccddee",
                    "bounding box": [10.0, 10.0, 110.0, 90.0],
                    "kids": [
                        _paragraph("Left", left=14.0, bottom=18.0, right=36.0, top=42.0),
                        _paragraph("Right", left=84.0, bottom=18.0, right=106.0, top=42.0),
                    ],
                }
            ]
        }
    ]
    return node


def _raw_table_node_with_single_empty_styled_cell() -> dict[str, object]:
    node = _table_node()
    node["number of rows"] = 1
    node["number of columns"] = 1
    node["rows"] = [
        {
            "cells": [
                {
                    "type": "cell",
                    "page number": 1,
                    "row number": 1,
                    "column number": 1,
                    "row span": 1,
                    "column span": 1,
                    "background color": "#ddeeff",
                    "bounding box": [10.0, 10.0, 110.0, 90.0],
                    "kids": [],
                }
            ]
        }
    ]
    return node


def _raw_table_node_with_empty_styled_cell() -> dict[str, object]:
    node = _table_node()
    node["number of rows"] = 1
    node["number of columns"] = 2
    node["rows"] = [
        {
            "cells": [
                {
                    "type": "cell",
                    "page number": 1,
                    "row number": 1,
                    "column number": 1,
                    "row span": 1,
                    "column span": 1,
                    "background color": "#aabbcc",
                    "bounding box": [10.0, 10.0, 60.0, 90.0],
                    "kids": [],
                },
                {
                    "type": "cell",
                    "page number": 1,
                    "row number": 1,
                    "column number": 2,
                    "row span": 1,
                    "column span": 1,
                    "bounding box": [60.0, 10.0, 110.0, 90.0],
                    "kids": [_paragraph("Filled", left=66.0, bottom=18.0, right=104.0, top=42.0)],
                },
            ]
        }
    ]
    return node


def _raw_table_node_with_direct_image_child() -> dict[str, object]:
    node = _table_node()
    node["number of rows"] = 1
    node["number of columns"] = 1
    node["rows"] = [
        {
            "cells": [
                {
                    "type": "cell",
                    "page number": 1,
                    "row number": 1,
                    "column number": 1,
                    "row span": 1,
                    "column span": 1,
                    "bounding box": [10.0, 10.0, 110.0, 90.0],
                    "kids": [
                        {
                            "type": "image",
                            "page number": 1,
                            "bounding box": [20.0, 20.0, 100.0, 80.0],
                            "image uri": "data:image/png;base64,AA==",
                            "width px": 8,
                            "height px": 6,
                        }
                    ],
                }
            ]
        }
    ]
    return node


def _paragraph(
    text: str,
    *,
    left: float | None,
    bottom: float | None,
    right: float | None,
    top: float | None,
):
    node = {"type": "paragraph", "content": text, "page number": 1}
    if None not in (left, bottom, right, top):
        node["bounding box"] = [left, bottom, right, top]
    return node


def _span_only_paragraph(text: str, *, left: float, bottom: float, right: float, top: float):
    return {
        "type": "paragraph",
        "content": text,
        "page number": 1,
        "spans": [
            {
                "type": "text chunk",
                "content": text,
                "page number": 1,
                "bounding box": [left, bottom, right, top],
            }
        ],
    }


def _nested_span_only_paragraph(text: str, *, left: float, bottom: float, right: float, top: float):
    return {
        "type": "paragraph",
        "content": text,
        "page number": 1,
        "kids": [
            {
                "type": "group",
                "kids": [
                    {
                        "type": "text chunk",
                        "content": text,
                        "page number": 1,
                        "bounding box": [left, bottom, right, top],
                    }
                ],
            }
        ],
    }


def _wrapped_span_paragraph(
    text: str,
    *,
    wrapper_left: float,
    wrapper_bottom: float,
    wrapper_right: float,
    wrapper_top: float,
    leaf_left: float,
    leaf_bottom: float,
    leaf_right: float,
    leaf_top: float,
):
    return {
        "type": "paragraph",
        "content": text,
        "page number": 1,
        "kids": [
            {
                "type": "group",
                "bounding box": [wrapper_left, wrapper_bottom, wrapper_right, wrapper_top],
                "kids": [
                    {
                        "type": "text chunk",
                        "content": text,
                        "page number": 1,
                        "bounding box": [leaf_left, leaf_bottom, leaf_right, leaf_top],
                    }
                ],
            }
        ],
    }


def _wrapped_run_paragraph(
    text: str,
    *,
    wrapper_left: float,
    wrapper_bottom: float,
    wrapper_right: float,
    wrapper_top: float,
    leaf_left: float,
    leaf_bottom: float,
    leaf_right: float,
    leaf_top: float,
):
    return {
        "type": "paragraph",
        "content": text,
        "page number": 1,
        "kids": [
            {
                "type": "group",
                "bounding box": [wrapper_left, wrapper_bottom, wrapper_right, wrapper_top],
                "kids": [
                    {
                        "type": "run",
                        "content": text,
                        "page number": 1,
                        "bounding box": [leaf_left, leaf_bottom, leaf_right, leaf_top],
                    }
                ],
            }
        ],
    }


def _nested_run_only_paragraph(text: str, *, left: float, bottom: float, right: float, top: float):
    return {
        "type": "paragraph",
        "content": text,
        "page number": 1,
        "kids": [
            {
                "type": "group",
                "kids": [
                    {
                        "type": "run",
                        "content": text,
                        "page number": 1,
                        "bounding box": [left, bottom, right, top],
                    }
                ],
            }
        ],
    }


def _paragraph_with_conflicting_span_bbox(
    text: str,
    *,
    paragraph_left: float,
    paragraph_bottom: float,
    paragraph_right: float,
    paragraph_top: float,
    span_left: float,
    span_bottom: float,
    span_right: float,
    span_top: float,
):
    return {
        "type": "paragraph",
        "content": text,
        "page number": 1,
        "bounding box": [paragraph_left, paragraph_bottom, paragraph_right, paragraph_top],
        "spans": [
            {
                "type": "text chunk",
                "content": text,
                "page number": 1,
                "bounding box": [span_left, span_bottom, span_right, span_top],
            }
        ],
    }


class TableReconstructTests(unittest.TestCase):
    def test_collect_lines_adds_outer_border_lines(self) -> None:
        table_bbox = PdfBoundingBox(left_pt=10.0, bottom_pt=10.0, right_pt=110.0, top_pt=90.0)
        h_lines, v_lines = collect_lines([], table_bbox)

        self.assertIn((90.0, 10.0, 110.0), h_lines)
        self.assertIn((10.0, 10.0, 110.0), h_lines)
        self.assertIn((10.0, 10.0, 90.0), v_lines)
        self.assertIn((110.0, 10.0, 90.0), v_lines)

    def test_reconstruct_table_grid_snaps_vertical_rule_near_left_border_to_outer_border(self) -> None:
        node = _table_node()
        primitives = [
            _primitive(
                object_type="segmented_vertical_rule",
                roles=["vertical_line_segment", "segmented_vertical_rule"],
                left=10.4,
                bottom=12.0,
                right=11.4,
                top=88.0,
            )
        ]

        grid = reconstruct_table_grid(node, primitives)

        self.assertIsNotNone(grid)
        assert grid is not None
        self.assertEqual((grid.row_count, grid.col_count), (1, 1))
        bbox = grid.group_bbox(grid.merge_groups[0])
        self.assertEqual((bbox.left_pt, bbox.bottom_pt, bbox.right_pt, bbox.top_pt), (10.0, 10.0, 110.0, 90.0))

    def test_reconstruct_table_grid_suppresses_vertical_rule_at_x_13_1_near_left_border(self) -> None:
        node = _table_node()
        primitives = [
            _primitive(
                object_type="segmented_vertical_rule",
                roles=["vertical_line_segment", "segmented_vertical_rule"],
                left=12.6,
                bottom=12.0,
                right=14.6,
                top=88.0,
            )
        ]

        grid = reconstruct_table_grid(node, primitives)

        self.assertIsNotNone(grid)
        assert grid is not None
        self.assertEqual((grid.row_count, grid.col_count), (1, 1))
        bbox = grid.group_bbox(grid.merge_groups[0])
        self.assertEqual((bbox.left_pt, bbox.bottom_pt, bbox.right_pt, bbox.top_pt), (10.0, 10.0, 110.0, 90.0))

    def test_reconstruct_table_grid_preserves_vertical_rule_at_x_14_0_near_left_border(self) -> None:
        node = _table_node()
        primitives = [
            _primitive(
                object_type="segmented_vertical_rule",
                roles=["vertical_line_segment", "segmented_vertical_rule"],
                left=13.5,
                bottom=12.0,
                right=14.5,
                top=88.0,
            )
        ]

        grid = reconstruct_table_grid(node, primitives)

        self.assertIsNotNone(grid)
        assert grid is not None
        self.assertEqual((grid.row_count, grid.col_count), (1, 2))
        bbox_left = grid.group_bbox(grid.merge_groups[0])
        self.assertEqual((bbox_left.left_pt, bbox_left.bottom_pt, bbox_left.right_pt, bbox_left.top_pt), (10.0, 10.0, 14.0, 90.0))

    def test_reconstruct_table_grid_suppresses_vertical_rule_at_x_12_5_near_left_border(self) -> None:
        node = _table_node()
        primitives = [
            _primitive(
                object_type="segmented_vertical_rule",
                roles=["vertical_line_segment", "segmented_vertical_rule"],
                left=12.0,
                bottom=12.0,
                right=13.0,
                top=88.0,
            )
        ]

        grid = reconstruct_table_grid(node, primitives)

        self.assertIsNotNone(grid)
        assert grid is not None
        self.assertEqual((grid.row_count, grid.col_count), (1, 1))
        bbox = grid.group_bbox(grid.merge_groups[0])
        self.assertEqual((bbox.left_pt, bbox.bottom_pt, bbox.right_pt, bbox.top_pt), (10.0, 10.0, 110.0, 90.0))

    def test_reconstruct_table_grid_preserves_horizontal_rule_at_y_14_0_near_bottom_border(self) -> None:
        node = _table_node()
        primitives = [
            _primitive(
                object_type="segmented_horizontal_rule",
                roles=["horizontal_line_segment", "segmented_horizontal_rule"],
                left=12.0,
                bottom=13.5,
                right=108.0,
                top=14.5,
            )
        ]

        grid = reconstruct_table_grid(node, primitives)

        self.assertIsNotNone(grid)
        assert grid is not None
        self.assertEqual((grid.row_count, grid.col_count), (2, 1))
        bbox_bottom = grid.group_bbox(grid.merge_groups[0])
        self.assertEqual((bbox_bottom.left_pt, bbox_bottom.bottom_pt, bbox_bottom.right_pt, bbox_bottom.top_pt), (10.0, 10.0, 110.0, 14.0))

    def test_reconstruct_table_grid_returns_none_when_page_number_is_missing(self) -> None:
        node = {
            "type": "table",
            "reading order index": 9,
            "bounding box": [10.0, 10.0, 110.0, 90.0],
        }
        primitives = [
            _primitive(
                object_type="segmented_horizontal_rule",
                roles=["horizontal_line_segment"],
                left=12.0,
                bottom=39.5,
                right=108.0,
                top=40.5,
            )
        ]

        grid = reconstruct_table_grid(node, primitives)

        self.assertIsNone(grid)

    def test_reconstruct_table_grid_splits_rows_from_horizontal_segmented_rules(self) -> None:
        node = _table_node()
        primitives = [
            _primitive(
                object_type="segmented_horizontal_rule",
                roles=["horizontal_line_segment", "segmented_horizontal_rule"],
                left=12.0,
                bottom=39.5,
                right=108.0,
                top=40.5,
            )
        ]

        grid = reconstruct_table_grid(node, primitives)

        self.assertIsNotNone(grid)
        assert grid is not None
        self.assertEqual((grid.row_count, grid.col_count), (2, 1))

    def test_reconstruct_table_grid_returns_none_for_non_rectangular_merge_component(self) -> None:
        node = _table_node()
        primitives = [
            _primitive(
                object_type="segmented_horizontal_rule",
                roles=["horizontal_line_segment"],
                left=12.0,
                bottom=49.5,
                right=60.0,
                top=50.5,
            ),
            _primitive(
                object_type="segmented_vertical_rule",
                roles=["vertical_line_segment"],
                left=59.5,
                bottom=50.5,
                right=60.5,
                top=88.0,
            ),
        ]

        grid = reconstruct_table_grid(node, primitives)

        self.assertIsNone(grid)


class TableReconstructMappingTests(unittest.TestCase):
    def test_assign_fragments_to_groups_ignores_wrapper_bbox_for_nested_span_bbox(self) -> None:
        grid = TableGrid(
            table_key=table_node_key(_table_node()),
            h_y=[10.0, 90.0],
            v_x=[10.0, 60.0, 110.0],
            merge_groups=[MergeGroup(0, 0, 0, 0), MergeGroup(0, 1, 0, 1)],
        )
        fragments = assign_fragments_to_groups(
            raw_cells=[
                {
                    "bounding box": [10.0, 10.0, 55.0, 90.0],
                    "kids": [
                        _wrapped_span_paragraph(
                            "Nested",
                            wrapper_left=10.0,
                            wrapper_bottom=10.0,
                            wrapper_right=110.0,
                            wrapper_top=90.0,
                            leaf_left=66.0,
                            leaf_bottom=18.0,
                            leaf_right=104.0,
                            leaf_top=42.0,
                        )
                    ],
                }
            ],
            grid=grid,
        )

        self.assertEqual([p["content"] for p in fragments[grid.merge_groups[1]]], ["Nested"])

    def test_assign_fragments_to_groups_uses_runs_bbox_fallback(self) -> None:
        grid = TableGrid(
            table_key=table_node_key(_table_node()),
            h_y=[10.0, 90.0],
            v_x=[10.0, 60.0, 110.0],
            merge_groups=[MergeGroup(0, 0, 0, 0), MergeGroup(0, 1, 0, 1)],
        )
        fragments = assign_fragments_to_groups(
            raw_cells=[
                {
                    "bounding box": [10.0, 10.0, 55.0, 90.0],
                    "kids": [
                        _wrapped_run_paragraph(
                            "Nested",
                            wrapper_left=10.0,
                            wrapper_bottom=10.0,
                            wrapper_right=110.0,
                            wrapper_top=90.0,
                            leaf_left=66.0,
                            leaf_bottom=18.0,
                            leaf_right=104.0,
                            leaf_top=42.0,
                        )
                    ],
                }
            ],
            grid=grid,
        )

        self.assertEqual([p["content"] for p in fragments[grid.merge_groups[1]]], ["Nested"])

    def test_assign_fragments_to_groups_uses_paragraph_bbox(self) -> None:
        grid = TableGrid(
            table_key=table_node_key(_table_node()),
            h_y=[10.0, 50.0, 90.0],
            v_x=[10.0, 110.0],
            merge_groups=[MergeGroup(0, 0, 0, 0), MergeGroup(1, 0, 1, 0)],
        )
        fragments = assign_fragments_to_groups(
            raw_cells=[
                {
                    "bounding box": [10.0, 10.0, 110.0, 90.0],
                    "kids": [
                        _paragraph_with_conflicting_span_bbox(
                            "Bottom",
                            paragraph_left=14.0,
                            paragraph_bottom=18.0,
                            paragraph_right=108.0,
                            paragraph_top=42.0,
                            span_left=14.0,
                            span_bottom=58.0,
                            span_right=108.0,
                            span_top=82.0,
                        ),
                        _paragraph_with_conflicting_span_bbox(
                            "Top",
                            paragraph_left=14.0,
                            paragraph_bottom=58.0,
                            paragraph_right=108.0,
                            paragraph_top=82.0,
                            span_left=14.0,
                            span_bottom=18.0,
                            span_right=108.0,
                            span_top=42.0,
                        ),
                    ],
                }
            ],
            grid=grid,
        )

        self.assertEqual([p["content"] for p in fragments[grid.merge_groups[0]]], ["Bottom"])
        self.assertEqual([p["content"] for p in fragments[grid.merge_groups[1]]], ["Top"])

    def test_assign_fragments_to_groups_uses_descendant_span_bbox_fallback(self) -> None:
        grid = TableGrid(
            table_key=table_node_key(_table_node()),
            h_y=[10.0, 50.0, 90.0],
            v_x=[10.0, 60.0],
            merge_groups=[MergeGroup(0, 0, 0, 0), MergeGroup(1, 0, 1, 0)],
        )
        fragments = assign_fragments_to_groups(
            raw_cells=[
                {
                    "bounding box": [10.0, 10.0, 60.0, 90.0],
                    "kids": [
                        _span_only_paragraph("Bottom", left=14.0, bottom=18.0, right=58.0, top=42.0),
                        _span_only_paragraph("Top", left=14.0, bottom=58.0, right=58.0, top=82.0),
                    ],
                }
            ],
            grid=grid,
        )

        self.assertEqual([p["content"] for p in fragments[grid.merge_groups[0]]], ["Bottom"])
        self.assertEqual([p["content"] for p in fragments[grid.merge_groups[1]]], ["Top"])

    def test_assign_fragments_to_groups_returns_empty_mapping_when_center_is_ambiguous(self) -> None:
        grid = TableGrid(
            table_key=table_node_key(_table_node()),
            h_y=[10.0, 90.0],
            v_x=[10.0, 60.0, 110.0],
            merge_groups=[MergeGroup(0, 0, 0, 0), MergeGroup(0, 1, 0, 1)],
        )
        fragments = assign_fragments_to_groups(
            raw_cells=[
                {
                    "bounding box": [10.0, 10.0, 110.0, 90.0],
                    "kids": [_paragraph("Ambiguous", left=50.0, bottom=20.0, right=70.0, top=30.0)],
                }
            ],
            grid=grid,
        )

        self.assertEqual(fragments, {})


class TableReconstructAdapterTests(unittest.TestCase):
    def test_adapter_emits_cells_from_reconstructed_grid_with_raw_order_preserved(self) -> None:
        node = _raw_table_node_with_single_cell()
        grid = TableGrid(
            table_key=table_node_key(node),
            h_y=[10.0, 50.0, 90.0],
            v_x=[10.0, 110.0],
            merge_groups=[MergeGroup(0, 0, 0, 0), MergeGroup(1, 0, 1, 0)],
        )

        table = odl_adapter._table_node_to_ir(
            node,
            unit_id="tbl1",
            assets={},
            table_grids={table_node_key(node): grid},
        )

        self.assertEqual((table.row_count, table.col_count), (2, 1))
        self.assertEqual([(cell.row_index, cell.col_index, cell.text) for cell in table.cells], [(1, 1, "Bottom"), (2, 1, "Top")])
        self.assertEqual([cell.paragraphs[0].text for cell in table.cells], ["Bottom", "Top"])
        self.assertEqual(
            [
                (cell.bbox.left_pt, cell.bbox.bottom_pt, cell.bbox.right_pt, cell.bbox.top_pt)
                for cell in table.cells
                if cell.bbox is not None
            ],
            [(10.0, 10.0, 110.0, 50.0), (10.0, 50.0, 110.0, 90.0)],
        )
        self.assertEqual(
            [cell.cell_style.background if cell.cell_style is not None else None for cell in table.cells],
            ["#ffeeaa", "#ffeeaa"],
        )
        self.assertEqual([cell.meta.page_number if cell.meta is not None else None for cell in table.cells], [1, 1])
        self.assertEqual(
            [
                (
                    cell.meta.bounding_box.left_pt,
                    cell.meta.bounding_box.bottom_pt,
                    cell.meta.bounding_box.right_pt,
                    cell.meta.bounding_box.top_pt,
                )
                if cell.meta is not None and cell.meta.bounding_box is not None
                else None
                for cell in table.cells
            ],
            [(10.0, 10.0, 110.0, 50.0), (10.0, 50.0, 110.0, 90.0)],
        )

    def test_adapter_empty_sibling_group_inherits_representative_raw_style_and_meta(self) -> None:
        node = _raw_table_node_with_single_cell_sparse_fragments()
        grid = TableGrid(
            table_key=table_node_key(node),
            h_y=[10.0, 90.0],
            v_x=[10.0, 40.0, 80.0, 110.0],
            merge_groups=[
                MergeGroup(0, 0, 0, 0),
                MergeGroup(0, 1, 0, 1),
                MergeGroup(0, 2, 0, 2),
            ],
        )

        table = odl_adapter._table_node_to_ir(
            node,
            unit_id="tbl1",
            assets={},
            table_grids={table_node_key(node): grid},
        )

        self.assertEqual((table.row_count, table.col_count), (1, 3))
        self.assertEqual([(cell.row_index, cell.col_index, cell.text) for cell in table.cells], [(1, 1, "Left"), (1, 2, ""), (1, 3, "Right")])
        self.assertIsNotNone(table.cells[1].cell_style)
        self.assertEqual(table.cells[1].cell_style.background, "#ccddee")
        self.assertIsNotNone(table.cells[1].meta)
        self.assertEqual(table.cells[1].meta.page_number, 1)
        self.assertIsNotNone(table.cells[1].meta.bounding_box)
        self.assertEqual(
            (
                table.cells[1].meta.bounding_box.left_pt,
                table.cells[1].meta.bounding_box.bottom_pt,
                table.cells[1].meta.bounding_box.right_pt,
                table.cells[1].meta.bounding_box.top_pt,
            ),
            (40.0, 10.0, 80.0, 90.0),
        )

    def test_adapter_falls_back_to_raw_topology_when_group_assignment_is_empty(self) -> None:
        node = _raw_table_node_with_single_cell()
        grid = TableGrid(
            table_key=table_node_key(node),
            h_y=[10.0, 50.0, 90.0],
            v_x=[10.0, 110.0],
            merge_groups=[MergeGroup(0, 0, 0, 0), MergeGroup(1, 0, 1, 0)],
        )

        with patch.object(odl_adapter, "assign_fragments_to_groups", return_value={}):
            table = odl_adapter._table_node_to_ir(
                node,
                unit_id="tbl1",
                assets={},
                table_grids={table_node_key(node): grid},
            )

        self.assertEqual((table.row_count, table.col_count), (1, 1))
        self.assertEqual(len(table.cells), 1)
        self.assertEqual(table.cells[0].row_index, 1)
        self.assertEqual(table.cells[0].col_index, 1)
        self.assertEqual(table.cells[0].text, "Bottom\nTop")
        self.assertEqual([paragraph.text for paragraph in table.cells[0].paragraphs], ["Bottom", "Top"])

    def test_adapter_reconstructed_empty_cell_keeps_representative_raw_style_and_meta(self) -> None:
        node = _raw_table_node_with_empty_styled_cell()
        grid = TableGrid(
            table_key=table_node_key(node),
            h_y=[10.0, 90.0],
            v_x=[10.0, 60.0, 110.0],
            merge_groups=[MergeGroup(0, 0, 0, 0), MergeGroup(0, 1, 0, 1)],
        )

        table = odl_adapter._table_node_to_ir(
            node,
            unit_id="tbl1",
            assets={},
            table_grids={table_node_key(node): grid},
        )

        self.assertEqual(len(table.cells), 2)
        self.assertEqual([(cell.row_index, cell.col_index) for cell in table.cells], [(1, 1), (1, 2)])
        self.assertEqual(table.cells[0].text, "")
        self.assertIsNotNone(table.cells[0].cell_style)
        self.assertEqual(table.cells[0].cell_style.background, "#aabbcc")
        self.assertIsNotNone(table.cells[0].bbox)
        self.assertEqual(
            (
                table.cells[0].bbox.left_pt,
                table.cells[0].bbox.bottom_pt,
                table.cells[0].bbox.right_pt,
                table.cells[0].bbox.top_pt,
            ),
            (10.0, 10.0, 60.0, 90.0),
        )

    def test_adapter_keeps_reconstructed_grid_for_fully_empty_style_only_table(self) -> None:
        node = _raw_table_node_with_single_empty_styled_cell()
        grid = TableGrid(
            table_key=table_node_key(node),
            h_y=[10.0, 90.0],
            v_x=[10.0, 60.0, 110.0],
            merge_groups=[MergeGroup(0, 0, 0, 0), MergeGroup(0, 1, 0, 1)],
        )

        table = odl_adapter._table_node_to_ir(
            node,
            unit_id="tbl1",
            assets={},
            table_grids={table_node_key(node): grid},
        )

        self.assertEqual((table.row_count, table.col_count), (1, 2))
        self.assertEqual(len(table.cells), 2)
        self.assertEqual([(cell.row_index, cell.col_index, cell.text) for cell in table.cells], [(1, 1, ""), (1, 2, "")])
        self.assertEqual(
            [cell.cell_style.background if cell.cell_style is not None else None for cell in table.cells],
            ["#ddeeff", "#ddeeff"],
        )
        self.assertEqual(
            [
                (cell.bbox.left_pt, cell.bbox.bottom_pt, cell.bbox.right_pt, cell.bbox.top_pt)
                for cell in table.cells
                if cell.bbox is not None
            ],
            [(10.0, 10.0, 60.0, 90.0), (60.0, 10.0, 110.0, 90.0)],
        )
        self.assertIsNotNone(table.cells[0].meta)
        self.assertEqual(table.cells[0].meta.page_number, 1)
        self.assertIsNotNone(table.cells[0].meta.bounding_box)
        self.assertEqual(
            (
                table.cells[0].meta.bounding_box.left_pt,
                table.cells[0].meta.bounding_box.bottom_pt,
                table.cells[0].meta.bounding_box.right_pt,
                table.cells[0].meta.bounding_box.top_pt,
            ),
            (10.0, 10.0, 60.0, 90.0),
        )

    def test_adapter_falls_back_to_raw_topology_for_direct_image_child(self) -> None:
        node = _raw_table_node_with_direct_image_child()
        grid = TableGrid(
            table_key=table_node_key(node),
            h_y=[10.0, 90.0],
            v_x=[10.0, 60.0, 110.0],
            merge_groups=[MergeGroup(0, 0, 0, 0), MergeGroup(0, 1, 0, 1)],
        )

        table = odl_adapter._table_node_to_ir(
            node,
            unit_id="tbl1",
            assets={},
            table_grids={table_node_key(node): grid},
        )

        self.assertEqual((table.row_count, table.col_count), (1, 1))
        self.assertEqual(len(table.cells), 1)
        self.assertEqual(len(table.cells[0].paragraphs), 1)
        self.assertEqual(len(table.cells[0].paragraphs[0].images), 1)
        self.assertEqual(table.cells[0].paragraphs[0].images[0].bbox.left_pt, 20.0)
