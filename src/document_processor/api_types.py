from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, Field, model_validator

from .io_utils import SourceDocType
from .models import DocIR, NativeAnchor

TargetKind = Literal["paragraph", "run", "cell", "table"]
TextTargetKind = Literal["paragraph", "run", "cell"]
AnnotationTargetKind = Literal["paragraph", "run"]
StructuralOperationKind = Literal[
    "insert_paragraph",
    "remove_paragraph",
    "insert_run",
    "remove_run",
    "insert_table",
    "remove_table",
    "set_cell_text",
    "insert_table_row",
    "remove_table_row",
    "insert_table_column",
    "remove_table_column",
]
InsertPosition = Literal["before", "after", "start", "end"]
EditValidationCode = Literal[
    "target_not_found",
    "target_kind_mismatch",
    "text_mismatch",
    "mixed_content_not_supported",
    "paragraph_count_mismatch",
    "invalid_operation",
    "invalid_position",
    "invalid_table_shape",
    "index_out_of_bounds",
    "unsupported_source_doc_type",
    "output_path_conflicts_with_source",
    "native_source_required",
]
AnnotationValidationCode = Literal[
    "target_not_found",
    "target_kind_mismatch",
    "mixed_content_not_supported",
    "selected_text_not_found",
    "selected_text_ambiguous",
    "occurrence_index_out_of_bounds",
]


class DocumentInput(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    source_path: str | None = Field(default=None, description="Filesystem path to the source document.")
    source_bytes: bytes | None = Field(default=None, description="Raw document bytes for stateless upload-style calls.")
    doc_ir: DocIR | None = Field(default=None, description="Pre-parsed DocIR for read/in-memory edit flows.")
    source_doc_type: SourceDocType = Field(
        default="auto",
        description="Explicit source document type when it cannot be inferred.",
    )
    source_name: str | None = Field(
        default=None,
        description="Optional filename for bytes-backed documents.",
    )

    @model_validator(mode="after")
    def _validate_sources(self) -> "DocumentInput":
        source_count = sum(
            1
            for value in (self.source_path, self.source_bytes, self.doc_ir)
            if value is not None
        )
        if source_count == 0:
            raise ValueError("Provide at least one of source_path, source_bytes, or doc_ir.")
        if self.source_path is not None and self.source_bytes is not None:
            raise ValueError("Specify either source_path or source_bytes, not both.")
        return self


class TextEdit(BaseModel):
    edit_type: Literal["text"] = Field(default="text", description="Discriminator for mixed edit batches.")
    target_kind: TextTargetKind = Field(description="Whether this edit targets a paragraph, run, or table cell.")
    target_id: str = Field(description="Stable opaque node id from the parsed document.")
    expected_text: str = Field(description="Exact current text that must match before the edit is applied.")
    new_text: str = Field(description="Replacement text for the target.")
    reason: str = Field(default="", description="Short rationale for the change.")


class StructuralEdit(BaseModel):
    edit_type: Literal["structural"] = Field(default="structural", description="Discriminator for mixed edit batches.")
    operation: StructuralOperationKind = Field(description="Structural edit operation to apply.")
    target_id: str = Field(description="Stable node_id used as the operation anchor.")
    position: InsertPosition = Field(
        default="after",
        description=(
            "Insertion position. Paragraph/table operations use before/after; "
            "run operations can use before/after for run targets or start/end for paragraph targets."
        ),
    )
    expected_text: str | None = Field(
        default=None,
        description="Optional current text guard for remove and set operations.",
    )
    text: str | None = Field(
        default=None,
        description="Text for inserted paragraphs/runs or replacement cell text.",
    )
    rows: list[list[str]] | None = Field(
        default=None,
        description="Rectangular text matrix for insert_table.",
    )
    values: list[str] | None = Field(
        default=None,
        description="Texts for inserted table rows or columns.",
    )
    row_index: int | None = Field(
        default=None,
        ge=1,
        description="Optional 1-based table row index when target_id is a table.",
    )
    column_index: int | None = Field(
        default=None,
        ge=1,
        description="Optional 1-based table column index when target_id is a table.",
    )
    reason: str = Field(default="", description="Short rationale for the change.")


DocumentEdit: TypeAlias = Annotated[TextEdit | StructuralEdit, Field(discriminator="edit_type")]


class TextAnnotation(BaseModel):
    target_kind: AnnotationTargetKind = Field(description="Whether this annotation targets a paragraph or a run.")
    target_id: str = Field(description="Stable opaque node id from the parsed document.")
    selected_text: str | None = Field(
        default=None,
        description="Exact substring to highlight inside the target. Omit to annotate the full target text.",
    )
    occurrence_index: int | None = Field(
        default=None,
        ge=0,
        description="Optional zero-based occurrence index when selected_text appears multiple times.",
    )
    label: str = Field(description="Short label shown in the review UI.")
    color: str = Field(default="#FFFF00", description="Highlight color.")
    note: str = Field(default="", description="Optional explanation shown on hover.")

    @model_validator(mode="after")
    def _validate_selection(self) -> "TextAnnotation":
        if self.selected_text == "":
            raise ValueError("selected_text must not be empty.")
        if self.selected_text is None and self.occurrence_index is not None:
            raise ValueError("occurrence_index requires selected_text.")
        return self


class EditableTarget(BaseModel):
    target_kind: TargetKind
    target_id: str
    parent_paragraph_id: str | None = None
    current_text: str
    page_number: int | None = None
    native_anchor: NativeAnchor | None = None
    writable: bool = True
    writable_reason: str | None = None


class EditValidationIssue(BaseModel):
    code: EditValidationCode
    target_kind: TargetKind | None = None
    target_id: str | None = None
    operation: StructuralOperationKind | None = None
    message: str
    expected_text: str | None = None
    current_text: str | None = None


class EditValidationResult(BaseModel):
    ok: bool = True
    issues: list[EditValidationIssue] = Field(default_factory=list)


class ApplyDocumentEditsResult(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    ok: bool = True
    source_doc_type: str | None = None
    source_name: str | None = None
    output_path: str | None = None
    output_filename: str | None = None
    output_bytes: bytes | None = None
    updated_doc_ir: DocIR | None = None
    edits_applied: int = 0
    operations_applied: int = 0
    modified_target_ids: list[str] = Field(default_factory=list)
    created_target_ids: list[str] = Field(default_factory=list)
    removed_target_ids: list[str] = Field(default_factory=list)
    modified_run_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation: EditValidationResult = Field(default_factory=EditValidationResult)


class AnnotationValidationIssue(BaseModel):
    code: AnnotationValidationCode
    target_kind: AnnotationTargetKind | None = None
    target_id: str | None = None
    message: str
    selected_text: str | None = None
    occurrence_index: int | None = None
    match_count: int | None = None
    current_text: str | None = None


class AnnotationValidationResult(BaseModel):
    ok: bool = True
    issues: list[AnnotationValidationIssue] = Field(default_factory=list)


class ResolvedTextAnnotation(BaseModel):
    target_kind: AnnotationTargetKind
    target_id: str
    selected_text: str
    occurrence_index: int | None = None
    start: int
    end: int
    label: str
    color: str
    note: str


class ReviewHtmlResult(BaseModel):
    ok: bool = True
    html: str | None = None
    resolved_annotations: list[ResolvedTextAnnotation] = Field(default_factory=list)
    validation: AnnotationValidationResult = Field(default_factory=AnnotationValidationResult)


class DocumentRunContext(BaseModel):
    node_id: str
    text: str
    start: int = Field(default=0, description="Start offset of this run in the containing paragraph text.")
    end: int = Field(default=0, description="End offset of this run in the containing paragraph text.")
    native_anchor: NativeAnchor | None = None


class DocumentParagraphContext(BaseModel):
    node_id: str
    text: str
    page_number: int | None = None
    has_tables: bool = False
    has_images: bool = False
    writable_as_paragraph: bool = False
    native_anchor: NativeAnchor | None = None
    runs: list[DocumentRunContext] = Field(default_factory=list)


class DocumentContextResult(BaseModel):
    source_path: str | None = None
    source_doc_type: str | None = None
    source_name: str | None = None
    paragraphs: list[DocumentParagraphContext] = Field(default_factory=list)
    missing_target_ids: list[str] = Field(default_factory=list)


class ReadDocumentResult(BaseModel):
    source_path: str | None = None
    source_doc_type: str | None = None
    source_name: str | None = None
    start: int = 0
    limit: int = 50
    total_paragraphs: int = 0
    next_start: int | None = None
    paragraphs: list[DocumentParagraphContext] = Field(default_factory=list)


class ListEditableTargetsResult(BaseModel):
    source_path: str | None = None
    source_doc_type: str | None = None
    source_name: str | None = None
    targets: list[EditableTarget] = Field(default_factory=list)
    missing_target_ids: list[str] = Field(default_factory=list)


__all__ = [
    "AnnotationTargetKind",
    "AnnotationValidationCode",
    "AnnotationValidationIssue",
    "AnnotationValidationResult",
    "ApplyDocumentEditsResult",
    "DocumentContextResult",
    "DocumentEdit",
    "DocumentInput",
    "DocumentParagraphContext",
    "DocumentRunContext",
    "ReadDocumentResult",
    "EditableTarget",
    "EditValidationCode",
    "EditValidationIssue",
    "EditValidationResult",
    "ListEditableTargetsResult",
    "ResolvedTextAnnotation",
    "ReviewHtmlResult",
    "TargetKind",
    "TextTargetKind",
    "TextAnnotation",
    "TextEdit",
    "StructuralEdit",
    "StructuralOperationKind",
    "InsertPosition",
]
