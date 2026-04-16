"""Compatibility shim for the staged preview package refactor."""

from . import (
    PdfLayoutRegion,
    PdfPreviewContext,
    PdfPreviewTableContext,
    PdfPreviewVisualBlockCandidate,
    PdfPreviewVisualPrimitive,
)

__all__ = [
    "PdfLayoutRegion",
    "PdfPreviewContext",
    "PdfPreviewTableContext",
    "PdfPreviewVisualBlockCandidate",
    "PdfPreviewVisualPrimitive",
]
