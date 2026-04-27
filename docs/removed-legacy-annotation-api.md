# Removed Legacy Annotation API

This package no longer exposes the old low-level annotation DTOs or direct HTML
annotation renderer/resolver functions. The public annotation API now uses
flattened keyword functions with `TextAnnotation` DTOs for LLM tool calling.

## Removed Names

The following names were removed from the package API:

- `Annotation`
- `ResolvedAnnotation`
- `AnnotationValidationError`
- `resolve_annotations`
- `render_annotated_html`

The removed DTO used `target_id`, `selected_text`, `occurrence_index`, `label`,
`color`, and `note` without an explicit target kind. That shape was replaced by
the LLM-facing schema:

```python
TextAnnotation(
    target_kind="paragraph",  # "paragraph" or "run"
    target_id="p_15cb9ef0efc99b82",
    selected_text="selected phrase",
    occurrence_index=0,
    label="Needs review",
    color="#FFFF00",
    note="Optional reviewer note",
)
```

## Supported Replacement

Use these public models and functions instead:

- `TextAnnotation`
- `AnnotationValidationResult`
- `ResolvedTextAnnotation`
- `ReviewHtmlResult`
- `validate_text_annotations`
- `render_review_html`

Example:

```python
from document_processor import (
    DocumentInput,
    TextAnnotation,
    render_review_html,
)

result = render_review_html(
    document=DocumentInput(source_path="/path/to/source.docx"),
    annotations=[
        TextAnnotation(
            target_kind="paragraph",
            target_id="p_15cb9ef0efc99b82",
            selected_text="selected phrase",
            occurrence_index=0,
            label="Needs review",
        )
    ],
    title="Review",
)

html = result.html
resolved = result.resolved_annotations
```

For in-memory `DocIR` review rendering, pass the IR through `DocumentInput`:

```python
from document_processor import (
    DocumentInput,
    TextAnnotation,
    render_review_html,
)

result = render_review_html(
    document=DocumentInput(doc_ir=doc),
    annotations=[
        TextAnnotation(
            target_kind="run",
            target_id=doc.paragraphs[0].runs[0].node_id,
            label="Review this run",
        )
    ],
)
```

## Migration Notes

- Add `target_kind` with `"paragraph"` or `"run"`.
- Continue using stable DocIR `node_id` values as `target_id`.
- Keep `selected_text` optional to annotate the full target.
- Keep `occurrence_index` when `selected_text` appears multiple times in the
  same target.
- Use `validate_text_annotations(...)` for validation without HTML rendering.
- Use `render_review_html(...)` for review HTML.

The lower-level HTML annotation resolver and renderer still exist internally,
but they are not part of the public API. This keeps external callers and LLM
tools aligned on stable DocIR targets and explicit target kinds.
