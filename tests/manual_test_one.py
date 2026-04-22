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
    ListEditableTargetsRequest,
    ReadDocumentRequest,
    RenderReviewHtmlRequest,
    TargetKind,
    TextAnnotation,
    TextEdit,
    ValidateTextAnnotationsRequest,
    ValidateTextEditsRequest,
    apply_text_edits,
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


def target_kinds_for_arg(target_kind: str) -> list[TargetKind]:
    if target_kind == "auto":
        return ["run", "paragraph", "cell"]
    return [target_kind]  # type: ignore[list-item]


def default_output_path(source_path: Path, output_dir: Path) -> Path:
    suffix = source_path.suffix or ".out"
    return output_dir / f"{source_path.stem}_manual_edit{suffix}"


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


def write_review_html(
    *,
    document: DocumentInput,
    annotation_target: EditableTarget,
    output_path: Path,
    label: str,
    color: str,
    note: str,
) -> list[dict[str, Any]]:
    annotation = TextAnnotation(
        target_kind=annotation_target.target_kind,  # type: ignore[arg-type]
        target_id=annotation_target.target_id,
        selected_text=annotation_target.current_text,
        label=label,
        color=color,
        note=note,
    )
    annotation_validation = validate_text_annotations(
        ValidateTextAnnotationsRequest(
            document=document,
            annotations=[annotation],
        )
    )
    require_validation_ok("validate_text_annotations", annotation_validation)

    review_result = render_review_html(
        RenderReviewHtmlRequest(
            document=document,
            annotations=[annotation],
            title=f"Manual Review: {Path(document.source_path or document.source_name or 'document').name}",
        )
    )
    require_result_ok("render_review_html", review_result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(review_result.html or "", encoding="utf-8")
    return [
        resolved.model_dump(mode="json")
        for resolved in review_result.resolved_annotations
    ]


def apply_and_verify_edit(
    *,
    document: DocumentInput,
    edit_target: EditableTarget,
    replacement: str,
    output_path: Path,
) -> tuple[Any, list[str]]:
    edit = TextEdit(
        target_kind=edit_target.target_kind,
        target_id=edit_target.target_id,
        expected_text=edit_target.current_text,
        new_text=replacement,
        reason="Manual edit smoke check.",
    )
    edit_validation = validate_text_edits(
        ValidateTextEditsRequest(
            document=document,
            edits=[edit],
        )
    )
    require_validation_ok("validate_text_edits", edit_validation)

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
    reparsed_texts = [paragraph.text for paragraph in reparsed.paragraphs]
    if not any(replacement in text for text in reparsed_texts):
        raise RuntimeError(f"Edited output did not contain expected text {replacement!r}: {actual_output_path}")
    return edit_result, reparsed_texts


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
    annotation_label: str,
    annotation_color: str,
    annotation_note: str,
    preview_limit: int,
) -> dict[str, Any]:
    if not source_path.exists():
        raise FileNotFoundError(f"Source file does not exist: {source_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    requested_output_path = output_path or default_output_path(source_path, output_dir)
    review_html_path = output_dir / f"{source_path.stem}_review.html"
    summary_path = output_dir / f"{source_path.stem}_summary.json"
    document = DocumentInput(
        source_path=str(source_path),
        source_doc_type=source_doc_type,  # type: ignore[arg-type]
    )

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
    resolved_annotations = write_review_html(
        document=document,
        annotation_target=annotation_target,
        output_path=review_html_path,
        label=annotation_label,
        color=annotation_color,
        note=annotation_note,
    )

    new_text = replacement if replacement is not None else f"{edit_target.current_text}{append_text}"
    edit_result, reparsed_texts = apply_and_verify_edit(
        document=document,
        edit_target=edit_target,
        replacement=new_text,
        output_path=requested_output_path,
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
            "review_html": str(review_html_path),
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
        "annotation": {
            "resolved": resolved_annotations,
        },
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
        annotation_label=args.annotation_label,
        annotation_color=args.annotation_color,
        annotation_note=args.annotation_note,
        preview_limit=args.preview_limit,
    )

    print("Manual file flow completed.")
    print(f"Source: {summary['source']['path']}")
    print(f"Edited output: {summary['paths']['edited_output']}")
    print(f"Review HTML: {summary['paths']['review_html']}")
    print(f"Summary JSON: {summary['paths']['summary_json']}")
    print(
        "Edited target: "
        f"{summary['selected_targets']['edit']['target_kind']} "
        f"{summary['selected_targets']['edit']['target_id']}"
    )
    print(f"Annotation target: {summary['selected_targets']['annotation']['target_id']}")
    print(f"Modified target ids: {', '.join(summary['edit']['modified_target_ids'])}")
    if summary["edit"]["warnings"]:
        print(f"Warnings: {'; '.join(summary['edit']['warnings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
