from __future__ import annotations

from pathlib import Path
import sys
import unittest

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor import DocIR, export_html
from document_processor.models import ParagraphIR, RunIR, TableCellIR, TableCellParagraphIR, TableIR
from document_processor.style_types import CellStyleInfo, ParaStyleInfo, RunStyleInfo


class HtmlExporterTests(unittest.TestCase):
    def test_export_html_renders_run_and_paragraph_styles(self) -> None:
        doc = DocIR(
            doc_id="sample",
            paragraphs=[
                ParagraphIR(
                    unit_id="s1.p1",
                    para_style=ParaStyleInfo(align="center", first_line_indent_pt=12.0),
                    runs=[
                        RunIR(
                            unit_id="s1.p1.r1",
                            text="Hello  world",
                            run_style=RunStyleInfo(
                                bold=True,
                                italic=True,
                                underline=True,
                                color="#112233",
                                size_pt=11.0,
                            ),
                        )
                    ],
                )
            ],
        )

        html = doc.to_html(title="Preview")

        self.assertIn("<title>Preview</title>", html)
        self.assertIn("text-align:center", html)
        self.assertIn("text-indent:12.0pt", html)
        self.assertIn("font-size:11.0pt", html)
        self.assertIn("color:#112233", html)
        self.assertIn("<b>Hello", html)
        self.assertIn("<i><b>", html)
        self.assertIn("&nbsp;&nbsp;", html)

    def test_export_html_renders_tables_and_cell_styles(self) -> None:
        doc = DocIR(
            paragraphs=[
                ParagraphIR(
                    unit_id="s1.p1",
                    tables=[
                        TableIR(
                            unit_id="s1.p1.r1.tbl1",
                            cells=[
                                TableCellIR(
                                    unit_id="s1.p1.r1.tbl1.tr1.tc1",
                                    row_index=1,
                                    col_index=1,
                                    cell_style=CellStyleInfo(
                                        background="#ffeeaa",
                                        horizontal_align="center",
                                        border_top="1px solid #000",
                                        border_bottom="1px solid #000",
                                        border_left="1px solid #000",
                                        border_right="1px solid #000",
                                        colspan=2,
                                    ),
                                    paragraphs=[
                                        TableCellParagraphIR(
                                            unit_id="s1.p1.r1.tbl1.tr1.tc1.p1",
                                            runs=[RunIR(unit_id="x", text="A1")],
                                        )
                                    ],
                                ),
                                TableCellIR(
                                    unit_id="s1.p1.r1.tbl1.tr2.tc1",
                                    row_index=2,
                                    col_index=1,
                                    paragraphs=[
                                        TableCellParagraphIR(
                                            unit_id="s1.p1.r1.tbl1.tr2.tc1.p1",
                                            runs=[RunIR(unit_id="y", text="B1")],
                                        )
                                    ],
                                ),
                            ],
                        )
                    ],
                )
            ]
        )

        html = export_html(doc, title="Table Preview")

        self.assertIn("<table", html)
        self.assertIn('colspan="2"', html)
        self.assertIn("background-color:#ffeeaa", html)
        self.assertIn("text-align:center", html)
        self.assertIn("A1", html)
        self.assertIn("B1", html)
        self.assertIn("margin-left:auto", html)
        self.assertIn("margin-right:auto", html)

    def test_export_html_centers_table_when_paragraph_align_is_justify(self) -> None:
        doc = DocIR(
            paragraphs=[
                ParagraphIR(
                    unit_id="s1.p1",
                    para_style=ParaStyleInfo(align="justify"),
                    tables=[
                        TableIR(
                            unit_id="s1.p1.r1.tbl1",
                            cells=[
                                TableCellIR(
                                    unit_id="s1.p1.r1.tbl1.tr1.tc1",
                                    row_index=1,
                                    col_index=1,
                                    paragraphs=[
                                        TableCellParagraphIR(
                                            unit_id="s1.p1.r1.tbl1.tr1.tc1.p1",
                                            runs=[RunIR(unit_id="x", text="Cell")],
                                        )
                                    ],
                                )
                            ],
                        )
                    ],
                )
            ]
        )

        html = export_html(doc)

        self.assertIn("<table", html)
        self.assertIn("margin-left:auto", html)
        self.assertIn("margin-right:auto", html)


if __name__ == "__main__":
    unittest.main()
