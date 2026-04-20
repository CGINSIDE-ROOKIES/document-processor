"""PDF parsing configuration."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class PdfTriageConfig(BaseModel):
    """Cheap probe-based PDF triage configuration."""

    force_complex_layout: bool = False
    force_complex_table: bool = False


class OdlPdfConfig(BaseModel):
    """ODL-backed PDF parsing configuration."""

    reading_order: str = "xycut"
    use_struct_tree: bool = False
    table_method: str = "cluster"
    include_header_footer: bool = False
    keep_line_breaks: bool = False
    preserve_whitespace: bool = False
    sanitize: bool = False
    detect_strikethrough: bool = False
    markdown_page_separator: str | None = None
    text_page_separator: str | None = None
    html_page_separator: str | None = None
    image_output: str | None = None
    image_format: str | None = None
    image_dir: str | None = None


class PdfParseConfig(BaseModel):
    """Top-level PDF parsing configuration."""

    triage: PdfTriageConfig = Field(default_factory=PdfTriageConfig)
    odl: OdlPdfConfig = Field(default_factory=OdlPdfConfig)
    infer_table_borders: bool = False
    table_border_dpi: int = 144

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_infer_table_splits(cls, value: Any) -> Any:
        if isinstance(value, dict) and "infer_table_splits" in value:
            raise ValueError(
                "`infer_table_splits` has been removed from PdfParseConfig; "
                "table split plans now run automatically during ODL parsing."
            )
        return value


__all__ = [
    "OdlPdfConfig",
    "PdfParseConfig",
    "PdfTriageConfig",
]
