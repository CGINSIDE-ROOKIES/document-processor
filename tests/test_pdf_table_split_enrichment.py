from __future__ import annotations

from pathlib import Path
import sys
import unittest

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor.pdf.preview.analyze import extract_pdfium_table_rule_primitives


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
