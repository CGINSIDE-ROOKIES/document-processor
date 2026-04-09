"""PDF-specific render preparation.

This layer keeps PDF-only grouping and fidelity work out of the shared HTML
renderer. The shared renderer should see only normalized `DocIR` structures and
style fields that are ready to render.
"""

from __future__ import annotations

from ..models import DocIR
from .enhancement import enrich_pdf_table_borders


def prepare_pdf_for_html(doc_ir: DocIR) -> DocIR:
    if (doc_ir.source_doc_type or "").lower() != "pdf":
        return doc_ir

    # Border enrichment is still the only active PDF render-prep step. Keep it
    # here so shared HTML rendering stays format-agnostic.
    enrich_pdf_table_borders(doc_ir)
    _prepare_pdf_caption_groups(doc_ir)
    _prepare_pdf_list_groups(doc_ir)
    return doc_ir


def _prepare_pdf_caption_groups(doc_ir: DocIR) -> DocIR:
    """Placeholder for future caption-to-table/image grouping."""
    return doc_ir


def _prepare_pdf_list_groups(doc_ir: DocIR) -> DocIR:
    """Placeholder for future list reconstruction from ODL list metadata."""
    return doc_ir


__all__ = ["prepare_pdf_for_html"]
