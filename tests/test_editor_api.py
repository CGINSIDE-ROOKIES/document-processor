from __future__ import annotations

from io import BytesIO
import unittest

from document_processor import (
    ApplyTextEditsRequest,
    DocumentInput,
    DocIR,
    GetDocumentContextRequest,
    RenderReviewHtmlRequest,
    TextAnnotation,
    TextEdit,
    apply_text_edits,
    get_document_context,
    render_review_html,
)


class EditorApiTests(unittest.TestCase):
    @staticmethod
    def _build_sample_docx_bytes() -> bytes:
        from docx import Document

        docx = Document()
        paragraph = docx.add_paragraph()
        paragraph.add_run("Hello ")
        paragraph.add_run("World")
        docx.add_paragraph("Second paragraph")

        buffer = BytesIO()
        docx.save(buffer)
        return buffer.getvalue()

    def test_get_document_context_accepts_bytes_backed_input(self) -> None:
        result = get_document_context(
            GetDocumentContextRequest(
                document=DocumentInput(
                    source_bytes=self._build_sample_docx_bytes(),
                    source_name="sample.docx",
                ),
                unit_ids=["s1.p1.r2"],
                before=0,
                after=1,
            )
        )

        self.assertEqual(result.source_name, "sample.docx")
        self.assertEqual([paragraph.unit_id for paragraph in result.paragraphs], ["s1.p1", "s1.p2"])
        self.assertEqual(result.paragraphs[0].runs[1].text, "World")

    def test_apply_text_edits_returns_output_bytes_for_bytes_backed_source(self) -> None:
        result = apply_text_edits(
            ApplyTextEditsRequest(
                document=DocumentInput(
                    source_bytes=self._build_sample_docx_bytes(),
                    source_name="sample.docx",
                ),
                edits=[
                    TextEdit(
                        target_kind="paragraph",
                        target_unit_id="s1.p1",
                        expected_text="Hello World",
                        new_text="Hello Legal World",
                        reason="Expand wording",
                    )
                ],
                return_doc_ir=True,
            )
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.output_filename, "sample_edited.docx")
        self.assertIsNone(result.output_path)
        self.assertIsNotNone(result.output_bytes)
        self.assertIsNotNone(result.updated_doc_ir)
        self.assertEqual(DocIR.from_file(result.output_bytes).paragraphs[0].text, "Hello Legal World")
        self.assertEqual(result.updated_doc_ir.paragraphs[0].text, "Hello Legal World")

    def test_apply_text_edits_with_doc_ir_only_returns_updated_doc_ir(self) -> None:
        doc = DocIR.from_mapping(
            {
                "s1.p1.r1": "Hello ",
                "s1.p1.r2": "World",
            },
            source_doc_type="docx",
        )

        result = apply_text_edits(
            ApplyTextEditsRequest(
                document=DocumentInput(doc_ir=doc),
                edits=[
                    TextEdit(
                        target_kind="paragraph",
                        target_unit_id="s1.p1",
                        expected_text="Hello World",
                        new_text="Hello Contract World",
                        reason="Expand wording",
                    )
                ],
            )
        )

        self.assertTrue(result.ok)
        self.assertIsNone(result.output_path)
        self.assertIsNone(result.output_bytes)
        self.assertIsNotNone(result.updated_doc_ir)
        self.assertEqual(result.updated_doc_ir.paragraphs[0].text, "Hello Contract World")

    def test_render_review_html_accepts_doc_ir_input(self) -> None:
        doc = DocIR.from_mapping({"s1.p1.r1": "Hello"})

        result = render_review_html(
            RenderReviewHtmlRequest(
                document=DocumentInput(doc_ir=doc),
                annotations=[
                    TextAnnotation(
                        target_kind="run",
                        target_unit_id="s1.p1.r1",
                        selected_text="Hello",
                        label="Greeting",
                    )
                ],
            )
        )

        self.assertTrue(result.ok)
        self.assertIn("<mark", result.html or "")
        self.assertEqual(result.resolved_annotations[0].selected_text, "Hello")

    def test_render_review_html_rejects_ambiguous_selected_text(self) -> None:
        doc = DocIR.from_mapping({"s1.p1.r1": "Hello Hello"})

        result = render_review_html(
            RenderReviewHtmlRequest(
                document=DocumentInput(doc_ir=doc),
                annotations=[
                    TextAnnotation(
                        target_kind="run",
                        target_unit_id="s1.p1.r1",
                        selected_text="Hello",
                        label="Ambiguous",
                    )
                ],
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.validation.issues[0].code, "selected_text_ambiguous")


if __name__ == "__main__":
    unittest.main()
