from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor import DocIR, PageInfo, ParagraphIR, TableCellIR, TableIR
from document_processor.pdf.enhancement import (
    RenderedPdfColorPage,
    RenderedPdfPage,
    enrich_pdf_table_backgrounds,
    enrich_pdf_table_borders,
    infer_cell_background_from_rendered_page,
    infer_cell_borders_from_rendered_page,
)
from document_processor.pdf.meta import PdfBoundingBox, PdfNodeMeta
from document_processor.pdf.preview import prepare_pdf_for_html
from document_processor.style_types import CellStyleInfo, TableStyleInfo


def _make_test_page(*, width: int = 40, height: int = 40) -> RenderedPdfPage:
    pixels = bytearray([255] * (width * height))

    for x in range(10, 30):
        pixels[(10 * width) + x] = 0
        pixels[(29 * width) + x] = 0
    for y in range(10, 30):
        pixels[(y * width) + 10] = 0
        pixels[(y * width) + 29] = 0

    return RenderedPdfPage(
        width_px=width,
        height_px=height,
        stride=width,
        pixels=bytes(pixels),
    )


def _make_test_color_page(*, width: int = 40, height: int = 40) -> RenderedPdfColorPage:
    pixels = bytearray([255] * (width * height * 3))

    def set_pixel(x: int, y: int, *, red: int, green: int, blue: int) -> None:
        idx = (y * width * 3) + (x * 3)
        pixels[idx] = blue
        pixels[idx + 1] = green
        pixels[idx + 2] = red

    for y in range(10, 30):
        for x in range(10, 30):
            set_pixel(x, y, red=223, green=230, blue=247)

    for y in range(16, 24):
        for x in range(16, 24):
            set_pixel(x, y, red=40, green=40, blue=40)

    return RenderedPdfColorPage(
        width_px=width,
        height_px=height,
        stride=width * 3,
        pixels=bytes(pixels),
    )


class PdfEnrichmentTests(unittest.TestCase):
    def test_infer_cell_borders_from_rendered_page_detects_rectangle_edges(self) -> None:
        rendered_page = _make_test_page()

        inferred = infer_cell_borders_from_rendered_page(
            rendered_page,
            bbox=PdfBoundingBox(left_pt=10.0, bottom_pt=10.0, right_pt=30.0, top_pt=30.0),
            page_height_pt=40.0,
            dpi=72,
        )

        self.assertEqual(inferred["top"], "1px solid #000000")
        self.assertEqual(inferred["bottom"], "1px solid #000000")
        self.assertEqual(inferred["left"], "1px solid #000000")
        self.assertEqual(inferred["right"], "1px solid #000000")

    def test_enrich_pdf_table_borders_applies_inferred_borders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "example.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%fake")

            doc = DocIR(
                source_doc_type="pdf",
                source_path=str(pdf_path),
                pages=[PageInfo(page_number=1, width_pt=40.0, height_pt=40.0)],
                paragraphs=[
                    ParagraphIR(
                        unit_id="p1",
                        content=[
                            TableIR(
                                unit_id="p1.tbl1",
                                meta=PdfNodeMeta(page_number=1),
                                table_style=TableStyleInfo(preview_grid=True),
                                cells=[
                                    TableCellIR(
                                        unit_id="p1.tbl1.tr1.tc1",
                                        row_index=1,
                                        col_index=1,
                                        meta=PdfNodeMeta(
                                            page_number=1,
                                            bounding_box=PdfBoundingBox(
                                                left_pt=10.0,
                                                bottom_pt=10.0,
                                                right_pt=30.0,
                                                top_pt=30.0,
                                            ),
                                        ),
                                    )
                                ],
                            )
                        ],
                    )
                ],
            )

            with patch(
                "document_processor.pdf.enhancement.enrichment.render_pdf_pages_to_grayscale",
                return_value={1: _make_test_page()},
            ):
                enrich_pdf_table_borders(doc, pdf_path=pdf_path, dpi=72)

        cell_style = doc.paragraphs[0].tables[0].cells[0].cell_style
        self.assertIsNotNone(cell_style)
        self.assertEqual(cell_style.border_top, "1px solid #000000")
        self.assertEqual(cell_style.border_bottom, "1px solid #000000")
        self.assertEqual(cell_style.border_left, "1px solid #000000")
        self.assertEqual(cell_style.border_right, "1px solid #000000")

    def test_enrich_pdf_table_borders_refines_coarse_parser_borders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "example.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%fake")

            doc = DocIR(
                source_doc_type="pdf",
                source_path=str(pdf_path),
                pages=[PageInfo(page_number=1, width_pt=40.0, height_pt=40.0)],
                paragraphs=[
                    ParagraphIR(
                        unit_id="p1",
                        content=[
                            TableIR(
                                unit_id="p1.tbl1",
                                meta=PdfNodeMeta(page_number=1),
                                table_style=TableStyleInfo(preview_grid=True),
                                cells=[
                                    TableCellIR(
                                        unit_id="p1.tbl1.tr1.tc1",
                                        row_index=1,
                                        col_index=1,
                                        cell_style=CellStyleInfo(
                                            border_top="1px solid",
                                            border_bottom="1px solid",
                                            border_left="1px solid",
                                            border_right="1px solid",
                                        ),
                                        meta=PdfNodeMeta(
                                            page_number=1,
                                            bounding_box=PdfBoundingBox(
                                                left_pt=10.0,
                                                bottom_pt=10.0,
                                                right_pt=30.0,
                                                top_pt=30.0,
                                            ),
                                        ),
                                    )
                                ],
                            )
                        ],
                    )
                ],
            )

            with patch(
                "document_processor.pdf.enhancement.enrichment.render_pdf_pages_to_grayscale",
                return_value={1: _make_test_page()},
            ):
                enrich_pdf_table_borders(doc, pdf_path=pdf_path, dpi=72)

        cell_style = doc.paragraphs[0].tables[0].cells[0].cell_style
        self.assertIsNotNone(cell_style)
        self.assertEqual(cell_style.border_top, "1px solid #000000")
        self.assertEqual(cell_style.border_bottom, "1px solid #000000")
        self.assertEqual(cell_style.border_left, "1px solid #000000")
        self.assertEqual(cell_style.border_right, "1px solid #000000")

    def test_infer_cell_background_from_rendered_page_detects_fill_color(self) -> None:
        rendered_page = _make_test_color_page()

        inferred = infer_cell_background_from_rendered_page(
            rendered_page,
            bbox=PdfBoundingBox(left_pt=10.0, bottom_pt=10.0, right_pt=30.0, top_pt=30.0),
            page_height_pt=40.0,
            dpi=72,
        )

        self.assertEqual(inferred, "#dfe6f7")

    def test_enrich_pdf_table_backgrounds_applies_inferred_background(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "example.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%fake")

            doc = DocIR(
                source_doc_type="pdf",
                source_path=str(pdf_path),
                pages=[PageInfo(page_number=1, width_pt=40.0, height_pt=40.0)],
                paragraphs=[
                    ParagraphIR(
                        unit_id="p1",
                        content=[
                            TableIR(
                                unit_id="p1.tbl1",
                                meta=PdfNodeMeta(page_number=1),
                                table_style=TableStyleInfo(preview_grid=True),
                                cells=[
                                    TableCellIR(
                                        unit_id="p1.tbl1.tr1.tc1",
                                        row_index=1,
                                        col_index=1,
                                        meta=PdfNodeMeta(
                                            page_number=1,
                                            bounding_box=PdfBoundingBox(
                                                left_pt=10.0,
                                                bottom_pt=10.0,
                                                right_pt=30.0,
                                                top_pt=30.0,
                                            ),
                                        ),
                                    )
                                ],
                            )
                        ],
                    )
                ],
            )

            with patch(
                "document_processor.pdf.enhancement.enrichment.render_pdf_pages_to_color",
                return_value={1: _make_test_color_page()},
            ):
                enrich_pdf_table_backgrounds(doc, pdf_path=pdf_path, dpi=72)

        cell_style = doc.paragraphs[0].tables[0].cells[0].cell_style
        self.assertIsNotNone(cell_style)
        self.assertEqual(cell_style.background, "#dfe6f7")

    def test_prepare_pdf_for_html_enriches_pdf_tables_by_default(self) -> None:
        doc = DocIR(source_doc_type="pdf", source_path="/tmp/example.pdf")

        with patch("document_processor.pdf.preview.enrich_pdf_table_borders") as enrich_borders, patch(
            "document_processor.pdf.preview.enrich_pdf_table_backgrounds"
        ) as enrich_backgrounds:
            prepare_pdf_for_html(doc)

        enrich_borders.assert_called_once_with(doc)
        enrich_backgrounds.assert_called_once_with(doc)

    def test_docir_to_html_routes_pdf_with_source_path_through_preview_path(self) -> None:
        doc = DocIR(source_doc_type="pdf", source_path="/tmp/example.pdf")

        with patch(
            "document_processor.pdf.pipeline._build_pdf_preview_context_for_path"
        ) as build_preview_context, patch(
            "document_processor.pdf.preview.render_pdf_preview_html"
        ) as render_preview:
            build_preview_context.return_value = object()
            render_preview.return_value = "<html>preview</html>"

            html = doc.to_html()

        self.assertEqual(html, "<html>preview</html>")
        build_preview_context.assert_called_once_with("/tmp/example.pdf")
        render_preview.assert_called_once()

    def test_docir_to_html_uses_attached_pdf_preview_context_before_rebuilding(self) -> None:
        doc = DocIR(source_doc_type="pdf", source_path="/tmp/example.pdf")
        attached_context = object()
        doc.set_pdf_preview_context(attached_context)

        with patch(
            "document_processor.pdf.pipeline._build_pdf_preview_context_for_path"
        ) as build_preview_context, patch(
            "document_processor.pdf.preview.render_pdf_preview_html"
        ) as render_preview:
            render_preview.return_value = "<html>preview</html>"

            html = doc.to_html()

        self.assertEqual(html, "<html>preview</html>")
        build_preview_context.assert_not_called()
        render_preview.assert_called_once_with(doc, preview_context=attached_context, title=None)

    def test_docir_to_html_routes_pdf_without_source_path_through_render_prep(self) -> None:
        doc = DocIR(source_doc_type="pdf")

        with patch("document_processor.render_prep.prepare_doc_ir_for_html") as prepare_doc:
            doc.to_html()

        prepare_doc.assert_called_once_with(doc)
