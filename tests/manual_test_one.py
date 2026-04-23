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
    DocIR,
    DocumentInput,
    EditableTarget,
    StructuralEdit,
    TargetKind,
    TextAnnotation,
    TextEdit,
    apply_document_edits,
    get_document_context,
    list_editable_targets,
    read_document,
    render_review_html,
    validate_document_edits,
    validate_text_annotations,
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


def default_structural_output_path(source_path: Path, output_dir: Path) -> Path:
    suffix = source_path.suffix or ".out"
    return output_dir / f"{source_path.stem}_manual_structural_edit{suffix}"


def default_bytes_output_filename(source_path: Path, *, marker: str = "manual_edit_bytes") -> str:
    suffix = source_path.suffix or ".bin"
    return f"{source_path.stem}_{marker}{suffix}"


def output_doc_type_for_name(output_name: str | Path | None, source_doc_type: str) -> str:
    if output_name is not None:
        suffix = Path(output_name).suffix.lower()
        if suffix == ".docx":
            return "docx"
        if suffix == ".hwpx":
            return "hwpx"
        if suffix == ".hwp":
            return "hwp"
    if source_doc_type == "hwp":
        return "hwpx"
    return source_doc_type


def parse_output_doc_ir(source, *, output_name: str | Path | None, source_doc_type: str) -> DocIR:
    doc_type = output_doc_type_for_name(output_name, source_doc_type)
    doc = DocIR.from_file(source, doc_type=doc_type)  # type: ignore[arg-type]
    return doc.ensure_node_identity()


def select_edit_target(
    *,
    document: DocumentInput,
    target_kind: str,
    target_id: str | None,
    contains: str | None,
    target_index: int,
) -> tuple[EditableTarget, list[EditableTarget]]:
    target_result = list_editable_targets(
        document=document,
        target_ids=[target_id] if target_id else [],
        target_kinds=target_kinds_for_arg(target_kind),
        only_writable=True,
        include_child_runs=False,
        max_targets=None,
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
        document=document,
        target_ids=[target_id] if target_id else [],
        target_kinds=["cell"],
        only_writable=True,
        include_child_runs=False,
        max_targets=None,
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
        document=document,
        target_ids=[edit_target.target_id],
        target_kinds=["run"],
        include_child_runs=True,
        only_writable=True,
        max_targets=None,
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
        document=document,
        annotations=annotations,
    )
    require_validation_ok("validate_text_annotations", annotation_validation)

    review_result = render_review_html(
        document=document,
        annotations=annotations,
        title=title,
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
    edit_validation = validate_document_edits(
        document=document,
        edits=[edit],
    )
    require_validation_ok("validate_document_edits", edit_validation)
    return edit_validation.model_dump(mode="json")


def run_dry_run_edit_suite(*, document: DocumentInput, edit: TextEdit) -> dict[str, Any]:
    dry_run_result = apply_document_edits(
        document=document,
        edits=[edit],
        dry_run=True,
        return_doc_ir=True,
    )
    require_result_ok("apply_document_edits dry_run", dry_run_result)
    updated_texts = collect_doc_texts(dry_run_result.updated_doc_ir) if dry_run_result.updated_doc_ir else []
    return {
        "ok": dry_run_result.ok,
        "edits_applied": dry_run_result.edits_applied,
        "modified_target_ids": dry_run_result.modified_target_ids,
        "updated_doc_ir_contains_new_text": any(edit.new_text in text for text in updated_texts),
    }


def run_doc_ir_edit_suite(*, doc: DocIR, edit: TextEdit) -> dict[str, Any]:
    doc_ir_result = apply_document_edits(
        document=DocumentInput(doc_ir=doc),
        edits=[edit],
        return_doc_ir=True,
    )
    require_result_ok("apply_document_edits doc_ir", doc_ir_result)
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
    bytes_result = apply_document_edits(
        document=DocumentInput(
            source_bytes=source_path.read_bytes(),
            source_doc_type=source_doc_type,  # type: ignore[arg-type]
            source_name=source_path.name,
        ),
        edits=[edit],
        output_filename=resolved_output_filename,
        return_doc_ir=True,
    )
    require_result_ok("apply_document_edits bytes", bytes_result)
    if bytes_result.output_bytes is None:
        raise RuntimeError("Bytes-backed edit did not return output_bytes.")

    saved_output_path = output_dir / (bytes_result.output_filename or resolved_output_filename)
    saved_output_path.write_bytes(bytes_result.output_bytes)

    reparsed = parse_output_doc_ir(
        bytes_result.output_bytes,
        output_name=bytes_result.output_filename or resolved_output_filename,
        source_doc_type=source_doc_type,
    )
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
    source_doc_type: str,
    edit: TextEdit,
    output_path: Path,
) -> tuple[Any, list[str]]:
    edit_result = apply_document_edits(
        document=document,
        edits=[edit],
        output_path=str(output_path),
        return_doc_ir=True,
    )
    require_result_ok("apply_document_edits", edit_result)

    actual_output_path = Path(edit_result.output_path or output_path)
    if not actual_output_path.exists():
        raise RuntimeError(f"Edited output file was not created: {actual_output_path}")

    reparsed = parse_output_doc_ir(
        actual_output_path,
        output_name=actual_output_path,
        source_doc_type=source_doc_type,
    )
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
        source_doc_type=source_doc_type,
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


def iter_doc_ir_tables(doc: DocIR) -> Any:
    for paragraph in doc.paragraphs:
        yield from iter_paragraph_tables(paragraph)


def iter_paragraph_tables(paragraph) -> Any:
    for table in paragraph.tables:
        yield table
        for cell in table.cells:
            for cell_paragraph in cell.paragraphs:
                yield from iter_paragraph_tables(cell_paragraph)


def table_shape(table) -> tuple[int, int]:
    row_count = table.row_count or max((cell.row_index for cell in table.cells), default=0)
    col_count = table.col_count or max((cell.col_index for cell in table.cells), default=0)
    return row_count, col_count


def table_is_rectangular(table) -> bool:
    row_count, col_count = table_shape(table)
    if row_count <= 0 or col_count <= 0:
        return False
    coordinates = {(cell.row_index, cell.col_index) for cell in table.cells}
    return len(coordinates) == row_count * col_count and all(
        (row_index, col_index) in coordinates
        for row_index in range(1, row_count + 1)
        for col_index in range(1, col_count + 1)
    )


def find_doc_ir_cell_table(doc: DocIR, cell_id: str) -> tuple[Any, Any] | None:
    for table in iter_doc_ir_tables(doc):
        for cell in table.cells:
            if cell.node_id == cell_id:
                return table, cell
    return None


def select_structural_paragraph_target(
    *,
    document: DocumentInput,
    preferred_target: EditableTarget,
) -> tuple[EditableTarget, list[EditableTarget]]:
    if preferred_target.target_kind == "paragraph":
        return preferred_target, [preferred_target]

    if preferred_target.parent_paragraph_id:
        parent_result = list_editable_targets(
            document=document,
            target_ids=[preferred_target.parent_paragraph_id],
            target_kinds=["paragraph"],
            only_writable=False,
            max_targets=None,
        )
        if parent_result.targets:
            return parent_result.targets[0], parent_result.targets

    result = list_editable_targets(
        document=document,
        target_kinds=["paragraph"],
        only_writable=True,
        max_targets=None,
    )
    candidates = [target for target in result.targets if target.current_text.strip()]
    if not candidates:
        fallback = list_editable_targets(
            document=document,
            target_kinds=["paragraph"],
            only_writable=False,
            max_targets=None,
        )
        candidates = fallback.targets
    if not candidates:
        raise RuntimeError("No paragraph target was available for structural edit tests.")
    return candidates[0], candidates


def select_structural_cell_target(
    *,
    document: DocumentInput,
    source_doc_ir: DocIR,
    target_id: str | None,
    contains: str | None,
    target_index: int,
) -> tuple[EditableTarget | None, Any | None, Any | None, list[EditableTarget], str | None]:
    result = list_editable_targets(
        document=document,
        target_ids=[target_id] if target_id else [],
        target_kinds=["cell"],
        only_writable=True,
        max_targets=None,
    )
    if result.missing_target_ids:
        raise RuntimeError(f"Missing structural cell target ids: {result.missing_target_ids}")

    candidates: list[tuple[EditableTarget, Any, Any]] = []
    for target in result.targets:
        if contains is not None and contains not in target.current_text:
            continue
        table_and_cell = find_doc_ir_cell_table(source_doc_ir, target.target_id)
        if table_and_cell is None:
            continue
        table, cell = table_and_cell
        candidates.append((target, table, cell))

    if not candidates:
        if target_id or contains:
            raise RuntimeError("No structural cell target matched --cell-target-id or --cell-contains.")
        return None, None, None, result.targets, "No table cell target was found."

    if target_index < 0 or target_index >= len(candidates):
        raise RuntimeError(
            f"--cell-target-index {target_index} is out of range for {len(candidates)} structural cell candidate(s)."
        )
    target, table, cell = candidates[target_index]
    return target, table, cell, [candidate[0] for candidate in candidates], None


def build_structural_operations(
    *,
    paragraph_target: EditableTarget,
    cell_target: EditableTarget | None,
    cell_table,
    paragraph_text: str,
    run_text: str,
    cell_text: str,
    table_rows: list[list[str]],
) -> tuple[list[StructuralEdit], list[str], dict[str, Any]]:
    operations = [
        StructuralEdit(
            operation="insert_run",
            target_id=paragraph_target.target_id,
            position="end",
            text=run_text,
            reason="Manual structural run insertion smoke check.",
        ),
        StructuralEdit(
            operation="insert_table",
            target_id=paragraph_target.target_id,
            position="after",
            rows=table_rows,
            reason="Manual structural table insertion smoke check.",
        ),
        StructuralEdit(
            operation="insert_paragraph",
            target_id=paragraph_target.target_id,
            position="after",
            text=paragraph_text,
            reason="Manual structural paragraph insertion smoke check.",
        ),
    ]
    expected_markers = [paragraph_text, run_text, table_rows[-1][-1]]
    table_summary: dict[str, Any] = {
        "cell_operations_added": False,
        "axis_operations_added": False,
        "axis_skip_reason": None,
    }

    if cell_target is None or cell_table is None:
        table_summary["cell_skip_reason"] = "No existing table cell was available."
        return operations, expected_markers, table_summary

    operations.append(
        StructuralEdit(
            operation="set_cell_text",
            target_id=cell_target.target_id,
            expected_text=cell_target.current_text,
            text=cell_text,
            reason="Manual structural cell replacement smoke check.",
        )
    )
    expected_markers.append(cell_text)
    table_summary["cell_operations_added"] = True

    row_count, col_count = table_shape(cell_table)
    table_summary["source_table_shape"] = {
        "row_count": row_count,
        "col_count": col_count,
        "rectangular": table_is_rectangular(cell_table),
    }
    if not table_is_rectangular(cell_table):
        table_summary["axis_skip_reason"] = "Existing table is not rectangular."
        return operations, expected_markers, table_summary
    if row_count <= 0 or col_count <= 0:
        table_summary["axis_skip_reason"] = "Existing table has no measurable shape."
        return operations, expected_markers, table_summary

    row_values = [f"Manual row c{index}" for index in range(1, col_count + 1)]
    operations.append(
        StructuralEdit(
            operation="insert_table_row",
            target_id=cell_target.target_id,
            position="after",
            values=row_values,
            reason="Manual structural table row insertion smoke check.",
        )
    )
    expected_markers.append(row_values[-1])

    column_values = [f"Manual column r{index}" for index in range(1, row_count + 2)]
    operations.append(
        StructuralEdit(
            operation="insert_table_column",
            target_id=cell_target.target_id,
            position="after",
            values=column_values,
            reason="Manual structural table column insertion smoke check.",
        )
    )
    expected_markers.append(column_values[-1])
    table_summary["axis_operations_added"] = True
    return operations, expected_markers, table_summary


def operation_summary(result) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "operations_applied": result.operations_applied,
        "modified_target_ids": result.modified_target_ids,
        "created_target_ids": result.created_target_ids,
        "removed_target_ids": result.removed_target_ids,
        "warnings": result.warnings,
    }


def assert_doc_contains_markers(doc: DocIR, markers: list[str], *, label: str) -> list[str]:
    texts = collect_doc_texts(doc)
    missing = [marker for marker in markers if marker and not any(marker in text for text in texts)]
    if missing:
        raise RuntimeError(f"{label} did not contain expected marker(s): {missing}")
    return texts


def build_structural_removal_probe_operations(
    *,
    document: DocumentInput,
    source_doc_ir: DocIR,
    paragraph_target: EditableTarget,
    cell_target: EditableTarget | None,
) -> list[StructuralEdit]:
    operations: list[StructuralEdit] = []

    run_result = list_editable_targets(
        document=document,
        target_kinds=["run"],
        only_writable=True,
        max_targets=None,
    )
    removable_runs = [target for target in run_result.targets if target.current_text.strip()]
    if removable_runs:
        run_target = removable_runs[-1]
        operations.append(
            StructuralEdit(
                operation="remove_run",
                target_id=run_target.target_id,
                expected_text=run_target.current_text,
                reason="Manual structural dry-run remove_run probe.",
            )
        )

    paragraph_result = list_editable_targets(
        document=document,
        target_kinds=["paragraph"],
        only_writable=False,
        max_targets=None,
    )
    removable_paragraphs = [
        target
        for target in paragraph_result.targets
        if target.target_id != paragraph_target.target_id and target.current_text.strip()
    ]
    if removable_paragraphs:
        paragraph = removable_paragraphs[-1]
        operations.append(
            StructuralEdit(
                operation="remove_paragraph",
                target_id=paragraph.target_id,
                expected_text=paragraph.current_text,
                reason="Manual structural dry-run remove_paragraph probe.",
            )
        )

    table_result = list_editable_targets(
        document=document,
        target_kinds=["table"],
        only_writable=False,
        max_targets=None,
    )
    if table_result.targets:
        operations.append(
            StructuralEdit(
                operation="remove_table",
                target_id=table_result.targets[0].target_id,
                reason="Manual structural dry-run remove_table probe.",
            )
        )

    if cell_target is not None:
        table_and_cell = find_doc_ir_cell_table(source_doc_ir, cell_target.target_id)
        if table_and_cell is not None:
            table, _ = table_and_cell
            row_count, col_count = table_shape(table)
            if table_is_rectangular(table) and row_count > 1:
                operations.append(
                    StructuralEdit(
                        operation="remove_table_row",
                        target_id=cell_target.target_id,
                        position="after",
                        reason="Manual structural dry-run remove_table_row probe.",
                    )
                )
            if table_is_rectangular(table) and col_count > 1:
                operations.append(
                    StructuralEdit(
                        operation="remove_table_column",
                        target_id=cell_target.target_id,
                        position="after",
                        reason="Manual structural dry-run remove_table_column probe.",
                    )
                )

    return operations


def run_structural_removal_probe(
    *,
    document: DocumentInput,
    operations: list[StructuralEdit],
) -> list[dict[str, Any]]:
    probe_results = []
    for operation in operations:
        validation = validate_document_edits(
            document=document,
            edits=[operation],
        )
        entry: dict[str, Any] = {
            "operation": operation.model_dump(mode="json"),
            "validation": validation.model_dump(mode="json"),
        }
        if validation.ok:
            dry_run = apply_document_edits(
                document=document,
                edits=[operation],
                dry_run=True,
                return_doc_ir=True,
            )
            require_result_ok(f"apply_document_edits dry-run {operation.operation}", dry_run)
            entry["dry_run"] = operation_summary(dry_run)
        probe_results.append(entry)
    return probe_results


def run_structural_edit_suite(
    *,
    source_path: Path,
    source_doc_type: str,
    source_doc_ir: DocIR,
    document: DocumentInput,
    output_dir: Path,
    structural_output_path: Path,
    preferred_target: EditableTarget,
    cell_target_id: str | None,
    cell_contains: str | None,
    cell_target_index: int,
    paragraph_text: str,
    run_text: str,
    cell_text: str,
) -> dict[str, Any]:
    paragraph_target, paragraph_candidates = select_structural_paragraph_target(
        document=document,
        preferred_target=preferred_target,
    )
    cell_target, cell_table, cell, cell_candidates, cell_skip_reason = select_structural_cell_target(
        document=document,
        source_doc_ir=source_doc_ir,
        target_id=cell_target_id,
        contains=cell_contains,
        target_index=cell_target_index,
    )
    table_rows = [
        ["Manual table A1", "Manual table B1"],
        ["Manual table A2", "Manual table B2"],
    ]
    operations, expected_markers, table_summary = build_structural_operations(
        paragraph_target=paragraph_target,
        cell_target=cell_target,
        cell_table=cell_table,
        paragraph_text=paragraph_text,
        run_text=run_text,
        cell_text=cell_text,
        table_rows=table_rows,
    )

    validation = validate_document_edits(
        document=document,
        edits=operations,
    )
    require_validation_ok("validate_document_edits", validation)

    dry_run_result = apply_document_edits(
        document=document,
        edits=operations,
        dry_run=True,
        return_doc_ir=True,
    )
    require_result_ok("apply_document_edits structural dry_run", dry_run_result)
    if dry_run_result.updated_doc_ir is not None:
        assert_doc_contains_markers(dry_run_result.updated_doc_ir, expected_markers, label="Structural dry run")

    doc_ir_result = apply_document_edits(
        document=DocumentInput(doc_ir=source_doc_ir),
        edits=operations,
        return_doc_ir=True,
    )
    require_result_ok("apply_document_edits structural doc_ir", doc_ir_result)
    doc_ir_texts = []
    if doc_ir_result.updated_doc_ir is not None:
        doc_ir_texts = assert_doc_contains_markers(
            doc_ir_result.updated_doc_ir,
            expected_markers,
            label="Structural DocIR result",
        )

    bytes_output_filename = default_bytes_output_filename(source_path, marker="manual_structural_edit_bytes")
    bytes_result = apply_document_edits(
        document=DocumentInput(
            source_bytes=source_path.read_bytes(),
            source_doc_type=source_doc_type,  # type: ignore[arg-type]
            source_name=source_path.name,
        ),
        edits=operations,
        output_filename=bytes_output_filename,
        return_doc_ir=True,
    )
    require_result_ok("apply_document_edits structural bytes", bytes_result)
    if bytes_result.output_bytes is None:
        raise RuntimeError("Bytes-backed structural edit did not return output_bytes.")
    saved_bytes_output_path = output_dir / (bytes_result.output_filename or bytes_output_filename)
    saved_bytes_output_path.write_bytes(bytes_result.output_bytes)
    bytes_reparsed = parse_output_doc_ir(
        bytes_result.output_bytes,
        output_name=bytes_result.output_filename or bytes_output_filename,
        source_doc_type=source_doc_type,
    )
    bytes_reparsed_texts = assert_doc_contains_markers(
        bytes_reparsed,
        expected_markers,
        label="Structural bytes output",
    )

    native_result = apply_document_edits(
        document=document,
        edits=operations,
        output_path=str(structural_output_path),
        return_doc_ir=True,
    )
    require_result_ok("apply_document_edits structural native_file", native_result)
    actual_structural_output_path = Path(native_result.output_path or structural_output_path)
    if not actual_structural_output_path.exists():
        raise RuntimeError(f"Structural edited output file was not created: {actual_structural_output_path}")
    native_reparsed = parse_output_doc_ir(
        actual_structural_output_path,
        output_name=actual_structural_output_path,
        source_doc_type=source_doc_type,
    )
    native_reparsed_texts = assert_doc_contains_markers(
        native_reparsed,
        expected_markers,
        label="Structural native output",
    )

    removal_probe = run_structural_removal_probe(
        document=document,
        operations=build_structural_removal_probe_operations(
            document=document,
            source_doc_ir=source_doc_ir,
            paragraph_target=paragraph_target,
            cell_target=cell_target,
        ),
    )

    return {
        "skipped": False,
        "paragraph_target": paragraph_target.model_dump(mode="json"),
        "paragraph_candidate_count": len(paragraph_candidates),
        "cell_target": cell_target.model_dump(mode="json") if cell_target else None,
        "cell_source_position": None
        if cell is None
        else {
            "row_index": cell.row_index,
            "col_index": cell.col_index,
        },
        "cell_candidate_count": len(cell_candidates),
        "cell_skip_reason": cell_skip_reason,
        "table_summary": table_summary,
        "operations": [operation.model_dump(mode="json") for operation in operations],
        "expected_markers": expected_markers,
        "validation": validation.model_dump(mode="json"),
        "dry_run": operation_summary(dry_run_result),
        "doc_ir": {
            **operation_summary(doc_ir_result),
            "updated_texts": doc_ir_texts[:50],
        },
        "bytes": {
            **operation_summary(bytes_result),
            "output_filename": bytes_result.output_filename,
            "saved_output": str(saved_bytes_output_path),
            "output_bytes": len(bytes_result.output_bytes),
            "reparsed_texts": bytes_reparsed_texts[:50],
        },
        "native_file": {
            **operation_summary(native_result),
            "output_path": native_result.output_path,
            "reparsed_texts": native_reparsed_texts[:50],
        },
        "removal_dry_run_probe": removal_probe,
    }


def run_manual_file_flow(
    *,
    source_path: Path,
    source_doc_type: str,
    output_dir: Path,
    output_path: Path | None,
    structural_output_path: Path | None,
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
    skip_structural_suite: bool,
    structural_paragraph_text: str,
    structural_run_text: str,
    structural_cell_text: str,
    preview_limit: int,
) -> dict[str, Any]:
    if not source_path.exists():
        raise FileNotFoundError(f"Source file does not exist: {source_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    requested_output_path = output_path or default_output_path(source_path, output_dir)
    requested_cell_output_path = default_cell_output_path(source_path, output_dir)
    requested_structural_output_path = structural_output_path or default_structural_output_path(source_path, output_dir)
    summary_path = output_dir / f"{source_path.stem}_summary.json"
    source_doc_ir = DocIR.from_file(source_path, doc_type=source_doc_type)  # type: ignore[arg-type]
    source_doc_ir.ensure_node_identity()
    cached_document = DocumentInput(
        doc_ir=source_doc_ir,
        source_path=str(source_path),
        source_doc_type=source_doc_ir.source_doc_type or source_doc_type,  # type: ignore[arg-type]
    )

    read_result = read_document(
        document=cached_document,
        include_runs=True,
        limit=preview_limit,
    )
    edit_target, candidates = select_edit_target(
        document=cached_document,
        target_kind=target_kind,
        target_id=target_id,
        contains=contains,
        target_index=target_index,
    )
    annotation_target = resolve_annotation_target(
        document=cached_document,
        edit_target=edit_target,
    )
    context_result = get_document_context(
        document=cached_document,
        target_ids=[edit_target.target_id, annotation_target.target_id],
        before=1,
        after=1,
        include_runs=True,
    )
    annotation_suite = run_annotation_suite(
        document=cached_document,
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
    edit_validation = validate_edit_suite(document=cached_document, edit=edit)
    dry_run_suite = run_dry_run_edit_suite(document=cached_document, edit=edit)
    doc_ir_suite = run_doc_ir_edit_suite(doc=source_doc_ir, edit=edit)
    bytes_suite = run_bytes_edit_suite(
        source_path=source_path,
        source_doc_type=source_doc_type,
        output_dir=output_dir,
        edit=edit,
    )
    edit_result, reparsed_texts = run_native_file_edit_suite(
        document=cached_document,
        source_doc_type=source_doc_type,
        edit=edit,
        output_path=requested_output_path,
    )
    cell_edit_suite = run_optional_cell_edit_suite(
        source_path=source_path,
        source_doc_type=source_doc_type,
        source_doc_ir=source_doc_ir,
        document=cached_document,
        output_dir=output_dir,
        cell_output_path=requested_cell_output_path,
        target_id=cell_target_id,
        contains=cell_contains,
        target_index=cell_target_index,
        replacement=cell_replacement,
        append_text=cell_append_text,
        excluded_target_ids={edit_target.target_id},
    )
    structural_edit_suite = (
        {
            "skipped": True,
            "reason": "Skipped by --skip-structural-suite.",
        }
        if skip_structural_suite
        else run_structural_edit_suite(
            source_path=source_path,
            source_doc_type=source_doc_type,
            source_doc_ir=source_doc_ir,
            document=cached_document,
            output_dir=output_dir,
            structural_output_path=requested_structural_output_path,
            preferred_target=edit_target,
            cell_target_id=cell_target_id,
            cell_contains=cell_contains,
            cell_target_index=cell_target_index,
            paragraph_text=structural_paragraph_text,
            run_text=structural_run_text,
            cell_text=structural_cell_text,
        )
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
            "requested_structural_output": str(requested_structural_output_path),
            "structural_edited_output": None
            if structural_edit_suite["skipped"]
            else structural_edit_suite["native_file"]["output_path"],
            "review_html_full": annotation_suite["full_target"]["review_html"],
            "review_html_selected": annotation_suite["selected_text"]["review_html"],
            "bytes_output": bytes_suite["saved_output"],
            "cell_bytes_output": None
            if cell_edit_suite["skipped"]
            else cell_edit_suite["bytes"]["saved_output"],
            "structural_bytes_output": None
            if structural_edit_suite["skipped"]
            else structural_edit_suite["bytes"]["saved_output"],
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
        "structural_edit_suite": structural_edit_suite,
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
        "--structural-output-path",
        type=Path,
        default=None,
        help="Exact requested output path for the structural edit suite.",
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
        "--skip-structural-suite",
        action="store_true",
        help="Skip apply_document_edits structural operation checks.",
    )
    parser.add_argument(
        "--structural-paragraph-text",
        default="Manual structural paragraph",
        help="Text inserted by the structural paragraph operation.",
    )
    parser.add_argument(
        "--structural-run-text",
        default=" [manual structural run]",
        help="Text inserted by the structural run operation.",
    )
    parser.add_argument(
        "--structural-cell-text",
        default="Manual structural cell",
        help="Text used by the structural set_cell_text operation when a table cell exists.",
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
        structural_output_path=args.structural_output_path,
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
        skip_structural_suite=args.skip_structural_suite,
        structural_paragraph_text=args.structural_paragraph_text,
        structural_run_text=args.structural_run_text,
        structural_cell_text=args.structural_cell_text,
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
    if summary["structural_edit_suite"]["skipped"]:
        print(f"Structural edits: skipped ({summary['structural_edit_suite']['reason']})")
    else:
        print(f"Structural edited output: {summary['paths']['structural_edited_output']}")
        print(f"Structural bytes output: {summary['paths']['structural_bytes_output']}")
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
        "validate/render annotations, validate/dry-run/doc_ir/bytes/native edits, "
        "validate/dry-run/doc_ir/bytes/native structural edits"
    )
    if summary["edit"]["warnings"]:
        print(f"Warnings: {'; '.join(summary['edit']['warnings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
