from __future__ import annotations

from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor import DocIR, PageInfo, ParagraphIR, RunIR, TableCellIR, TableIR
from document_processor.models import BoundingBox
from document_processor.pdf.enhancement import enrich_pdf_table_splits
from document_processor.pdf.meta import PdfBoundingBox, PdfNodeMeta
from document_processor.pdf.preview.analyze import extract_pdfium_table_rule_primitives
from document_processor.pdf.preview.models import PdfPreviewVisualPrimitive


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


class PdfTableSplitPrimitiveTests(unittest.TestCase):
    def test_extract_pdfium_table_rule_primitives_promotes_segmented_vertical_rule(self) -> None:
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

        primitives = extract_pdfium_table_rule_primitives(
            _FakePage(objects, width=100.0, height=100.0),
            page_number=1,
            raw_module=_FakeRawModule,
        )

        self.assertEqual(len(primitives), 1)
        self.assertEqual(primitives[0].object_type, "segmented_vertical_rule")
        self.assertEqual(
            set(primitives[0].candidate_roles),
            {"vertical_line_segment", "segmented_vertical_rule"},
        )


def _text_paragraph(unit_id: str, text: str, *, left: float, bottom: float, right: float, top: float) -> ParagraphIR:
    bbox = BoundingBox(left_pt=left, bottom_pt=bottom, right_pt=right, top_pt=top)
    paragraph = ParagraphIR(
        unit_id=unit_id,
        bbox=bbox,
        content=[RunIR(unit_id=f"{unit_id}.r1", text=text, bbox=bbox)],
    )
    paragraph.recompute_text()
    return paragraph


def _single_cell_doc(*, left_para: ParagraphIR, right_para: ParagraphIR | None = None) -> DocIR:
    cell_bbox = PdfBoundingBox(left_pt=10.0, bottom_pt=10.0, right_pt=90.0, top_pt=40.0)
    paragraphs = [left_para]
    if right_para is not None:
        paragraphs.append(right_para)
    cell = TableCellIR(
        unit_id="p1.tbl1.tr1.tc1",
        row_index=1,
        col_index=1,
        bbox=cell_bbox,
        meta=PdfNodeMeta(page_number=1, bounding_box=cell_bbox),
        paragraphs=paragraphs,
    )
    cell.recompute_text()
    table = TableIR(
        unit_id="p1.tbl1",
        row_count=1,
        col_count=1,
        bbox=cell_bbox,
        meta=PdfNodeMeta(page_number=1, bounding_box=cell_bbox),
        cells=[cell],
    )
    return DocIR(
        source_doc_type="pdf",
        source_path="/tmp/example.pdf",
        pages=[PageInfo(page_number=1, width_pt=100.0, height_pt=50.0)],
        paragraphs=[ParagraphIR(unit_id="p1", content=[table])],
    )


class PdfTableSplitEnrichmentTests(unittest.TestCase):
    def test_enrich_pdf_table_splits_splits_vertical_text_bearing_cell(self) -> None:
        doc = _single_cell_doc(
            left_para=_text_paragraph("p1.tbl1.tr1.tc1.p1", "Left", left=14.0, bottom=14.0, right=42.0, top=22.0),
            right_para=_text_paragraph("p1.tbl1.tr1.tc1.p2", "Right", left=58.0, bottom=14.0, right=86.0, top=22.0),
        )
        primitive = PdfPreviewVisualPrimitive(
            page_number=1,
            draw_order=1,
            object_type="segmented_vertical_rule",
            bounding_box=PdfBoundingBox(left_pt=49.5, bottom_pt=11.0, right_pt=50.5, top_pt=39.0),
            stroke_color="#0000ffff",
            stroke_width_pt=1.0,
            has_stroke=True,
            candidate_roles=["vertical_line_segment", "segmented_vertical_rule"],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%fake")
            doc.source_path = str(pdf_path)

            with patch(
                "document_processor.pdf.enhancement.table_split_inference._extract_rule_primitives_for_pages",
                return_value={1: [primitive]},
            ):
                enrich_pdf_table_splits(doc, pdf_path=pdf_path)

        table = doc.paragraphs[0].tables[0]
        self.assertEqual(table.row_count, 1)
        self.assertEqual(table.col_count, 2)
        self.assertEqual(
            [(cell.row_index, cell.col_index, cell.text) for cell in table.cells],
            [(1, 1, "Left"), (1, 2, "Right")],
        )

    def test_enrich_pdf_table_splits_leaves_single_sided_text_unsplit(self) -> None:
        doc = _single_cell_doc(
            left_para=_text_paragraph("p1.tbl1.tr1.tc1.p1", "Only left", left=14.0, bottom=14.0, right=42.0, top=22.0),
        )
        primitive = PdfPreviewVisualPrimitive(
            page_number=1,
            draw_order=1,
            object_type="segmented_vertical_rule",
            bounding_box=PdfBoundingBox(left_pt=49.5, bottom_pt=11.0, right_pt=50.5, top_pt=39.0),
            stroke_color="#0000ffff",
            stroke_width_pt=1.0,
            has_stroke=True,
            candidate_roles=["vertical_line_segment", "segmented_vertical_rule"],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%fake")
            doc.source_path = str(pdf_path)

            with patch(
                "document_processor.pdf.enhancement.table_split_inference._extract_rule_primitives_for_pages",
                return_value={1: [primitive]},
            ):
                enrich_pdf_table_splits(doc, pdf_path=pdf_path)

        table = doc.paragraphs[0].tables[0]
        self.assertEqual(table.row_count, 1)
        self.assertEqual(table.col_count, 1)
        self.assertEqual(
            [(cell.row_index, cell.col_index, cell.text) for cell in table.cells],
            [(1, 1, "Only left")],
        )

    def test_enrich_pdf_table_splits_splits_horizontal_text_bearing_cell(self) -> None:
        cell_bbox = PdfBoundingBox(left_pt=10.0, bottom_pt=10.0, right_pt=90.0, top_pt=70.0)
        top_para = _text_paragraph("p1.tbl1.tr1.tc1.p1", "Top", left=20.0, bottom=50.0, right=50.0, top=60.0)
        bottom_para = _text_paragraph("p1.tbl1.tr1.tc1.p2", "Bottom", left=20.0, bottom=18.0, right=60.0, top=28.0)
        cell = TableCellIR(
            unit_id="p1.tbl1.tr1.tc1",
            row_index=1,
            col_index=1,
            bbox=cell_bbox,
            meta=PdfNodeMeta(page_number=1, bounding_box=cell_bbox),
            paragraphs=[top_para, bottom_para],
        )
        cell.recompute_text()
        table = TableIR(
            unit_id="p1.tbl1",
            row_count=1,
            col_count=1,
            bbox=cell_bbox,
            meta=PdfNodeMeta(page_number=1, bounding_box=cell_bbox),
            cells=[cell],
        )
        doc = DocIR(
            source_doc_type="pdf",
            source_path="/tmp/example.pdf",
            pages=[PageInfo(page_number=1, width_pt=100.0, height_pt=80.0)],
            paragraphs=[ParagraphIR(unit_id="p1", content=[table])],
        )
        primitive = PdfPreviewVisualPrimitive(
            page_number=1,
            draw_order=1,
            object_type="segmented_horizontal_rule",
            bounding_box=PdfBoundingBox(left_pt=11.0, bottom_pt=39.5, right_pt=89.0, top_pt=40.5),
            stroke_color="#0000ffff",
            stroke_width_pt=1.0,
            has_stroke=True,
            candidate_roles=["horizontal_line_segment", "segmented_horizontal_rule"],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%fake")
            doc.source_path = str(pdf_path)

            with patch(
                "document_processor.pdf.enhancement.table_split_inference._extract_rule_primitives_for_pages",
                return_value={1: [primitive]},
            ):
                enrich_pdf_table_splits(doc, pdf_path=pdf_path)

        table = doc.paragraphs[0].tables[0]
        self.assertEqual(table.row_count, 2)
        self.assertEqual(table.col_count, 1)
        self.assertEqual(
            [(cell.row_index, cell.col_index, cell.text) for cell in table.cells],
            [(1, 1, "Top"), (2, 1, "Bottom")],
        )
