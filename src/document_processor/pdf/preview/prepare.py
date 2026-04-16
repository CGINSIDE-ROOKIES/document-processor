"""Compatibility shim for the staged preview package refactor."""

from ..enhancement import enrich_pdf_table_backgrounds, enrich_pdf_table_borders
from . import prepare_pdf_for_html

__all__ = ["enrich_pdf_table_backgrounds", "enrich_pdf_table_borders", "prepare_pdf_for_html"]
