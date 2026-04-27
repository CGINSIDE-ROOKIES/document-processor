"""Structural document IR models."""

from __future__ import annotations

import base64
from collections import OrderedDict
from pathlib import Path
from typing import Any, BinaryIO, Generic, TypeAlias, TypeVar

from pydantic import BaseModel, Field, computed_field

from .io_utils import TemporarySourcePath, coerce_source_to_supported_value, get_source_name, infer_doc_type
from .style_types import CellStyleInfo, ParaStyleInfo, RunStyleInfo, TableStyleInfo

T = TypeVar("T", bound=BaseModel)


class BoundingBox(BaseModel):
    """Generic layout bounding box in page coordinates."""

    left_pt: float
    bottom_pt: float
    right_pt: float
    top_pt: float


class RunIR(BaseModel, Generic[T]):
    """Smallest style-preserving text unit."""

    model_config = {"validate_assignment": True}
    meta: T | None = None

    unit_id: str
    text: str = ""
    bbox: BoundingBox | None = None
    run_style: RunStyleInfo | None = None


class ImageAsset(BaseModel, Generic[T]):
    """Binary image asset stored once per document."""

    model_config = {"validate_assignment": True}
    meta: T | None = None

    mime_type: str
    filename: str | None = None
    data_base64: str
    intrinsic_width_px: int | None = None
    intrinsic_height_px: int | None = None

    @classmethod
    def from_bytes(
        cls,
        *,
        data: bytes,
        mime_type: str,
        filename: str | None = None,
        intrinsic_width_px: int | None = None,
        intrinsic_height_px: int | None = None,
    ) -> "ImageAsset[T]":
        return cls(
            mime_type=mime_type,
            filename=filename,
            data_base64=base64.b64encode(data).decode("ascii"),
            intrinsic_width_px=intrinsic_width_px,
            intrinsic_height_px=intrinsic_height_px,
        )

    def bytes_data(self) -> bytes:
        return base64.b64decode(self.data_base64.encode("ascii"))

    def as_data_url(self) -> str:
        return f"data:{self.mime_type};base64,{self.data_base64}"


class PageInfo(BaseModel):
    """Document page metadata used for approximate paged rendering."""

    page_number: int
    width_pt: float | None = None
    height_pt: float | None = None
    margin_left_pt: float | None = None
    margin_right_pt: float | None = None
    margin_top_pt: float | None = None
    margin_bottom_pt: float | None = None


class ColumnLayoutInfo(BaseModel):
    """Active text-column layout for a paragraph or section."""

    count: int = 1
    column_index: int | None = None
    gap_pt: float | None = None
    widths_pt: list[float] = Field(default_factory=list)
    gaps_pt: list[float] = Field(default_factory=list)
    equal_width: bool | None = None


class ImageIR(BaseModel, Generic[T]):
    """Image placement node inside paragraph-like content."""

    model_config = {"validate_assignment": True}
    meta: T | None = None

    unit_id: str
    image_id: str
    alt_text: str | None = None
    title: str | None = None
    bbox: BoundingBox | None = None
    display_width_pt: float | None = None
    display_height_pt: float | None = None


class ParagraphIR(BaseModel, Generic[T]):
    """Structural paragraph unit used both at the document level and inside table cells."""

    model_config = {"validate_assignment": True}
    meta: T | None = None

    unit_id: str
    text: str = ""
    page_number: int | None = None
    bbox: BoundingBox | None = None
    column_layout: ColumnLayoutInfo | None = None
    para_style: ParaStyleInfo | None = None
    content: list["ParagraphContentNode"] = Field(default_factory=list)

    @property
    def runs(self) -> list[RunIR]:
        return [item for item in self.content if isinstance(item, RunIR)]

    @property
    def images(self) -> list[ImageIR]:
        return [item for item in self.content if isinstance(item, ImageIR)]

    @property
    def tables(self) -> list["TableIR"]:
        return [item for item in self.content if isinstance(item, TableIR)]

    def append_content(self, node: "ParagraphContentNode") -> None:
        self.content.append(node)

    def extend_content(self, nodes: list["ParagraphContentNode"]) -> None:
        self.content.extend(nodes)

    def sort_content(self, *, key) -> None:
        self.content.sort(key=key)

    def iter_all_runs(self, *, include_table_runs: bool = True):
        yield from self.runs
        if not include_table_runs:
            return
        for table in self.tables:
            for cell in table.cells:
                for cell_paragraph in cell.paragraphs:
                    yield from cell_paragraph.iter_all_runs(include_table_runs=True)

    def recompute_text(self) -> None:
        parts: list[str] = []
        if self.runs:
            parts.append("".join(run.text for run in self.runs))
        for table in self.tables:
            cell_texts = [cell.text for cell in table.cells if cell.text]
            if cell_texts:
                parts.append("\n".join(cell_texts))

        self.text = "\n".join(part for part in parts if part) if self.tables else "".join(run.text for run in self.runs)


class TableCellIR(BaseModel, Generic[T]):
    """Table cell node."""

    model_config = {"validate_assignment": True}
    meta: T | None = None

    unit_id: str
    row_index: int
    col_index: int
    text: str = ""
    bbox: BoundingBox | None = None
    cell_style: CellStyleInfo | None = None
    paragraphs: list["ParagraphIR"] = Field(default_factory=list)

    def recompute_text(self) -> None:
        self.text = "\n".join(p.text for p in self.paragraphs)


class TableIR(BaseModel, Generic[T]):
    """Nested table node under a paragraph."""

    model_config = {"validate_assignment": True}
    meta: T | None = None

    unit_id: str
    row_count: int = 0
    col_count: int = 0
    bbox: BoundingBox | None = None
    table_style: TableStyleInfo | None = None
    cells: list[TableCellIR] = Field(default_factory=list)

    @computed_field
    def markdown(self) -> str:
        return _render_table_markdown(self)


class DocIR(BaseModel, Generic[T]):
    """Top-level structural document IR."""

    meta: T | None = None

    doc_id: str | None = None
    source_path: str | None = None
    source_doc_type: str | None = None
    assets: dict[str, ImageAsset[T]] = Field(default_factory=dict)
    pages: list[PageInfo] = Field(default_factory=list)
    paragraphs: list[ParagraphIR] = Field(default_factory=list)

    @computed_field
    @property
    def has_page_metadata(self) -> bool:
        return bool(self.pages)

    def get_image_asset(self, image_or_id: ImageIR | str) -> ImageAsset[T] | None:
        image_id = image_or_id if isinstance(image_or_id, str) else image_or_id.image_id
        return self.assets.get(image_id)

    @classmethod
    def from_file(
        cls,
        source: str | Path | bytes | BinaryIO,
        *,
        doc_type: str = "auto",
        include_tables: bool = True,
        skip_empty: bool = False,
        metadata: dict[str, Any] | None = None,
        doc_id: str | None = None,
        **doc_kwargs: Any,
    ) -> "DocIR":
        """Build document IR from a path, bytes, or binary file object."""
        from .core.document_ir_parser import build_doc_ir_from_file
        from .core.style_extractor import extract_styles

        resolved_doc_type = infer_doc_type(source, doc_type)  # type: ignore[arg-type]
        source_name = get_source_name(source)
        resolved_source_path = source_name

        if resolved_doc_type in {"hwp", "pdf"}:
            suffix = ".hwp" if resolved_doc_type == "hwp" else ".pdf"
            with TemporarySourcePath(source, suffix=suffix) as source_path:
                doc_ir = build_doc_ir_from_file(
                    source_path,
                    doc_type=resolved_doc_type,
                    skip_empty=skip_empty,
                    include_tables=include_tables,
                    source_path=resolved_source_path,
                    metadata=metadata,
                    doc_id=doc_id,
                    doc_cls=cls,
                    **doc_kwargs,
                )
                style_map = extract_styles(
                    source_path,
                    doc_type=resolved_doc_type,
                    include_tables=include_tables,
                )
        else:
            supported_source = coerce_source_to_supported_value(source, doc_type=resolved_doc_type)
            doc_ir = build_doc_ir_from_file(
                supported_source,
                doc_type=resolved_doc_type,
                skip_empty=skip_empty,
                include_tables=include_tables,
                source_path=resolved_source_path,
                metadata=metadata,
                doc_id=doc_id,
                doc_cls=cls,
                **doc_kwargs,
            )
            style_map = extract_styles(
                supported_source,
                doc_type=resolved_doc_type,
                include_tables=include_tables,
            )

        from .builder import apply_style_map_to_doc_ir

        apply_style_map_to_doc_ir(doc_ir, style_map)
        doc_ir.source_doc_type = resolved_doc_type
        if resolved_source_path is not None:
            doc_ir.source_path = resolved_source_path
        return doc_ir

    @classmethod
    def from_mapping(
        cls,
        mapping: dict[str, str],
        *,
        style_map=None,
        source_path: str | Path | None = None,
        source_doc_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        doc_id: str | None = None,
        **doc_kwargs: Any,
    ) -> "DocIR":
        """Build document IR from a run-level mapping."""
        from .builder import build_doc_ir_from_mapping

        return build_doc_ir_from_mapping(
            mapping,
            style_map=style_map,
            source_path=source_path,
            source_doc_type=source_doc_type,
            metadata=metadata,
            doc_id=doc_id,
            doc_cls=cls,
            **doc_kwargs,
        )

    def to_html(self, *, title: str | None = None, debug_layout: bool = False) -> str:
        """Render this document IR as styled HTML."""
        if (self.source_doc_type or "").lower() == "pdf":
            from .pdf.preview.render import render_pdf_preview_html
            return render_pdf_preview_html(self, title=title)

        from .render_prep import prepare_doc_ir_for_html
        from .html_exporter import render_html_document

        prepare_doc_ir_for_html(self)
        return render_html_document(self, title=title, debug_layout=debug_layout)


ParagraphContentNode: TypeAlias = RunIR | ImageIR | TableIR

ParagraphIR.model_rebuild()
TableCellIR.model_rebuild()
TableIR.model_rebuild()


def _cell_rowspan(cell: TableCellIR) -> int:
    if cell.cell_style is None or cell.cell_style.rowspan is None:
        return 1
    return max(cell.cell_style.rowspan, 1)


def _cell_colspan(cell: TableCellIR) -> int:
    if cell.cell_style is None or cell.cell_style.colspan is None:
        return 1
    return max(cell.cell_style.colspan, 1)


def _image_markdown_placeholder(image: ImageIR) -> str:
    label = image.alt_text or image.title or image.image_id
    return f"[image:{label}]"


def _paragraph_markdown_text(
    paragraph: ParagraphIR,
    *,
    nested_tables: "OrderedDict[str, TableIR]",
) -> str:
    parts: list[str] = []
    for node in paragraph.content:
        if isinstance(node, RunIR):
            parts.append(node.text)
        elif isinstance(node, ImageIR):
            parts.append(_image_markdown_placeholder(node))
        elif isinstance(node, TableIR):
            nested_tables.setdefault(node.unit_id, node)
            parts.append(f"[tbl:{node.unit_id}]")
    return "".join(parts).strip()


def _escape_markdown_cell_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _cell_markdown_text(
    cell: TableCellIR,
    *,
    nested_tables: "OrderedDict[str, TableIR]",
) -> str:
    paragraph_texts = [
        text
        for paragraph in cell.paragraphs
        if (text := _paragraph_markdown_text(paragraph, nested_tables=nested_tables))
    ]
    return _escape_markdown_cell_text("<br><br>".join(paragraph_texts))


def _table_grid(table: TableIR) -> tuple[list[list[TableCellIR | None]], int, int]:
    if not table.cells:
        return [], 0, 0

    max_row = max(cell.row_index + _cell_rowspan(cell) - 1 for cell in table.cells)
    max_col = max(cell.col_index + _cell_colspan(cell) - 1 for cell in table.cells)
    grid: list[list[TableCellIR | None]] = [[None for _ in range(max_col)] for _ in range(max_row)]

    for cell in sorted(table.cells, key=lambda c: (c.row_index, c.col_index, c.unit_id)):
        for row in range(cell.row_index - 1, cell.row_index - 1 + _cell_rowspan(cell)):
            for col in range(cell.col_index - 1, cell.col_index - 1 + _cell_colspan(cell)):
                grid[row][col] = cell

    return grid, max_row, max_col


def _render_table_markdown(
    table: TableIR,
    *,
    visited: set[str] | None = None,
) -> str:
    seen = visited if visited is not None else set()
    if table.unit_id in seen:
        return f"[tbl:{table.unit_id}]"
    seen.add(table.unit_id)

    grid, _max_row, max_col = _table_grid(table)
    if max_col == 0:
        return ""

    nested_tables: OrderedDict[str, TableIR] = OrderedDict()
    headers = [f"col{idx}" for idx in range(1, max_col + 1)]
    lines = [
        f"| {' | '.join(headers)} |",
        f"| {' | '.join('---' for _ in headers)} |",
    ]

    for row in grid:
        cells = [
            _cell_markdown_text(cell, nested_tables=nested_tables) if cell is not None else ""
            for cell in row
        ]
        lines.append(f"| {' | '.join(cells)} |")

    sections = ["\n".join(lines)]
    for nested_table in nested_tables.values():
        nested_markdown = _render_table_markdown(nested_table, visited=seen)
        if nested_markdown:
            sections.append(f"[tbl:{nested_table.unit_id}]\n{nested_markdown}")

    return "\n\n".join(section for section in sections if section)


__all__ = [
    "BoundingBox",
    "ColumnLayoutInfo",
    "BoundingBox",
    "ColumnLayoutInfo",
    "DocIR",
    "ImageAsset",
    "ImageIR",
    "PageInfo",
    "ParagraphContentNode",
    "ParagraphIR",
    "RunIR",
    "TableCellIR",
    "TableIR",
]
