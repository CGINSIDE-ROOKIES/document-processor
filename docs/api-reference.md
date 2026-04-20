# API Reference

This document describes the public Python API exported by `document_processor`.

## Package Surface

Import directly from the package root:

```python
from document_processor import (
    Annotation,
    ApplyTextEditsRequest,
    CellTextEdit,
    DocIR,
    DocumentInput,
    GetDocumentContextRequest,
    RenderReviewHtmlRequest,
    TextAnnotation,
    TextEdit,
    apply_text_edits,
    apply_edits_to_doc_ir,
    apply_edits_to_source,
    get_document_context,
    render_review_html,
)
```

## Core IR Models

### `DocIR`

Top-level structural document model.

Key fields:

- `doc_id: str | None`
- `source_path: str | None`
- `source_doc_type: str | None`
- `assets: dict[str, ImageAsset]`
- `pages: list[PageInfo]`
- `paragraphs: list[ParagraphIR]`

Key methods:

#### `DocIR.from_file(source, *, doc_type="auto", include_tables=True, skip_empty=False, metadata=None, doc_id=None, **doc_kwargs) -> DocIR`

Parse a document into `DocIR`.

Accepted `source` values:

- `str | Path`
- `bytes`
- binary file object

Supported document types:

- `docx`
- `hwpx`
- `hwp`

Notes:

- `doc_type="auto"` infers the type from the filename or bytes.
- `pdf` is currently not implemented.
- For `.hwp`, the package converts through HWPX internally before building `DocIR`.

#### `DocIR.from_mapping(mapping, *, style_map=None, source_path=None, source_doc_type=None, metadata=None, doc_id=None, **doc_kwargs) -> DocIR`

Build a `DocIR` from a run-level mapping such as:

```python
{
    "s1.p1.r1": "Hello ",
    "s1.p1.r2": "World",
}
```

This is useful for tests, fixtures, or synthetic documents.

#### `DocIR.to_html(*, title=None, debug_layout=False) -> str`

Render the document as styled HTML using the built-in exporter.

Set `debug_layout=True` to add visual outlines and data labels for pages,
tables, cells, and paragraphs. The debug view also measures rendered element
sizes in the browser so extracted point values can be compared with actual HTML
layout.

Paragraph indents are clamped during HTML rendering so negative or hanging
indents cannot start text outside the page/table-cell content edge. Valid
hanging indents are preserved when the positive left indent is large enough.
Table cell padding is rendered from `CellStyleInfo` when extracted from source
cell margins such as HWPX `hp:cellMargin` or DOCX `w:tcMar`.

### `ParagraphIR`

Paragraph-like structural node.

Important fields:

- `unit_id`
- `text`
- `page_number`
- `para_style`
- `content`

Computed/content helpers:

- `.runs`
- `.images`
- `.tables`
- `.iter_all_runs(...)`
- `.recompute_text()`

### `RunIR`

Smallest text unit that preserves run-level styling.

Important fields:

- `unit_id`
- `text`
- `run_style`

### `TableIR`

Nested table node under a paragraph.

Important fields:

- `unit_id`
- `row_count`
- `col_count`
- `table_style`
- `cells`

Computed helper:

- `.markdown`

### Supporting Models

- `ImageAsset`
- `ImageIR`
- `PageInfo`
- `TableCellIR`
- `CellStyleInfo`
- `ParaStyleInfo`
- `RunStyleInfo`
- `TableStyleInfo`
- `StyleMap`

#### `CellStyleInfo`

Cell-level formatting for `TableCellIR.cell_style`.

Important fields:

- `background`
- `vertical_align`
- `horizontal_align`
- `width_pt`
- `height_pt`
- `padding_top_pt`
- `padding_right_pt`
- `padding_bottom_pt`
- `padding_left_pt`
- `border_top`
- `border_bottom`
- `border_left`
- `border_right`
- `diagonal_tl_br`
- `diagonal_tr_bl`
- `rowspan`
- `colspan`

HWPX `hp:cellMargin` and DOCX `w:tcMar`/`w:tblCellMar` are represented as
cell padding fields in points. Paragraph indents remain in `ParaStyleInfo`.

## Source/Input Models

### `DocumentInput`

Stateless input wrapper for read/edit/annotation APIs.

Fields:

- `source_path: str | None`
- `source_bytes: bytes | None`
- `doc_ir: DocIR | None`
- `source_doc_type: Literal["auto", "hwp", "hwpx", "docx", "pdf"]`
- `source_name: str | None`

Rules:

- Provide at least one of `source_path`, `source_bytes`, or `doc_ir`.
- `source_path` and `source_bytes` cannot both be set.
- `doc_ir` may be combined with native source data when you want in-memory reads plus native write-back.

## Stateless Read/Edit API

These functions operate on `DocumentInput` and are intended for public API usage.

### `get_document_context(request: GetDocumentContextRequest) -> DocumentContextResult`

Return surrounding paragraph context for paragraph or run ids.

Request fields:

- `document`
- `unit_ids`
- `before`
- `after`
- `include_runs`

Response fields:

- `source_path`
- `source_doc_type`
- `source_name`
- `paragraphs`
- `missing_unit_ids`

### `list_editable_targets(request: ListEditableTargetsRequest) -> ListEditableTargetsResult`

Enumerate paragraph, run, and cell targets that can be edited safely.

Request fields:

- `document`
- `unit_ids`
- `target_kinds`
- `include_child_runs`
- `only_writable`
- `max_targets`

Response fields:

- `source_path`
- `source_doc_type`
- `source_name`
- `targets`
- `missing_unit_ids`

### `validate_text_edits(request: ValidateTextEditsRequest) -> EditValidationResult`

Validate proposed edits against the current document state.

Validation checks include:

- target exists
- target kind matches
- expected text matches exactly
- paragraph target is not mixed content
- cell target is not mixed content and preserves the existing paragraph count
- native write-back type is supported when native source data is present

### `apply_text_edits(request: ApplyTextEditsRequest) -> ApplyTextEditsResult`

Validate and apply edits using either:

- in-memory `DocIR` updates
- path-backed native write-back
- bytes-backed native write-back

Request fields:

- `document`
- `edits`
- `output_path`
- `output_filename`
- `return_doc_ir`

Response fields:

- `ok`
- `source_doc_type`
- `source_name`
- `output_path`
- `output_filename`
- `output_bytes`
- `updated_doc_ir`
- `edits_applied`
- `modified_target_ids`
- `modified_run_ids`
- `warnings`
- `validation`

Behavior by input type:

- `DocumentInput(doc_ir=...)`: returns `updated_doc_ir`; no native file output is produced.
- `DocumentInput(source_path=...)`: writes to `output_path` or a default sibling `*_edited.*` file.
- `DocumentInput(source_bytes=...)`: returns `output_bytes` and `output_filename`.

Native write-back is currently supported for:

- `docx`
- `hwpx`
- `hwp`

For `.hwp`, edited output is written as `.hwpx`.

### `render_review_html(request: RenderReviewHtmlRequest) -> ReviewHtmlResult`

Render annotated review HTML from `DocIR`, bytes, or a source path.

Request fields:

- `document`
- `annotations`
- `title`

Response fields:

- `ok`
- `html`
- `resolved_annotations`
- `validation`

## Edit/Annotation DTOs

### `TextEdit`

Fields:

- `target_kind: Literal["paragraph", "run", "cell"]`
- `target_unit_id: str`
- `expected_text: str`
- `new_text: str`
- `reason: str = ""`

Cell text edits replace the full text of a table cell. For multi-paragraph cells, `new_text`
must contain the same number of newline-separated lines as the current cell text; the API
does not create or delete paragraphs inside cells.

### `TextAnnotation`

Fields:

- `target_kind: Literal["paragraph", "run"]`
- `target_unit_id: str`
- `selected_text: str | None`
- `occurrence_index: int | None`
- `label: str`
- `color: str = "#FFFF00"`
- `note: str = ""`

Behavior:

- If `selected_text` is omitted, the full target is annotated.
- If `selected_text` appears multiple times, provide `occurrence_index`.
- Canonical `start` / `end` offsets are computed by the backend and returned in `ResolvedTextAnnotation`.

### `EditableTarget`

Fields:

- `target_kind`
- `target_unit_id`
- `parent_paragraph_unit_id`
- `current_text`
- `page_number`
- `writable`
- `writable_reason`

## Low-Level Edit Engine

Use these when you want direct programmatic control rather than the request/response API.

### `RunTextEdit`

Low-level run edit DTO.

Fields:

- `run_unit_id`
- `old_text`
- `new_text`
- `reason`

### `ParagraphTextEdit`

Low-level paragraph edit DTO.

Fields:

- `paragraph_unit_id`
- `old_text`
- `new_text`
- `reason`

### `CellTextEdit`

Low-level table-cell text edit DTO.

Fields:

- `cell_unit_id`
- `old_text`
- `new_text`
- `reason`

Cell edits preserve the existing cell paragraph count and reject nested tables/images.

### `validate_edit_commands(doc: DocIR, edits: list[RunTextEdit | ParagraphTextEdit | CellTextEdit]) -> None`

Raise `EditValidationError` if any low-level edit is invalid.

### `apply_edits_to_doc_ir(doc: DocIR, edits: list[RunTextEdit | ParagraphTextEdit | CellTextEdit]) -> tuple[DocIR, ApplyEditsResult]`

Apply edits to a deep copy of the given `DocIR`.

### `apply_edits_to_file(source_path, edits, *, output_path=None) -> ApplyEditsResult`

Apply edits back to a native source file.

### `apply_edits_to_bytes(source_bytes, edits, *, doc_type="auto", source_name=None, output_filename=None) -> ApplyEditsResult`

Apply edits to bytes-backed native input and return edited bytes.

### `apply_edits_to_source(source, edits, *, doc_type="auto", source_name=None, output_path=None, output_filename=None) -> ApplyEditsResult`

Unified low-level entrypoint over:

- `DocIR`
- `str | Path`
- `bytes`
- binary file object

## Annotation Helpers

### `Annotation`

Low-level annotation DTO used by the HTML annotation renderer.

Fields:

- `target_unit_id`
- `selected_text`
- `occurrence_index`
- `label`
- `color`
- `note`

### `resolve_annotations(doc: DocIR, annotations: list[Annotation]) -> list[ResolvedAnnotation]`

Resolve annotations against a `DocIR` and compute canonical offsets from exact matched text.

### `render_annotated_html(doc: DocIR, annotations: list[Annotation], *, title=None) -> str`

Render review HTML with `<mark>` tags and unit-id data attributes.

## Diagram Helpers

### `draw_model_diagram(...)`

Render the Pydantic model graph to a file.

### `create_model_diagram(...)`

Return the generated diagram object.

## Error Types

### `EditValidationError`

Raised by low-level edit functions when an edit cannot be applied safely.

### `AnnotationValidationError`

Raised by low-level annotation resolution when a target or range is invalid.

## Current Limits

- `pdf` parsing is not implemented.
- Table structure edits are out of scope.
- Paragraph edits are blocked when the paragraph contains tables or images.
- Native write-back is limited to same-format `docx`, `hwpx`, and `hwp -> hwpx`.
- Annotation matching is exact-string based within the selected paragraph or run.
