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
    def test_preview_submodules_are_importable(self) -> None:
        for module_name in (
            "document_processor.pdf.preview.models",
            "document_processor.pdf.preview.context",
            "document_processor.pdf.preview.primitives",
            "document_processor.pdf.preview.candidates",
            "document_processor.pdf.preview.layout",
            "document_processor.pdf.preview.compose",
            "document_processor.pdf.preview.render",
            "document_processor.pdf.preview.prepare",
        ):
            module = importlib.import_module(module_name)
            self.assertIsNotNone(module)
