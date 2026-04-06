from __future__ import annotations

from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor import DocIR
from document_processor.diagram import create_model_diagram, draw_model_diagram, main, resolve_model


class DiagramTests(unittest.TestCase):
    def test_resolve_model_defaults_to_docir(self) -> None:
        self.assertIs(resolve_model(), DocIR)

    def test_resolve_model_by_dotted_path(self) -> None:
        self.assertIs(resolve_model("document_processor.DocIR"), DocIR)

    def test_create_model_diagram_uses_erdantic_create(self) -> None:
        fake_module = types.ModuleType("erdantic")
        calls: list[type] = []

        def fake_create(model):
            calls.append(model)
            return {"model": model}

        fake_module.create = fake_create

        with patch.dict(sys.modules, {"erdantic": fake_module}):
            diagram = create_model_diagram()

        self.assertEqual(diagram["model"], DocIR)
        self.assertEqual(calls, [DocIR])

    def test_draw_model_diagram_and_cli_use_erdantic_draw(self) -> None:
        fake_module = types.ModuleType("erdantic")
        draw_calls: list[tuple[type, str]] = []

        def fake_draw(model, *, out, graph_attr=None, node_attr=None, edge_attr=None):
            draw_calls.append((model, out))

        fake_module.draw = fake_draw
        fake_module.create = lambda model: {"model": model}

        with patch.dict(sys.modules, {"erdantic": fake_module}):
            out_path = draw_model_diagram(out="docir.svg")
            exit_code = main(["--out", "cli-docir.svg"])

        self.assertEqual(out_path, Path("docir.svg"))
        self.assertEqual(exit_code, 0)
        self.assertEqual(draw_calls[0], (DocIR, "docir.svg"))
        self.assertEqual(draw_calls[1], (DocIR, "cli-docir.svg"))


if __name__ == "__main__":
    unittest.main()
