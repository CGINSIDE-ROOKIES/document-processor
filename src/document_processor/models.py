"""Structural document IR models."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, BinaryIO, Generic, TypeAlias, TypeVar

from pydantic import BaseModel, Field

from .io_utils import TemporarySourcePath, coerce_source_to_supported_value, get_source_name, infer_doc_type
from .style_types import CellStyleInfo, ParaStyleInfo, RunStyleInfo, TableStyleInfo

T = TypeVar("T", bound=BaseModel)


class RunIR(BaseModel, Generic[T]):
    """Smallest style-preserving text unit."""

    model_config = {"validate_assignment": True}
    meta: T | None = None

    unit_id: str
    text: str = ""
    run_style: RunStyleInfo | None = None


class ImageAsset(BaseModel):
    """Binary image asset stored once per document."""

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
    ) -> "ImageAsset":
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


class ImageIR(BaseModel, Generic[T]):
    """Image placement node inside paragraph-like content."""

    model_config = {"validate_assignment": True}
    meta: T | None = None

    unit_id: str
    image_id: str
    alt_text: str | None = None
    title: str | None = None
    display_width_pt: float | None = None
    display_height_pt: float | None = None
class ParagraphIR(BaseModel, Generic[T]):
    """Structural paragraph unit used both at the document level and inside table cells."""

    model_config = {"validate_assignment": True}
    meta: T | None = None

    unit_id: str
    text: str = ""
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
    table_style: TableStyleInfo | None = None
    cells: list[TableCellIR] = Field(default_factory=list)


class DocIR(BaseModel, Generic[T]):
    """Top-level structural document IR."""

    meta: T | None = None

    doc_id: str | None = None
    source_path: str | None = None
    source_doc_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    assets: dict[str, ImageAsset] = Field(default_factory=dict)
    paragraphs: list[ParagraphIR] = Field(default_factory=list)

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
        if resolved_doc_type == "pdf":
            raise NotImplementedError("PDF parsing is not implemented yet.")

        source_name = get_source_name(source)
        resolved_source_path = source_name

        if resolved_doc_type == "hwp":
            with TemporarySourcePath(source, suffix=".hwp") as source_path:
                doc_ir = build_doc_ir_from_file(
                    source_path,
                    doc_type="hwp",
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
                    doc_type="hwp",
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

    def to_html(self, *, title: str | None = None) -> str:
        """Render this document IR as styled HTML."""
        from .html_exporter import render_html_document

        return render_html_document(self, title=title)


ParagraphContentNode: TypeAlias = RunIR | ImageIR | TableIR

ParagraphIR.model_rebuild()
TableCellIR.model_rebuild()
TableIR.model_rebuild()


__all__ = [
    "DocIR",
    "ImageAsset",
    "ImageIR",
    "ParagraphContentNode",
    "ParagraphIR",
    "RunIR",
    "TableCellIR",
    "TableIR",
]
