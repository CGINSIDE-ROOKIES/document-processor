from __future__ import annotations

import importlib
from pathlib import Path
import sys
import unittest

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class PdfPreviewModuleApiTests(unittest.TestCase):
    def test_preview_submodules_export_expected_compatibility_api(self) -> None:
        expected_exports = {
            "document_processor.pdf.preview.models": {
                "PdfPreviewContext": "document_processor.pdf.preview",
                "PdfPreviewVisualPrimitive": "document_processor.pdf.preview",
                "PdfPreviewVisualBlockCandidate": "document_processor.pdf.preview",
            },
            "document_processor.pdf.preview.context": {
                "build_pdf_preview_context": "document_processor.pdf.preview",
                "_layout_regions_from_raw": "document_processor.pdf.preview",
                "_collect_table_preview_context": "document_processor.pdf.preview",
                "_table_preview_context_from_node": "document_processor.pdf.preview",
                "_float_list": "document_processor.pdf.preview",
                "_line_art_boxes": "document_processor.pdf.preview",
                "_augment_layout_regions_with_pdfium": "document_processor.pdf.preview",
                "_detect_pdfium_split_regions": "document_processor.pdf.preview",
            },
            "document_processor.pdf.preview.primitives": {
                "_extract_pdfium_visual_primitives": "document_processor.pdf.preview",
                "_build_segmented_rule_primitives": "document_processor.pdf.preview",
                "_build_axis_box_edge_primitives": "document_processor.pdf.preview",
                "_segmented_rule_can_extend": "document_processor.pdf.preview",
                "_build_segmented_rule_primitive": "document_processor.pdf.preview",
                "_pdfium_object_type_name": "document_processor.pdf.preview",
                "_pdfium_color": "document_processor.pdf.preview",
                "_pdfium_stroke_width": "document_processor.pdf.preview",
                "_pdfium_has_fill": "document_processor.pdf.preview",
                "_pdfium_has_stroke": "document_processor.pdf.preview",
                "_pdfium_is_axis_aligned_box": "document_processor.pdf.preview",
                "_pdfium_path_points": "document_processor.pdf.preview",
                "_candidate_roles_for_visual_primitive": "document_processor.pdf.preview",
            },
            "document_processor.pdf.preview.candidates": {
                "_build_visual_block_candidates": "document_processor.pdf.preview",
                "_connected_line_components": "document_processor.pdf.preview",
                "_dedupe_line_primitives_for_graph": "document_processor.pdf.preview",
                "_line_primitives_are_graph_duplicates": "document_processor.pdf.preview",
                "_build_axis_box_candidates_from_component": "document_processor.pdf.preview",
                "_find_axis_box_seed_bboxes_from_component": "document_processor.pdf.preview",
                "_build_non_box_line_candidates": "document_processor.pdf.preview",
                "_component_has_box_outline": "document_processor.pdf.preview",
                "_dedupe_visual_block_candidates": "document_processor.pdf.preview",
                "_suppress_boundary_semantic_lines": "document_processor.pdf.preview",
                "_semantic_line_matches_structure_boundary": "document_processor.pdf.preview",
            },
            "document_processor.pdf.preview.layout": {
                "_build_logical_pages_for_page": "document_processor.pdf.preview",
            },
            "document_processor.pdf.preview.compose": {
                "_compose_logical_page": "document_processor.pdf.preview",
            },
            "document_processor.pdf.preview.render": {
                "render_pdf_html": "document_processor.pdf.preview",
                "render_pdf_preview_html": "document_processor.pdf.preview",
                "render_pdf_preview_html_from_file": "document_processor.pdf.preview",
            },
            "document_processor.pdf.preview.prepare": {
                "prepare_pdf_for_html": "document_processor.pdf.preview",
                "enrich_pdf_table_borders": "document_processor.pdf.enhancement",
                "enrich_pdf_table_backgrounds": "document_processor.pdf.enhancement",
            },
        }

        for module_name, exports in expected_exports.items():
            module = importlib.import_module(module_name)
            self.assertIsNotNone(module)
            module_all = getattr(module, "__all__", [])
            for export_name, source_module_name in exports.items():
                self.assertIn(export_name, module_all)
                self.assertTrue(hasattr(module, export_name))
                source_module = importlib.import_module(source_module_name)
                self.assertIs(getattr(module, export_name), getattr(source_module, export_name))
