"""Structural document IR models."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Callable, Self

from pydantic import BaseModel, Field

from .io_utils import TemporarySourcePath, coerce_source_to_supported_value, get_source_name, infer_doc_type
from .style_types import CellStyleInfo, ParaStyleInfo, RunStyleInfo, TableStyleInfo


class SourceType(str, Enum):
    """Structural paragraph source categories."""

    PARAGRAPH = "paragraph"
    TABLE_BLOCK = "table_block"


class RunIR(BaseModel):
    """Smallest style-preserving text unit."""

    unit_id: str
    text: str = ""
    normalized_text: str = ""
    run_style: RunStyleInfo | None = None


class TableCellParagraphIR(BaseModel):
    """Paragraph inside a table cell."""

    unit_id: str
    text: str = ""
    normalized_text: str = ""
    para_style: ParaStyleInfo | None = None
    runs: list[RunIR] = Field(default_factory=list)


class TableCellIR(BaseModel):
    """Table cell node."""

    unit_id: str
    row_index: int
    col_index: int
    text: str = ""
    normalized_text: str = ""
    cell_style: CellStyleInfo | None = None
    paragraphs: list[TableCellParagraphIR] = Field(default_factory=list)

    def recompute_text(self, *, normalizer: Callable[[str], str] | None = None) -> None:
        normalize = normalizer or (lambda s: s.strip())
        self.text = "\n".join(p.text for p in self.paragraphs)
        self.normalized_text = normalize(self.text)


class TableIR(BaseModel):
    """Nested table node under a paragraph."""

    unit_id: str
    row_count: int = 0
    col_count: int = 0
    table_style: TableStyleInfo | None = None
    cells: list[TableCellIR] = Field(default_factory=list)


class ParagraphIR(BaseModel):
    """Structural paragraph unit."""

    unit_id: str
    text: str = ""
    normalized_text: str = ""
    source_type: SourceType = SourceType.PARAGRAPH
    para_style: ParaStyleInfo | None = None
    runs: list[RunIR] = Field(default_factory=list)
    tables: list[TableIR] = Field(default_factory=list)

    def iter_all_runs(self, *, include_table_runs: bool = True):
        yield from self.runs
        if not include_table_runs:
            return
        for table in self.tables:
            for cell in table.cells:
                for cell_paragraph in cell.paragraphs:
                    yield from cell_paragraph.runs

    def recompute_text(self, *, normalizer: Callable[[str], str] | None = None) -> None:
        normalize = normalizer or (lambda s: s.strip())

        if self.source_type == SourceType.TABLE_BLOCK and self.tables:
            parts: list[str] = []
            if self.runs:
                parts.append("".join(run.text for run in self.runs))
            for table in self.tables:
                cell_texts = [cell.text for cell in table.cells if cell.text]
                if cell_texts:
                    parts.append("\n".join(cell_texts))
            self.text = "\n".join(part for part in parts if part)
        else:
            self.text = "".join(run.text for run in self.runs)

        self.normalized_text = normalize(self.text)


class DocIR(BaseModel):
    """Top-level structural document IR."""

    doc_id: str | None = None
    source_path: str | None = None
    source_doc_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
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
        normalizer: Callable[[str], str] | None = None,
        doc_id: str | None = None,
        **doc_kwargs: Any,
    ) -> Self:
        """Build document IR from a path, bytes, or binary file object."""
        from .builder import build_doc_ir_from_mapping
        from .core.structured_mapping_exporter import export_structured_mapping
        from .core.style_extractor import extract_styles

        resolved_doc_type = infer_doc_type(source, doc_type)  # type: ignore[arg-type]
        if resolved_doc_type == "pdf":
            raise NotImplementedError("PDF parsing is not implemented yet.")

        source_name = get_source_name(source)
        resolved_source_path = source_name

        if resolved_doc_type == "hwp":
            with TemporarySourcePath(source, suffix=".hwp") as source_path:
                mapping = export_structured_mapping(
                    source_path,
                    doc_type="hwp",
                    skip_empty=skip_empty,
                    include_tables=include_tables,
                )
                style_map = extract_styles(
                    source_path,
                    doc_type="hwp",
                    include_tables=include_tables,
                )
        else:
            supported_source = coerce_source_to_supported_value(source, doc_type=resolved_doc_type)
            mapping = export_structured_mapping(
                supported_source,
                doc_type=resolved_doc_type,
                skip_empty=skip_empty,
                include_tables=include_tables,
            )
            style_map = extract_styles(
                supported_source,
                doc_type=resolved_doc_type,
                include_tables=include_tables,
            )

        return build_doc_ir_from_mapping(
            mapping,
            style_map=style_map,
            source_path=resolved_source_path,
            source_doc_type=resolved_doc_type,
            metadata=metadata,
            normalizer=normalizer,
            doc_id=doc_id,
            doc_cls=cls,
            **doc_kwargs,
        )

    @classmethod
    def from_mapping(
        cls,
        mapping: dict[str, str],
        *,
        style_map=None,
        source_path: str | Path | None = None,
        source_doc_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        normalizer: Callable[[str], str] | None = None,
        doc_id: str | None = None,
        **doc_kwargs: Any,
    ) -> Self:
        """Build document IR from a run-level mapping."""
        from .builder import build_doc_ir_from_mapping

        return build_doc_ir_from_mapping(
            mapping,
            style_map=style_map,
            source_path=source_path,
            source_doc_type=source_doc_type,
            metadata=metadata,
            normalizer=normalizer,
            doc_id=doc_id,
            doc_cls=cls,
            **doc_kwargs,
        )


__all__ = [
    "DocIR",
    "ParagraphIR",
    "RunIR",
    "SourceType",
    "TableCellIR",
    "TableCellParagraphIR",
    "TableIR",
]
