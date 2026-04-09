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
from document_processor.pdf.enhancement import RenderedPdfPage, enrich_pdf_table_borders, infer_cell_borders_from_rendered_page
from document_processor.pdf.meta import PdfBoundingBox, PdfNodeMeta
from document_processor.pdf.render_prep import prepare_pdf_for_html
from document_processor.style_types import TableStyleInfo


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
                                meta=PdfNodeMeta(source_type="table", page_number=1),
                                table_style=TableStyleInfo(preview_grid=True),
                                cells=[
                                    TableCellIR(
                                        unit_id="p1.tbl1.tr1.tc1",
                                        row_index=1,
                                        col_index=1,
                                        meta=PdfNodeMeta(
                                            source_type="table cell",
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

    def test_prepare_pdf_for_html_enriches_pdf_tables_by_default(self) -> None:
        doc = DocIR(source_doc_type="pdf", source_path="/tmp/example.pdf")

        with patch("document_processor.pdf.render_prep.enrich_pdf_table_borders") as enrich_pdf:
            prepare_pdf_for_html(doc)

        enrich_pdf.assert_called_once_with(doc)

    def test_docir_to_html_routes_through_render_prep(self) -> None:
        doc = DocIR(source_doc_type="pdf", source_path="/tmp/example.pdf")

        with patch("document_processor.render_prep.prepare_doc_ir_for_html") as prepare_doc:
            doc.to_html()

        prepare_doc.assert_called_once_with(doc)
