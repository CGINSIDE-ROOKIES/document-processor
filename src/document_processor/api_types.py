from __future__ import annotations

from pathlib import PurePath
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .io_utils import SourceDocType
from .models import DocIR

TargetKind = Literal["paragraph", "run", "cell"]
AnnotationTargetKind = Literal["paragraph", "run"]
EditValidationCode = Literal[
    "target_not_found",
    "target_kind_mismatch",
    "text_mismatch",
    "mixed_content_not_supported",
    "paragraph_count_mismatch",
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
    target_kind: TargetKind = Field(description="Whether this edit targets a paragraph, run, or table cell.")
    target_unit_id: str = Field(
        description="Stable unit id from the parsed document, such as `s1.p22`, `s1.p22.r1`, or `s1.p2.r1.tbl1.tr1.tc1`."
    )
    expected_text: str = Field(description="Exact current text that must match before the edit is applied.")
    new_text: str = Field(description="Replacement text for the target.")
    reason: str = Field(default="", description="Short rationale for the change.")


class TextAnnotation(BaseModel):
    target_kind: AnnotationTargetKind = Field(description="Whether this annotation targets a paragraph or a run.")
    target_unit_id: str = Field(description="Stable unit id from the parsed document.")
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
    target_unit_id: str
    parent_paragraph_unit_id: str | None = None
    current_text: str
    page_number: int | None = None
    writable: bool = True
    writable_reason: str | None = None


class EditValidationIssue(BaseModel):
    code: EditValidationCode
    target_kind: TargetKind | None = None
    target_unit_id: str | None = None
    message: str
    expected_text: str | None = None
    current_text: str | None = None


class EditValidationResult(BaseModel):
    ok: bool = True
    issues: list[EditValidationIssue] = Field(default_factory=list)


class ApplyTextEditsResult(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    ok: bool = True
    source_doc_type: str | None = None
    source_name: str | None = None
    output_path: str | None = None
    output_filename: str | None = None
    output_bytes: bytes | None = None
    updated_doc_ir: DocIR | None = None
    edits_applied: int = 0
    modified_target_ids: list[str] = Field(default_factory=list)
    modified_run_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation: EditValidationResult = Field(default_factory=EditValidationResult)


class AnnotationValidationIssue(BaseModel):
    code: AnnotationValidationCode
    target_kind: AnnotationTargetKind | None = None
    target_unit_id: str | None = None
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
    target_unit_id: str
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
    unit_id: str
    text: str


class DocumentParagraphContext(BaseModel):
    unit_id: str
    text: str
    page_number: int | None = None
    has_tables: bool = False
    has_images: bool = False
    writable_as_paragraph: bool = False
    runs: list[DocumentRunContext] = Field(default_factory=list)


class DocumentBoundRequest(BaseModel):
    document: DocumentInput | None = Field(default=None, description="Document source for this request.")
    source_path: str | None = Field(
        default=None,
        description="Deprecated convenience field for path-backed calls.",
    )

    @model_validator(mode="after")
    def _coerce_document(self) -> "DocumentBoundRequest":
        if self.document is not None and self.source_path is not None:
            raise ValueError("Specify either document or source_path, not both.")
        if self.document is None:
            if self.source_path is None:
                raise ValueError("Provide either document or source_path.")
            self.document = DocumentInput(source_path=self.source_path)
        return self


class GetDocumentContextRequest(DocumentBoundRequest):
    unit_ids: list[str] = Field(description="Paragraph and/or run unit ids to inspect.")
    before: int = Field(default=1, ge=0, description="How many surrounding paragraphs to include before each target.")
    after: int = Field(default=1, ge=0, description="How many surrounding paragraphs to include after each target.")
    include_runs: bool = Field(default=True, description="Whether to include exact run texts for returned paragraphs.")


class DocumentContextResult(BaseModel):
    source_path: str | None = None
    source_doc_type: str | None = None
    source_name: str | None = None
    paragraphs: list[DocumentParagraphContext] = Field(default_factory=list)
    missing_unit_ids: list[str] = Field(default_factory=list)


class ListEditableTargetsRequest(DocumentBoundRequest):
    unit_ids: list[str] = Field(default_factory=list, description="Optional exact unit ids to filter by.")
    target_kinds: list[TargetKind] = Field(default_factory=lambda: ["paragraph", "cell", "run"])
    include_child_runs: bool = Field(
        default=False,
        description="When a paragraph or cell id is requested, also return its run targets.",
    )
    only_writable: bool = Field(default=True)
    max_targets: int | None = Field(default=200, ge=1)


class ListEditableTargetsResult(BaseModel):
    source_path: str | None = None
    source_doc_type: str | None = None
    source_name: str | None = None
    targets: list[EditableTarget] = Field(default_factory=list)
    missing_unit_ids: list[str] = Field(default_factory=list)


class ValidateTextEditsRequest(DocumentBoundRequest):
    edits: list[TextEdit]


class ApplyTextEditsRequest(DocumentBoundRequest):
    edits: list[TextEdit]
    output_path: str | None = Field(default=None, description="Optional output path for the edited native file.")
    output_filename: str | None = Field(
        default=None,
        description=(
            "Optional basename written next to the source document or used as the returned filename "
            "for bytes-backed calls."
        ),
    )
    return_doc_ir: bool = Field(
        default=False,
        description="Whether to parse and include the updated DocIR in the response after apply.",
    )

    @model_validator(mode="after")
    def _validate_output_target(self) -> "ApplyTextEditsRequest":
        if self.output_path is not None and self.output_filename is not None:
            raise ValueError("Specify either output_path or output_filename, not both.")
        if self.output_filename is None:
            return self

        filename = self.output_filename.strip()
        if not filename:
            raise ValueError("output_filename must not be empty.")

        pure = PurePath(filename)
        if pure.is_absolute() or pure.name != filename or filename in {".", ".."}:
            raise ValueError("output_filename must be a filename only, without directory segments.")
        return self


class RenderReviewHtmlRequest(DocumentBoundRequest):
    annotations: list[TextAnnotation]
    title: str = Field(default="Review")


__all__ = [
    "AnnotationTargetKind",
    "AnnotationValidationCode",
    "AnnotationValidationIssue",
    "AnnotationValidationResult",
    "ApplyTextEditsRequest",
    "ApplyTextEditsResult",
    "DocumentBoundRequest",
    "DocumentContextResult",
    "DocumentInput",
    "DocumentParagraphContext",
    "DocumentRunContext",
    "EditableTarget",
    "EditValidationCode",
    "EditValidationIssue",
    "EditValidationResult",
    "GetDocumentContextRequest",
    "ListEditableTargetsRequest",
    "ListEditableTargetsResult",
    "RenderReviewHtmlRequest",
    "ResolvedTextAnnotation",
    "ReviewHtmlResult",
    "TargetKind",
    "TextAnnotation",
    "TextEdit",
    "ValidateTextEditsRequest",
]
