"""Compatibility shim for the staged preview package refactor."""

from . import render_pdf_html, render_pdf_preview_html, render_pdf_preview_html_from_file

__all__ = ["render_pdf_html", "render_pdf_preview_html", "render_pdf_preview_html_from_file"]
