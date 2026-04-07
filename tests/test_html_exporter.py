from __future__ import annotations

from pathlib import Path
import sys
import unittest

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor import DocIR
from document_processor.models import ImageAsset, ImageIR, ParagraphIR, RunIR, TableCellIR, TableIR
from document_processor.style_types import CellStyleInfo, ParaStyleInfo, RunStyleInfo, TableStyleInfo


class HtmlExporterTests(unittest.TestCase):
    def test_export_html_renders_run_and_paragraph_styles(self) -> None:
        doc = DocIR(
            doc_id="sample",
            paragraphs=[
                ParagraphIR(
                    unit_id="s1.p1",
                    para_style=ParaStyleInfo(align="center", first_line_indent_pt=12.0),
                    content=[
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
                    content=[
                        TableIR(
                            unit_id="s1.p1.r1.tbl1",
                            table_style=TableStyleInfo(width_pt=240.0),
                            cells=[
                                TableCellIR(
                                    unit_id="s1.p1.r1.tbl1.tr1.tc1",
                                    row_index=1,
                                    col_index=1,
                                    cell_style=CellStyleInfo(
                                        background="#ffeeaa",
                                        horizontal_align="center",
                                        width_pt=120.0,
                                        height_pt=36.0,
                                        border_top="1px solid #000",
                                        border_bottom="1px solid #000",
                                        border_left="1px solid #000",
                                        border_right="1px solid #000",
                                        colspan=2,
                                    ),
                                    paragraphs=[
                                        ParagraphIR(
                                            unit_id="s1.p1.r1.tbl1.tr1.tc1.p1",
                                            content=[RunIR(unit_id="x", text="A1")],
                                        )
                                    ],
                                ),
                                TableCellIR(
                                    unit_id="s1.p1.r1.tbl1.tr2.tc1",
                                    row_index=2,
                                    col_index=1,
                                    paragraphs=[
                                        ParagraphIR(
                                            unit_id="s1.p1.r1.tbl1.tr2.tc1.p1",
                                            content=[RunIR(unit_id="y", text="B1")],
                                        )
                                    ],
                                ),
                            ],
                        )
                    ],
                )
            ]
        )

        html = doc.to_html(title="Table Preview")

        self.assertIn("<table", html)
        self.assertIn('colspan="2"', html)
        self.assertIn("background-color:#ffeeaa", html)
        self.assertIn("text-align:center", html)
        self.assertIn("A1", html)
        self.assertIn("B1", html)
        self.assertIn("width:240.0pt", html)
        self.assertIn("width:120.0pt", html)
        self.assertIn("height:36.0pt", html)
        self.assertIn("margin-left:0", html)
        self.assertIn("margin-right:auto", html)

    def test_export_html_renders_cell_diagonal_borders(self) -> None:
        doc = DocIR(
            paragraphs=[
                ParagraphIR(
                    unit_id="s1.p1",
                    content=[
                        TableIR(
                            unit_id="s1.p1.r1.tbl1",
                            cells=[
                                TableCellIR(
                                    unit_id="s1.p1.r1.tbl1.tr1.tc1",
                                    row_index=1,
                                    col_index=1,
                                    cell_style=CellStyleInfo(
                                        diagonal_tl_br="1px solid #000000",
                                        diagonal_tr_bl="1px dashed #ff0000",
                                        border_top="1px solid #000000",
                                        border_bottom="1px solid #000000",
                                        border_left="1px solid #000000",
                                        border_right="1px solid #000000",
                                    ),
                                    paragraphs=[
                                        ParagraphIR(
                                            unit_id="s1.p1.r1.tbl1.tr1.tc1.p1",
                                            content=[RunIR(unit_id="x", text="Diag")],
                                        )
                                    ],
                                )
                            ],
                        )
                    ],
                )
            ]
        )

        html = doc.to_html()

        self.assertIn("background-image:url(data:image/svg+xml;base64,", html)
        self.assertNotIn('background-image:url("data:image/svg+xml,', html)
        self.assertIn("Diag", html)

    def test_export_html_leaves_justify_table_left_aligned_by_default(self) -> None:
        doc = DocIR(
            paragraphs=[
                ParagraphIR(
                    unit_id="s1.p1",
                    para_style=ParaStyleInfo(align="justify"),
                    content=[
                        TableIR(
                            unit_id="s1.p1.r1.tbl1",
                            cells=[
                                TableCellIR(
                                    unit_id="s1.p1.r1.tbl1.tr1.tc1",
                                    row_index=1,
                                    col_index=1,
                                    paragraphs=[
                                        ParagraphIR(
                                            unit_id="s1.p1.r1.tbl1.tr1.tc1.p1",
                                            content=[RunIR(unit_id="x", text="Cell")],
                                        )
                                    ],
                                )
                            ],
                        )
                    ],
                )
            ]
        )

        html = doc.to_html()

        self.assertIn("<table", html)
        self.assertIn("margin-left:0", html)
        self.assertIn("margin-right:auto", html)

    def test_export_html_uses_image_display_size(self) -> None:
        doc = DocIR(
            assets={
                "img1": ImageAsset(
                    image_id="img1",
                    mime_type="image/png",
                    filename="x.png",
                    data_base64="AAAA",
                    intrinsic_width_px=1,
                    intrinsic_height_px=1,
                )
            },
            paragraphs=[
                ParagraphIR(
                    unit_id="s1.p1",
                    content=[
                        ImageIR(
                            unit_id="s1.p1.img1",
                            image_id="img1",
                            display_width_pt=72.0,
                            display_height_pt=36.0,
                        )
                    ],
                )
            ],
        )

        html = doc.to_html()

        self.assertIn("<img ", html)
        self.assertIn("width:72.0pt", html)
        self.assertIn("height:36.0pt", html)

    def test_export_html_clamps_negative_first_line_indent_inside_table_cells(self) -> None:
        doc = DocIR(
            paragraphs=[
                ParagraphIR(
                    unit_id="s1.p1",
                    content=[
                        TableIR(
                            unit_id="s1.p1.r1.tbl1",
                            cells=[
                                TableCellIR(
                                    unit_id="s1.p1.r1.tbl1.tr1.tc1",
                                    row_index=1,
                                    col_index=1,
                                    paragraphs=[
                                        ParagraphIR(
                                            unit_id="s1.p1.r1.tbl1.tr1.tc1.p1",
                                            para_style=ParaStyleInfo(
                                                align="center",
                                                first_line_indent_pt=-159.3,
                                            ),
                                            content=[RunIR(unit_id="x", text="스토리")],
                                        )
                                    ],
                                )
                            ],
                        )
                    ],
                )
            ]
        )

        html = doc.to_html()

        self.assertIn("text-indent:0.0pt", html)
        self.assertNotIn("text-indent:-159.3pt", html)

    def test_export_html_preserves_negative_first_line_indent_for_top_level_paragraphs(self) -> None:
        doc = DocIR(
            paragraphs=[
                ParagraphIR(
                    unit_id="s1.p1",
                    para_style=ParaStyleInfo(first_line_indent_pt=-27.6),
                    content=[RunIR(unit_id="s1.p1.r1", text="Bullet-like text")],
                )
            ]
        )

        html = doc.to_html()

        self.assertIn("text-indent:-27.6pt", html)

    def test_export_html_renders_nested_tables(self) -> None:
        doc = DocIR(
            paragraphs=[
                ParagraphIR(
                    unit_id="s1.p1",
                    content=[
                        TableIR(
                            unit_id="s1.p1.r1.tbl1",
                            cells=[
                                TableCellIR(
                                    unit_id="s1.p1.r1.tbl1.tr1.tc1",
                                    row_index=1,
                                    col_index=1,
                                    paragraphs=[
                                        ParagraphIR(
                                            unit_id="s1.p1.r1.tbl1.tr1.tc1.p1",
                                            content=[
                                                RunIR(unit_id="outer", text="Outer"),
                                                TableIR(
                                                    unit_id="s1.p1.r1.tbl1.tr1.tc1.p1.tbl1",
                                                    cells=[
                                                        TableCellIR(
                                                            unit_id="s1.p1.r1.tbl1.tr1.tc1.p1.tbl1.tr1.tc1",
                                                            row_index=1,
                                                            col_index=1,
                                                            paragraphs=[
                                                                ParagraphIR(
                                                                    unit_id="s1.p1.r1.tbl1.tr1.tc1.p1.tbl1.tr1.tc1.p1",
                                                                    content=[RunIR(unit_id="inner", text="Inner")],
                                                                )
                                                            ],
                                                        )
                                                    ],
                                                )
                                            ],
                                        )
                                    ],
                                )
                            ],
                        )
                    ],
                )
            ]
        )

        html = doc.to_html()

        self.assertGreaterEqual(html.count("<table"), 2)
        self.assertIn("Outer", html)
        self.assertIn("Inner", html)


if __name__ == "__main__":
    unittest.main()
