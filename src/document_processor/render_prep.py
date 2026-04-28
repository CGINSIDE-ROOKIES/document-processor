"""Document-level render preparation before HTML export."""

from __future__ import annotations

from .models import DocIR


def prepare_doc_ir_for_html(doc_ir: DocIR) -> DocIR:
    # Format-specific enrichment happens before a DocIR reaches the shared
    # renderer. This hook stays format-agnostic so `to_html()` is just rendering.
    return doc_ir


__all__ = ["prepare_doc_ir_for_html"]
