from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor.pdf.odl import build_doc_ir_from_odl_result
from document_processor.pdf.meta import PdfBoundingBox
from document_processor.pdf.preview.analyze import (
    _build_visual_block_candidates,
    _connected_line_components,
    _extract_pdfium_visual_primitives,
)
from document_processor.pdf.preview.context import build_pdf_preview_context
from document_processor.pdf.preview.models import (
    PdfPreviewVisualBlockCandidate,
    PdfPreviewVisualPrimitive,
)
from document_processor.pdf.preview.normalize import (
    _build_logical_pages_for_page,
    prepare_pdf_for_html,
)
from document_processor.pdf.preview.render import render_pdf_html, render_pdf_preview_html


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
        self.assertNotIn("visual_primitives", context.model_dump())

    def test_build_logical_pages_for_page_uses_single_page_fallback_without_regions(self) -> None:
        page = build_doc_ir_from_odl_result(
            {
                "file name": "sample.pdf",
                "number of pages": 1,
                "pages": [{"page number": 1, "width pt": 250, "height pt": 200}],
                "kids": [],
            },
            source_path="sample.pdf",
        ).pages[0]

        logical_pages = _build_logical_pages_for_page(page, [])

        self.assertEqual(len(logical_pages), 1)
        self.assertEqual(logical_pages[0].logical_page_type, "single")
        self.assertEqual(logical_pages[0].source_region_ids, [])
        self.assertEqual(logical_pages[0].bounding_box.left_pt, 0.0)
        self.assertEqual(logical_pages[0].bounding_box.right_pt, 250.0)

    def test_build_logical_pages_for_page_splits_left_and_right_regions(self) -> None:
        doc = build_doc_ir_from_odl_result(
            {
                "file name": "sample.pdf",
                "number of pages": 1,
                "pages": [{"page number": 1, "width pt": 250, "height pt": 200}],
                "kids": [
                    {
                        "type": "paragraph",
                        "content": "- 1 -",
                        "page number": 1,
                        "bounding box": [30, 5, 50, 15],
                    },
                    {
                        "type": "paragraph",
                        "content": "- 2 -",
                        "page number": 1,
                        "bounding box": [190, 5, 210, 15],
                    },
                ],
            },
            source_path="sample.pdf",
        )
        page = doc.pages[0]
        context = build_pdf_preview_context(
            {
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
                    {
                        "region id": "p1-full",
                        "region type": "full",
                        "page number": 1,
                        "bounding box": [0, 0, 250, 200],
                    },
                ]
            }
        )

        logical_pages = _build_logical_pages_for_page(page, context.layout_regions, page_paragraphs=doc.paragraphs)

        self.assertEqual([logical_page.logical_page_type for logical_page in logical_pages], ["left", "right"])
        self.assertEqual(logical_pages[0].source_region_ids, ["p1-left"])
        self.assertEqual(logical_pages[1].source_region_ids, ["p1-right"])

    def test_prepare_pdf_for_html_normalizes_spread_pdf_into_logical_pages(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "pages": [
                {
                    "page number": 1,
                    "width pt": 250,
                    "height pt": 200,
                    "margin left pt": 11,
                    "margin right pt": 12,
                    "margin top pt": 13,
                    "margin bottom pt": 14,
                },
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
                    "type": "paragraph",
                    "content": "- 1 -",
                    "page number": 1,
                    "bounding box": [30, 5, 50, 15],
                    "reading order index": 98,
                },
                {
                    "type": "paragraph",
                    "content": "- 2 -",
                    "page number": 1,
                    "bounding box": [190, 5, 210, 15],
                    "reading order index": 99,
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
        context = build_pdf_preview_context(raw_document)

        prepare_pdf_for_html(doc, preview_context=context)

        self.assertEqual([page.page_number for page in doc.pages], [1, 2])
        self.assertEqual(
            sorted((paragraph.text.strip(), paragraph.page_number) for paragraph in doc.paragraphs),
            [
                ("- 1 -", 1),
                ("- 2 -", 2),
                ("Left body", 1),
                ("Right body", 2),
            ],
        )
        self.assertEqual([page.width_pt for page in doc.pages], [200.0, 200.0])
        self.assertEqual(
            [
                (
                    round(page.margin_left_pt or 0.0, 1),
                    round(page.margin_right_pt or 0.0, 1),
                    round(page.margin_top_pt or 0.0, 1),
                    round(page.margin_bottom_pt or 0.0, 1),
                )
                for page in doc.pages
            ],
            [(20.0, 21.8, 23.6, 25.5), (15.7, 17.1, 18.6, 20.0)],
        )
        self.assertEqual(context.layout_regions, [])

    def test_prepare_pdf_for_html_scales_split_spread_back_up_after_rebasing(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "pages": [
                {
                    "page number": 1,
                    "width pt": 300,
                    "height pt": 200,
                    "margin left pt": 10,
                    "margin right pt": 12,
                    "margin top pt": 8,
                    "margin bottom pt": 9,
                },
            ],
            "layout regions": [
                {
                    "region id": "p1-left",
                    "region type": "left",
                    "page number": 1,
                    "bounding box": [0, 0, 140, 200],
                },
                {
                    "region id": "p1-right",
                    "region type": "right",
                    "page number": 1,
                    "bounding box": [160, 0, 300, 200],
                },
            ],
            "kids": [
                {
                    "type": "paragraph",
                    "content": "Left body",
                    "page number": 1,
                    "layout region id": "p1-left",
                    "bounding box": [20, 60, 80, 80],
                    "font size": 9,
                    "reading order index": 1,
                },
                {
                    "type": "table",
                    "page number": 1,
                    "layout region id": "p1-left",
                    "reading order index": 2,
                    "bounding box": [20, 90, 130, 150],
                    "width pt": 110,
                    "height pt": 60,
                    "number of rows": 1,
                    "number of columns": 1,
                    "rows": [
                        {
                            "cells": [
                                {
                                    "type": "table cell",
                                    "row number": 1,
                                    "column number": 1,
                                    "page number": 1,
                                    "width pt": 110,
                                    "height pt": 60,
                                    "kids": [
                                        {
                                            "type": "paragraph",
                                            "content": "Cell",
                                            "page number": 1,
                                            "font size": 8,
                                        }
                                    ],
                                }
                            ]
                        }
                    ],
                },
                {
                    "type": "paragraph",
                    "content": "- 1 -",
                    "page number": 1,
                    "bounding box": [30, 5, 50, 15],
                    "reading order index": 98,
                },
                {
                    "type": "paragraph",
                    "content": "- 2 -",
                    "page number": 1,
                    "bounding box": [250, 5, 270, 15],
                    "reading order index": 99,
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
        context = build_pdf_preview_context(raw_document)

        prepare_pdf_for_html(doc, preview_context=context)

        self.assertEqual([page.page_number for page in doc.pages], [1, 2])
        self.assertAlmostEqual(doc.pages[0].width_pt or 0.0, 200.0, places=1)
        self.assertAlmostEqual(doc.pages[0].height_pt or 0.0, 266.7, places=1)
        self.assertAlmostEqual(doc.pages[0].margin_left_pt or 0.0, 13.3, places=1)
        self.assertAlmostEqual(doc.pages[0].margin_right_pt or 0.0, 16.0, places=1)
        self.assertAlmostEqual(doc.pages[0].margin_top_pt or 0.0, 10.7, places=1)
        self.assertAlmostEqual(doc.pages[0].margin_bottom_pt or 0.0, 12.0, places=1)

        left_body = next(paragraph for paragraph in doc.paragraphs if paragraph.text.strip() == "Left body")
        self.assertEqual(left_body.page_number, 1)
        self.assertIsNotNone(left_body.bbox)
        self.assertAlmostEqual(left_body.bbox.left_pt, 26.7, places=1)
        self.assertAlmostEqual(left_body.bbox.right_pt, 106.7, places=1)
        self.assertAlmostEqual(left_body.bbox.top_pt, 106.7, places=1)
        self.assertAlmostEqual(left_body.bbox.bottom_pt, 80.0, places=1)
        self.assertEqual(len(left_body.runs), 1)
        self.assertIsNotNone(left_body.runs[0].run_style)
        self.assertAlmostEqual(left_body.runs[0].run_style.size_pt or 0.0, 12.0, places=1)

        scaled_table = next(
            table
            for paragraph in doc.paragraphs
            for table in paragraph.tables
            if table.unit_id == "p2.tbl1"
        )
        self.assertIsNotNone(scaled_table.table_style)
        self.assertAlmostEqual(scaled_table.table_style.width_pt or 0.0, 146.7, places=1)
        self.assertAlmostEqual(scaled_table.table_style.height_pt or 0.0, 80.0, places=1)
        self.assertEqual(len(scaled_table.cells), 1)
        self.assertIsNotNone(scaled_table.cells[0].cell_style)
        self.assertAlmostEqual(scaled_table.cells[0].cell_style.width_pt or 0.0, 146.7, places=1)
        self.assertAlmostEqual(scaled_table.cells[0].cell_style.height_pt or 0.0, 80.0, places=1)

    def test_prepare_pdf_for_html_keeps_portrait_page_single_when_side_regions_are_columns(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "pages": [
                {"page number": 1, "width pt": 200, "height pt": 250},
            ],
            "layout regions": [
                {
                    "region id": "p1-main",
                    "region type": "main",
                    "page number": 1,
                    "bounding box": [20, 0, 180, 250],
                },
                {
                    "region id": "p1-left",
                    "region type": "left",
                    "page number": 1,
                    "bounding box": [20, 20, 90, 230],
                },
                {
                    "region id": "p1-right",
                    "region type": "right",
                    "page number": 1,
                    "bounding box": [110, 20, 180, 230],
                },
            ],
            "kids": [
                {
                    "type": "paragraph",
                    "content": "Column A",
                    "page number": 1,
                    "layout region id": "p1-left",
                    "reading order index": 1,
                },
                {
                    "type": "paragraph",
                    "content": "Column B",
                    "page number": 1,
                    "layout region id": "p1-right",
                    "reading order index": 2,
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
        context = build_pdf_preview_context(raw_document)

        prepare_pdf_for_html(doc, preview_context=context)

        self.assertEqual([page.page_number for page in doc.pages], [1])
        self.assertEqual([page.width_pt for page in doc.pages], [200.0])
        self.assertEqual([paragraph.page_number for paragraph in doc.paragraphs], [1])
        self.assertEqual(len(doc.paragraphs[0].tables), 1)
        self.assertEqual(context.layout_regions, [])

    def test_prepare_pdf_for_html_keeps_landscape_page_single_without_footer_page_number_pair(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "pages": [
                {
                    "page number": 1,
                    "width pt": 250,
                    "height pt": 200,
                    "margin left pt": 9,
                    "margin right pt": 10,
                    "margin top pt": 11,
                    "margin bottom pt": 12,
                },
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
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
        context = build_pdf_preview_context(raw_document)

        prepare_pdf_for_html(doc, preview_context=context)

        self.assertEqual([page.page_number for page in doc.pages], [1])
        self.assertEqual([page.width_pt for page in doc.pages], [250.0])
        self.assertEqual(
            [
                (
                    doc.pages[0].margin_left_pt,
                    doc.pages[0].margin_right_pt,
                    doc.pages[0].margin_top_pt,
                    doc.pages[0].margin_bottom_pt,
                )
            ],
            [(9.0, 10.0, 11.0, 12.0)],
        )
        self.assertEqual([paragraph.page_number for paragraph in doc.paragraphs], [1])
        self.assertEqual(len(doc.paragraphs[0].tables), 1)

    def test_prepare_pdf_for_html_preserves_reading_order_for_single_page_column_flow(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "pages": [
                {"page number": 1, "width pt": 200, "height pt": 250},
            ],
            "layout regions": [
                {
                    "region id": "p1-main",
                    "region type": "main",
                    "page number": 1,
                    "bounding box": [20, 0, 180, 250],
                },
                {
                    "region id": "p1-left",
                    "region type": "left",
                    "page number": 1,
                    "bounding box": [20, 20, 90, 230],
                },
                {
                    "region id": "p1-right",
                    "region type": "right",
                    "page number": 1,
                    "bounding box": [110, 20, 180, 230],
                },
            ],
            "kids": [
                {
                    "type": "paragraph",
                    "content": "Left column first",
                    "page number": 1,
                    "layout region id": "p1-left",
                    "reading order index": 1,
                    "bounding box": [20, 30, 90, 120],
                },
                {
                    "type": "paragraph",
                    "content": "Right column second",
                    "page number": 1,
                    "layout region id": "p1-right",
                    "reading order index": 2,
                    "bounding box": [110, 140, 180, 220],
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
        context = build_pdf_preview_context(raw_document)

        prepare_pdf_for_html(doc, preview_context=context)

        self.assertEqual([page.page_number for page in doc.pages], [1])
        self.assertEqual(len(doc.paragraphs), 1)
        self.assertEqual(len(doc.paragraphs[0].tables), 1)
        cells = doc.paragraphs[0].tables[0].cells
        self.assertEqual(cells[0].text.strip(), "Left column first")
        self.assertEqual(cells[2].text.strip(), "Right column second")

    def test_prepare_pdf_for_html_preserves_missing_page_margins_when_pdf_margin_metadata_missing(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "pages": [
                {"page number": 1, "width pt": 200, "height pt": 250},
            ],
            "kids": [
                {
                    "type": "paragraph",
                    "content": "Body",
                    "page number": 1,
                    "reading order index": 1,
                }
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
        context = build_pdf_preview_context(raw_document)

        prepare_pdf_for_html(doc, preview_context=context)

        self.assertEqual(len(doc.pages), 1)
        self.assertEqual(
            (
                doc.pages[0].margin_left_pt,
                doc.pages[0].margin_right_pt,
                doc.pages[0].margin_top_pt,
                doc.pages[0].margin_bottom_pt,
            ),
            (None, None, None, None),
        )

    def test_prepare_pdf_for_html_detects_mid_page_two_column_band_without_page_side_regions(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "pages": [
                {"page number": 1, "width pt": 400, "height pt": 600},
            ],
            "layout regions": [
                {
                    "region id": "p1-main",
                    "region type": "main",
                    "page number": 1,
                    "bounding box": [20, 0, 380, 600],
                },
            ],
            "kids": [
                {
                    "type": "paragraph",
                    "content": "Full width intro",
                    "page number": 1,
                    "reading order index": 1,
                    "bounding box": [20, 520, 380, 560],
                },
                {
                    "type": "paragraph",
                    "content": "Left column heading",
                    "page number": 1,
                    "reading order index": 2,
                    "bounding box": [20, 360, 160, 380],
                },
                {
                    "type": "paragraph",
                    "content": "Right column heading",
                    "page number": 1,
                    "reading order index": 3,
                    "bounding box": [240, 360, 380, 380],
                },
                {
                    "type": "paragraph",
                    "content": "Left column body",
                    "page number": 1,
                    "reading order index": 4,
                    "bounding box": [20, 140, 180, 350],
                },
                {
                    "type": "paragraph",
                    "content": "Right column body",
                    "page number": 1,
                    "reading order index": 5,
                    "bounding box": [240, 140, 380, 350],
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
        context = build_pdf_preview_context(raw_document)

        prepare_pdf_for_html(doc, preview_context=context)

        self.assertEqual([page.page_number for page in doc.pages], [1])
        self.assertEqual(len(doc.paragraphs), 2)
        self.assertEqual(doc.paragraphs[0].text.strip(), "Full width intro")
        self.assertEqual(len(doc.paragraphs[1].tables), 1)
        cells = doc.paragraphs[1].tables[0].cells
        self.assertIn("Left column heading", cells[0].text)
        self.assertIn("Left column body", cells[0].text)
        self.assertIn("Right column heading", cells[2].text)
        self.assertIn("Right column body", cells[2].text)

    def test_prepare_pdf_for_html_groups_consecutive_image_strips_into_single_paragraph(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "pages": [
                {"page number": 1, "width pt": 300, "height pt": 400},
            ],
            "kids": [
                {
                    "type": "image",
                    "page number": 1,
                    "bounding box": [100, 260, 220, 264],
                    "data": "data:image/png;base64,AAAA",
                    "width px": 120,
                    "height px": 4,
                },
                {
                    "type": "image",
                    "page number": 1,
                    "bounding box": [100, 256, 220, 260],
                    "data": "data:image/png;base64,AAAA",
                    "width px": 120,
                    "height px": 4,
                },
                {
                    "type": "image",
                    "page number": 1,
                    "bounding box": [100, 252, 220, 256],
                    "data": "data:image/png;base64,AAAA",
                    "width px": 120,
                    "height px": 4,
                },
                {
                    "type": "paragraph",
                    "content": "Figure caption",
                    "page number": 1,
                    "reading order index": 4,
                    "bounding box": [100, 236, 220, 246],
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
        context = build_pdf_preview_context(raw_document)

        prepare_pdf_for_html(doc, preview_context=context)

        self.assertEqual(len(doc.paragraphs), 2)
        self.assertIn("Figure caption", [paragraph.text.strip() for paragraph in doc.paragraphs])
        merged_images = [paragraph for paragraph in doc.paragraphs if len(paragraph.images) == 3]
        self.assertEqual(len(merged_images), 1)

    def test_extract_pdfium_visual_primitives_collects_box_metadata(self) -> None:
        class _FakeSegment:
            def __init__(self, segment_type: int, x: float, y: float, *, close: bool = False) -> None:
                self.segment_type = segment_type
                self.x = x
                self.y = y
                self.close = close

        class _FakeRawObject:
            def __init__(
                self,
                *,
                object_type: int,
                fill: tuple[int, int, int, int],
                stroke: tuple[int, int, int, int],
                stroke_width: float,
                segments: list[_FakeSegment],
                fill_mode: int = 1,
            ) -> None:
                self.object_type = object_type
                self.fill = fill
                self.stroke = stroke
                self.stroke_width = stroke_width
                self.segments = segments
                self.fill_mode = fill_mode

        class _FakeObject:
            def __init__(self, raw, bounds) -> None:
                self.raw = raw
                self._bounds = bounds

            def get_bounds(self):
                return self._bounds

        class _FakePage:
            def __init__(self, objects, *, width: float = 100.0, height: float = 100.0) -> None:
                self._objects = objects
                self._width = width
                self._height = height

            def get_objects(self):
                return self._objects

            def get_width(self):
                return self._width

            def get_height(self):
                return self._height

        class _FakeRawModule:
            FPDF_PAGEOBJ_PATH = 1
            FPDF_PAGEOBJ_SHADING = 2
            FPDF_PAGEOBJ_IMAGE = 3
            FPDF_PAGEOBJ_TEXT = 4
            FPDF_FILLMODE_NONE = 0
            FPDF_SEGMENT_MOVETO = 2
            FPDF_SEGMENT_LINETO = 0

            @staticmethod
            def FPDFPageObj_GetType(obj_raw) -> int:
                return obj_raw.object_type

            @staticmethod
            def FPDFPageObj_GetFillColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.fill
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.stroke
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeWidth(obj_raw, width) -> int:
                width.value = obj_raw.stroke_width
                return 1

            @staticmethod
            def FPDFPath_CountSegments(obj_raw) -> int:
                return len(obj_raw.segments)

            @staticmethod
            def FPDFPath_GetPathSegment(obj_raw, index: int):
                return obj_raw.segments[index]

            @staticmethod
            def FPDFPath_GetDrawMode(obj_raw, fill_mode, stroke) -> int:
                fill_mode.value = obj_raw.fill_mode
                stroke.value = 1
                return 1

            @staticmethod
            def FPDFPathSegment_GetType(segment) -> int:
                return segment.segment_type

            @staticmethod
            def FPDFPathSegment_GetPoint(segment, x, y) -> int:
                x.value = segment.x
                y.value = segment.y
                return 1

            @staticmethod
            def FPDFPathSegment_GetClose(segment) -> int:
                return 1 if segment.close else 0

        rectangle = _FakeObject(
            _FakeRawObject(
                object_type=_FakeRawModule.FPDF_PAGEOBJ_PATH,
                fill=(230, 235, 255, 255),
                stroke=(40, 40, 40, 255),
                stroke_width=1.5,
                segments=[
                    _FakeSegment(_FakeRawModule.FPDF_SEGMENT_MOVETO, 10.0, 10.0),
                    _FakeSegment(_FakeRawModule.FPDF_SEGMENT_LINETO, 60.0, 10.0),
                    _FakeSegment(_FakeRawModule.FPDF_SEGMENT_LINETO, 60.0, 40.0),
                    _FakeSegment(_FakeRawModule.FPDF_SEGMENT_LINETO, 10.0, 40.0, close=True),
                ],
            ),
            (10.0, 10.0, 60.0, 40.0),
        )

        primitives = _extract_pdfium_visual_primitives(
            _FakePage([rectangle]),
            page_number=3,
            raw_module=_FakeRawModule,
        )

        self.assertEqual(len(primitives), 4)
        self.assertTrue(all(primitive.page_number == 3 for primitive in primitives))
        self.assertEqual(
            {primitive.object_type for primitive in primitives},
            {"axis_box_edge_horizontal", "axis_box_edge_vertical"},
        )
        self.assertTrue(all(primitive.stroke_color == "#282828ff" for primitive in primitives))
        self.assertTrue(all((primitive.stroke_width_pt or 0.0) == 1.5 for primitive in primitives))
        self.assertTrue(all(primitive.has_stroke for primitive in primitives))
        self.assertTrue(all(not primitive.has_fill for primitive in primitives))
        self.assertEqual(
            {frozenset(primitive.candidate_roles) for primitive in primitives},
            {frozenset({"horizontal_line_segment"}), frozenset({"vertical_line_segment"})},
        )

    def test_extract_pdfium_visual_primitives_drops_fill_only_white_rectangle(self) -> None:
        class _FakeSegment:
            def __init__(self, segment_type: int, x: float, y: float, *, close: bool = False) -> None:
                self.segment_type = segment_type
                self.x = x
                self.y = y
                self.close = close

        class _FakeRawObject:
            def __init__(
                self,
                *,
                fill: tuple[int, int, int, int],
                stroke: tuple[int, int, int, int],
                stroke_width: float,
                segments: list[_FakeSegment],
                fill_mode: int = 1,
                stroke_mode: int = 0,
            ) -> None:
                self.object_type = 1
                self.fill = fill
                self.stroke = stroke
                self.stroke_width = stroke_width
                self.segments = segments
                self.fill_mode = fill_mode
                self.stroke_mode = stroke_mode

        class _FakeObject:
            def __init__(self, raw, bounds) -> None:
                self.raw = raw
                self._bounds = bounds

            def get_bounds(self):
                return self._bounds

        class _FakePage:
            def __init__(self, objects, *, width: float = 100.0, height: float = 100.0) -> None:
                self._objects = objects
                self._width = width
                self._height = height

            def get_objects(self):
                return self._objects

            def get_width(self):
                return self._width

            def get_height(self):
                return self._height

        class _FakeRawModule:
            FPDF_PAGEOBJ_PATH = 1
            FPDF_PAGEOBJ_SHADING = 2
            FPDF_PAGEOBJ_IMAGE = 3
            FPDF_PAGEOBJ_TEXT = 4
            FPDF_FILLMODE_NONE = 0
            FPDF_SEGMENT_MOVETO = 2
            FPDF_SEGMENT_LINETO = 0

            @staticmethod
            def FPDFPageObj_GetType(obj_raw) -> int:
                return obj_raw.object_type

            @staticmethod
            def FPDFPageObj_GetFillColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.fill
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.stroke
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeWidth(obj_raw, width) -> int:
                width.value = obj_raw.stroke_width
                return 1

            @staticmethod
            def FPDFPath_CountSegments(obj_raw) -> int:
                return len(obj_raw.segments)

            @staticmethod
            def FPDFPath_GetPathSegment(obj_raw, index: int):
                return obj_raw.segments[index]

            @staticmethod
            def FPDFPath_GetDrawMode(obj_raw, fill_mode, stroke) -> int:
                fill_mode.value = obj_raw.fill_mode
                stroke.value = obj_raw.stroke_mode
                return 1

            @staticmethod
            def FPDFPathSegment_GetType(segment) -> int:
                return segment.segment_type

            @staticmethod
            def FPDFPathSegment_GetPoint(segment, x, y) -> int:
                x.value = segment.x
                y.value = segment.y
                return 1

            @staticmethod
            def FPDFPathSegment_GetClose(segment) -> int:
                return 1 if segment.close else 0

        white_fill_only_box = _FakeObject(
            _FakeRawObject(
                fill=(255, 255, 255, 255),
                stroke=(0, 0, 0, 255),
                stroke_width=0.5,
                fill_mode=1,
                stroke_mode=0,
                segments=[
                    _FakeSegment(2, 10.0, 10.0),
                    _FakeSegment(0, 70.0, 10.0),
                    _FakeSegment(0, 70.0, 30.0),
                    _FakeSegment(0, 10.0, 30.0, close=True),
                ],
            ),
            (10.0, 10.0, 70.0, 30.0),
        )

        primitives = _extract_pdfium_visual_primitives(
            _FakePage([white_fill_only_box]),
            page_number=1,
            raw_module=_FakeRawModule,
        )

        self.assertEqual(len(primitives), 0)
        self.assertEqual(_build_visual_block_candidates(primitives), [])

    def test_extract_pdfium_visual_primitives_drops_white_stroke_only_box(self) -> None:
        class _FakeSegment:
            def __init__(self, segment_type: int, x: float, y: float, *, close: bool = False) -> None:
                self.segment_type = segment_type
                self.x = x
                self.y = y
                self.close = close

        class _FakeRawObject:
            def __init__(
                self,
                *,
                fill: tuple[int, int, int, int],
                stroke: tuple[int, int, int, int],
                stroke_width: float,
                segments: list[_FakeSegment],
                fill_mode: int = 0,
                stroke_mode: int = 1,
            ) -> None:
                self.object_type = 1
                self.fill = fill
                self.stroke = stroke
                self.stroke_width = stroke_width
                self.segments = segments
                self.fill_mode = fill_mode
                self.stroke_mode = stroke_mode

        class _FakeObject:
            def __init__(self, raw, bounds) -> None:
                self.raw = raw
                self._bounds = bounds

            def get_bounds(self):
                return self._bounds

        class _FakePage:
            def __init__(self, objects, *, width: float = 100.0, height: float = 100.0) -> None:
                self._objects = objects
                self._width = width
                self._height = height

            def get_objects(self):
                return self._objects

            def get_width(self):
                return self._width

            def get_height(self):
                return self._height

        class _FakeRawModule:
            FPDF_PAGEOBJ_PATH = 1
            FPDF_PAGEOBJ_SHADING = 2
            FPDF_PAGEOBJ_IMAGE = 3
            FPDF_PAGEOBJ_TEXT = 4
            FPDF_FILLMODE_NONE = 0
            FPDF_SEGMENT_MOVETO = 2
            FPDF_SEGMENT_LINETO = 0

            @staticmethod
            def FPDFPageObj_GetType(obj_raw) -> int:
                return obj_raw.object_type

            @staticmethod
            def FPDFPageObj_GetFillColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.fill
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.stroke
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeWidth(obj_raw, width) -> int:
                width.value = obj_raw.stroke_width
                return 1

            @staticmethod
            def FPDFPath_CountSegments(obj_raw) -> int:
                return len(obj_raw.segments)

            @staticmethod
            def FPDFPath_GetPathSegment(obj_raw, index: int):
                return obj_raw.segments[index]

            @staticmethod
            def FPDFPath_GetDrawMode(obj_raw, fill_mode, stroke) -> int:
                fill_mode.value = obj_raw.fill_mode
                stroke.value = obj_raw.stroke_mode
                return 1

            @staticmethod
            def FPDFPathSegment_GetType(segment) -> int:
                return segment.segment_type

            @staticmethod
            def FPDFPathSegment_GetPoint(segment, x, y) -> int:
                x.value = segment.x
                y.value = segment.y
                return 1

            @staticmethod
            def FPDFPathSegment_GetClose(segment) -> int:
                return 1 if segment.close else 0

        white_stroke_box = _FakeObject(
            _FakeRawObject(
                fill=(0, 0, 0, 0),
                stroke=(255, 255, 255, 255),
                stroke_width=0.5,
                fill_mode=0,
                stroke_mode=1,
                segments=[
                    _FakeSegment(2, 10.0, 10.0),
                    _FakeSegment(0, 70.0, 10.0),
                    _FakeSegment(0, 70.0, 30.0),
                    _FakeSegment(0, 10.0, 30.0, close=True),
                ],
            ),
            (10.0, 10.0, 70.0, 30.0),
        )

        primitives = _extract_pdfium_visual_primitives(
            _FakePage([white_stroke_box]),
            page_number=1,
            raw_module=_FakeRawModule,
        )

        self.assertEqual(len(primitives), 0)
        self.assertEqual(_build_visual_block_candidates(primitives), [])

    def test_extract_pdfium_visual_primitives_keeps_only_line_roles(self) -> None:
        class _FakeSegment:
            def __init__(self, segment_type: int, x: float, y: float, *, close: bool = False) -> None:
                self.segment_type = segment_type
                self.x = x
                self.y = y
                self.close = close

        class _FakeRawObject:
            def __init__(
                self,
                *,
                fill: tuple[int, int, int, int],
                stroke: tuple[int, int, int, int],
                stroke_width: float,
                segments: list[_FakeSegment],
                fill_mode: int = 0,
            ) -> None:
                self.object_type = 1
                self.fill = fill
                self.stroke = stroke
                self.stroke_width = stroke_width
                self.segments = segments
                self.fill_mode = fill_mode

        class _FakeObject:
            def __init__(self, raw, bounds) -> None:
                self.raw = raw
                self._bounds = bounds

            def get_bounds(self):
                return self._bounds

        class _FakePage:
            def __init__(self, objects, *, width: float, height: float) -> None:
                self._objects = objects
                self._width = width
                self._height = height

            def get_objects(self):
                return self._objects

            def get_width(self):
                return self._width

            def get_height(self):
                return self._height

        class _FakeRawModule:
            FPDF_PAGEOBJ_PATH = 1
            FPDF_PAGEOBJ_SHADING = 2
            FPDF_PAGEOBJ_IMAGE = 3
            FPDF_PAGEOBJ_TEXT = 4
            FPDF_FILLMODE_NONE = 0
            FPDF_SEGMENT_MOVETO = 2
            FPDF_SEGMENT_LINETO = 0

            @staticmethod
            def FPDFPageObj_GetType(obj_raw) -> int:
                return obj_raw.object_type

            @staticmethod
            def FPDFPageObj_GetFillColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.fill
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.stroke
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeWidth(obj_raw, width) -> int:
                width.value = obj_raw.stroke_width
                return 1

            @staticmethod
            def FPDFPath_CountSegments(obj_raw) -> int:
                return len(obj_raw.segments)

            @staticmethod
            def FPDFPath_GetPathSegment(obj_raw, index: int):
                return obj_raw.segments[index]

            @staticmethod
            def FPDFPath_GetDrawMode(obj_raw, fill_mode, stroke) -> int:
                fill_mode.value = obj_raw.fill_mode
                stroke.value = 1
                return 1

            @staticmethod
            def FPDFPathSegment_GetType(segment) -> int:
                return segment.segment_type

            @staticmethod
            def FPDFPathSegment_GetPoint(segment, x, y) -> int:
                x.value = segment.x
                y.value = segment.y
                return 1

            @staticmethod
            def FPDFPathSegment_GetClose(segment) -> int:
                return 1 if segment.close else 0

        box = _FakeObject(
            _FakeRawObject(
                fill=(255, 255, 255, 255),
                stroke=(0, 0, 0, 255),
                stroke_width=1.0,
                fill_mode=1,
                segments=[
                    _FakeSegment(2, 10.0, 10.0),
                    _FakeSegment(0, 60.0, 10.0),
                    _FakeSegment(0, 60.0, 40.0),
                    _FakeSegment(0, 10.0, 40.0, close=True),
                ],
            ),
            (10.0, 10.0, 60.0, 40.0),
        )
        attached_rule = _FakeObject(
            _FakeRawObject(
                fill=(0, 0, 0, 0),
                stroke=(0, 0, 0, 255),
                stroke_width=1.0,
                segments=[
                    _FakeSegment(2, 60.0, 18.0),
                    _FakeSegment(0, 90.0, 18.0),
                ],
            ),
            (60.0, 17.5, 90.0, 18.5),
        )
        closed_shape = _FakeObject(
            _FakeRawObject(
                fill=(200, 200, 200, 255),
                stroke=(0, 0, 0, 255),
                stroke_width=1.0,
                fill_mode=1,
                segments=[
                    _FakeSegment(2, 100.0, 20.0),
                    _FakeSegment(0, 120.0, 20.0),
                    _FakeSegment(0, 110.0, 38.0, close=True),
                ],
            ),
            (100.0, 20.0, 120.0, 38.0),
        )
        long_vertical = _FakeObject(
            _FakeRawObject(
                fill=(0, 0, 0, 0),
                stroke=(0, 0, 0, 255),
                stroke_width=1.0,
                segments=[
                    _FakeSegment(2, 150.0, 5.0),
                    _FakeSegment(0, 150.0, 165.0),
                ],
            ),
            (149.5, 5.0, 150.5, 165.0),
        )
        long_horizontal = _FakeObject(
            _FakeRawObject(
                fill=(0, 0, 0, 0),
                stroke=(0, 0, 0, 255),
                stroke_width=1.0,
                segments=[
                    _FakeSegment(2, 10.0, 180.0),
                    _FakeSegment(0, 190.0, 180.0),
                ],
            ),
            (10.0, 179.5, 190.0, 180.5),
        )

        primitives = _extract_pdfium_visual_primitives(
            _FakePage([box, attached_rule, closed_shape, long_vertical, long_horizontal], width=200.0, height=200.0),
            page_number=1,
            raw_module=_FakeRawModule,
        )

        roles = {primitive.draw_order: set(primitive.candidate_roles) for primitive in primitives}
        self.assertEqual(roles[1], {"horizontal_line_segment"})
        self.assertNotIn(2, roles)
        self.assertEqual(roles[3], {"long_vertical_rule", "vertical_line_segment"})
        self.assertEqual(roles[4], {"long_horizontal_rule", "horizontal_line_segment"})
        edge_roles = [
            set(primitive.candidate_roles)
            for primitive in primitives
            if primitive.object_type in {"axis_box_edge_horizontal", "axis_box_edge_vertical"}
        ]
        self.assertEqual(len(edge_roles), 4)
        self.assertEqual(
            {frozenset(role_set) for role_set in edge_roles},
            {frozenset({"horizontal_line_segment"}), frozenset({"vertical_line_segment"})},
        )

    def test_extract_pdfium_visual_primitives_promotes_segmented_rule(self) -> None:
        class _FakeSegment:
            def __init__(self, segment_type: int, x: float, y: float, *, close: bool = False) -> None:
                self.segment_type = segment_type
                self.x = x
                self.y = y
                self.close = close

        class _FakeRawObject:
            def __init__(
                self,
                *,
                fill: tuple[int, int, int, int],
                stroke: tuple[int, int, int, int],
                stroke_width: float,
                segments: list[_FakeSegment],
                fill_mode: int = 0,
            ) -> None:
                self.object_type = 1
                self.fill = fill
                self.stroke = stroke
                self.stroke_width = stroke_width
                self.segments = segments
                self.fill_mode = fill_mode

        class _FakeObject:
            def __init__(self, raw, bounds) -> None:
                self.raw = raw
                self._bounds = bounds

            def get_bounds(self):
                return self._bounds

        class _FakePage:
            def __init__(self, objects, *, width: float, height: float) -> None:
                self._objects = objects
                self._width = width
                self._height = height

            def get_objects(self):
                return self._objects

            def get_width(self):
                return self._width

            def get_height(self):
                return self._height

        class _FakeRawModule:
            FPDF_PAGEOBJ_PATH = 1
            FPDF_PAGEOBJ_SHADING = 2
            FPDF_PAGEOBJ_IMAGE = 3
            FPDF_PAGEOBJ_TEXT = 4
            FPDF_FILLMODE_NONE = 0
            FPDF_SEGMENT_MOVETO = 2
            FPDF_SEGMENT_LINETO = 0

            @staticmethod
            def FPDFPageObj_GetType(obj_raw) -> int:
                return obj_raw.object_type

            @staticmethod
            def FPDFPageObj_GetFillColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.fill
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.stroke
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeWidth(obj_raw, width) -> int:
                width.value = obj_raw.stroke_width
                return 1

            @staticmethod
            def FPDFPath_CountSegments(obj_raw) -> int:
                return len(obj_raw.segments)

            @staticmethod
            def FPDFPath_GetPathSegment(obj_raw, index: int):
                return obj_raw.segments[index]

            @staticmethod
            def FPDFPath_GetDrawMode(obj_raw, fill_mode, stroke) -> int:
                fill_mode.value = obj_raw.fill_mode
                stroke.value = 1
                return 1

            @staticmethod
            def FPDFPathSegment_GetType(segment) -> int:
                return segment.segment_type

            @staticmethod
            def FPDFPathSegment_GetPoint(segment, x, y) -> int:
                x.value = segment.x
                y.value = segment.y
                return 1

            @staticmethod
            def FPDFPathSegment_GetClose(segment) -> int:
                return 1 if segment.close else 0

        primitives = _extract_pdfium_visual_primitives(
            _FakePage(
                [
                    _FakeObject(
                        _FakeRawObject(
                            fill=(0, 0, 0, 0),
                            stroke=(0, 0, 255, 255),
                            stroke_width=1.0,
                            segments=[_FakeSegment(2, 10.0, 20.0), _FakeSegment(0, 14.0, 20.0)],
                        ),
                        (10.0, 19.5, 14.0, 20.5),
                    ),
                    _FakeObject(
                        _FakeRawObject(
                            fill=(0, 0, 0, 0),
                            stroke=(0, 0, 255, 255),
                            stroke_width=1.0,
                            segments=[_FakeSegment(2, 16.0, 20.0), _FakeSegment(0, 20.0, 20.0)],
                        ),
                        (16.0, 19.5, 20.0, 20.5),
                    ),
                    _FakeObject(
                        _FakeRawObject(
                            fill=(0, 0, 0, 0),
                            stroke=(0, 0, 255, 255),
                            stroke_width=1.0,
                            segments=[_FakeSegment(2, 22.0, 20.0), _FakeSegment(0, 26.0, 20.0)],
                        ),
                        (22.0, 19.5, 26.0, 20.5),
                    ),
                ],
                width=100.0,
                height=100.0,
            ),
            page_number=4,
            raw_module=_FakeRawModule,
        )

        self.assertEqual(len(primitives), 1)
        self.assertEqual(primitives[0].object_type, "segmented_horizontal_rule")
        self.assertEqual(
            set(primitives[0].candidate_roles),
            {"horizontal_line_segment", "segmented_horizontal_rule"},
        )

    def test_extract_pdfium_visual_primitives_promotes_contiguous_micro_fragments(self) -> None:
        class _FakeSegment:
            def __init__(self, segment_type: int, x: float, y: float, *, close: bool = False) -> None:
                self.segment_type = segment_type
                self.x = x
                self.y = y
                self.close = close

        class _FakeRawObject:
            def __init__(
                self,
                *,
                fill: tuple[int, int, int, int],
                stroke: tuple[int, int, int, int],
                stroke_width: float,
                segments: list[_FakeSegment],
                fill_mode: int = 0,
            ) -> None:
                self.object_type = 1
                self.fill = fill
                self.stroke = stroke
                self.stroke_width = stroke_width
                self.segments = segments
                self.fill_mode = fill_mode

        class _FakeObject:
            def __init__(self, raw, bounds) -> None:
                self.raw = raw
                self._bounds = bounds

            def get_bounds(self):
                return self._bounds

        class _FakePage:
            def __init__(self, objects, *, width: float, height: float) -> None:
                self._objects = objects
                self._width = width
                self._height = height

            def get_objects(self):
                return self._objects

            def get_width(self):
                return self._width

            def get_height(self):
                return self._height

        class _FakeRawModule:
            FPDF_PAGEOBJ_PATH = 1
            FPDF_PAGEOBJ_SHADING = 2
            FPDF_PAGEOBJ_IMAGE = 3
            FPDF_PAGEOBJ_TEXT = 4
            FPDF_FILLMODE_NONE = 0
            FPDF_SEGMENT_MOVETO = 2
            FPDF_SEGMENT_LINETO = 0

            @staticmethod
            def FPDFPageObj_GetType(obj_raw) -> int:
                return obj_raw.object_type

            @staticmethod
            def FPDFPageObj_GetFillColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.fill
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.stroke
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeWidth(obj_raw, width) -> int:
                width.value = obj_raw.stroke_width
                return 1

            @staticmethod
            def FPDFPath_CountSegments(obj_raw) -> int:
                return len(obj_raw.segments)

            @staticmethod
            def FPDFPath_GetPathSegment(obj_raw, index: int):
                return obj_raw.segments[index]

            @staticmethod
            def FPDFPath_GetDrawMode(obj_raw, fill_mode, stroke) -> int:
                fill_mode.value = obj_raw.fill_mode
                stroke.value = 1
                return 1

            @staticmethod
            def FPDFPathSegment_GetType(segment) -> int:
                return segment.segment_type

            @staticmethod
            def FPDFPathSegment_GetPoint(segment, x, y) -> int:
                x.value = segment.x
                y.value = segment.y
                return 1

            @staticmethod
            def FPDFPathSegment_GetClose(segment) -> int:
                return 1 if segment.close else 0

        objects = []
        for index in range(25):
            bottom = 10.0 + index * 1.0
            top = bottom + 0.8
            objects.append(
                _FakeObject(
                        _FakeRawObject(
                            fill=(0, 0, 0, 0),
                            stroke=(0, 0, 255, 255),
                            stroke_width=1.0,
                            segments=[_FakeSegment(2, 20.0, bottom), _FakeSegment(0, 20.0, top)],
                        ),
                    (19.9, bottom, 20.1, top),
                )
            )

        primitives = _extract_pdfium_visual_primitives(
            _FakePage(objects, width=100.0, height=100.0),
            page_number=5,
            raw_module=_FakeRawModule,
        )

        self.assertEqual(len(primitives), 1)
        self.assertEqual(primitives[0].object_type, "segmented_vertical_rule")
        self.assertEqual(
            set(primitives[0].candidate_roles),
            {"vertical_line_segment", "segmented_vertical_rule"},
        )

    def test_build_visual_block_candidates_promotes_open_frame(self) -> None:
        class _FakeSegment:
            def __init__(self, segment_type: int, x: float, y: float, *, close: bool = False) -> None:
                self.segment_type = segment_type
                self.x = x
                self.y = y
                self.close = close

        class _FakeRawObject:
            def __init__(
                self,
                *,
                fill: tuple[int, int, int, int],
                stroke: tuple[int, int, int, int],
                stroke_width: float,
                segments: list[_FakeSegment],
                fill_mode: int = 0,
            ) -> None:
                self.object_type = 1
                self.fill = fill
                self.stroke = stroke
                self.stroke_width = stroke_width
                self.segments = segments
                self.fill_mode = fill_mode

        class _FakeObject:
            def __init__(self, raw, bounds) -> None:
                self.raw = raw
                self._bounds = bounds

            def get_bounds(self):
                return self._bounds

        class _FakePage:
            def __init__(self, objects, *, width: float, height: float) -> None:
                self._objects = objects
                self._width = width
                self._height = height

            def get_objects(self):
                return self._objects

            def get_width(self):
                return self._width

            def get_height(self):
                return self._height

        class _FakeRawModule:
            FPDF_PAGEOBJ_PATH = 1
            FPDF_PAGEOBJ_SHADING = 2
            FPDF_PAGEOBJ_IMAGE = 3
            FPDF_PAGEOBJ_TEXT = 4
            FPDF_FILLMODE_NONE = 0
            FPDF_SEGMENT_MOVETO = 2
            FPDF_SEGMENT_LINETO = 0

            @staticmethod
            def FPDFPageObj_GetType(obj_raw) -> int:
                return obj_raw.object_type

            @staticmethod
            def FPDFPageObj_GetFillColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.fill
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.stroke
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeWidth(obj_raw, width) -> int:
                width.value = obj_raw.stroke_width
                return 1

            @staticmethod
            def FPDFPath_CountSegments(obj_raw) -> int:
                return len(obj_raw.segments)

            @staticmethod
            def FPDFPath_GetPathSegment(obj_raw, index: int):
                return obj_raw.segments[index]

            @staticmethod
            def FPDFPath_GetDrawMode(obj_raw, fill_mode, stroke) -> int:
                fill_mode.value = obj_raw.fill_mode
                stroke.value = 1
                return 1

            @staticmethod
            def FPDFPathSegment_GetType(segment) -> int:
                return segment.segment_type

            @staticmethod
            def FPDFPathSegment_GetPoint(segment, x, y) -> int:
                x.value = segment.x
                y.value = segment.y
                return 1

            @staticmethod
            def FPDFPathSegment_GetClose(segment) -> int:
                return 1 if segment.close else 0

        primitives = _extract_pdfium_visual_primitives(
            _FakePage(
                [
                    _FakeObject(
                        _FakeRawObject(
                            fill=(0, 0, 0, 0),
                            stroke=(0, 0, 0, 255),
                            stroke_width=1.0,
                            segments=[_FakeSegment(2, 10.0, 10.0), _FakeSegment(0, 10.0, 50.0)],
                        ),
                        (9.5, 10.0, 10.5, 50.0),
                    ),
                    _FakeObject(
                        _FakeRawObject(
                            fill=(0, 0, 0, 0),
                            stroke=(0, 0, 0, 255),
                            stroke_width=1.0,
                            segments=[_FakeSegment(2, 10.0, 10.0), _FakeSegment(0, 80.0, 10.0)],
                        ),
                        (10.0, 9.5, 80.0, 10.5),
                    ),
                    _FakeObject(
                        _FakeRawObject(
                            fill=(0, 0, 0, 0),
                            stroke=(0, 0, 0, 255),
                            stroke_width=1.0,
                            segments=[_FakeSegment(2, 80.0, 10.0), _FakeSegment(0, 80.0, 50.0)],
                        ),
                        (79.5, 10.0, 80.5, 50.0),
                    ),
                    _FakeObject(
                        _FakeRawObject(
                            fill=(0, 0, 0, 0),
                            stroke=(0, 0, 0, 255),
                            stroke_width=1.0,
                            segments=[_FakeSegment(2, 10.0, 50.0), _FakeSegment(0, 38.0, 50.0)],
                        ),
                        (10.0, 49.5, 38.0, 50.5),
                    ),
                    _FakeObject(
                        _FakeRawObject(
                            fill=(0, 0, 0, 0),
                            stroke=(0, 0, 0, 255),
                            stroke_width=1.0,
                            segments=[_FakeSegment(2, 52.0, 50.0), _FakeSegment(0, 80.0, 50.0)],
                        ),
                        (52.0, 49.5, 80.0, 50.5),
                    ),
                ],
                width=120.0,
                height=100.0,
            ),
            page_number=2,
            raw_module=_FakeRawModule,
        )

        candidates = _build_visual_block_candidates(primitives)
        open_frame_candidates = [candidate for candidate in candidates if candidate.candidate_type == "open_frame"]

        self.assertEqual(len(open_frame_candidates), 1)
        self.assertEqual(open_frame_candidates[0].candidate_type, "open_frame")
        self.assertEqual(open_frame_candidates[0].page_number, 2)
        self.assertEqual(open_frame_candidates[0].primitive_draw_orders, [0, 1, 2, 3, 4])
        self.assertIn("horizontal_line_segment", open_frame_candidates[0].source_roles)
        self.assertIn("vertical_line_segment", open_frame_candidates[0].source_roles)

    def test_build_visual_block_candidates_keeps_axis_box_without_child_cells(self) -> None:
        class _FakeSegment:
            def __init__(self, segment_type: int, x: float, y: float, *, close: bool = False) -> None:
                self.segment_type = segment_type
                self.x = x
                self.y = y
                self.close = close

        class _FakeRawObject:
            def __init__(
                self,
                *,
                fill: tuple[int, int, int, int],
                stroke: tuple[int, int, int, int],
                stroke_width: float,
                segments: list[_FakeSegment],
                fill_mode: int = 0,
            ) -> None:
                self.object_type = 1
                self.fill = fill
                self.stroke = stroke
                self.stroke_width = stroke_width
                self.segments = segments
                self.fill_mode = fill_mode

        class _FakeObject:
            def __init__(self, raw, bounds) -> None:
                self.raw = raw
                self._bounds = bounds

            def get_bounds(self):
                return self._bounds

        class _FakePage:
            def __init__(self, objects, *, width: float, height: float) -> None:
                self._objects = objects
                self._width = width
                self._height = height

            def get_objects(self):
                return self._objects

            def get_width(self):
                return self._width

            def get_height(self):
                return self._height

        class _FakeRawModule:
            FPDF_PAGEOBJ_PATH = 1
            FPDF_PAGEOBJ_SHADING = 2
            FPDF_PAGEOBJ_IMAGE = 3
            FPDF_PAGEOBJ_TEXT = 4
            FPDF_FILLMODE_NONE = 0
            FPDF_SEGMENT_MOVETO = 2
            FPDF_SEGMENT_LINETO = 0

            @staticmethod
            def FPDFPageObj_GetType(obj_raw) -> int:
                return obj_raw.object_type

            @staticmethod
            def FPDFPageObj_GetFillColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.fill
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeColor(obj_raw, red, green, blue, alpha) -> int:
                red.value, green.value, blue.value, alpha.value = obj_raw.stroke
                return 1

            @staticmethod
            def FPDFPageObj_GetStrokeWidth(obj_raw, width) -> int:
                width.value = obj_raw.stroke_width
                return 1

            @staticmethod
            def FPDFPath_CountSegments(obj_raw) -> int:
                return len(obj_raw.segments)

            @staticmethod
            def FPDFPath_GetPathSegment(obj_raw, index: int):
                return obj_raw.segments[index]

            @staticmethod
            def FPDFPath_GetDrawMode(obj_raw, fill_mode, stroke) -> int:
                fill_mode.value = obj_raw.fill_mode
                stroke.value = 1
                return 1

            @staticmethod
            def FPDFPathSegment_GetType(segment) -> int:
                return segment.segment_type

            @staticmethod
            def FPDFPathSegment_GetPoint(segment, x, y) -> int:
                x.value = segment.x
                y.value = segment.y
                return 1

            @staticmethod
            def FPDFPathSegment_GetClose(segment) -> int:
                return 1 if segment.close else 0

        primitives = _extract_pdfium_visual_primitives(
            _FakePage(
                [
                    _FakeObject(
                        _FakeRawObject(
                            fill=(255, 255, 255, 255),
                            stroke=(0, 0, 0, 255),
                            stroke_width=1.0,
                            fill_mode=1,
                            segments=[
                                _FakeSegment(2, 10.0, 10.0),
                                _FakeSegment(0, 90.0, 10.0),
                                _FakeSegment(0, 90.0, 40.0),
                                _FakeSegment(0, 10.0, 40.0, close=True),
                            ],
                        ),
                        (10.0, 10.0, 90.0, 40.0),
                    ),
                    _FakeObject(
                        _FakeRawObject(
                            fill=(0, 0, 0, 0),
                            stroke=(0, 0, 0, 255),
                            stroke_width=1.0,
                            segments=[_FakeSegment(2, 35.0, 10.0), _FakeSegment(0, 35.0, 40.0)],
                        ),
                        (34.5, 10.0, 35.5, 40.0),
                    ),
                    _FakeObject(
                        _FakeRawObject(
                            fill=(0, 0, 0, 0),
                            stroke=(0, 0, 0, 255),
                            stroke_width=1.0,
                            segments=[_FakeSegment(2, 62.0, 10.0), _FakeSegment(0, 62.0, 40.0)],
                        ),
                        (61.5, 10.0, 62.5, 40.0),
                    ),
                ],
                width=120.0,
                height=80.0,
            ),
            page_number=1,
            raw_module=_FakeRawModule,
        )

        candidates = _build_visual_block_candidates(primitives)
        axis_candidates = [candidate for candidate in candidates if candidate.candidate_type == "axis_box"]

        self.assertEqual(len(axis_candidates), 1)
        self.assertEqual(axis_candidates[0].child_cells, [])

    def test_build_visual_block_candidates_skips_open_frame_graph_when_primitive_count_is_too_high(self) -> None:
        primitives = [
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=index,
                object_type="path",
                bounding_box=PdfBoundingBox(
                    left_pt=float(index),
                    bottom_pt=10.0,
                    right_pt=float(index) + 6.0,
                    top_pt=10.8,
                ),
                stroke_color="#000000ff",
                has_stroke=True,
                candidate_roles=["horizontal_line_segment"],
            )
            for index in range(501)
        ]

        candidates = _build_visual_block_candidates(primitives)

        self.assertEqual(candidates, [])

    def test_build_visual_block_candidates_splits_connected_boxes_and_connector_lines(self) -> None:
        primitives = [
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=0,
                object_type="axis_box_edge_horizontal",
                bounding_box=PdfBoundingBox(left_pt=10.0, bottom_pt=49.5, right_pt=40.0, top_pt=50.5),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["horizontal_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=1,
                object_type="axis_box_edge_horizontal",
                bounding_box=PdfBoundingBox(left_pt=10.0, bottom_pt=9.5, right_pt=40.0, top_pt=10.5),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["horizontal_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=2,
                object_type="axis_box_edge_vertical",
                bounding_box=PdfBoundingBox(left_pt=9.5, bottom_pt=10.0, right_pt=10.5, top_pt=50.0),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["vertical_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=3,
                object_type="axis_box_edge_vertical",
                bounding_box=PdfBoundingBox(left_pt=39.5, bottom_pt=10.0, right_pt=40.5, top_pt=50.0),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["vertical_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=4,
                object_type="axis_box_edge_horizontal",
                bounding_box=PdfBoundingBox(left_pt=60.0, bottom_pt=49.5, right_pt=90.0, top_pt=50.5),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["horizontal_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=5,
                object_type="axis_box_edge_horizontal",
                bounding_box=PdfBoundingBox(left_pt=60.0, bottom_pt=9.5, right_pt=90.0, top_pt=10.5),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["horizontal_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=6,
                object_type="axis_box_edge_vertical",
                bounding_box=PdfBoundingBox(left_pt=59.5, bottom_pt=10.0, right_pt=60.5, top_pt=50.0),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["vertical_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=7,
                object_type="axis_box_edge_vertical",
                bounding_box=PdfBoundingBox(left_pt=89.5, bottom_pt=10.0, right_pt=90.5, top_pt=50.0),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["vertical_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=8,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=40.0, bottom_pt=29.5, right_pt=60.0, top_pt=30.5),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["horizontal_line_segment"],
            ),
        ]

        candidates = _build_visual_block_candidates(primitives)

        axis_boxes = [candidate for candidate in candidates if candidate.candidate_type == "axis_box"]
        semantic_lines = [candidate for candidate in candidates if candidate.candidate_type == "semantic_line"]

        self.assertEqual(len(axis_boxes), 2)
        self.assertEqual(len(semantic_lines), 1)
        self.assertEqual(semantic_lines[0].primitive_draw_orders, [8])

    def test_build_visual_block_candidates_absorbs_long_line_hint_into_open_frame(self) -> None:
        primitives = [
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=0,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=10.0, bottom_pt=9.5, right_pt=90.0, top_pt=10.5),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["horizontal_line_segment", "long_horizontal_rule"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=1,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=9.5, bottom_pt=10.0, right_pt=10.5, top_pt=60.0),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["vertical_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=2,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=89.5, bottom_pt=10.0, right_pt=90.5, top_pt=60.0),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["vertical_line_segment"],
            ),
        ]

        candidates = _build_visual_block_candidates(primitives)

        open_frames = [candidate for candidate in candidates if candidate.candidate_type == "open_frame"]
        self.assertEqual(len(open_frames), 1)
        self.assertEqual(open_frames[0].primitive_draw_orders, [0, 1, 2])
        self.assertEqual({candidate.candidate_type for candidate in candidates}, {"open_frame"})

    def test_build_visual_block_candidates_keeps_standalone_long_line_hint_as_semantic_line(self) -> None:
        primitives = [
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=0,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=10.0, bottom_pt=9.5, right_pt=90.0, top_pt=10.5),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["horizontal_line_segment", "long_horizontal_rule"],
            ),
        ]

        candidates = _build_visual_block_candidates(primitives)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].candidate_type, "semantic_line")
        self.assertEqual(candidates[0].primitive_draw_orders, [0])

    def test_build_visual_block_candidates_suppresses_semantic_line_on_open_frame_boundary(self) -> None:
        primitives = [
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=0,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=10.0, bottom_pt=49.5, right_pt=60.0, top_pt=50.5),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["horizontal_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=1,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=9.5, bottom_pt=10.0, right_pt=10.5, top_pt=50.0),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["vertical_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=2,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=59.5, bottom_pt=10.0, right_pt=60.5, top_pt=50.0),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["vertical_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=3,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=10.0, bottom_pt=8.3, right_pt=60.0, top_pt=9.3),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["horizontal_line_segment"],
            ),
        ]

        candidates = _build_visual_block_candidates(primitives)

        axis_boxes = [candidate for candidate in candidates if candidate.candidate_type == "axis_box"]
        semantic_lines = [candidate for candidate in candidates if candidate.candidate_type == "semantic_line"]

        self.assertEqual(len(axis_boxes), 1)
        self.assertEqual(len(semantic_lines), 0)

    def test_build_visual_block_candidates_dedupes_duplicate_lines_before_open_frame_promotion(self) -> None:
        primitives = [
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=0,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=10.0, bottom_pt=49.5, right_pt=60.0, top_pt=50.5),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["horizontal_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=1,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=10.0, bottom_pt=49.5, right_pt=60.0, top_pt=50.5),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["horizontal_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=2,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=9.5, bottom_pt=10.0, right_pt=10.5, top_pt=50.0),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["vertical_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=3,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=9.5, bottom_pt=10.0, right_pt=10.5, top_pt=50.0),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["vertical_line_segment"],
            ),
        ]

        candidates = _build_visual_block_candidates(primitives)

        open_frames = [candidate for candidate in candidates if candidate.candidate_type == "open_frame"]
        semantic_lines = [candidate for candidate in candidates if candidate.candidate_type == "semantic_line"]

        self.assertEqual(len(open_frames), 0)
        self.assertEqual(len(semantic_lines), 1)
        self.assertEqual(semantic_lines[0].primitive_draw_orders, [0, 2])

    def test_connected_line_components_uses_1pt5_join_tolerance(self) -> None:
        primitives = [
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=0,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=9.5, bottom_pt=10.0, right_pt=10.5, top_pt=50.0),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["vertical_line_segment"],
            ),
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=1,
                object_type="path",
                bounding_box=PdfBoundingBox(left_pt=11.3, bottom_pt=49.5, right_pt=60.0, top_pt=50.5),
                stroke_color="#000000ff",
                stroke_width_pt=1.0,
                has_stroke=True,
                candidate_roles=["horizontal_line_segment"],
            ),
        ]

        components = _connected_line_components(primitives)

        self.assertEqual(len(components), 1)
        self.assertEqual(sorted(item.draw_order for item in components[0]), [0, 1])

    def test_build_visual_block_candidates_fast_path_still_groups_long_line_hints_into_open_frame(self) -> None:
        primitives = [
            PdfPreviewVisualPrimitive(
                page_number=1,
                draw_order=index,
                object_type="path",
                bounding_box=PdfBoundingBox(
                    left_pt=float(index),
                    bottom_pt=10.0,
                    right_pt=float(index) + 6.0,
                    top_pt=10.8,
                ),
                stroke_color="#000000ff",
                has_stroke=True,
                candidate_roles=["horizontal_line_segment"],
            )
            for index in range(501)
        ]
        primitives.extend(
            [
                PdfPreviewVisualPrimitive(
                    page_number=1,
                    draw_order=600,
                    object_type="path",
                    bounding_box=PdfBoundingBox(left_pt=10.0, bottom_pt=9.5, right_pt=90.0, top_pt=10.5),
                    stroke_color="#000000ff",
                    stroke_width_pt=1.0,
                    has_stroke=True,
                    candidate_roles=["horizontal_line_segment", "long_horizontal_rule"],
                ),
                PdfPreviewVisualPrimitive(
                    page_number=1,
                    draw_order=601,
                    object_type="path",
                    bounding_box=PdfBoundingBox(left_pt=9.5, bottom_pt=10.0, right_pt=10.5, top_pt=60.0),
                    stroke_color="#000000ff",
                    stroke_width_pt=1.0,
                    has_stroke=True,
                    candidate_roles=["vertical_line_segment", "long_vertical_rule"],
                ),
                PdfPreviewVisualPrimitive(
                    page_number=1,
                    draw_order=602,
                    object_type="path",
                    bounding_box=PdfBoundingBox(left_pt=89.5, bottom_pt=10.0, right_pt=90.5, top_pt=60.0),
                    stroke_color="#000000ff",
                    stroke_width_pt=1.0,
                    has_stroke=True,
                    candidate_roles=["vertical_line_segment", "long_vertical_rule"],
                ),
            ]
        )

        candidates = _build_visual_block_candidates(primitives)

        open_frames = [candidate for candidate in candidates if candidate.candidate_type == "open_frame"]
        self.assertEqual(len(open_frames), 1)
        self.assertEqual(open_frames[0].primitive_draw_orders, [600, 601, 602])
        self.assertEqual({candidate.candidate_type for candidate in candidates}, {"open_frame"})

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
                {
                    "type": "paragraph",
                    "content": "- 1 -",
                    "page number": 1,
                    "bounding box": [30, 5, 50, 15],
                    "reading order index": 98,
                },
                {
                    "type": "paragraph",
                    "content": "- 2 -",
                    "page number": 1,
                    "bounding box": [190, 5, 210, 15],
                    "reading order index": 99,
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
        context = build_pdf_preview_context(raw_document)

        html = render_pdf_preview_html(doc, preview_context=context, title="Preview")

        self.assertEqual(html.count('<section class="document-page"'), 2)
        self.assertIn("Left body", html)
        self.assertIn("Right body", html)
        self.assertIn("A1", html)
        self.assertIn("B1", html)
        self.assertIn("width:90.9pt", html)
        self.assertNotIn("document-region-band--columns", html)
        self.assertNotIn("pdf-preview-page-layer", html)

    def test_render_pdf_html_matches_render_pdf_preview_html_for_minimal_input(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "pages": [{"page number": 1, "width pt": 200, "height pt": 120}],
            "kids": [
                {
                    "type": "paragraph",
                    "content": "Hello preview",
                    "page number": 1,
                    "bounding box": [10, 20, 80, 36],
                }
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
        context = build_pdf_preview_context(raw_document)
        expected_html = render_pdf_preview_html(doc, preview_context=context, title="Preview")

        with patch("document_processor.pdf.pipeline._parse_pdf_to_doc_ir_with_preview", return_value=(doc, context)):
            actual_html = render_pdf_html("sample.pdf", title="Preview")

        self.assertEqual(actual_html, expected_html)

    def test_render_pdf_preview_html_preserves_single_page_side_regions_as_column_band(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "pages": [
                {"page number": 1, "width pt": 200, "height pt": 250},
            ],
            "layout regions": [
                {
                    "region id": "p1-main",
                    "region type": "main",
                    "page number": 1,
                    "bounding box": [20, 0, 180, 250],
                },
                {
                    "region id": "p1-left",
                    "region type": "left",
                    "page number": 1,
                    "bounding box": [20, 20, 90, 230],
                },
                {
                    "region id": "p1-right",
                    "region type": "right",
                    "page number": 1,
                    "bounding box": [110, 20, 180, 230],
                },
            ],
            "kids": [
                {
                    "type": "paragraph",
                    "content": "Abstract",
                    "page number": 1,
                    "bounding box": [20, 225, 180, 240],
                    "reading order index": 1,
                },
                {
                    "type": "paragraph",
                    "content": "Left column body",
                    "page number": 1,
                    "layout region id": "p1-left",
                    "bounding box": [20, 30, 90, 150],
                    "reading order index": 2,
                },
                {
                    "type": "paragraph",
                    "content": "Right column body",
                    "page number": 1,
                    "layout region id": "p1-right",
                    "bounding box": [110, 30, 180, 150],
                    "reading order index": 3,
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
        context = build_pdf_preview_context(raw_document)

        html = render_pdf_preview_html(doc, preview_context=context, title="Preview")

        self.assertEqual(html.count('<section class="document-page"'), 1)
        self.assertNotIn("pdf-preview-page-layer", html)
        self.assertIn("Abstract", html)
        self.assertIn("Left column body", html)
        self.assertIn("Right column body", html)
        self.assertGreaterEqual(html.count("<table"), 1)
        self.assertGreaterEqual(html.count("<td"), 3)

    def test_render_pdf_preview_html_skips_candidate_overlay_when_it_matches_table_bbox(self) -> None:
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
                    "bounding box": [0, 0, 120, 200],
                }
            ],
            "kids": [
                {
                    "type": "table",
                    "page number": 1,
                    "layout region id": "p1-left",
                    "reading order index": 1,
                    "bounding box": [10, 20, 110, 120],
                    "number of rows": 1,
                    "number of columns": 2,
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
        context.visual_block_candidates.append(
            PdfPreviewVisualBlockCandidate(
                page_number=1,
                candidate_type="axis_box",
                bounding_box=PdfBoundingBox(left_pt=10.0, bottom_pt=20.0, right_pt=110.0, top_pt=120.0),
                primitive_draw_orders=[],
                source_roles=["axis_box"],
                child_cells=[],
            )
        )

        html = render_pdf_preview_html(doc, preview_context=context, title="Preview")

        self.assertIn("A1", html)
        self.assertIn("B1", html)
        self.assertNotIn("pdf-preview-candidate--axis_box", html)

    def test_render_pdf_preview_html_promotes_single_candidate_to_layout_table_and_keeps_leftover_flow(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "pages": [
                {"page number": 1, "width pt": 250, "height pt": 200},
            ],
            "kids": [
                {
                    "type": "paragraph",
                    "content": "Box text",
                    "page number": 1,
                    "bounding box": [72, 52, 148, 88],
                    "reading order index": 1,
                },
                {
                    "type": "paragraph",
                    "content": "Flow text",
                    "page number": 1,
                    "bounding box": [170, 100, 220, 116],
                    "reading order index": 2,
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
        context = build_pdf_preview_context(raw_document)
        context.visual_block_candidates.append(
            PdfPreviewVisualBlockCandidate(
                page_number=1,
                candidate_type="axis_box",
                bounding_box=PdfBoundingBox(left_pt=60.0, bottom_pt=40.0, right_pt=160.0, top_pt=120.0),
                primitive_draw_orders=[],
                source_roles=["axis_box"],
                child_cells=[],
            )
        )

        html = render_pdf_preview_html(doc, preview_context=context, title="Preview")

        self.assertEqual(html.count("<table"), 1)
        self.assertIn("width:100.0pt", html)
        self.assertNotIn("height:80.0pt", html)
        self.assertIn("border-top:1px solid #4a4f57", html)
        self.assertIn("Box text", html)
        self.assertIn("Flow text", html)
        self.assertNotIn("pdf-preview-candidate--axis_box", html)
        self.assertNotIn("pdf-preview-page-candidates", html)

    def test_render_pdf_preview_html_promotes_aligned_candidates_to_multicell_layout_table(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "pages": [
                {"page number": 1, "width pt": 250, "height pt": 200},
            ],
            "kids": [
                {
                    "type": "paragraph",
                    "content": "Left cell",
                    "page number": 1,
                    "bounding box": [62, 54, 106, 86],
                    "reading order index": 1,
                },
                {
                    "type": "paragraph",
                    "content": "Right cell",
                    "page number": 1,
                    "bounding box": [118, 54, 162, 86],
                    "reading order index": 2,
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
        context = build_pdf_preview_context(raw_document)
        context.visual_block_candidates.extend(
            [
                PdfPreviewVisualBlockCandidate(
                    page_number=1,
                    candidate_type="axis_box",
                    bounding_box=PdfBoundingBox(left_pt=56.0, bottom_pt=46.0, right_pt=112.0, top_pt=96.0),
                    primitive_draw_orders=[],
                    source_roles=["axis_box"],
                    child_cells=[],
                ),
                PdfPreviewVisualBlockCandidate(
                    page_number=1,
                    candidate_type="open_frame",
                    bounding_box=PdfBoundingBox(left_pt=114.0, bottom_pt=46.0, right_pt=170.0, top_pt=96.0),
                    primitive_draw_orders=[],
                    source_roles=["open_frame"],
                    child_cells=[],
                ),
            ]
        )

        html = render_pdf_preview_html(doc, preview_context=context, title="Preview")

        self.assertEqual(html.count("<table"), 1)
        self.assertEqual(html.count("<td"), 2)
        self.assertIn("width:114.0pt", html)
        self.assertNotIn("height:50.0pt", html)
        self.assertIn("Left cell", html)
        self.assertIn("Right cell", html)
        self.assertNotIn("pdf-preview-candidate--axis_box", html)
        self.assertNotIn("pdf-preview-candidate--open_frame", html)


if __name__ == "__main__":
    unittest.main()
