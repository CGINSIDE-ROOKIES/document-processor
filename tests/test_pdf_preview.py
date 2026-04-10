from __future__ import annotations

from pathlib import Path
import sys
import unittest

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor.pdf.odl import build_doc_ir_from_odl_result
from document_processor.pdf.preview import (
    build_pdf_preview_context,
    render_pdf_preview_html,
)


class PdfPreviewTests(unittest.TestCase):
    def test_build_pdf_preview_context_collects_layout_regions_and_table_geometry(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "layout regions": [
                {
                    "region id": "p1-left",
                    "region type": "left",
                    "page number": 1,
                    "bounding box": [0, 0, 120, 200],
                },
                {
                    "region id": "p1-right",
                    "region type": "right",
                    "page number": 1,
                    "bounding box": [130, 0, 250, 200],
                },
            ],
            "kids": [
                {
                    "type": "table",
                    "page number": 1,
                    "layout region id": "p1-left",
                    "reading order index": 3,
                    "bounding box": [10, 20, 110, 120],
                    "grid row boundaries": [120, 90, 60],
                    "grid column boundaries": [10, 60, 110],
                    "serialized cell count": 4,
                    "logical cell count": 4,
                    "covered logical cell count": 4,
                    "rows": [],
                    "line arts": [
                        {
                            "bounding box": [10, 20, 110, 21],
                        }
                    ],
                }
            ],
        }

        context = build_pdf_preview_context(raw_document)

        self.assertEqual(len(context.layout_regions), 2)
        self.assertEqual(context.layout_regions[0].region_id, "p1-left")
        self.assertEqual(context.tables[0].layout_region_id, "p1-left")
        self.assertEqual(context.tables[0].reading_order_index, 3)
        self.assertEqual(context.tables[0].grid_column_boundaries, [10.0, 60.0, 110.0])
        self.assertEqual(context.tables[0].line_art_boxes[0].top_pt, 21.0)

    def test_render_pdf_preview_html_uses_region_layout_and_table_geometry(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "pages": [
                {"page number": 1, "width pt": 250, "height pt": 200},
            ],
            "layout regions": [
                {
                    "region id": "p1-left",
                    "region type": "left",
                    "page number": 1,
                    "bounding box": [0, 0, 100, 200],
                },
                {
                    "region id": "p1-right",
                    "region type": "right",
                    "page number": 1,
                    "bounding box": [120, 0, 250, 200],
                },
            ],
            "kids": [
                {
                    "type": "paragraph",
                    "content": "Left body",
                    "page number": 1,
                    "layout region id": "p1-left",
                    "reading order index": 1,
                },
                {
                    "type": "paragraph",
                    "content": "Right body",
                    "page number": 1,
                    "layout region id": "p1-right",
                    "reading order index": 2,
                },
                {
                    "type": "table",
                    "page number": 1,
                    "layout region id": "p1-left",
                    "reading order index": 3,
                    "bounding box": [10, 20, 110, 120],
                    "number of rows": 1,
                    "number of columns": 2,
                    "grid row boundaries": [120, 90],
                    "grid column boundaries": [10, 60, 110],
                    "rows": [
                        {
                            "cells": [
                                {
                                    "type": "table cell",
                                    "row number": 1,
                                    "column number": 1,
                                    "page number": 1,
                                    "kids": [{"type": "paragraph", "content": "A1", "page number": 1}],
                                },
                                {
                                    "type": "table cell",
                                    "row number": 1,
                                    "column number": 2,
                                    "page number": 1,
                                    "kids": [{"type": "paragraph", "content": "B1", "page number": 1}],
                                },
                            ]
                        }
                    ],
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
        context = build_pdf_preview_context(raw_document)

        html = render_pdf_preview_html(doc, preview_context=context, title="Preview")

        self.assertIn("document-region-band--columns", html)
        self.assertIn("Left body", html)
        self.assertIn("Right body", html)
        self.assertIn("grid-template-columns:43.48% 56.52%", html)
        self.assertIn("width:50.0pt", html)
        self.assertIn("A1", html)
        self.assertIn("B1", html)


if __name__ == "__main__":
    unittest.main()
