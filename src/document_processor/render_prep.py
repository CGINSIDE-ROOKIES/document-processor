"""Document-level render preparation before HTML export."""

from __future__ import annotations

from .models import DocIR


def prepare_doc_ir_for_html(doc_ir: DocIR) -> DocIR:
    # Canonical formats go straight to the shared renderer.
    # PDF alone gets an extra preview-prep stage because table borders,
    # backgrounds, and layout hints often need format-specific refinement.
    if (doc_ir.source_doc_type or "").lower() == "pdf":
        from .pdf.preview.normalize import prepare_pdf_for_html

        return prepare_pdf_for_html(doc_ir)
    return doc_ir


__all__ = ["prepare_doc_ir_for_html"]
