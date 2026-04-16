"""PDF HTML preview implementation."""

from __future__ import annotations

from .context import build_pdf_preview_context
from .models import (
    PdfLayoutRegion,
    PdfPreviewContext,
    PdfPreviewTableContext,
    PdfPreviewVisualBlockCandidate,
    PdfPreviewVisualPrimitive,
)
from .prepare import prepare_pdf_for_html
from .render import render_pdf_html, render_pdf_preview_html, render_pdf_preview_html_from_file

__all__ = [
    "PdfLayoutRegion",
    "PdfPreviewVisualBlockCandidate",
    "PdfPreviewContext",
    "PdfPreviewTableContext",
    "PdfPreviewVisualPrimitive",
    "build_pdf_preview_context",
    "prepare_pdf_for_html",
    "render_pdf_html",
    "render_pdf_preview_html",
    "render_pdf_preview_html_from_file",
]
