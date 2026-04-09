from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

THIS_DIR = Path(__file__).resolve().parent
SRC_ROOT = THIS_DIR.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor import DocIR
from document_processor.pdf import export_pdf_local_outputs
from document_processor.pdf.odl import build_doc_ir_from_odl_result, convert_pdf_local, resolve_odl_jar_path
from document_processor.pdf.pipeline import parse_pdf_to_doc_ir
from document_processor.pdf.parsing import PageProfile, PdfProfile


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

    def test_build_doc_ir_from_odl_result_builds_paragraph_table_and_asset(self) -> None:
        raw_document = {
            "file name": "sample.pdf",
            "number of pages": 2,
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
                    "heading level": 2,
                    "text color": "#112233",
                    "font size": 11,
                },
                {
                    "type": "table",
                    "page number": 2,
                    "id": 202,
                    "previous table id": 201,
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
                    "data": "data:image/png;base64,QUJD",
                    "width px": 120,
                    "height px": 40,
                },
            ],
        }

        doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")

        self.assertEqual(doc.source_doc_type, "pdf")
        self.assertEqual([page.page_number for page in doc.pages], [1, 2])
        self.assertEqual(doc.paragraphs[0].text, "Hello PDF")
        self.assertEqual(doc.paragraphs[0].meta.source_id, 101)
        self.assertEqual(doc.paragraphs[0].meta.heading_level, 2)
        self.assertEqual(doc.paragraphs[0].meta.bounding_box.left_pt, 10.0)
        self.assertEqual(doc.paragraphs[0].runs[0].meta.source_id, 101)
        self.assertEqual(doc.paragraphs[1].tables[0].cells[0].text, "A1")
        self.assertEqual(doc.paragraphs[1].tables[0].meta.previous_table_id, 201)
        self.assertTrue(doc.paragraphs[1].tables[0].meta.render_table_grid)
        self.assertEqual(doc.paragraphs[1].tables[0].cells[0].meta.source_id, 303)
        self.assertIn("odl-img-p3", doc.assets)
        self.assertEqual(doc.paragraphs[2].images[0].image_id, "odl-img-p3")
        self.assertEqual(doc.paragraphs[2].images[0].meta.source_type, "image")
        self.assertEqual(doc.assets["odl-img-p3"].meta.source_type, "image")

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
                with patch("document_processor.pdf.pipeline.run_odl_json", return_value=raw_document) as run_odl:
                    doc = parse_pdf_to_doc_ir(pdf_path)

        self.assertEqual(run_odl.call_args.kwargs, {})
        self.assertEqual(run_odl.call_args.args[1]["pages"], [2, 3])
        self.assertEqual(run_odl.call_args.args[1]["image_output"], "embedded")
        self.assertEqual([page.page_number for page in doc.pages], [1, 2, 3])
        self.assertEqual([paragraph.page_number for paragraph in doc.paragraphs], [2, 3])
        self.assertEqual(doc.meta.parser, "odl-local")
        self.assertEqual(doc.meta.structured_pages, [2, 3])
        self.assertEqual(doc.meta.scan_like_pages, [1])

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
