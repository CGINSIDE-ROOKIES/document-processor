from __future__ import annotations

from pathlib import Path
import sys
import unittest

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor.pdf.meta import PdfBoundingBox
from document_processor.pdf.odl.table_reconstruct import (
    MergeGroup,
    collect_lines,
    reconstruct_table_grid,
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
