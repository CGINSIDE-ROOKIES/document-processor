"""Format-agnostic style models for structural document IR."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RunStyleInfo(BaseModel):
    """Text-level formatting for a single run."""

    font_family: str | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    superscript: bool = False
    subscript: bool = False
    color: str | None = None
    highlight: str | None = None
    size_pt: float | None = None
    hidden: bool = False


class ColumnLayoutInfo(BaseModel):
    """Paragraph column layout metadata."""

    count: int | None = None
    column_index: int | None = None
    gap_pt: float | None = None
    widths_pt: list[float] = Field(default_factory=list)
    gaps_pt: list[float] = Field(default_factory=list)
    equal_width: bool | None = None


class ListItemInfo(BaseModel):
    """Resolved paragraph list marker metadata."""

    list_id: str | None = None
    level: int = 0
    marker: str | None = None
    marker_type: str | None = None
    marker_text: str | None = None


class ParaStyleInfo(BaseModel):
    """Paragraph-level formatting."""

    align: str | None = None
    left_indent_pt: float | None = None
    right_indent_pt: float | None = None
    first_line_indent_pt: float | None = None
    hanging_indent_pt: float | None = None
    render_tag: str | None = None
    column_layout: ColumnLayoutInfo | None = None
    list_info: ListItemInfo | None = None


class CellStyleInfo(BaseModel):
    """Table cell formatting."""

    background: str | None = None
    vertical_align: str | None = None
    horizontal_align: str | None = None
    width_pt: float | None = None
    height_pt: float | None = None
    padding_top_pt: float | None = None
    padding_right_pt: float | None = None
    padding_bottom_pt: float | None = None
    padding_left_pt: float | None = None
    border_top: str | None = None
    border_bottom: str | None = None
    border_left: str | None = None
    border_right: str | None = None
    diagonal_tl_br: str | None = None
    diagonal_tr_bl: str | None = None
    rowspan: int = 1
    colspan: int = 1


class TableStyleInfo(BaseModel):
    """Table-level metadata."""

    row_count: int = 0
    col_count: int = 0
    width_pt: float | None = None
    height_pt: float | None = None
    render_grid: bool = False


class StyleMap(BaseModel):
    """Style lookup map keyed by native structural paths."""

    runs: dict[str, RunStyleInfo] = Field(default_factory=dict)
    paragraphs: dict[str, ParaStyleInfo] = Field(default_factory=dict)
    cells: dict[str, CellStyleInfo] = Field(default_factory=dict)
    tables: dict[str, TableStyleInfo] = Field(default_factory=dict)


__all__ = [
    "ColumnLayoutInfo",
    "ListItemInfo",
    "RunStyleInfo",
    "ParaStyleInfo",
    "CellStyleInfo",
    "TableStyleInfo",
    "StyleMap",
]
