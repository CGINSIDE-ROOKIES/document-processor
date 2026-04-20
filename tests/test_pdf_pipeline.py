from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor import DocIR
from document_processor.pdf import export_pdf_local_outputs
from document_processor.pdf.config import PdfParseConfig
import document_processor.pdf.odl.adapter as pdf_odl_adapter
import document_processor.pdf.odl as pdf_odl
from document_processor.pdf.odl import (
    build_doc_ir_from_odl_result,
    convert_pdf_local,
    resolve_odl_jar_path,
)
from document_processor.pdf.odl.table_reconstruct import TableNodeKey
from document_processor.pdf.odl.table_split_plan import BoundaryEvent, TableSplitPlan
from document_processor.pdf.pipeline import parse_pdf_to_doc_ir
from document_processor.pdf.parsing import PageClass, PageDecision, PageProfile, PdfProfile
from document_processor.pdf.preview.context import build_pdf_preview_context
from document_processor.pdf.preview.models import PdfPreviewContext


class PdfPipelineTests(unittest.TestCase):
    def test_docir_from_file_pdf_uses_pdf_pipeline_for_file_object(self) -> None:
        with patch("document_processor.pdf.parse_pdf_to_doc_ir") as parse_pdf:
            parse_pdf.return_value = DocIR(
                doc_id="sample",
                source_path="sample.pdf",
                source_doc_type="pdf",
            )
            result = DocIR.from_file(BytesIO(b"%PDF-1.7\n%fake"), doc_type="pdf")

        self.assertEqual(result.source_doc_type, "pdf")
        parse_pdf.assert_called_once()
        parsed_path = parse_pdf.call_args.args[0]
        self.assertTrue(str(parsed_path).endswith(".pdf"))
        self.assertNotIn("config", parse_pdf.call_args.kwargs)

    def test_docir_from_file_pdf_to_html_renders_preview_content(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "pages": [{"page number": 1, "width pt": 200, "height pt": 120}],
            "layout regions": [
                {
                    "region id": "p1-main",
                    "region type": "full",
                    "page number": 1,
                    "bounding box": [0, 0, 200, 120],
                }
            ],
            "kids": [
                {
                    "type": "paragraph",
                    "content": "Preview body",
                    "page number": 1,
                    "layout region id": "p1-main",
                    "reading order index": 1,
                },
                {
                    "type": "table",
                    "page number": 1,
                    "layout region id": "p1-main",
                    "reading order index": 2,
                    "bounding box": [20, 30, 180, 90],
                    "rows": [
                        {
                            "cells": [
                                {
                                    "type": "table cell",
                                    "row number": 1,
                                    "column number": 1,
                                    "page number": 1,
                                    "kids": [{"type": "paragraph", "content": "A1", "page number": 1}],
                                }
                            ]
                        }
                    ],
                },
            ],
        }
        profile = PdfProfile(
            page_count=1,
            avg_chars_per_page=10.0,
            normal_text_ratio=1.0,
            text_readable=True,
            text_readable_page_ratio=1.0,
            page_profiles=[
                PageProfile(
                    page_number=1,
                    char_count=10,
                    normal_text_ratio=1.0,
                    replacement_char_ratio=0.0,
                    text_readable=True,
                    image_area_ratio=0.0,
                    image_area_in_content_ratio=0.0,
                    page_width_pt=200.0,
                    page_height_pt=120.0,
                )
            ],
        )

        with patch("document_processor.pdf.pipeline.probe_pdf", return_value=profile), patch(
            "document_processor.pdf.pipeline.run_odl_json", return_value=raw_document
        ), patch("document_processor.pdf.preview.context._augment_layout_regions_with_pdfium", return_value=None):
            doc = DocIR.from_file(BytesIO(b"%PDF-1.7\n%fake"), doc_type="pdf")
            preview_context = doc.get_pdf_preview_context()
            self.assertIsNotNone(preview_context)
            self.assertTrue(any(region.region_id == "p1-main" for region in preview_context.layout_regions))
            self.assertTrue(any(table.layout_region_id == "p1-main" for table in preview_context.tables))
            html = doc.to_html(title="Preview")

        self.assertIn("Preview body", html)
        self.assertEqual(html.count('<section class="document-page"'), 1)
        self.assertNotIn("document-region-band--columns", html)

    def test_build_doc_ir_from_odl_result_builds_paragraph_table_and_asset(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 2,
            "author": "Hancom",
            "title": "Sample PDF",
            "creation date": "2026-04-09T09:00:00Z",
            "modification date": "2026-04-09T10:00:00Z",
            "pages": [
                {"page number": 1, "width pt": 612, "height pt": 792},
                {"page number": 2, "width pt": 612, "height pt": 792},
            ],
            "kids": [
                {
                    "type": "paragraph",
                    "content": "Hello PDF",
                    "page number": 1,
                    "id": 101,
                    "bounding box": [10, 20, 30, 40],
                    "layout region id": "p1-main",
                    "reading order index": 1,
                    "heading level": 2,
                    "font": "Noto Serif KR",
                    "text color": "#112233",
                    "font size": 11,
                },
                {
                    "type": "formula",
                    "content": "\\frac{a}{b}",
                    "page number": 1,
                    "id": 111,
                    "layout region id": "p1-main",
                    "reading order index": 2,
                },
                {
                    "type": "list",
                    "numbering style": "ordered",
                    "previous list id": 10,
                    "next list id": 12,
                    "list items": [
                        {
                            "type": "list item",
                            "content": "First item",
                            "page number": 1,
                            "id": 112,
                            "layout region id": "p1-main",
                            "reading order index": 3,
                        }
                    ],
                },
                {
                    "type": "table",
                    "page number": 2,
                    "id": 202,
                    "bounding box": [200, 210, 260, 310],
                    "layout region id": "p2-main",
                    "reading order index": 4,
                    "number of rows": 1,
                    "number of columns": 1,
                    "rows": [
                        {
                            "cells": [
                                {
                                    "type": "table cell",
                                    "id": 303,
                                    "row number": 1,
                                    "column number": 1,
                                    "page number": 2,
                                    "bounding box": [210, 220, 250, 300],
                                    "layout region id": "p2-main",
                                    "reading order index": 5,
                                    "has top border": True,
                                    "has bottom border": True,
                                    "has left border": True,
                                    "has right border": True,
                                    "kids": [
                                        {
                                            "type": "paragraph",
                                            "content": "A1",
                                            "page number": 2,
                                        }
                                    ],
                                }
                            ]
                        }
                    ],
                },
                {
                    "type": "image",
                    "page number": 2,
                    "bounding box": [300, 100, 420, 140],
                    "data": "data:image/png;base64,QUJD",
                    "width px": 120,
                    "height px": 40,
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")

        self.assertEqual(doc.source_doc_type, "pdf")
        self.assertEqual(doc.meta.file_name, "sample.pdf")
        self.assertEqual(doc.meta.number_of_pages, 2)
        self.assertEqual(doc.meta.author, "Hancom")
        self.assertEqual(doc.meta.title, "Sample PDF")
        self.assertEqual(doc.meta.creation_date, "2026-04-09T09:00:00Z")
        self.assertEqual(doc.meta.modification_date, "2026-04-09T10:00:00Z")
        self.assertEqual([page.page_number for page in doc.pages], [1, 2])
        self.assertEqual(doc.paragraphs[0].text, "Hello PDF")
        self.assertEqual(doc.paragraphs[0].bbox.left_pt, 10.0)
        self.assertEqual(doc.paragraphs[0].meta.page_number, 1)
        self.assertEqual(doc.paragraphs[0].meta.bounding_box.left_pt, 10.0)
        self.assertEqual(doc.paragraphs[0].meta.layout_region_id, "p1-main")
        self.assertEqual(doc.paragraphs[0].meta.reading_order_index, 1)
        self.assertEqual(doc.paragraphs[0].runs[0].bbox.left_pt, 10.0)
        self.assertEqual(doc.paragraphs[0].runs[0].meta.page_number, 1)
        self.assertEqual(doc.paragraphs[0].runs[0].run_style.font_family, "Noto Serif KR")
        self.assertEqual(doc.paragraphs[1].text, "\\frac{a}{b}")
        self.assertEqual(doc.paragraphs[1].meta.reading_order_index, 2)
        self.assertEqual(doc.paragraphs[2].text, "First item")
        self.assertEqual(doc.paragraphs[2].meta.layout_region_id, "p1-main")
        self.assertEqual(doc.paragraphs[2].meta.reading_order_index, 3)
        self.assertEqual(doc.paragraphs[3].tables[0].cells[0].text, "A1")
        self.assertEqual(doc.paragraphs[3].bbox.left_pt, 200.0)
        self.assertEqual(doc.paragraphs[3].tables[0].bbox.left_pt, 200.0)
        self.assertEqual(doc.paragraphs[3].tables[0].meta.layout_region_id, "p2-main")
        self.assertTrue(doc.paragraphs[3].tables[0].table_style.preview_grid)
        self.assertEqual(doc.paragraphs[3].tables[0].cells[0].bbox.left_pt, 210.0)
        self.assertEqual(doc.paragraphs[3].tables[0].cells[0].meta.reading_order_index, 5)
        self.assertEqual(doc.paragraphs[3].tables[0].cells[0].cell_style.border_top, "1px solid")
        self.assertEqual(doc.paragraphs[3].tables[0].cells[0].cell_style.border_right, "1px solid")
        self.assertIn("odl-img-p5", doc.assets)
        self.assertEqual(doc.paragraphs[4].images[0].image_id, "odl-img-p5")
        self.assertEqual(doc.paragraphs[4].bbox.left_pt, 300.0)
        self.assertEqual(doc.paragraphs[4].images[0].bbox.left_pt, 300.0)
        self.assertEqual(doc.assets["odl-img-p5"].meta.page_number, 2)

    def test_build_doc_ir_from_odl_result_forwards_table_grids_into_table_node_adapter(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "kids": [
                {
                    "type": "table",
                    "page number": 1,
                    "reading order index": 1,
                    "bounding box": [10, 20, 110, 120],
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
                                    "kids": [
                                        {
                                            "type": "table",
                                            "page number": 1,
                                            "reading order index": 2,
                                            "bounding box": [20, 30, 60, 70],
                                            "rows": [],
                                        }
                                    ],
                                }
                            ]
                        }
                    ],
                },
                {
                    "type": "header",
                    "page number": 1,
                    "kids": [
                        {
                            "type": "table",
                            "page number": 1,
                            "reading order index": 3,
                            "bounding box": [120, 20, 160, 60],
                            "rows": [],
                        }
                    ],
                },
                {
                    "type": "list",
                    "list items": [
                        {
                            "type": "list item",
                            "page number": 1,
                            "content": "item",
                            "kids": [
                                {
                                    "type": "table",
                                    "page number": 1,
                                    "reading order index": 4,
                                    "bounding box": [170, 20, 210, 60],
                                    "rows": [],
                                }
                            ],
                        }
                    ],
                },
                {
                    "type": "text block",
                    "kids": [
                        {
                            "type": "table",
                            "page number": 1,
                            "reading order index": 5,
                            "bounding box": [220, 20, 260, 60],
                            "rows": [],
                        }
                    ],
                },
            ],
        }
        table_grids = {
            TableNodeKey(
                page_number=1,
                reading_order_index=1,
                left_pt=10.0,
                bottom_pt=20.0,
                right_pt=110.0,
                top_pt=120.0,
            ): object()
        }

        with patch(
            "document_processor.pdf.odl.adapter._table_node_to_ir",
            wraps=pdf_odl_adapter._table_node_to_ir,
        ) as table_node_to_ir:
            build_doc_ir_from_odl_result(
                raw_document,
                source_path="sample.pdf",
                table_grids=table_grids,
            )

        self.assertEqual(table_node_to_ir.call_count, 5)
        self.assertTrue(all(call.kwargs["table_grids"] is table_grids for call in table_node_to_ir.call_args_list))

    def test_build_doc_ir_from_odl_result_preserves_text_whitespace_and_header_footer_children(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "kids": [
                {
                    "type": "paragraph",
                    "content": "  Hello PDF  ",
                    "page number": 1,
                    "id": 101,
                },
                {
                    "type": "paragraph",
                    "content": "   ",
                    "page number": 1,
                    "id": 102,
                },
                {
                    "type": "header",
                    "page number": 1,
                    "id": 201,
                    "kids": [
                        {
                            "type": "paragraph",
                            "content": "Header line",
                            "page number": 1,
                            "id": 202,
                            "font": "Noto Sans KR",
                            "font size": 9,
                        }
                    ],
                },
                {
                    "type": "table",
                    "page number": 1,
                    "rows": [
                        {
                            "cells": [
                                {
                                    "type": "table cell",
                                    "row number": 1,
                                    "column number": 1,
                                    "page number": 1,
                                    "kids": [
                                        {
                                            "type": "paragraph",
                                            "content": "  A1  ",
                                            "page number": 1,
                                        }
                                    ],
                                }
                            ]
                        }
                    ],
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")

        self.assertEqual(len(doc.paragraphs), 3)
        self.assertEqual(doc.paragraphs[0].text, "  Hello PDF  ")
        self.assertEqual(doc.paragraphs[1].text, "Header line")
        self.assertEqual(doc.paragraphs[1].meta.page_number, 1)
        self.assertEqual(doc.paragraphs[1].runs[0].meta.page_number, 1)
        self.assertEqual(doc.paragraphs[1].runs[0].run_style.font_family, "Noto Sans KR")
        self.assertEqual(doc.paragraphs[1].runs[0].run_style.size_pt, 9.0)
        self.assertEqual(doc.paragraphs[2].tables[0].cells[0].text, "  A1  ")
        self.assertEqual(doc.paragraphs[2].tables[0].cells[0].paragraphs[0].text, "  A1  ")

    def test_build_doc_ir_from_odl_result_uses_additive_spans_for_multi_run_text(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "kids": [
                {
                    "type": "heading",
                    "content": "Hello PDF",
                    "page number": 1,
                    "id": 101,
                    "heading level": 1,
                    "font": "Parent Font",
                    "font size": 18,
                    "text color": "#112233",
                    "spans": [
                        {
                            "type": "text chunk",
                            "content": "Hello",
                            "page number": 1,
                            "bounding box": [1, 2, 3, 4],
                            "font": "Span Font",
                            "font size": 19,
                            "text color": "#abcdef",
                            "font weight": 700,
                        },
                        {
                            "type": "text chunk",
                            "content": " ",
                            "page number": 1,
                            "bounding box": [3, 2, 4, 4],
                        },
                        {
                            "type": "text chunk",
                            "content": "PDF",
                            "page number": 1,
                            "bounding box": [4, 2, 6, 4],
                            "italic angle": 12,
                            "underline": True,
                        },
                    ],
                },
                {
                    "type": "table",
                    "page number": 1,
                    "rows": [
                        {
                            "cells": [
                                {
                                    "type": "table cell",
                                    "row number": 1,
                                    "column number": 1,
                                    "page number": 1,
                                    "kids": [
                                        {
                                            "type": "paragraph",
                                            "content": "A1",
                                            "page number": 1,
                                            "font": "Cell Font",
                                            "font size": 10,
                                            "spans": [
                                                {
                                                    "type": "text chunk",
                                                    "content": "A",
                                                    "page number": 1,
                                                    "font": "Cell Span Font",
                                                    "font size": 11,
                                                },
                                                {
                                                    "type": "text chunk",
                                                    "content": "1",
                                                    "page number": 1,
                                                },
                                            ],
                                        }
                                    ],
                                }
                            ]
                        }
                    ],
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")

        self.assertEqual(doc.paragraphs[0].text, "Hello PDF")
        self.assertEqual([run.text for run in doc.paragraphs[0].runs], ["Hello", " ", "PDF"])
        self.assertEqual(doc.paragraphs[0].runs[0].run_style.font_family, "Span Font")
        self.assertEqual(doc.paragraphs[0].runs[0].run_style.size_pt, 19.0)
        self.assertEqual(doc.paragraphs[0].runs[0].run_style.color, "#abcdef")
        self.assertTrue(doc.paragraphs[0].runs[0].run_style.bold)
        self.assertEqual(doc.paragraphs[0].runs[1].run_style.font_family, "Parent Font")
        self.assertEqual(doc.paragraphs[0].runs[1].run_style.size_pt, 18.0)
        self.assertTrue(doc.paragraphs[0].runs[2].run_style.italic)
        self.assertTrue(doc.paragraphs[0].runs[2].run_style.underline)
        self.assertEqual(doc.paragraphs[0].runs[0].meta.page_number, 1)
        self.assertEqual(doc.paragraphs[0].runs[0].meta.bounding_box.left_pt, 1.0)
        self.assertEqual(doc.paragraphs[1].tables[0].cells[0].text, "A1")
        self.assertEqual(
            [run.text for run in doc.paragraphs[1].tables[0].cells[0].paragraphs[0].runs],
            ["A", "1"],
        )
        self.assertEqual(
            doc.paragraphs[1].tables[0].cells[0].paragraphs[0].runs[0].run_style.font_family,
            "Cell Span Font",
        )
        self.assertEqual(
            doc.paragraphs[1].tables[0].cells[0].paragraphs[0].runs[1].run_style.font_family,
            "Cell Font",
        )

    def test_build_doc_ir_from_odl_result_merges_adjacent_spans_with_same_style(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "kids": [
                {
                    "type": "paragraph",
                    "content": "Hello World",
                    "page number": 1,
                    "font": "Base Font",
                    "font size": 12,
                    "text color": "#111111",
                    "spans": [
                        {
                            "type": "text chunk",
                            "content": "Hello",
                            "page number": 1,
                            "bounding box": [1, 2, 3, 4],
                            "font": "Base Font",
                            "font size": 12,
                            "text color": "#111111",
                        },
                        {
                            "type": "text chunk",
                            "content": " ",
                            "page number": 1,
                            "bounding box": [3, 2, 4, 4],
                        },
                        {
                            "type": "text chunk",
                            "content": "World",
                            "page number": 1,
                            "bounding box": [4, 2, 7, 4],
                            "font": "Base Font",
                            "font size": 12,
                            "text color": "#111111",
                        },
                    ],
                }
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")

        self.assertEqual(doc.paragraphs[0].text, "Hello World")
        self.assertEqual(len(doc.paragraphs[0].runs), 1)
        self.assertEqual(doc.paragraphs[0].runs[0].text, "Hello World")
        self.assertEqual(doc.paragraphs[0].runs[0].meta.bounding_box.left_pt, 1.0)
        self.assertEqual(doc.paragraphs[0].runs[0].meta.bounding_box.right_pt, 7.0)

    def test_build_doc_ir_from_odl_result_prefers_node_text_when_spans_flatten_newlines(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "kids": [
                {
                    "type": "paragraph",
                    "content": "64\n65\n66 68 69390",
                    "page number": 1,
                    "font": "Base Font",
                    "font size": 12,
                    "spans": [
                        {"type": "text chunk", "content": "64", "page number": 1},
                        {"type": "text chunk", "content": " ", "page number": 1},
                        {"type": "text chunk", "content": "65", "page number": 1},
                        {"type": "text chunk", "content": " ", "page number": 1},
                        {"type": "text chunk", "content": "66", "page number": 1},
                    ],
                }
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")

        self.assertEqual(doc.paragraphs[0].text, "64\n65\n66 68 69390")
        self.assertEqual(len(doc.paragraphs[0].runs), 1)
        self.assertEqual(doc.paragraphs[0].runs[0].text, "64\n65\n66 68 69390")

    def test_parse_pdf_to_doc_ir_uses_probe_for_page_sizes_and_filters_scan_pages(self) -> None:
        profile = PdfProfile(
            page_count=3,
            avg_chars_per_page=30.0,
            normal_text_ratio=0.8,
            text_readable=True,
            text_readable_page_ratio=2 / 3,
            page_profiles=[
                PageProfile(
                    page_number=1,
                    char_count=0,
                    normal_text_ratio=0.0,
                    replacement_char_ratio=0.0,
                    text_readable=False,
                    image_area_ratio=1.0,
                    image_area_in_content_ratio=1.0,
                    page_width_pt=612.0,
                    page_height_pt=792.0,
                ),
                PageProfile(
                    page_number=2,
                    char_count=40,
                    normal_text_ratio=0.8,
                    replacement_char_ratio=0.0,
                    text_readable=True,
                    image_area_ratio=0.1,
                    image_area_in_content_ratio=0.1,
                    page_width_pt=612.0,
                    page_height_pt=792.0,
                ),
                PageProfile(
                    page_number=3,
                    char_count=35,
                    normal_text_ratio=0.7,
                    replacement_char_ratio=0.0,
                    text_readable=True,
                    image_area_ratio=0.1,
                    image_area_in_content_ratio=0.1,
                    page_width_pt=612.0,
                    page_height_pt=792.0,
                ),
            ],
        )
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 3,
            "kids": [
                {
                    "type": "paragraph",
                    "content": "Structured page 2",
                    "page number": 2,
                },
                {
                    "type": "paragraph",
                    "content": "Structured page 3",
                    "page number": 3,
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%fake")

            with patch("document_processor.pdf.pipeline.probe_pdf", return_value=profile):
                with patch("document_processor.pdf.pipeline.run_odl_json", return_value=raw_document) as run_odl, patch(
                    "document_processor.pdf.pipeline.build_pdf_preview_context"
                ) as build_preview_context:
                    build_preview_context.return_value = object()
                    doc = parse_pdf_to_doc_ir(pdf_path)

        self.assertEqual(run_odl.call_args.kwargs, {})
        self.assertEqual(run_odl.call_args.args[1]["pages"], [2, 3])
        self.assertEqual(run_odl.call_args.args[1]["image_output"], "embedded")
        self.assertEqual(
            build_preview_context.call_args.kwargs,
            {"pdf_path": pdf_path, "page_numbers": [2, 3]},
        )
        self.assertEqual([page.page_number for page in doc.pages], [1, 2, 3])
        self.assertEqual([paragraph.page_number for paragraph in doc.paragraphs], [2, 3])
        self.assertEqual(doc.meta.parser, "odl-local")
        self.assertEqual(doc.meta.file_name, "sample.pdf")
        self.assertEqual(doc.meta.number_of_pages, 3)
        self.assertEqual(doc.meta.structured_pages, [2, 3])
        self.assertEqual(doc.meta.scan_like_pages, [1])
        self.assertIs(doc.get_pdf_preview_context(), build_preview_context.return_value)

    def test_parse_pdf_to_doc_ir_passes_table_grids_into_adapter(self) -> None:
        profile = PdfProfile(
            page_count=1,
            avg_chars_per_page=100.0,
            normal_text_ratio=0.9,
            text_readable=True,
            text_readable_page_ratio=1.0,
            page_profiles=[
                PageProfile(
                    page_number=1,
                    char_count=100,
                    normal_text_ratio=0.9,
                    replacement_char_ratio=0.0,
                    text_readable=True,
                    image_area_ratio=0.0,
                    image_area_in_content_ratio=0.0,
                    page_width_pt=612.0,
                    page_height_pt=792.0,
                ),
            ],
        )
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "kids": [],
        }
        table_grids = {
            TableNodeKey(
                page_number=1,
                reading_order_index=3,
                left_pt=10.0,
                bottom_pt=20.0,
                right_pt=30.0,
                top_pt=40.0,
            ): object()
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%fake")

            with patch("document_processor.pdf.pipeline.probe_pdf", return_value=profile), patch(
                "document_processor.pdf.pipeline.decide_page",
                return_value=PageDecision(page_number=1, page_class=PageClass.STRUCTURED),
            ), patch("document_processor.pdf.pipeline.run_odl_json", return_value=raw_document), patch(
                "document_processor.pdf.pipeline.build_pdf_preview_context",
                return_value=PdfPreviewContext(),
            ), patch(
                "document_processor.pdf.pipeline.build_table_grids",
                return_value=table_grids,
                create=True,
            ), patch(
                "document_processor.pdf.pipeline.build_doc_ir_from_odl_result",
                return_value=DocIR(source_doc_type="pdf", source_path=str(pdf_path)),
            ) as build_doc:
                parse_pdf_to_doc_ir(pdf_path)

        build_doc.assert_called_once_with(
            raw_document,
            source_path=str(pdf_path),
            doc_id=None,
            doc_cls=DocIR,
            table_grids=table_grids,
        )

    def test_pdf_parse_config_no_longer_exposes_infer_table_splits(self) -> None:
        config = PdfParseConfig()

        self.assertNotIn("infer_table_splits", PdfParseConfig.model_fields)
        self.assertFalse(hasattr(config, "infer_table_splits"))

    def test_parse_pdf_to_doc_ir_rejects_legacy_infer_table_splits_config_key(self) -> None:
        with self.assertRaisesRegex(ValidationError, "infer_table_splits"):
            parse_pdf_to_doc_ir("sample.pdf", config={"infer_table_splits": False})

    def test_pdf_parse_config_rejects_legacy_infer_table_splits_key(self) -> None:
        with self.assertRaisesRegex(ValidationError, "infer_table_splits"):
            PdfParseConfig.model_validate({"infer_table_splits": False})

    def test_build_table_grids_returns_empty_when_pdfium_cannot_open_pdf(self) -> None:
        raw_document = {
            "number of pages": 1,
            "kids": [
                {
                    "type": "table",
                    "page number": 1,
                    "reading order index": 1,
                    "bounding box": [10, 20, 30, 40],
                    "rows": [],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%fake")

            self.assertEqual(
                pdf_odl.build_table_grids(raw_document, pdf_path=pdf_path, page_numbers=[1]),
                {},
            )

    def test_build_table_grids_returns_empty_when_primitive_extraction_raises(self) -> None:
        raw_document = {
            "number of pages": 1,
            "kids": [
                {
                    "type": "table",
                    "page number": 1,
                    "reading order index": 1,
                    "bounding box": [10, 20, 30, 40],
                    "rows": [],
                }
            ],
        }

        class FakePdfDocument:
            page_count = 1

            def __getitem__(self, index):
                return object()

            def close(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%fake")

            with patch.dict(
                sys.modules,
                {"pypdfium2": SimpleNamespace(PdfDocument=lambda path: FakePdfDocument())},
            ), patch(
                "document_processor.pdf.odl.table_reconstruct.extract_pdfium_table_rule_primitives",
                side_effect=RuntimeError("boom"),
            ):
                self.assertEqual(
                    pdf_odl.build_table_grids(raw_document, pdf_path=pdf_path, page_numbers=[1]),
                    {},
                )

    def test_build_doc_ir_from_odl_result_accepts_split_plans_keyed_by_package_level_table_node_key(self) -> None:
        table_node = {
            "type": "table",
            "page number": 1,
            "reading order index": 1,
            "bounding box": [10, 20, 30, 40],
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
                            "bounding box": [10, 20, 30, 40],
                            "kids": [{"type": "paragraph", "content": "A1", "page number": 1}],
                        }
                    ]
                }
            ],
        }
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 1,
            "kids": [table_node],
        }

        self.assertTrue(callable(pdf_odl.build_table_split_plans))
        self.assertTrue(callable(pdf_odl.build_table_split_plan_for_table_node))

        key = pdf_odl.table_node_key(table_node)
        split_plan = TableSplitPlan(
            table_key=key,
            column_events=[BoundaryEvent(source_index=1, axis_pt=20.0, supporting_cells=frozenset())],
        )

        doc = build_doc_ir_from_odl_result(
            raw_document,
            source_path="sample.pdf",
            table_split_plans={key: split_plan},
        )

        self.assertEqual(doc.paragraphs[0].tables[0].col_count, 2)

    def test_build_table_split_plans_round_trips_with_package_level_table_node_key(self) -> None:
        table_node = {
            "type": "table",
            "page number": 1,
            "reading order index": 1,
            "bounding box": [10, 20, 30, 40],
            "rows": [],
        }
        raw_document = {
            "number of pages": 1,
            "kids": [table_node],
        }

        class FakePdfDocument:
            page_count = 1

            def __getitem__(self, index):
                return object()

            def close(self) -> None:
                return None

        legacy_plan = TableSplitPlan(
            table_key=pdf_odl.table_split_plan.table_node_key(table_node),
            column_events=[BoundaryEvent(source_index=1, axis_pt=20.0, supporting_cells=frozenset())],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%fake")

            with patch.dict(
                sys.modules,
                {"pypdfium2": SimpleNamespace(PdfDocument=lambda path: FakePdfDocument())},
            ), patch(
                "document_processor.pdf.odl.table_split_plan.extract_pdfium_table_rule_primitives",
                return_value=[object()],
            ), patch(
                "document_processor.pdf.odl.table_split_plan.build_table_split_plan_for_table_node",
                return_value=legacy_plan,
            ):
                plans = pdf_odl.build_table_split_plans(
                    raw_document,
                    pdf_path=pdf_path,
                    page_numbers=[1],
                )

        self.assertIs(plans.get(pdf_odl.table_node_key(table_node)), legacy_plan)

    def test_resolve_odl_jar_path_uses_vendored_jar(self) -> None:
        jar_path = resolve_odl_jar_path()

        self.assertTrue(jar_path.exists())
        self.assertEqual(jar_path.name, "opendataloader-pdf-cli-2.2.1.jar")

    def test_convert_pdf_local_runs_vendored_jar_and_returns_expected_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%fake")
            output_dir = Path(tmp_dir) / "out"

            def fake_run(command, **kwargs):
                self.assertEqual(
                    command[:4],
                    ["java", "-Djava.awt.headless=true", "-jar", str(resolve_odl_jar_path())],
                )
                self.assertIn("--format", command)
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "sample.json").write_text('{"ok": true}', encoding="utf-8")
                (output_dir / "sample.html").write_text("<p>ok</p>", encoding="utf-8")
                (output_dir / "sample.md").write_text("# ok", encoding="utf-8")
                return None

            with patch("document_processor.pdf.odl.runner.subprocess.run", side_effect=fake_run) as run_cli:
                outputs = convert_pdf_local(
                    pdf_path,
                    output_dir=output_dir,
                    formats=["json", "html", "markdown"],
                    config={"pages": [2, 3], "use_struct_tree": True},
                )

        run_cli.assert_called_once()
        self.assertEqual(outputs["json"].name, "sample.json")
        self.assertEqual(outputs["html"].name, "sample.html")
        self.assertEqual(outputs["markdown"].name, "sample.md")

    def test_convert_pdf_local_passes_preserve_whitespace_flag_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%fake")
            output_dir = Path(tmp_dir) / "out"

            def fake_run(command, **kwargs):
                self.assertIn("--preserve-whitespace", command)
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "sample.json").write_text('{"ok": true}', encoding="utf-8")
                return None

            with patch("document_processor.pdf.odl.runner.subprocess.run", side_effect=fake_run):
                outputs = convert_pdf_local(
                    pdf_path,
                    output_dir=output_dir,
                    formats=["json"],
                    config={"preserve_whitespace": True},
                )

        self.assertEqual(outputs["json"].name, "sample.json")

    def test_export_pdf_local_outputs_returns_readable_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "native"

            def fake_convert(path, *, output_dir, formats, config):
                self.assertEqual(Path(path).suffix, ".pdf")
                self.assertEqual(list(formats), ["json", "html", "markdown"])
                output_dir.mkdir(parents=True, exist_ok=True)
                json_path = output_dir / "sample.json"
                html_path = output_dir / "sample.html"
                markdown_path = output_dir / "sample.md"
                json_path.write_text('{"source": "odl"}', encoding="utf-8")
                html_path.write_text("<article>native</article>", encoding="utf-8")
                markdown_path.write_text("# native", encoding="utf-8")
                return {
                    "json": json_path,
                    "html": html_path,
                    "markdown": markdown_path,
                }

            with patch(
                "document_processor.pdf.local_outputs.convert_pdf_local",
                side_effect=fake_convert,
            ):
                outputs = export_pdf_local_outputs(
                    BytesIO(b"%PDF-1.7\n%fake"),
                    output_dir=output_dir,
                )

                self.assertEqual(outputs.read_json()["source"], "odl")
                self.assertEqual(outputs.read_text("html"), "<article>native</article>")
                self.assertEqual(outputs.read_text("markdown"), "# native")
                self.assertEqual(outputs.html_path.name, "sample.html")
                self.assertEqual(outputs.markdown_path.name, "sample.md")
