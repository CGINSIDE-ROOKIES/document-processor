from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .annotations import _Annotation, _render_annotated_html
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
    ReadDocumentRequest,
    ReadDocumentResult,
    RenderReviewHtmlRequest,
    ResolvedTextAnnotation,
    ReviewHtmlResult,
    TargetKind,
    TextAnnotation,
    TextEdit,
    ValidateTextAnnotationsRequest,
    ValidateTextEditsRequest,
)
from .edit_engine import (
    EditValidationError,
    _apply_text_edits_to_source,
    _build_doc_ir_index,
    _iter_doc_ir_paragraphs,
)
from .models import DocIR, NativeAnchor, ParagraphIR, RunIR, TableCellIR, TableIR

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


@dataclass(frozen=True)
class _TargetIdentity:
    kind: TargetKind
    node_id: str
    native_anchor: NativeAnchor | None = None
    parent_paragraph_id: str | None = None


@dataclass
class _TargetIdentityIndex:
    by_identifier: dict[str, _TargetIdentity]


@dataclass(frozen=True)
class _ResolvedTextEdit:
    edit: TextEdit
    identity: _TargetIdentity


@dataclass(frozen=True)
class _ResolvedTextAnnotation:
    annotation: TextAnnotation
    identity: _TargetIdentity


def read_document(request: ReadDocumentRequest) -> ReadDocumentResult:
    resolved = _resolve_document_input(request.document)
    paragraphs = list(_iter_doc_ir_paragraphs(resolved.doc.paragraphs))
    end = min(len(paragraphs), request.start + request.limit)
    selected = paragraphs[request.start:end]
    next_start = end if end < len(paragraphs) else None
    return ReadDocumentResult(
        source_path=resolved.source_path,
        source_doc_type=resolved.source_doc_type,
        source_name=resolved.source_name,
        start=request.start,
        limit=request.limit,
        total_paragraphs=len(paragraphs),
        next_start=next_start,
        paragraphs=[_paragraph_context(paragraph, include_runs=request.include_runs) for paragraph in selected],
    )


def get_document_context(request: GetDocumentContextRequest) -> DocumentContextResult:
    resolved = _resolve_document_input(request.document)
    identity_index = _build_target_identity_index(resolved.doc)
    paragraphs = list(_iter_doc_ir_paragraphs(resolved.doc.paragraphs))
    paragraph_indices = {paragraph.node_id: offset for offset, paragraph in enumerate(paragraphs)}
    run_to_paragraph = {run.node_id: paragraph for paragraph in paragraphs for run in paragraph.runs}
    cell_to_anchor_paragraph = {
        cell.node_id: cell.paragraphs[0]
        for cell in _iter_doc_ir_cells(resolved.doc.paragraphs)
        if cell.node_id is not None and cell.paragraphs and cell.paragraphs[0].node_id is not None
    }

    selected_indices: set[int] = set()
    missing_target_ids: list[str] = []
    for target_id in request.target_ids:
        identity = identity_index.by_identifier.get(target_id)
        if identity is None:
            missing_target_ids.append(target_id)
            continue
        node_id = identity.node_id
        if node_id in paragraph_indices:
            anchor_index = paragraph_indices[node_id]
        elif node_id in run_to_paragraph:
            anchor_index = paragraph_indices[run_to_paragraph[node_id].node_id]
        elif node_id in cell_to_anchor_paragraph:
            anchor_index = paragraph_indices[cell_to_anchor_paragraph[node_id].node_id]
        else:
            missing_target_ids.append(target_id)
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
        missing_target_ids=missing_target_ids,
    )


def _iter_doc_ir_cells(paragraphs: list[ParagraphIR]):
    for paragraph in paragraphs:
        for table in paragraph.tables:
            yield from _iter_doc_ir_table_cells(table)


def _iter_doc_ir_table_cells(table: TableIR):
    for cell in table.cells:
        yield cell
        for paragraph in cell.paragraphs:
            for nested_table in paragraph.tables:
                yield from _iter_doc_ir_table_cells(nested_table)


def list_editable_targets(request: ListEditableTargetsRequest) -> ListEditableTargetsResult:
    resolved = _resolve_document_input(request.document)
    identity_index = _build_target_identity_index(resolved.doc)
    requested_target_ids = {
        identity.node_id
        for identifier in request.target_ids
        if (identity := identity_index.by_identifier.get(identifier)) is not None
    }

    targets = _collect_editable_targets(
        resolved.doc,
        target_kinds=request.target_kinds,
        only_writable=request.only_writable,
        exact_target_ids=requested_target_ids if request.target_ids else None,
        include_child_runs=request.include_child_runs,
        max_targets=request.max_targets,
    )

    missing_target_ids = [
        target_id
        for target_id in request.target_ids
        if target_id not in identity_index.by_identifier
    ]
    return ListEditableTargetsResult(
        source_path=resolved.source_path,
        source_doc_type=resolved.source_doc_type,
        source_name=resolved.source_name,
        targets=targets,
        missing_target_ids=missing_target_ids,
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
        resolved_edits, _issues = _resolve_text_edits_for_doc(resolved.doc, request.edits)
        if request.dry_run:
            preview_result = None
            if request.return_doc_ir:
                preview_result = _apply_text_edits_to_source(
                    resolved.doc,
                    [_to_canonical_text_edit(resolved_edit) for resolved_edit in resolved_edits],
                    doc_type=resolved.source_doc_type or "auto",
                    source_name=resolved.source_name,
                )
            return ApplyTextEditsResult(
                ok=True,
                source_doc_type=resolved.source_doc_type,
                source_name=resolved.source_name,
                updated_doc_ir=preview_result.updated_doc_ir if preview_result is not None else None,
                edits_applied=0,
                validation=validation,
            )

        resolved_output_path = _resolved_native_output_path(resolved, request)
        resolved_output_filename = (
            request.output_filename if resolved.native_source_path is None else None
        )
        internal_result = _apply_text_edits_to_source(
            _native_apply_source(resolved),
            [_to_canonical_text_edit(resolved_edit) for resolved_edit in resolved_edits],
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
        modified_target_ids=internal_result.modified_target_ids,
        modified_run_ids=internal_result.modified_run_ids,
        warnings=internal_result.warnings,
        validation=validation,
    )


def render_review_html(request: RenderReviewHtmlRequest) -> ReviewHtmlResult:
    resolved = _resolve_document_input(request.document)
    validation, resolved_annotations = _validate_text_annotations_for_doc(resolved.doc, request.annotations)
    if not validation.ok:
        return ReviewHtmlResult(ok=False, validation=validation)

    resolved_annotation_edits, _issues = _resolve_text_annotations_for_doc(resolved.doc, request.annotations)
    html = _render_annotated_html(
        resolved.doc,
        [_to_render_annotation(resolved_annotation) for resolved_annotation in resolved_annotation_edits],
        title=request.title,
    )
    return ReviewHtmlResult(
        ok=True,
        html=html,
        resolved_annotations=resolved_annotations,
        validation=validation,
    )


def validate_text_annotations(request: ValidateTextAnnotationsRequest) -> AnnotationValidationResult:
    resolved = _resolve_document_input(request.document)
    validation, _resolved_annotations = _validate_text_annotations_for_doc(resolved.doc, request.annotations)
    return validation


def _resolve_document_input(document_input: DocumentInput) -> _ResolvedDocument:
    native_source_path = document_input.source_path
    native_source_bytes = document_input.source_bytes
    resolved_source_name = (
        document_input.source_name
        or (Path(document_input.source_path).name if document_input.source_path is not None else None)
    )

    if document_input.doc_ir is not None:
        doc = document_input.doc_ir
        doc.ensure_node_identity()
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
        doc=doc.ensure_node_identity(),
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


def _register_target_identity(
    by_identifier: dict[str, _TargetIdentity],
    identity: _TargetIdentity,
) -> None:
    by_identifier[identity.node_id] = identity


def _build_target_identity_index(doc: DocIR) -> _TargetIdentityIndex:
    doc.ensure_node_identity()
    by_identifier: dict[str, _TargetIdentity] = {}

    def register_paragraph(paragraph: ParagraphIR, *, parent_paragraph: ParagraphIR | None = None) -> None:
        identity = _TargetIdentity(
            kind="paragraph",
            node_id=paragraph.node_id,
            native_anchor=paragraph.native_anchor,
            parent_paragraph_id=parent_paragraph.node_id if parent_paragraph is not None else None,
        )
        _register_target_identity(by_identifier, identity)
        for run in paragraph.runs:
            register_run(run, paragraph)
        for table in paragraph.tables:
            register_table(table)

    def register_run(run: RunIR, paragraph: ParagraphIR) -> None:
        identity = _TargetIdentity(
            kind="run",
            node_id=run.node_id,
            native_anchor=run.native_anchor,
            parent_paragraph_id=paragraph.node_id,
        )
        _register_target_identity(by_identifier, identity)

    def register_table(table: TableIR) -> None:
        for cell in table.cells:
            register_cell(cell)

    def register_cell(cell: TableCellIR) -> None:
        identity = _TargetIdentity(
            kind="cell",
            node_id=cell.node_id,
            native_anchor=cell.native_anchor,
        )
        _register_target_identity(by_identifier, identity)
        for paragraph in cell.paragraphs:
            register_paragraph(paragraph)

    for paragraph in doc.paragraphs:
        register_paragraph(paragraph)

    return _TargetIdentityIndex(by_identifier=by_identifier)


def _resolve_text_edits_for_doc(
    doc: DocIR,
    edits: list[TextEdit],
) -> tuple[list[_ResolvedTextEdit], list[EditValidationIssue]]:
    identity_index = _build_target_identity_index(doc)
    resolved: list[_ResolvedTextEdit] = []
    issues: list[EditValidationIssue] = []
    for edit in edits:
        identity = identity_index.by_identifier.get(edit.target_id)
        if identity is None:
            issues.append(
                EditValidationIssue(
                    code="target_not_found",
                    target_kind=edit.target_kind,
                    target_id=edit.target_id,
                    message=f"Target does not exist: {edit.target_id}.",
                    expected_text=edit.expected_text,
                )
            )
            continue
        resolved.append(_ResolvedTextEdit(edit=edit, identity=identity))
    return resolved, issues


def _resolve_text_annotations_for_doc(
    doc: DocIR,
    annotations: list[TextAnnotation],
) -> tuple[list[_ResolvedTextAnnotation], list[AnnotationValidationIssue]]:
    identity_index = _build_target_identity_index(doc)
    resolved: list[_ResolvedTextAnnotation] = []
    issues: list[AnnotationValidationIssue] = []
    for annotation in annotations:
        identity = identity_index.by_identifier.get(annotation.target_id)
        if identity is None:
            issues.append(
                AnnotationValidationIssue(
                    code="target_not_found",
                    target_kind=annotation.target_kind,
                    target_id=annotation.target_id,
                    message=f"Annotation target does not exist: {annotation.target_id}.",
                    selected_text=annotation.selected_text,
                    occurrence_index=annotation.occurrence_index,
                )
            )
            continue
        resolved.append(_ResolvedTextAnnotation(annotation=annotation, identity=identity))
    return resolved, issues


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
    resolved_edits, resolution_issues = _resolve_text_edits_for_doc(doc, edits)
    issues.extend(resolution_issues)

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

    for resolved_edit in resolved_edits:
        issues.extend(_validate_single_text_edit(index, resolved_edit))

    return EditValidationResult(ok=not issues, issues=issues)


def _validate_single_text_edit(index, resolved_edit: _ResolvedTextEdit) -> list[EditValidationIssue]:
    edit = resolved_edit.edit
    target_id = resolved_edit.identity.node_id

    if edit.target_kind == "paragraph":
        paragraph = index.paragraphs.get(target_id)
        if paragraph is None:
            if target_id in index.runs:
                return [
                    EditValidationIssue(
                        code="target_kind_mismatch",
                        target_kind=edit.target_kind,
                        target_id=target_id,
                        message=f"{target_id} is a run target, not a paragraph target.",
                    )
                ]
            if target_id in index.cells:
                return [
                    EditValidationIssue(
                        code="target_kind_mismatch",
                        target_kind=edit.target_kind,
                        target_id=target_id,
                        message=f"{target_id} is a cell target, not a paragraph target.",
                    )
                ]
            return [
                EditValidationIssue(
                    code="target_not_found",
                    target_kind=edit.target_kind,
                    target_id=target_id,
                    message=f"Paragraph target does not exist: {target_id}.",
                )
            ]
        if paragraph.has_non_run_content:
            return [
                EditValidationIssue(
                    code="mixed_content_not_supported",
                    target_kind=edit.target_kind,
                    target_id=target_id,
                    message=f"Paragraph target has mixed content and is not safely writable: {target_id}.",
                    expected_text=edit.expected_text,
                    current_text=paragraph.text,
                )
            ]
        if paragraph.text != edit.expected_text:
            return [
                EditValidationIssue(
                    code="text_mismatch",
                    target_kind=edit.target_kind,
                    target_id=target_id,
                    message=f"Paragraph text mismatch for {target_id}.",
                    expected_text=edit.expected_text,
                    current_text=paragraph.text,
                )
            ]
        return []

    if edit.target_kind == "cell":
        cell = index.cells.get(target_id)
        if cell is None:
            if target_id in index.paragraphs:
                return [
                    EditValidationIssue(
                        code="target_kind_mismatch",
                        target_kind=edit.target_kind,
                        target_id=target_id,
                        message=f"{target_id} is a paragraph target, not a cell target.",
                    )
                ]
            if target_id in index.runs:
                return [
                    EditValidationIssue(
                        code="target_kind_mismatch",
                        target_kind=edit.target_kind,
                        target_id=target_id,
                        message=f"{target_id} is a run target, not a cell target.",
                    )
                ]
            return [
                EditValidationIssue(
                    code="target_not_found",
                    target_kind=edit.target_kind,
                    target_id=target_id,
                    message=f"Cell target does not exist: {target_id}.",
                )
            ]

        writable, writable_reason = _cell_writable(cell)
        if not writable:
            return [
                EditValidationIssue(
                    code="mixed_content_not_supported",
                    target_kind=edit.target_kind,
                    target_id=target_id,
                    message=writable_reason or f"Cell target is not safely writable: {target_id}.",
                    expected_text=edit.expected_text,
                    current_text=cell.text,
                )
            ]
        if cell.text != edit.expected_text:
            return [
                EditValidationIssue(
                    code="text_mismatch",
                    target_kind=edit.target_kind,
                    target_id=target_id,
                    message=f"Cell text mismatch for {target_id}.",
                    expected_text=edit.expected_text,
                    current_text=cell.text,
                )
            ]
        expected_paragraphs = len(cell.paragraphs)
        new_paragraphs = len(edit.new_text.split("\n"))
        if new_paragraphs != expected_paragraphs:
            return [
                EditValidationIssue(
                    code="paragraph_count_mismatch",
                    target_kind=edit.target_kind,
                    target_id=target_id,
                    message=(
                        f"Cell text replacement must preserve paragraph count for {target_id}: "
                        f"expected {expected_paragraphs} line(s), got {new_paragraphs}."
                    ),
                    expected_text=edit.expected_text,
                    current_text=cell.text,
                )
            ]
        return []

    run = index.runs.get(target_id)
    if run is None:
        if target_id in index.paragraphs:
            return [
                EditValidationIssue(
                    code="target_kind_mismatch",
                    target_kind=edit.target_kind,
                    target_id=target_id,
                    message=f"{target_id} is a paragraph target, not a run target.",
                )
            ]
        if target_id in index.cells:
            return [
                EditValidationIssue(
                    code="target_kind_mismatch",
                    target_kind=edit.target_kind,
                    target_id=target_id,
                    message=f"{target_id} is a cell target, not a run target.",
                )
            ]
        return [
            EditValidationIssue(
                code="target_not_found",
                target_kind=edit.target_kind,
                target_id=target_id,
                message=f"Run target does not exist: {target_id}.",
            )
        ]
    if run.text != edit.expected_text:
        return [
            EditValidationIssue(
                code="text_mismatch",
                target_kind=edit.target_kind,
                target_id=target_id,
                message=f"Run text mismatch for {target_id}.",
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
    doc.ensure_node_identity()
    paragraphs = list(_iter_doc_ir_paragraphs(doc.paragraphs))
    paragraph_map = {paragraph.node_id: paragraph for paragraph in paragraphs}
    run_map = {run.node_id: run for paragraph in paragraphs for run in paragraph.runs}
    resolved_annotations, resolution_issues = _resolve_text_annotations_for_doc(doc, annotations)

    issues: list[AnnotationValidationIssue] = list(resolution_issues)
    resolved: list[ResolvedTextAnnotation] = []

    for resolved_annotation in resolved_annotations:
        annotation = resolved_annotation.annotation
        target_id = resolved_annotation.identity.node_id
        if annotation.target_kind == "paragraph":
            paragraph = paragraph_map.get(target_id)
            if paragraph is None:
                if target_id in run_map:
                    issues.append(
                        AnnotationValidationIssue(
                            code="target_kind_mismatch",
                            target_kind=annotation.target_kind,
                            target_id=target_id,
                            message=f"{target_id} is a run target, not a paragraph target.",
                        )
                    )
                else:
                    issues.append(
                        AnnotationValidationIssue(
                            code="target_not_found",
                            target_kind=annotation.target_kind,
                            target_id=target_id,
                            message=f"Paragraph target does not exist: {target_id}.",
                        )
                    )
                continue
            if paragraph.tables or paragraph.images:
                issues.append(
                    AnnotationValidationIssue(
                        code="mixed_content_not_supported",
                        target_kind=annotation.target_kind,
                        target_id=target_id,
                        message=f"Paragraph annotations do not support mixed content: {target_id}.",
                        current_text=paragraph.text,
                    )
                )
                continue
            text = paragraph.text or ""
        else:
            run = run_map.get(target_id)
            if run is None:
                if target_id in paragraph_map:
                    issues.append(
                        AnnotationValidationIssue(
                            code="target_kind_mismatch",
                            target_kind=annotation.target_kind,
                            target_id=target_id,
                            message=f"{target_id} is a paragraph target, not a run target.",
                        )
                    )
                else:
                    issues.append(
                        AnnotationValidationIssue(
                            code="target_not_found",
                            target_kind=annotation.target_kind,
                            target_id=target_id,
                            message=f"Run target does not exist: {target_id}.",
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
                    target_id=target_id,
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
                target_id=target_id,
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
        node_id=paragraph.node_id,
        text=paragraph.text or "",
        page_number=paragraph.page_number,
        has_tables=bool(paragraph.tables),
        has_images=bool(paragraph.images),
        writable_as_paragraph=writable,
        native_anchor=paragraph.native_anchor,
        runs=_run_contexts(paragraph) if include_runs else [],
    )


def _run_contexts(paragraph: ParagraphIR) -> list[DocumentRunContext]:
    contexts: list[DocumentRunContext] = []
    cursor = 0
    for run in paragraph.runs:
        start = cursor
        end = start + len(run.text)
        contexts.append(
            DocumentRunContext(
                node_id=run.node_id,
                text=run.text,
                start=start,
                end=end,
                native_anchor=run.native_anchor,
            )
        )
        cursor = end
    return contexts


def _collect_editable_targets(
    doc: DocIR,
    *,
    target_kinds: list[TargetKind],
    only_writable: bool,
    exact_target_ids: set[str] | None = None,
    include_child_runs: bool = False,
    max_targets: int | None = None,
) -> list[EditableTarget]:
    doc.ensure_node_identity()
    results: list[EditableTarget] = []
    requested_parent_ids = exact_target_ids or set()
    paragraph_to_cell = {
        paragraph.node_id: cell
        for cell in _iter_doc_ir_cells(doc.paragraphs)
        for paragraph in cell.paragraphs
    }
    emitted_cell_ids: set[str] = set()
    for paragraph in _iter_doc_ir_paragraphs(doc.paragraphs):
        parent_cell = paragraph_to_cell.get(paragraph.node_id)
        if parent_cell is not None and parent_cell.node_id not in emitted_cell_ids:
            cell_requested = exact_target_ids is None or parent_cell.node_id in exact_target_ids
            cell_writable, cell_writable_reason = _cell_writable(parent_cell)
            if "cell" in target_kinds and cell_requested:
                if not only_writable or cell_writable:
                    results.append(
                        EditableTarget(
                            target_kind="cell",
                            target_id=parent_cell.node_id,
                            current_text=parent_cell.text,
                            page_number=paragraph.page_number,
                            native_anchor=parent_cell.native_anchor,
                            writable=cell_writable,
                            writable_reason=cell_writable_reason,
                        )
                    )
            emitted_cell_ids.add(parent_cell.node_id)

        paragraph_requested = exact_target_ids is None or paragraph.node_id in exact_target_ids
        writable, writable_reason = _paragraph_writable(paragraph)

        if "paragraph" in target_kinds and paragraph_requested:
            if not only_writable or writable:
                results.append(
                        EditableTarget(
                            target_kind="paragraph",
                            target_id=paragraph.node_id,
                            current_text=paragraph.text or "",
                            page_number=paragraph.page_number,
                            native_anchor=paragraph.native_anchor,
                        writable=writable,
                        writable_reason=writable_reason,
                    )
                )

        if "run" in target_kinds:
            for run in paragraph.runs:
                run_requested = exact_target_ids is None or run.node_id in exact_target_ids
                inherited_request = include_child_runs and (
                    paragraph.node_id in requested_parent_ids
                    or (parent_cell is not None and parent_cell.node_id in requested_parent_ids)
                )
                if run_requested or inherited_request:
                    results.append(
                        EditableTarget(
                            target_kind="run",
                            target_id=run.node_id,
                            parent_paragraph_id=paragraph.node_id,
                            current_text=run.text,
                            page_number=paragraph.page_number,
                            native_anchor=run.native_anchor,
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


def _cell_writable(cell) -> tuple[bool, str | None]:
    if not cell.paragraphs:
        return False, "Cell does not contain editable paragraphs."
    if any(_paragraph_has_non_run_content(paragraph) for paragraph in cell.paragraphs):
        return False, "Cell contains nested tables or images."
    if any(not paragraph.runs for paragraph in cell.paragraphs):
        return False, "Cell contains a paragraph without editable runs."
    return True, None


def _paragraph_has_non_run_content(paragraph) -> bool:
    if hasattr(paragraph, "has_non_run_content"):
        return bool(paragraph.has_non_run_content)
    return bool(paragraph.tables or paragraph.images)


def _resolve_requested_output_path(source: Path, request: ApplyTextEditsRequest) -> Path | None:
    if request.output_path is not None:
        return Path(request.output_path)
    if request.output_filename is not None:
        return source.with_name(request.output_filename)
    return None


def _same_path(left: Path, right: Path) -> bool:
    return left.expanduser().resolve(strict=False) == right.expanduser().resolve(strict=False)


def _to_canonical_text_edit(resolved_edit: _ResolvedTextEdit) -> TextEdit:
    edit = resolved_edit.edit
    return edit.model_copy(update={"target_id": resolved_edit.identity.node_id})


def _to_render_annotation(resolved_annotation: _ResolvedTextAnnotation) -> _Annotation:
    annotation = resolved_annotation.annotation
    return _Annotation(
        target_id=resolved_annotation.identity.node_id,
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
                f"Selected text does not occur in target {annotation.target_id}: "
                f"{annotation.selected_text!r}."
            ),
        }

    if annotation.occurrence_index is None:
        if len(matches) > 1:
            return 0, 0, "", None, {
                "code": "selected_text_ambiguous",
                "message": (
                    f"Selected text is ambiguous in target {annotation.target_id}; "
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
                f"{annotation.target_id}; found {len(matches)} match(es)."
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
    "ReadDocumentRequest",
    "ReadDocumentResult",
    "RenderReviewHtmlRequest",
    "ResolvedTextAnnotation",
    "ReviewHtmlResult",
    "TargetKind",
    "TextAnnotation",
    "TextEdit",
    "ValidateTextAnnotationsRequest",
    "ValidateTextEditsRequest",
    "apply_text_edits",
    "get_document_context",
    "list_editable_targets",
    "read_document",
    "render_review_html",
    "validate_text_annotations",
    "validate_text_edits",
]
