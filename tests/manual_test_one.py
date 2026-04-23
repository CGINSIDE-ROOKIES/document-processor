from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor import (  # noqa: E402
    ApplyTextEditsRequest,
    DocIR,
    DocumentInput,
    EditableTarget,
    GetDocumentContextRequest,
    ListEditableTargetsRequest,
    ReadDocumentRequest,
    RenderReviewHtmlRequest,
    TargetKind,
    TextAnnotation,
    TextEdit,
    ValidateTextAnnotationsRequest,
    ValidateTextEditsRequest,
    apply_text_edits,
    get_document_context,
    list_editable_targets,
    read_document,
    render_review_html,
    validate_text_annotations,
    validate_text_edits,
)


TARGET_KIND_PRIORITY = {
    "run": 0,
    "paragraph": 1,
    "cell": 2,
}


def validation_issues_to_dicts(validation) -> list[dict[str, Any]]:
    return [issue.model_dump(mode="json") for issue in validation.issues]


def require_validation_ok(label: str, validation) -> None:
    if validation.ok:
        return
    raise RuntimeError(f"{label} failed:\n{json.dumps(validation_issues_to_dicts(validation), indent=2)}")


def require_result_ok(label: str, result) -> None:
    require_validation_ok(label, result.validation)
    if result.ok:
        return
    raise RuntimeError(f"{label} failed without validation issues.")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def iter_paragraph_texts(paragraphs) -> Any:
    for paragraph in paragraphs:
        yield paragraph.text
        for table in paragraph.tables:
            yield from iter_table_texts(table)


def iter_table_texts(table) -> Any:
    for cell in table.cells:
        yield cell.text
        for paragraph in cell.paragraphs:
            yield paragraph.text
            for nested_table in paragraph.tables:
                yield from iter_table_texts(nested_table)


def collect_doc_texts(doc: DocIR) -> list[str]:
    return [text for text in iter_paragraph_texts(doc.paragraphs) if text]


def target_kinds_for_arg(target_kind: str) -> list[TargetKind]:
    if target_kind == "auto":
        return ["run", "paragraph", "cell"]
    return [target_kind]  # type: ignore[list-item]


def default_output_path(source_path: Path, output_dir: Path) -> Path:
    suffix = source_path.suffix or ".out"
    return output_dir / f"{source_path.stem}_manual_edit{suffix}"


def default_cell_output_path(source_path: Path, output_dir: Path) -> Path:
    suffix = source_path.suffix or ".out"
    return output_dir / f"{source_path.stem}_manual_cell_edit{suffix}"


def default_bytes_output_filename(source_path: Path, *, marker: str = "manual_edit_bytes") -> str:
    suffix = source_path.suffix or ".bin"
    return f"{source_path.stem}_{marker}{suffix}"


def select_edit_target(
    *,
    document: DocumentInput,
    target_kind: str,
    target_id: str | None,
    contains: str | None,
    target_index: int,
) -> tuple[EditableTarget, list[EditableTarget]]:
    target_result = list_editable_targets(
        ListEditableTargetsRequest(
            document=document,
            target_ids=[target_id] if target_id else [],
            target_kinds=target_kinds_for_arg(target_kind),
            only_writable=True,
            include_child_runs=False,
            max_targets=None,
        )
    )
    if target_result.missing_target_ids:
        raise RuntimeError(f"Missing target ids: {target_result.missing_target_ids}")

    candidates = [
        target
        for target in target_result.targets
        if target.current_text.strip()
        and (contains is None or contains in target.current_text)
    ]
    candidates.sort(key=lambda target: TARGET_KIND_PRIORITY[target.target_kind])
    if not candidates:
        raise RuntimeError(
            "No writable non-empty targets matched. Try --target-kind, --target-id, or --contains."
        )
    if target_index < 0 or target_index >= len(candidates):
        raise RuntimeError(f"--target-index {target_index} is out of range for {len(candidates)} candidate(s).")
    return candidates[target_index], candidates


def select_optional_cell_target(
    *,
    document: DocumentInput,
    target_id: str | None,
    contains: str | None,
    target_index: int,
    excluded_target_ids: set[str],
) -> tuple[EditableTarget | None, list[EditableTarget], str | None]:
    target_result = list_editable_targets(
        ListEditableTargetsRequest(
            document=document,
            target_ids=[target_id] if target_id else [],
            target_kinds=["cell"],
            only_writable=True,
            include_child_runs=False,
            max_targets=None,
        )
    )
    if target_result.missing_target_ids:
        raise RuntimeError(f"Missing cell target ids: {target_result.missing_target_ids}")

    candidates = [
        target
        for target in target_result.targets
        if target.target_kind == "cell"
        and target.current_text.strip()
        and target.target_id not in excluded_target_ids
        and (contains is None or contains in target.current_text)
    ]
    if not candidates:
        if target_id or contains:
            raise RuntimeError("No writable non-empty cell targets matched --cell-target-id or --cell-contains.")
        return None, [], "No writable non-empty cell target was found."
    if target_index < 0 or target_index >= len(candidates):
        raise RuntimeError(f"--cell-target-index {target_index} is out of range for {len(candidates)} cell candidate(s).")
    return candidates[target_index], candidates, None


def resolve_annotation_target(
    *,
    document: DocumentInput,
    edit_target: EditableTarget,
) -> EditableTarget:
    if edit_target.target_kind in {"paragraph", "run"}:
        return edit_target

    child_targets = list_editable_targets(
        ListEditableTargetsRequest(
            document=document,
            target_ids=[edit_target.target_id],
            target_kinds=["run"],
            include_child_runs=True,
            only_writable=True,
            max_targets=None,
        )
    )
    child_runs = [target for target in child_targets.targets if target.current_text.strip()]
    if not child_runs:
        raise RuntimeError(f"Cell target has no non-empty child run to annotate: {edit_target.target_id}")
    return child_runs[0]


def resolve_annotation_selection(
    *,
    annotation_target: EditableTarget,
    selected_text: str | None,
    occurrence_index: int | None,
) -> tuple[str | None, int | None, int]:
    text = annotation_target.current_text
    if selected_text is None:
        stripped = text.strip()
        if not stripped:
            return None, None, 0
        selected_text = stripped.split()[0] if stripped.split() else stripped[: min(len(stripped), 16)]

    occurrences = []
    search_from = 0
    while selected_text:
        index = text.find(selected_text, search_from)
        if index < 0:
            break
        occurrences.append(index)
        search_from = index + 1

    if not occurrences:
        raise RuntimeError(
            f"Annotation selected text {selected_text!r} was not found in target {annotation_target.target_id}."
        )

    resolved_occurrence_index = occurrence_index
    if resolved_occurrence_index is None and len(occurrences) > 1:
        resolved_occurrence_index = 0
    if resolved_occurrence_index is not None and resolved_occurrence_index >= len(occurrences):
        raise RuntimeError(
            f"--annotation-occurrence-index {resolved_occurrence_index} is out of range for "
            f"{len(occurrences)} occurrence(s) of {selected_text!r}."
        )
    return selected_text, resolved_occurrence_index, len(occurrences)


def write_review_html(
    *,
    document: DocumentInput,
    annotations: list[TextAnnotation],
    output_path: Path,
    title: str,
) -> list[dict[str, Any]]:
    annotation_validation = validate_text_annotations(
        ValidateTextAnnotationsRequest(
            document=document,
            annotations=annotations,
        )
    )
    require_validation_ok("validate_text_annotations", annotation_validation)

    review_result = render_review_html(
        RenderReviewHtmlRequest(
            document=document,
            annotations=annotations,
            title=title,
        )
    )
    require_result_ok("render_review_html", review_result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(review_result.html or "", encoding="utf-8")
    return [
        resolved.model_dump(mode="json")
        for resolved in review_result.resolved_annotations
    ]


def run_annotation_suite(
    *,
    document: DocumentInput,
    annotation_target: EditableTarget,
    output_dir: Path,
    source_stem: str,
    selected_text: str | None,
    occurrence_index: int | None,
    label: str,
    color: str,
    note: str,
) -> dict[str, Any]:
    full_review_path = output_dir / f"{source_stem}_review_full.html"
    selected_review_path = output_dir / f"{source_stem}_review_selected.html"
    target_kind = annotation_target.target_kind
    if target_kind not in {"paragraph", "run"}:
        raise RuntimeError(f"Annotation target must be a paragraph or run, got {target_kind!r}.")

    full_annotation = TextAnnotation(
        target_kind=target_kind,  # type: ignore[arg-type]
        target_id=annotation_target.target_id,
        label=f"{label} (full target)",
        color=color,
        note=note,
    )
    full_resolved = write_review_html(
        document=document,
        annotations=[full_annotation],
        output_path=full_review_path,
        title=f"Manual Review Full Target: {Path(document.source_path or document.source_name or 'document').name}",
    )

    resolved_selected_text, resolved_occurrence_index, occurrence_count = resolve_annotation_selection(
        annotation_target=annotation_target,
        selected_text=selected_text,
        occurrence_index=occurrence_index,
    )
    selected_annotation = TextAnnotation(
        target_kind=target_kind,  # type: ignore[arg-type]
        target_id=annotation_target.target_id,
        selected_text=resolved_selected_text,
        occurrence_index=resolved_occurrence_index,
        label=label,
        color=color,
        note=note,
    )
    selected_resolved = write_review_html(
        document=document,
        annotations=[selected_annotation],
        output_path=selected_review_path,
        title=f"Manual Review Selected Text: {Path(document.source_path or document.source_name or 'document').name}",
    )

    return {
        "full_target": {
            "review_html": str(full_review_path),
            "annotation": full_annotation.model_dump(mode="json"),
            "resolved": full_resolved,
        },
        "selected_text": {
            "review_html": str(selected_review_path),
            "annotation": selected_annotation.model_dump(mode="json"),
            "occurrence_count": occurrence_count,
            "resolved": selected_resolved,
        },
    }


def build_text_edit(*, edit_target: EditableTarget, replacement: str) -> TextEdit:
    return TextEdit(
        target_kind=edit_target.target_kind,
        target_id=edit_target.target_id,
        expected_text=edit_target.current_text,
        new_text=replacement,
        reason="Manual edit smoke check.",
    )


def validate_edit_suite(*, document: DocumentInput, edit: TextEdit) -> dict[str, Any]:
    edit_validation = validate_text_edits(
        ValidateTextEditsRequest(
            document=document,
            edits=[edit],
        )
    )
    require_validation_ok("validate_text_edits", edit_validation)
    return edit_validation.model_dump(mode="json")


def run_dry_run_edit_suite(*, document: DocumentInput, edit: TextEdit) -> dict[str, Any]:
    dry_run_result = apply_text_edits(
        ApplyTextEditsRequest(
            document=document,
            edits=[edit],
            dry_run=True,
            return_doc_ir=True,
        )
    )
    require_result_ok("apply_text_edits dry_run", dry_run_result)
    updated_texts = collect_doc_texts(dry_run_result.updated_doc_ir) if dry_run_result.updated_doc_ir else []
    return {
        "ok": dry_run_result.ok,
        "edits_applied": dry_run_result.edits_applied,
        "modified_target_ids": dry_run_result.modified_target_ids,
        "updated_doc_ir_contains_new_text": any(edit.new_text in text for text in updated_texts),
    }


def run_doc_ir_edit_suite(*, doc: DocIR, edit: TextEdit) -> dict[str, Any]:
    doc_ir_result = apply_text_edits(
        ApplyTextEditsRequest(
            document=DocumentInput(doc_ir=doc),
            edits=[edit],
            return_doc_ir=True,
        )
    )
    require_result_ok("apply_text_edits doc_ir", doc_ir_result)
    updated_texts = collect_doc_texts(doc_ir_result.updated_doc_ir) if doc_ir_result.updated_doc_ir else []
    if not any(edit.new_text in text for text in updated_texts):
        raise RuntimeError("DocIR edit result did not contain the replacement text.")
    return {
        "ok": doc_ir_result.ok,
        "edits_applied": doc_ir_result.edits_applied,
        "modified_target_ids": doc_ir_result.modified_target_ids,
        "modified_run_ids": doc_ir_result.modified_run_ids,
        "updated_texts": updated_texts[:50],
    }


def run_bytes_edit_suite(
    *,
    source_path: Path,
    source_doc_type: str,
    output_dir: Path,
    edit: TextEdit,
    output_filename: str | None = None,
) -> dict[str, Any]:
    resolved_output_filename = output_filename or default_bytes_output_filename(source_path)
    bytes_result = apply_text_edits(
        ApplyTextEditsRequest(
            document=DocumentInput(
                source_bytes=source_path.read_bytes(),
                source_doc_type=source_doc_type,  # type: ignore[arg-type]
                source_name=source_path.name,
            ),
            edits=[edit],
            output_filename=resolved_output_filename,
            return_doc_ir=True,
        )
    )
    require_result_ok("apply_text_edits bytes", bytes_result)
    if bytes_result.output_bytes is None:
        raise RuntimeError("Bytes-backed edit did not return output_bytes.")

    saved_output_path = output_dir / (bytes_result.output_filename or resolved_output_filename)
    saved_output_path.write_bytes(bytes_result.output_bytes)

    reparsed = DocIR.from_file(bytes_result.output_bytes)
    reparsed.ensure_node_identity()
    reparsed_texts = collect_doc_texts(reparsed)
    if not any(edit.new_text in text for text in reparsed_texts):
        raise RuntimeError(f"Bytes output did not contain expected text {edit.new_text!r}.")
    return {
        "ok": bytes_result.ok,
        "output_filename": bytes_result.output_filename,
        "saved_output": str(saved_output_path),
        "output_bytes": len(bytes_result.output_bytes),
        "edits_applied": bytes_result.edits_applied,
        "modified_target_ids": bytes_result.modified_target_ids,
        "modified_run_ids": bytes_result.modified_run_ids,
        "reparsed_texts": reparsed_texts[:50],
    }


def run_native_file_edit_suite(
    *,
    document: DocumentInput,
    edit: TextEdit,
    output_path: Path,
) -> tuple[Any, list[str]]:
    edit_result = apply_text_edits(
        ApplyTextEditsRequest(
            document=document,
            edits=[edit],
            output_path=str(output_path),
            return_doc_ir=True,
        )
    )
    require_result_ok("apply_text_edits", edit_result)

    actual_output_path = Path(edit_result.output_path or output_path)
    if not actual_output_path.exists():
        raise RuntimeError(f"Edited output file was not created: {actual_output_path}")

    reparsed = DocIR.from_file(actual_output_path)
    reparsed.ensure_node_identity()
    reparsed_texts = collect_doc_texts(reparsed)
    if not any(edit.new_text in text for text in reparsed_texts):
        raise RuntimeError(f"Edited output did not contain expected text {edit.new_text!r}: {actual_output_path}")
    return edit_result, reparsed_texts


def run_optional_cell_edit_suite(
    *,
    source_path: Path,
    source_doc_type: str,
    source_doc_ir: DocIR,
    document: DocumentInput,
    output_dir: Path,
    cell_output_path: Path,
    target_id: str | None,
    contains: str | None,
    target_index: int,
    replacement: str | None,
    append_text: str,
    excluded_target_ids: set[str],
) -> dict[str, Any]:
    cell_target, candidates, skip_reason = select_optional_cell_target(
        document=document,
        target_id=target_id,
        contains=contains,
        target_index=target_index,
        excluded_target_ids=excluded_target_ids,
    )
    if cell_target is None:
        return {
            "skipped": True,
            "reason": skip_reason,
            "candidate_count": len(candidates),
        }

    new_text = replacement if replacement is not None else f"{cell_target.current_text}{append_text}"
    edit = build_text_edit(edit_target=cell_target, replacement=new_text)
    edit_validation = validate_edit_suite(document=document, edit=edit)
    dry_run_suite = run_dry_run_edit_suite(document=document, edit=edit)
    doc_ir_suite = run_doc_ir_edit_suite(doc=source_doc_ir, edit=edit)
    bytes_suite = run_bytes_edit_suite(
        source_path=source_path,
        source_doc_type=source_doc_type,
        output_dir=output_dir,
        edit=edit,
        output_filename=default_bytes_output_filename(source_path, marker="manual_cell_edit_bytes"),
    )
    edit_result, reparsed_texts = run_native_file_edit_suite(
        document=document,
        edit=edit,
        output_path=cell_output_path,
    )
    return {
        "skipped": False,
        "target": cell_target.model_dump(mode="json"),
        "candidate_count": len(candidates),
        "first_candidates": [
            {
                "target_kind": target.target_kind,
                "target_id": target.target_id,
                "current_text": target.current_text,
                "native_anchor": target.native_anchor.model_dump(mode="json") if target.native_anchor else None,
            }
            for target in candidates[:20]
        ],
        "edit": edit.model_dump(mode="json"),
        "validation": edit_validation,
        "dry_run": dry_run_suite,
        "doc_ir": doc_ir_suite,
        "bytes": bytes_suite,
        "native_file": {
            "output_path": edit_result.output_path,
            "new_text": new_text,
            "edits_applied": edit_result.edits_applied,
            "modified_target_ids": edit_result.modified_target_ids,
            "modified_run_ids": edit_result.modified_run_ids,
            "warnings": edit_result.warnings,
            "reparsed_texts": reparsed_texts[:50],
        },
    }


def run_manual_file_flow(
    *,
    source_path: Path,
    source_doc_type: str,
    output_dir: Path,
    output_path: Path | None,
    target_kind: str,
    target_id: str | None,
    contains: str | None,
    target_index: int,
    replacement: str | None,
    append_text: str,
    cell_target_id: str | None,
    cell_contains: str | None,
    cell_target_index: int,
    cell_replacement: str | None,
    cell_append_text: str,
    annotation_selected_text: str | None,
    annotation_occurrence_index: int | None,
    annotation_label: str,
    annotation_color: str,
    annotation_note: str,
    preview_limit: int,
) -> dict[str, Any]:
    if not source_path.exists():
        raise FileNotFoundError(f"Source file does not exist: {source_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    requested_output_path = output_path or default_output_path(source_path, output_dir)
    requested_cell_output_path = default_cell_output_path(source_path, output_dir)
    summary_path = output_dir / f"{source_path.stem}_summary.json"
    document = DocumentInput(
        source_path=str(source_path),
        source_doc_type=source_doc_type,  # type: ignore[arg-type]
    )
    source_doc_ir = DocIR.from_file(source_path, doc_type=source_doc_type)  # type: ignore[arg-type]
    source_doc_ir.ensure_node_identity()

    read_result = read_document(
        ReadDocumentRequest(
            document=document,
            include_runs=True,
            limit=preview_limit,
        )
    )
    edit_target, candidates = select_edit_target(
        document=document,
        target_kind=target_kind,
        target_id=target_id,
        contains=contains,
        target_index=target_index,
    )
    annotation_target = resolve_annotation_target(
        document=document,
        edit_target=edit_target,
    )
    context_result = get_document_context(
        GetDocumentContextRequest(
            document=document,
            target_ids=[edit_target.target_id, annotation_target.target_id],
            before=1,
            after=1,
            include_runs=True,
        )
    )
    annotation_suite = run_annotation_suite(
        document=document,
        annotation_target=annotation_target,
        output_dir=output_dir,
        source_stem=source_path.stem,
        selected_text=annotation_selected_text,
        occurrence_index=annotation_occurrence_index,
        label=annotation_label,
        color=annotation_color,
        note=annotation_note,
    )

    new_text = replacement if replacement is not None else f"{edit_target.current_text}{append_text}"
    edit = build_text_edit(edit_target=edit_target, replacement=new_text)
    edit_validation = validate_edit_suite(document=document, edit=edit)
    dry_run_suite = run_dry_run_edit_suite(document=document, edit=edit)
    doc_ir_suite = run_doc_ir_edit_suite(doc=source_doc_ir, edit=edit)
    bytes_suite = run_bytes_edit_suite(
        source_path=source_path,
        source_doc_type=source_doc_type,
        output_dir=output_dir,
        edit=edit,
    )
    edit_result, reparsed_texts = run_native_file_edit_suite(
        document=document,
        edit=edit,
        output_path=requested_output_path,
    )
    cell_edit_suite = run_optional_cell_edit_suite(
        source_path=source_path,
        source_doc_type=source_doc_type,
        source_doc_ir=source_doc_ir,
        document=document,
        output_dir=output_dir,
        cell_output_path=requested_cell_output_path,
        target_id=cell_target_id,
        contains=cell_contains,
        target_index=cell_target_index,
        replacement=cell_replacement,
        append_text=cell_append_text,
        excluded_target_ids={edit_target.target_id},
    )

    summary = {
        "ok": True,
        "source": {
            "path": str(source_path),
            "requested_doc_type": source_doc_type,
        },
        "paths": {
            "requested_output": str(requested_output_path),
            "edited_output": edit_result.output_path,
            "requested_cell_output": str(requested_cell_output_path),
            "cell_edited_output": None
            if cell_edit_suite["skipped"]
            else cell_edit_suite["native_file"]["output_path"],
            "review_html_full": annotation_suite["full_target"]["review_html"],
            "review_html_selected": annotation_suite["selected_text"]["review_html"],
            "bytes_output": bytes_suite["saved_output"],
            "cell_bytes_output": None
            if cell_edit_suite["skipped"]
            else cell_edit_suite["bytes"]["saved_output"],
            "summary_json": str(summary_path),
        },
        "read_document_preview": {
            "start": read_result.start,
            "limit": read_result.limit,
            "total_paragraphs": read_result.total_paragraphs,
            "next_start": read_result.next_start,
            "paragraphs": [
                {
                    "node_id": paragraph.node_id,
                    "text": paragraph.text,
                    "runs": [
                        {
                            "node_id": run.node_id,
                            "text": run.text,
                            "start": run.start,
                            "end": run.end,
                        }
                        for run in paragraph.runs
                    ],
                }
                for paragraph in read_result.paragraphs
            ],
        },
        "document_context": {
            "missing_target_ids": context_result.missing_target_ids,
            "paragraphs": [
                {
                    "node_id": paragraph.node_id,
                    "text": paragraph.text,
                    "runs": [
                        {
                            "node_id": run.node_id,
                            "text": run.text,
                            "start": run.start,
                            "end": run.end,
                        }
                        for run in paragraph.runs
                    ],
                }
                for paragraph in context_result.paragraphs
            ],
        },
        "target_selection": {
            "target_kind_arg": target_kind,
            "target_id_arg": target_id,
            "contains_arg": contains,
            "target_index_arg": target_index,
            "candidate_count": len(candidates),
            "first_candidates": [
                {
                    "target_kind": target.target_kind,
                    "target_id": target.target_id,
                    "current_text": target.current_text,
                    "native_anchor": target.native_anchor.model_dump(mode="json") if target.native_anchor else None,
                }
                for target in candidates[:20]
            ],
        },
        "selected_targets": {
            "edit": edit_target.model_dump(mode="json"),
            "annotation": annotation_target.model_dump(mode="json"),
        },
        "annotation_suites": annotation_suite,
        "edit_suites": {
            "edit": edit.model_dump(mode="json"),
            "validation": edit_validation,
            "dry_run": dry_run_suite,
            "doc_ir": doc_ir_suite,
            "bytes": bytes_suite,
            "native_file": {
                "new_text": new_text,
                "edits_applied": edit_result.edits_applied,
                "modified_target_ids": edit_result.modified_target_ids,
                "modified_run_ids": edit_result.modified_run_ids,
                "warnings": edit_result.warnings,
                "reparsed_texts": reparsed_texts[:50],
            },
        },
        "cell_edit_suite": cell_edit_suite,
        # Kept as a compact alias for terminal output and quick manual inspection.
        "edit": {
            "new_text": new_text,
            "edits_applied": edit_result.edits_applied,
            "modified_target_ids": edit_result.modified_target_ids,
            "modified_run_ids": edit_result.modified_run_ids,
            "warnings": edit_result.warnings,
        },
        "reparsed_texts": reparsed_texts[:50],
    }
    write_json(summary_path, summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manually annotate, edit, and export one existing DOCX/HWPX/HWP file through the public API.",
    )
    parser.add_argument(
        "source_path",
        type=Path,
        help="Existing .docx, .hwpx, or .hwp file to process.",
    )
    parser.add_argument(
        "--source-doc-type",
        choices=["auto", "docx", "hwpx", "hwp"],
        default="auto",
        help="Override source type inference.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "tests" / "results" / "manual_test_one",
        help="Directory for edited output, review HTML, and summary JSON.",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Exact requested output path. HWP outputs are normalized to .hwpx by the API.",
    )
    parser.add_argument(
        "--target-kind",
        choices=["auto", "run", "paragraph", "cell"],
        default="auto",
        help="Editable target kind to select. auto prefers runs, then paragraphs, then cells.",
    )
    parser.add_argument(
        "--target-id",
        default=None,
        help="Exact stable target id to edit.",
    )
    parser.add_argument(
        "--contains",
        default=None,
        help="Select the first writable target whose current text contains this substring.",
    )
    parser.add_argument(
        "--target-index",
        type=int,
        default=0,
        help="Zero-based index among matched writable targets.",
    )
    parser.add_argument(
        "--replacement",
        default=None,
        help="Replacement text for the selected target. Defaults to appending --append-text.",
    )
    parser.add_argument(
        "--append-text",
        default=" [manual edit]",
        help="Text appended when --replacement is omitted.",
    )
    parser.add_argument(
        "--cell-target-id",
        default=None,
        help="Exact stable cell target id for the optional cell edit suite.",
    )
    parser.add_argument(
        "--cell-contains",
        default=None,
        help="Select the optional cell edit target by substring.",
    )
    parser.add_argument(
        "--cell-target-index",
        type=int,
        default=0,
        help="Zero-based index among matched writable cell targets.",
    )
    parser.add_argument(
        "--cell-replacement",
        default=None,
        help="Replacement text for the optional cell edit. Defaults to appending --cell-append-text.",
    )
    parser.add_argument(
        "--cell-append-text",
        default=" [manual cell edit]",
        help="Text appended to the selected cell when --cell-replacement is omitted.",
    )
    parser.add_argument(
        "--annotation-selected-text",
        default=None,
        help="Substring to annotate in the selected annotation suite. Defaults to the first token in the annotation target.",
    )
    parser.add_argument(
        "--annotation-occurrence-index",
        type=int,
        default=None,
        help="Zero-based occurrence index for --annotation-selected-text when it appears multiple times.",
    )
    parser.add_argument(
        "--annotation-label",
        default="Manual note",
        help="Label shown in the generated review HTML.",
    )
    parser.add_argument(
        "--annotation-color",
        default="#FFE08A",
        help="Highlight color for the generated review HTML.",
    )
    parser.add_argument(
        "--annotation-note",
        default="Manual annotation before applying the edit.",
        help="Annotation note shown in the generated review HTML.",
    )
    parser.add_argument(
        "--preview-limit",
        type=int,
        default=25,
        help="Number of paragraphs to include in the read_document preview summary.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = run_manual_file_flow(
        source_path=args.source_path,
        source_doc_type=args.source_doc_type,
        output_dir=args.output_dir,
        output_path=args.output_path,
        target_kind=args.target_kind,
        target_id=args.target_id,
        contains=args.contains,
        target_index=args.target_index,
        replacement=args.replacement,
        append_text=args.append_text,
        cell_target_id=args.cell_target_id,
        cell_contains=args.cell_contains,
        cell_target_index=args.cell_target_index,
        cell_replacement=args.cell_replacement,
        cell_append_text=args.cell_append_text,
        annotation_selected_text=args.annotation_selected_text,
        annotation_occurrence_index=args.annotation_occurrence_index,
        annotation_label=args.annotation_label,
        annotation_color=args.annotation_color,
        annotation_note=args.annotation_note,
        preview_limit=args.preview_limit,
    )

    print("Manual file flow completed.")
    print(f"Source: {summary['source']['path']}")
    print(f"Edited output: {summary['paths']['edited_output']}")
    print(f"Bytes output: {summary['paths']['bytes_output']}")
    if summary["cell_edit_suite"]["skipped"]:
        print(f"Cell edit: skipped ({summary['cell_edit_suite']['reason']})")
    else:
        print(f"Cell edited output: {summary['paths']['cell_edited_output']}")
        print(f"Cell bytes output: {summary['paths']['cell_bytes_output']}")
    print(f"Review HTML full: {summary['paths']['review_html_full']}")
    print(f"Review HTML selected: {summary['paths']['review_html_selected']}")
    print(f"Summary JSON: {summary['paths']['summary_json']}")
    print(
        "Edited target: "
        f"{summary['selected_targets']['edit']['target_kind']} "
        f"{summary['selected_targets']['edit']['target_id']}"
    )
    print(f"Annotation target: {summary['selected_targets']['annotation']['target_id']}")
    print(f"Modified target ids: {', '.join(summary['edit']['modified_target_ids'])}")
    print(
        "Suites: read_document, get_document_context, list_editable_targets, "
        "validate/render annotations, validate/dry-run/doc_ir/bytes/native edits"
    )
    if summary["edit"]["warnings"]:
        print(f"Warnings: {'; '.join(summary['edit']['warnings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
