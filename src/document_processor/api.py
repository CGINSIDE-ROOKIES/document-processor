from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .annotations import Annotation, render_annotated_html
from .api_types import (
    AnnotationValidationIssue,
    AnnotationValidationResult,
    ApplyTextEditsRequest,
    ApplyTextEditsResult,
    DocumentContextResult,
    DocumentInput,
    DocumentParagraphContext,
    DocumentRunContext,
    EditableTarget,
    EditValidationIssue,
    EditValidationResult,
    GetDocumentContextRequest,
    ListEditableTargetsRequest,
    ListEditableTargetsResult,
    RenderReviewHtmlRequest,
    ResolvedTextAnnotation,
    ReviewHtmlResult,
    TargetKind,
    TextAnnotation,
    TextEdit,
    ValidateTextEditsRequest,
)
from .edit_engine import (
    EditValidationError,
    ParagraphTextEdit,
    RunTextEdit,
    _build_doc_ir_index,
    _iter_doc_ir_paragraphs,
    apply_edits_to_source,
)
from .models import DocIR, ParagraphIR

_WRITEBACK_SOURCE_TYPES = {"docx", "hwpx", "hwp"}


@dataclass
class _ResolvedDocument:
    doc: DocIR
    source_path: str | None
    source_doc_type: str | None
    source_name: str | None
    native_source_path: str | None = None
    native_source_bytes: bytes | None = None

    @property
    def has_native_source(self) -> bool:
        return self.native_source_path is not None or self.native_source_bytes is not None


def get_document_context(request: GetDocumentContextRequest) -> DocumentContextResult:
    resolved = _resolve_document_input(request.document)
    paragraphs = list(_iter_doc_ir_paragraphs(resolved.doc.paragraphs))
    paragraph_indices = {paragraph.unit_id: index for index, paragraph in enumerate(paragraphs)}
    run_to_paragraph = {run.unit_id: paragraph for paragraph in paragraphs for run in paragraph.runs}

    selected_indices: set[int] = set()
    missing_unit_ids: list[str] = []
    for unit_id in request.unit_ids:
        if unit_id in paragraph_indices:
            anchor_index = paragraph_indices[unit_id]
        elif unit_id in run_to_paragraph:
            anchor_index = paragraph_indices[run_to_paragraph[unit_id].unit_id]
        else:
            missing_unit_ids.append(unit_id)
            continue
        start = max(0, anchor_index - request.before)
        end = min(len(paragraphs), anchor_index + request.after + 1)
        selected_indices.update(range(start, end))

    ordered_indices = sorted(selected_indices)
    return DocumentContextResult(
        source_path=resolved.source_path,
        source_doc_type=resolved.source_doc_type,
        source_name=resolved.source_name,
        paragraphs=[_paragraph_context(paragraphs[index], include_runs=request.include_runs) for index in ordered_indices],
        missing_unit_ids=missing_unit_ids,
    )


def list_editable_targets(request: ListEditableTargetsRequest) -> ListEditableTargetsResult:
    resolved = _resolve_document_input(request.document)
    paragraphs = list(_iter_doc_ir_paragraphs(resolved.doc.paragraphs))
    requested_ids = set(request.unit_ids)
    paragraph_ids = {paragraph.unit_id for paragraph in paragraphs}
    run_ids = {run.unit_id for paragraph in paragraphs for run in paragraph.runs}

    targets = _collect_editable_targets(
        resolved.doc,
        target_kinds=request.target_kinds,
        only_writable=request.only_writable,
        exact_unit_ids=requested_ids or None,
        include_child_runs=request.include_child_runs,
        max_targets=request.max_targets,
    )

    missing_unit_ids = [
        unit_id
        for unit_id in request.unit_ids
        if unit_id not in paragraph_ids and unit_id not in run_ids
    ]
    return ListEditableTargetsResult(
        source_path=resolved.source_path,
        source_doc_type=resolved.source_doc_type,
        source_name=resolved.source_name,
        targets=targets,
        missing_unit_ids=missing_unit_ids,
    )


def validate_text_edits(request: ValidateTextEditsRequest) -> EditValidationResult:
    resolved = _resolve_document_input(request.document)
    return _validate_text_edits_for_doc(
        resolved.doc,
        request.edits,
        include_writeback_support=resolved.has_native_source,
    )


def apply_text_edits(request: ApplyTextEditsRequest) -> ApplyTextEditsResult:
    resolved = _resolve_document_input(request.document)
    validation = _validate_apply_request(resolved, request)
    if not validation.ok:
        return ApplyTextEditsResult(
            ok=False,
            source_doc_type=resolved.source_doc_type,
            source_name=resolved.source_name,
            validation=validation,
        )

    try:
        resolved_output_path = _resolved_native_output_path(resolved, request)
        resolved_output_filename = (
            request.output_filename if resolved.native_source_path is None else None
        )
        internal_result = apply_edits_to_source(
            _native_apply_source(resolved),
            [_to_internal_edit(edit) for edit in request.edits],
            doc_type=resolved.source_doc_type or "auto",
            source_name=resolved.source_name,
            output_path=resolved_output_path,
            output_filename=resolved_output_filename,
        )
    except EditValidationError as exc:
        return ApplyTextEditsResult(
            ok=False,
            source_doc_type=resolved.source_doc_type,
            source_name=resolved.source_name,
            validation=EditValidationResult(
                ok=False,
                issues=[
                    EditValidationIssue(
                        code="output_path_conflicts_with_source",
                        message=str(exc),
                    )
                ],
            ),
        )

    updated_doc_ir = internal_result.updated_doc_ir
    if request.return_doc_ir and updated_doc_ir is None:
        if internal_result.output_bytes is not None:
            updated_doc_ir = DocIR.from_file(internal_result.output_bytes)
        elif internal_result.output_path is not None:
            updated_doc_ir = DocIR.from_file(Path(internal_result.output_path))

    return ApplyTextEditsResult(
        ok=True,
        source_doc_type=internal_result.source_doc_type or resolved.source_doc_type,
        source_name=resolved.source_name,
        output_path=internal_result.output_path,
        output_filename=internal_result.output_filename,
        output_bytes=internal_result.output_bytes,
        updated_doc_ir=updated_doc_ir,
        edits_applied=internal_result.edits_applied,
        modified_target_ids=internal_result.modified_unit_ids,
        modified_run_ids=internal_result.modified_run_ids,
        warnings=internal_result.warnings,
        validation=validation,
    )


def render_review_html(request: RenderReviewHtmlRequest) -> ReviewHtmlResult:
    resolved = _resolve_document_input(request.document)
    validation, resolved_annotations = _validate_text_annotations_for_doc(resolved.doc, request.annotations)
    if not validation.ok:
        return ReviewHtmlResult(ok=False, validation=validation)

    html = render_annotated_html(
        resolved.doc,
        [_to_internal_annotation(annotation) for annotation in request.annotations],
        title=request.title,
    )
    return ReviewHtmlResult(
        ok=True,
        html=html,
        resolved_annotations=resolved_annotations,
        validation=validation,
    )


def _resolve_document_input(document_input: DocumentInput) -> _ResolvedDocument:
    native_source_path = document_input.source_path
    native_source_bytes = document_input.source_bytes
    resolved_source_name = (
        document_input.source_name
        or (Path(document_input.source_path).name if document_input.source_path is not None else None)
    )

    if document_input.doc_ir is not None:
        doc = document_input.doc_ir
        return _ResolvedDocument(
            doc=doc,
            source_path=native_source_path or doc.source_path,
            source_doc_type=doc.source_doc_type,
            source_name=resolved_source_name or (Path(doc.source_path).name if doc.source_path else None),
            native_source_path=native_source_path,
            native_source_bytes=native_source_bytes,
        )

    if native_source_path is not None:
        doc = DocIR.from_file(Path(native_source_path), doc_type=document_input.source_doc_type)
    elif native_source_bytes is not None:
        doc = DocIR.from_file(native_source_bytes, doc_type=document_input.source_doc_type)
    else:
        raise ValueError("DocumentInput did not provide a usable source.")

    return _ResolvedDocument(
        doc=doc,
        source_path=doc.source_path,
        source_doc_type=doc.source_doc_type,
        source_name=resolved_source_name or (Path(doc.source_path).name if doc.source_path else None),
        native_source_path=native_source_path,
        native_source_bytes=native_source_bytes,
    )


def _native_apply_source(resolved: _ResolvedDocument) -> DocIR | str | bytes:
    if resolved.native_source_path is not None:
        return resolved.native_source_path
    if resolved.native_source_bytes is not None:
        return resolved.native_source_bytes
    return resolved.doc


def _resolved_native_output_path(
    resolved: _ResolvedDocument,
    request: ApplyTextEditsRequest,
) -> str | Path | None:
    if request.output_path is not None:
        return request.output_path
    if request.output_filename is None or resolved.native_source_path is None:
        return None
    return Path(resolved.native_source_path).with_name(request.output_filename)


def _validate_apply_request(
    resolved: _ResolvedDocument,
    request: ApplyTextEditsRequest,
) -> EditValidationResult:
    issues = _validate_text_edits_for_doc(
        resolved.doc,
        request.edits,
        include_writeback_support=resolved.has_native_source,
    ).issues

    if not resolved.has_native_source and (request.output_path is not None or request.output_filename is not None):
        issues.append(
            EditValidationIssue(
                code="native_source_required",
                message="output_path and output_filename require a native source document.",
            )
        )

    if resolved.native_source_path is not None:
        issues.extend(
            _validate_apply_output_request(
                Path(resolved.native_source_path),
                resolved.source_doc_type,
                request,
            ).issues
        )

    return EditValidationResult(ok=not issues, issues=issues)


def _validate_text_edits_for_doc(
    doc: DocIR,
    edits: list[TextEdit],
    *,
    include_writeback_support: bool,
) -> EditValidationResult:
    issues: list[EditValidationIssue] = []
    index = _build_doc_ir_index(doc)

    if include_writeback_support and doc.source_doc_type not in _WRITEBACK_SOURCE_TYPES:
        issues.append(
            EditValidationIssue(
                code="unsupported_source_doc_type",
                message=(
                    "Native write-back is currently supported only for docx, hwp, and hwpx; "
                    f"got {doc.source_doc_type!r}."
                ),
            )
        )

    for edit in edits:
        issues.extend(_validate_single_text_edit(index, edit))

    return EditValidationResult(ok=not issues, issues=issues)


def _validate_single_text_edit(index, edit: TextEdit) -> list[EditValidationIssue]:
    if edit.target_kind == "paragraph":
        paragraph = index.paragraphs.get(edit.target_unit_id)
        if paragraph is None:
            if edit.target_unit_id in index.runs:
                return [
                    EditValidationIssue(
                        code="target_kind_mismatch",
                        target_kind=edit.target_kind,
                        target_unit_id=edit.target_unit_id,
                        message=f"{edit.target_unit_id} is a run target, not a paragraph target.",
                    )
                ]
            return [
                EditValidationIssue(
                    code="target_not_found",
                    target_kind=edit.target_kind,
                    target_unit_id=edit.target_unit_id,
                    message=f"Paragraph target does not exist: {edit.target_unit_id}.",
                )
            ]
        if paragraph.has_non_run_content:
            return [
                EditValidationIssue(
                    code="mixed_content_not_supported",
                    target_kind=edit.target_kind,
                    target_unit_id=edit.target_unit_id,
                    message=f"Paragraph target has mixed content and is not safely writable: {edit.target_unit_id}.",
                    expected_text=edit.expected_text,
                    current_text=paragraph.text,
                )
            ]
        if paragraph.text != edit.expected_text:
            return [
                EditValidationIssue(
                    code="text_mismatch",
                    target_kind=edit.target_kind,
                    target_unit_id=edit.target_unit_id,
                    message=f"Paragraph text mismatch for {edit.target_unit_id}.",
                    expected_text=edit.expected_text,
                    current_text=paragraph.text,
                )
            ]
        return []

    run = index.runs.get(edit.target_unit_id)
    if run is None:
        if edit.target_unit_id in index.paragraphs:
            return [
                EditValidationIssue(
                    code="target_kind_mismatch",
                    target_kind=edit.target_kind,
                    target_unit_id=edit.target_unit_id,
                    message=f"{edit.target_unit_id} is a paragraph target, not a run target.",
                )
            ]
        return [
            EditValidationIssue(
                code="target_not_found",
                target_kind=edit.target_kind,
                target_unit_id=edit.target_unit_id,
                message=f"Run target does not exist: {edit.target_unit_id}.",
            )
        ]
    if run.text != edit.expected_text:
        return [
            EditValidationIssue(
                code="text_mismatch",
                target_kind=edit.target_kind,
                target_unit_id=edit.target_unit_id,
                message=f"Run text mismatch for {edit.target_unit_id}.",
                expected_text=edit.expected_text,
                current_text=run.text,
            )
        ]
    return []


def _validate_apply_output_request(
    source: Path,
    source_doc_type: str | None,
    request: ApplyTextEditsRequest,
) -> EditValidationResult:
    output_path = _resolve_requested_output_path(source, request)
    if output_path is None:
        return EditValidationResult()

    final_output_path = _normalize_output_path_for_source_doc_type(output_path, source_doc_type)

    if _same_path(source, final_output_path):
        return EditValidationResult(
            ok=False,
            issues=[
                EditValidationIssue(
                    code="output_path_conflicts_with_source",
                    message=(
                        f"Output path would overwrite the source file: {final_output_path}. "
                        "Pick a different output_path or output_filename."
                    ),
                )
            ],
        )

    return EditValidationResult()


def _normalize_output_path_for_source_doc_type(output_path: Path, source_doc_type: str | None) -> Path:
    if source_doc_type == "docx" and output_path.suffix.lower() != ".docx":
        return output_path.with_suffix(".docx")
    if source_doc_type in {"hwpx", "hwp"} and output_path.suffix.lower() != ".hwpx":
        return output_path.with_suffix(".hwpx")
    return output_path


def _validate_text_annotations_for_doc(
    doc: DocIR,
    annotations: list[TextAnnotation],
) -> tuple[AnnotationValidationResult, list[ResolvedTextAnnotation]]:
    paragraphs = list(_iter_doc_ir_paragraphs(doc.paragraphs))
    paragraph_map = {paragraph.unit_id: paragraph for paragraph in paragraphs}
    run_map = {run.unit_id: run for paragraph in paragraphs for run in paragraph.runs}

    issues: list[AnnotationValidationIssue] = []
    resolved: list[ResolvedTextAnnotation] = []

    for annotation in annotations:
        if annotation.target_kind == "paragraph":
            paragraph = paragraph_map.get(annotation.target_unit_id)
            if paragraph is None:
                if annotation.target_unit_id in run_map:
                    issues.append(
                        AnnotationValidationIssue(
                            code="target_kind_mismatch",
                            target_kind=annotation.target_kind,
                            target_unit_id=annotation.target_unit_id,
                            message=f"{annotation.target_unit_id} is a run target, not a paragraph target.",
                        )
                    )
                else:
                    issues.append(
                        AnnotationValidationIssue(
                            code="target_not_found",
                            target_kind=annotation.target_kind,
                            target_unit_id=annotation.target_unit_id,
                            message=f"Paragraph target does not exist: {annotation.target_unit_id}.",
                        )
                    )
                continue
            if paragraph.tables or paragraph.images:
                issues.append(
                    AnnotationValidationIssue(
                        code="mixed_content_not_supported",
                        target_kind=annotation.target_kind,
                        target_unit_id=annotation.target_unit_id,
                        message=f"Paragraph annotations do not support mixed content: {annotation.target_unit_id}.",
                        current_text=paragraph.text,
                    )
                )
                continue
            text = paragraph.text or ""
        else:
            run = run_map.get(annotation.target_unit_id)
            if run is None:
                if annotation.target_unit_id in paragraph_map:
                    issues.append(
                        AnnotationValidationIssue(
                            code="target_kind_mismatch",
                            target_kind=annotation.target_kind,
                            target_unit_id=annotation.target_unit_id,
                            message=f"{annotation.target_unit_id} is a paragraph target, not a run target.",
                        )
                    )
                else:
                    issues.append(
                        AnnotationValidationIssue(
                            code="target_not_found",
                            target_kind=annotation.target_kind,
                            target_unit_id=annotation.target_unit_id,
                            message=f"Run target does not exist: {annotation.target_unit_id}.",
                        )
                )
                continue
            text = run.text

        start, end, match_text, resolved_occurrence_index, issue = _resolve_text_annotation_span(
            text=text,
            annotation=annotation,
        )
        if issue is not None:
            issues.append(
                AnnotationValidationIssue(
                    code=issue["code"],
                    target_kind=annotation.target_kind,
                    target_unit_id=annotation.target_unit_id,
                    message=issue["message"],
                    selected_text=annotation.selected_text,
                    occurrence_index=annotation.occurrence_index,
                    match_count=issue.get("match_count"),
                    current_text=text,
                )
            )
            continue

        resolved.append(
            ResolvedTextAnnotation(
                target_kind=annotation.target_kind,
                target_unit_id=annotation.target_unit_id,
                selected_text=match_text,
                occurrence_index=resolved_occurrence_index,
                start=start,
                end=end,
                label=annotation.label,
                color=annotation.color,
                note=annotation.note,
            )
        )

    return AnnotationValidationResult(ok=not issues, issues=issues), resolved


def _paragraph_context(paragraph: ParagraphIR, *, include_runs: bool) -> DocumentParagraphContext:
    writable, _reason = _paragraph_writable(paragraph)
    return DocumentParagraphContext(
        unit_id=paragraph.unit_id,
        text=paragraph.text or "",
        page_number=paragraph.page_number,
        has_tables=bool(paragraph.tables),
        has_images=bool(paragraph.images),
        writable_as_paragraph=writable,
        runs=[DocumentRunContext(unit_id=run.unit_id, text=run.text) for run in paragraph.runs] if include_runs else [],
    )


def _collect_editable_targets(
    doc: DocIR,
    *,
    target_kinds: list[TargetKind],
    only_writable: bool,
    exact_unit_ids: set[str] | None = None,
    include_child_runs: bool = False,
    max_targets: int | None = None,
) -> list[EditableTarget]:
    results: list[EditableTarget] = []
    requested_parent_ids = exact_unit_ids or set()
    for paragraph in _iter_doc_ir_paragraphs(doc.paragraphs):
        paragraph_requested = exact_unit_ids is None or paragraph.unit_id in exact_unit_ids
        writable, writable_reason = _paragraph_writable(paragraph)

        if "paragraph" in target_kinds and paragraph_requested:
            if not only_writable or writable:
                results.append(
                    EditableTarget(
                        target_kind="paragraph",
                        target_unit_id=paragraph.unit_id,
                        current_text=paragraph.text or "",
                        page_number=paragraph.page_number,
                        writable=writable,
                        writable_reason=writable_reason,
                    )
                )

        if "run" in target_kinds:
            for run in paragraph.runs:
                run_requested = exact_unit_ids is None or run.unit_id in exact_unit_ids
                inherited_request = include_child_runs and paragraph.unit_id in requested_parent_ids
                if run_requested or inherited_request:
                    results.append(
                        EditableTarget(
                            target_kind="run",
                            target_unit_id=run.unit_id,
                            parent_paragraph_unit_id=paragraph.unit_id,
                            current_text=run.text,
                            page_number=paragraph.page_number,
                            writable=True,
                        )
                    )

        if max_targets is not None and len(results) >= max_targets:
            return results[:max_targets]
    return results


def _paragraph_writable(paragraph: ParagraphIR) -> tuple[bool, str | None]:
    if paragraph.tables or paragraph.images:
        return False, "Paragraph contains tables or images."
    return True, None


def _resolve_requested_output_path(source: Path, request: ApplyTextEditsRequest) -> Path | None:
    if request.output_path is not None:
        return Path(request.output_path)
    if request.output_filename is not None:
        return source.with_name(request.output_filename)
    return None


def _same_path(left: Path, right: Path) -> bool:
    return left.expanduser().resolve(strict=False) == right.expanduser().resolve(strict=False)


def _to_internal_edit(edit: TextEdit):
    if edit.target_kind == "paragraph":
        return ParagraphTextEdit(
            paragraph_unit_id=edit.target_unit_id,
            old_text=edit.expected_text,
            new_text=edit.new_text,
            reason=edit.reason,
        )
    return RunTextEdit(
        run_unit_id=edit.target_unit_id,
        old_text=edit.expected_text,
        new_text=edit.new_text,
        reason=edit.reason,
    )


def _to_internal_annotation(annotation: TextAnnotation) -> Annotation:
    return Annotation(
        target_unit_id=annotation.target_unit_id,
        selected_text=annotation.selected_text,
        occurrence_index=annotation.occurrence_index,
        label=annotation.label,
        color=annotation.color,
        note=annotation.note,
    )


def _resolve_text_annotation_span(
    *,
    text: str,
    annotation: TextAnnotation,
) -> tuple[int, int, str, int | None, dict[str, object] | None]:
    if annotation.selected_text is None:
        return 0, len(text), text, None, None

    matches = _find_text_occurrences(text, annotation.selected_text)
    if not matches:
        return 0, 0, "", None, {
            "code": "selected_text_not_found",
            "message": (
                f"Selected text does not occur in target {annotation.target_unit_id}: "
                f"{annotation.selected_text!r}."
            ),
        }

    if annotation.occurrence_index is None:
        if len(matches) > 1:
            return 0, 0, "", None, {
                "code": "selected_text_ambiguous",
                "message": (
                    f"Selected text is ambiguous in target {annotation.target_unit_id}; "
                    "specify occurrence_index."
                ),
                "match_count": len(matches),
            }
        occurrence_index = 0
    elif annotation.occurrence_index >= len(matches):
        return 0, 0, "", None, {
            "code": "occurrence_index_out_of_bounds",
            "message": (
                f"occurrence_index {annotation.occurrence_index} is out of bounds for "
                f"{annotation.target_unit_id}; found {len(matches)} match(es)."
            ),
            "match_count": len(matches),
        }
    else:
        occurrence_index = annotation.occurrence_index

    start = matches[occurrence_index]
    end = start + len(annotation.selected_text)
    return start, end, annotation.selected_text, occurrence_index, None


def _find_text_occurrences(text: str, selected_text: str) -> list[int]:
    matches: list[int] = []
    search_from = 0
    while True:
        index = text.find(selected_text, search_from)
        if index < 0:
            return matches
        matches.append(index)
        search_from = index + 1


__all__ = [
    "AnnotationValidationIssue",
    "AnnotationValidationResult",
    "ApplyTextEditsRequest",
    "ApplyTextEditsResult",
    "DocumentContextResult",
    "DocumentInput",
    "DocumentParagraphContext",
    "DocumentRunContext",
    "EditableTarget",
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
    "apply_text_edits",
    "get_document_context",
    "list_editable_targets",
    "render_review_html",
    "validate_text_edits",
]
