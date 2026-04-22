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
    HwpxDocument,
    ListEditableTargetsRequest,
    NativeAnchor,
    NodeKind,
    ReadDocumentRequest,
    RenderReviewHtmlRequest,
    TextAnnotation,
    TextEdit,
    ValidateTextAnnotationsRequest,
    ValidateTextEditsRequest,
    apply_text_edits,
    apply_edits_to_doc_ir,
    apply_edits_to_source,
    get_document_context,
    list_editable_targets,
    read_document,
    render_review_html,
    validate_text_annotations,
    validate_text_edits,
)
```

## Core IR Models

See [PDF Parser DocIR Integration](pdf-parser-docir-integration.md) for guidance on
building stable IDs and native anchors from an external PDF parser.

### `DocIR`

Top-level structural document model.

Key fields:

- `doc_id: str | None`
- `source_path: str | None`
- `source_doc_type: str | None`
- `identity_version: int`
- `assets: dict[str, ImageAsset]`
- `pages: list[PageInfo]`
- `paragraphs: list[ParagraphIR]`

All addressable IR nodes expose:

- `node_id`: stable opaque id intended for LLM tool calls and edit/annotation targets.

Nodes parsed from a native document also receive `native_anchor`, which records the
source document type, debug path, parent debug path, native part name, structural
path, and a source text hash where available. Use `node_id` in exposed tool-call
APIs. Native/package locators live only under `native_anchor`.

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
Top-level consecutive paragraphs with the same multi-column `column_layout`
are wrapped in a CSS multi-column group when rendered to HTML.

### `ParagraphIR`

Paragraph-like structural node.

Important fields:

- `node_id`
- `text`
- `page_number`
- `column_layout`
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

- `node_id`
- `text`
- `run_style`

### `TableIR`

Nested table node under a paragraph.

Important fields:

- `node_id`
- `row_count`
- `col_count`
- `table_style`
- `cells`

Computed helper:

- `.markdown`

### Supporting Models

- `ImageAsset`
- `ImageIR`
- `ColumnLayoutInfo`
- `PageInfo`
- `TableCellIR`
- `NativeAnchor`
- `CellStyleInfo`
- `ParaStyleInfo`
- `RunStyleInfo`
- `TableStyleInfo`
- `StyleMap`

#### `NativeAnchor`

Native/source-location metadata attached to addressable nodes.

Fields:

- `source_doc_type`: source format such as `docx`, `hwpx`, `hwp`, or parser-defined values.
- `node_kind`: one of `paragraph`, `run`, `image`, `table`, or `cell`.
- `debug_path`: human-readable internal path for diagnostics and native write-back tracing.
- `parent_debug_path`: debug path of the containing native/IR node when available.
- `part_name`: package part or source segment name, such as `word/document.xml`,
  `Contents/section0.xml`, or `page:3`.
- `structural_path`: optional parser-native structural locator.
- `text_hash`: SHA-1 hash of the source text for drift detection.

`NativeAnchor` helps a writer or external parser reconnect a stable `node_id` to
native structures. It is returned for inspection, but LLM edit and annotation calls
should still target `node_id`.

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

#### `ColumnLayoutInfo`

Active section/text-column layout for a paragraph.

Important fields:

- `count`
- `gap_pt`
- `widths_pt`
- `gaps_pt`
- `equal_width`

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

### `read_document(request: ReadDocumentRequest) -> ReadDocumentResult`

Read a bounded paragraph window from a document. This is the preferred tool-call entry
point when an LLM needs to inspect a document incrementally.

Request fields:

- `document`
- `start`
- `limit`
- `include_runs`

Response fields:

- `source_path`
- `source_doc_type`
- `source_name`
- `start`
- `limit`
- `total_paragraphs`
- `next_start`
- `paragraphs`

Each paragraph contains a fully constructed `text` field for readability. When
`include_runs=True`, each run includes `start` and `end` offsets relative to that
paragraph text so callers can map readable text spans back to editable run IDs.

### `get_document_context(request: GetDocumentContextRequest) -> DocumentContextResult`

Return surrounding paragraph context for paragraph or run ids.

Request fields:

- `document`
- `target_ids`
- `before`
- `after`
- `include_runs`

Response fields:

- `source_path`
- `source_doc_type`
- `source_name`
- `paragraphs`
- `missing_target_ids`

### `list_editable_targets(request: ListEditableTargetsRequest) -> ListEditableTargetsResult`

Enumerate paragraph, run, and cell targets that can be edited safely.

Request fields:

- `document`
- `target_ids`
- `target_kinds`
- `include_child_runs`
- `only_writable`
- `max_targets`

Response fields:

- `source_path`
- `source_doc_type`
- `source_name`
- `targets`
- `missing_target_ids`

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
- `dry_run`
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

`modified_target_ids` and `modified_run_ids` contain stable `node_id` values.

### `validate_text_annotations(request: ValidateTextAnnotationsRequest) -> AnnotationValidationResult`

Validate annotation targets and selected text without rendering HTML.

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
- `target_id: str`
- `expected_text: str`
- `new_text: str`
- `reason: str = ""`

Use the `node_id` returned by `read_document`, `get_document_context`, or
`list_editable_targets` as `target_id`.

Cell text edits replace the full text of a table cell. For multi-paragraph cells, `new_text`
must contain the same number of newline-separated lines as the current cell text; the API
does not create or delete paragraphs inside cells.

### `TextAnnotation`

Fields:

- `target_kind: Literal["paragraph", "run"]`
- `target_id: str`
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
- `target_id`
- `parent_paragraph_id`
- `current_text`
- `page_number`
- `native_anchor`
- `writable`
- `writable_reason`

### `DocumentRunContext`

Fields:

- `node_id`
- `text`
- `start`
- `end`
- `native_anchor`

`start` and `end` are character offsets into the containing
`DocumentParagraphContext.text`.

## Low-Level Edit Engine

Use these when you want direct programmatic control rather than the request/response API.

### `RunTextEdit`

Low-level run edit DTO.

Fields:

- `run_id`
- `old_text`
- `new_text`
- `reason`

### `ParagraphTextEdit`

Low-level paragraph edit DTO.

Fields:

- `paragraph_id`
- `old_text`
- `new_text`
- `reason`

### `CellTextEdit`

Low-level table-cell text edit DTO.

Fields:

- `cell_id`
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

- `target_id`
- `selected_text`
- `occurrence_index`
- `label`
- `color`
- `note`

### `resolve_annotations(doc: DocIR, annotations: list[Annotation]) -> list[ResolvedAnnotation]`

Resolve annotations against a `DocIR` node ID and compute canonical offsets from
exact matched text.

### `render_annotated_html(doc: DocIR, annotations: list[Annotation], *, title=None) -> str`

Render review HTML with `<mark>` tags and diagnostic data attributes.

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
