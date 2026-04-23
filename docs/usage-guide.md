# Usage Guide

This guide shows common `document_processor` workflows with executable Python
examples.

## Installation

```bash
pip install document-processor
```

For local development against a checkout:

```bash
uv pip install -e /path/to/document-processor
```

For model diagrams:

```bash
pip install "document-processor[viz]"
```

## 1. Parse A Native Document

### From a file path

```python
from document_processor import DocIR

doc = DocIR.from_file("/path/to/contract.docx")

print(doc.source_doc_type)
print(doc.source_path)
print(len(doc.paragraphs))
print(doc.paragraphs[0].node_id, doc.paragraphs[0].text)
```

### From bytes

```python
from pathlib import Path

from document_processor import DocIR

doc_bytes = Path("/path/to/contract.hwpx").read_bytes()
doc = DocIR.from_file(doc_bytes, doc_type="hwpx")

print(doc.source_doc_type)
print(doc.paragraphs[0].text)
```

### From a binary file object

```python
from document_processor import DocIR

with open("/path/to/contract.docx", "rb") as handle:
    doc = DocIR.from_file(handle)

print(doc.paragraphs[0].text)
```

## 2. Build A Synthetic `DocIR`

This is useful for tests, prototyping, and examples.

```python
from document_processor import DocIR

doc = DocIR.from_mapping(
    {
        "s1.p1.r1": "Hello ",
        "s1.p1.r2": "World",
        "s1.p2.r1": "Second paragraph",
    },
    source_doc_type="docx",
)

print(doc.paragraphs[0].text)
print([run.node_id for run in doc.paragraphs[0].runs])
```

## 3. Inspect The IR

```python
from document_processor import DocIR

doc = DocIR.from_file("/path/to/contract.docx")

for paragraph in doc.paragraphs[:3]:
    print(paragraph.node_id, paragraph.page_number, paragraph.text)
    for run in paragraph.runs:
        print(" ", run.node_id, repr(run.text))
```

Useful helpers:

- `paragraph.runs`
- `paragraph.images`
- `paragraph.tables`
- `table.markdown`
- `doc.pages`

## 4. Render HTML

### Standard document preview

```python
from document_processor import DocIR

doc = DocIR.from_file("/path/to/contract.docx")
html = doc.to_html(title="Preview")

with open("preview.html", "w", encoding="utf-8") as handle:
    handle.write(html)
```

For layout investigation, render an instrumented preview:

```python
debug_html = doc.to_html(title="Layout Debug", debug_layout=True)
```

The debug view outlines pages, tables, cells, and paragraphs, then annotates
each element with declared point sizes and measured browser-rendered sizes.
HTML rendering also clamps negative paragraph indents so text starts within the
page or table-cell content edge. Table cell margins from the source document
are exposed as `CellStyleInfo.padding_*_pt` and rendered as cell padding.

### Annotated review preview

```python
from document_processor import (
    DocIR,
    DocumentInput,
    RenderReviewHtmlRequest,
    TextAnnotation,
    render_review_html,
)

doc = DocIR.from_mapping({"s1.p1.r1": "Hello ", "s1.p1.r2": "World"})
paragraph_id = doc.paragraphs[0].node_id

review = render_review_html(
    RenderReviewHtmlRequest(
        document=DocumentInput(doc_ir=doc),
        annotations=[
            TextAnnotation(
                target_kind="paragraph",
                target_id=paragraph_id,
                selected_text="World",
                label="Focus",
                color="#FFEE88",
                note="Review this phrase",
            )
        ],
        title="Annotated Review",
    )
)
html = review.html
```

If the same substring repeats inside the target, set `occurrence_index`
instead of guessing offsets:

```python
doc = DocIR.from_mapping({"s1.p1.r1": "Beta Beta Beta"})
run_id = doc.paragraphs[0].runs[0].node_id

review = render_review_html(
    RenderReviewHtmlRequest(
        document=DocumentInput(doc_ir=doc),
        annotations=[
            TextAnnotation(
                target_kind="run",
                target_id=run_id,
                selected_text="Beta",
                occurrence_index=1,
                label="Second match",
            )
        ],
    )
)
html = review.html
```

## 5. Use The Stateless Edit API

The high-level edit API works with `DocumentInput`.

### Path-backed workflow

```python
from document_processor import (
    ApplyTextEditsRequest,
    DocumentInput,
    ReadDocumentRequest,
    TextEdit,
    apply_text_edits,
    read_document,
)

preview = read_document(
    ReadDocumentRequest(
        document=DocumentInput(source_path="/path/to/contract.docx"),
        start=0,
        limit=5,
        include_runs=True,
    )
)
first_paragraph_id = preview.paragraphs[0].node_id

result = apply_text_edits(
    ApplyTextEditsRequest(
        document=DocumentInput(source_path="/path/to/contract.docx"),
        edits=[
            TextEdit(
                target_kind="paragraph",
                target_id=first_paragraph_id,
                expected_text="Hello World",
                new_text="Hello Legal World",
                reason="Expand wording",
            )
        ],
        output_filename="contract_reviewed.docx",
        return_doc_ir=True,
    )
)

print(result.ok)
print(result.output_path)
print(result.modified_target_ids)
print(result.updated_doc_ir.paragraphs[0].text)
```

### Bytes-backed workflow

```python
from pathlib import Path

from document_processor import (
    ApplyTextEditsRequest,
    DocumentInput,
    DocIR,
    ReadDocumentRequest,
    TextEdit,
    apply_text_edits,
    read_document,
)

source_bytes = Path("/path/to/contract.docx").read_bytes()
document = DocumentInput(
    source_bytes=source_bytes,
    source_name="contract.docx",
)
preview = read_document(
    ReadDocumentRequest(
        document=document,
        start=0,
        limit=1,
        include_runs=False,
    )
)

result = apply_text_edits(
    ApplyTextEditsRequest(
        document=document,
        edits=[
            TextEdit(
                target_kind="paragraph",
                target_id=preview.paragraphs[0].node_id,
                expected_text="Hello World",
                new_text="Hello Contract World",
                reason="Clarify wording",
            )
        ],
        return_doc_ir=True,
    )
)

edited_doc = DocIR.from_file(result.output_bytes)
print(result.output_filename)
print(edited_doc.paragraphs[0].text)
```

### `DocIR`-only workflow

Use this when you want in-memory updates without native file output.

```python
from document_processor import (
    ApplyTextEditsRequest,
    DocumentInput,
    DocIR,
    TextEdit,
    apply_text_edits,
)

doc = DocIR.from_mapping(
    {
        "s1.p1.r1": "Hello ",
        "s1.p1.r2": "World",
        "s1.p2.r1.tbl1.tr1.tc1.p1.r1": "Old cell text",
    },
    source_doc_type="docx",
)
first_paragraph_id = doc.paragraphs[0].node_id

result = apply_text_edits(
    ApplyTextEditsRequest(
        document=DocumentInput(doc_ir=doc),
        edits=[
            TextEdit(
                target_kind="paragraph",
                target_id=first_paragraph_id,
                expected_text="Hello World",
                new_text="Hello Contract World",
            )
        ],
    )
)

print(result.updated_doc_ir.paragraphs[0].text)
print(result.output_path)
print(result.output_bytes)
```

### Cell text edits

Use `target_kind="cell"` to replace all editable text in a table cell. Multi-paragraph
cells must keep the same number of newline-separated text lines; this avoids creating or
deleting native document paragraphs during a cell edit.

```python
from document_processor import (
    ApplyTextEditsRequest,
    DocumentInput,
    ListEditableTargetsRequest,
    TextEdit,
    apply_text_edits,
    list_editable_targets,
)

document = DocumentInput(source_path="/path/to/contract.docx")
cells = list_editable_targets(
    ListEditableTargetsRequest(
        document=document,
        target_kinds=["cell"],
        max_targets=10,
    )
)
first_cell_id = cells.targets[0].target_id

result = apply_text_edits(
    ApplyTextEditsRequest(
        document=document,
        edits=[
            TextEdit(
                target_kind="cell",
                target_id=first_cell_id,
                expected_text="Old cell text",
                new_text="Updated cell text",
            )
        ],
    )
)

print(result.modified_target_ids)
```

## 6. Inspect Context Before Editing

Use this before emitting exact-match edits.

```python
from document_processor import (
    DocumentInput,
    GetDocumentContextRequest,
    get_document_context,
)

context = get_document_context(
    GetDocumentContextRequest(
        document=DocumentInput(source_path="/path/to/contract.docx"),
        target_ids=["r_3f1ff7241702452b"],
        before=1,
        after=1,
        include_runs=True,
    )
)

for paragraph in context.paragraphs:
    print(paragraph.node_id, paragraph.text)
    for run in paragraph.runs:
        print(" ", run.node_id, run.start, run.end, repr(run.text))
```

## 7. List Safe Edit Targets

```python
from document_processor import (
    DocumentInput,
    ListEditableTargetsRequest,
    list_editable_targets,
)

targets = list_editable_targets(
    ListEditableTargetsRequest(
        document=DocumentInput(source_path="/path/to/contract.docx"),
        target_kinds=["cell", "run"],
        include_child_runs=True,
    )
)

for target in targets.targets:
    print(target.target_id, target.target_kind, repr(target.current_text))
```

## 8. Validate Edits Before Applying

```python
from document_processor import (
    DocumentInput,
    TextEdit,
    ValidateTextEditsRequest,
    validate_text_edits,
)

validation = validate_text_edits(
    ValidateTextEditsRequest(
        document=DocumentInput(source_path="/path/to/contract.docx"),
        edits=[
            TextEdit(
                target_kind="run",
                target_id="r_3f1ff7241702452b",
                expected_text="wrong text",
                new_text="updated text",
            )
        ],
    )
)

print(validation.ok)
for issue in validation.issues:
    print(issue.code, issue.message, issue.current_text)
```

## 9. Render Review HTML Through The Stateless API

```python
from document_processor import (
    DocumentInput,
    RenderReviewHtmlRequest,
    TextAnnotation,
    render_review_html,
)

review = render_review_html(
    RenderReviewHtmlRequest(
        document=DocumentInput(source_path="/path/to/contract.docx"),
        annotations=[
            TextAnnotation(
                target_kind="paragraph",
                target_id="p_15cb9ef0efc99b82",
                selected_text="계약기간",
                label="Key clause",
                color="#FFD966",
                note="Human review requested",
            )
        ],
        title="Contract Review",
    )
)

with open("review.html", "w", encoding="utf-8") as handle:
    handle.write(review.html)
```

## 10. Edit Through Structured Requests

Edits should go through `TextEdit`, `ValidateTextEditsRequest`,
`ApplyTextEditsRequest`, `validate_text_edits`, and `apply_text_edits`.
This keeps LLM tool calls on one schema and avoids exposing internal native
write-back plumbing.

```python
from document_processor import (
    ApplyTextEditsRequest,
    DocumentInput,
    TextEdit,
    apply_text_edits,
)

result = apply_text_edits(
    ApplyTextEditsRequest(
        document=DocumentInput(source_path="/path/to/contract.hwpx"),
        edits=[
            TextEdit(
                target_kind="run",
                target_id="r_10b2809a0c03f6e1",
                expected_text="World",
                new_text="HWPX",
            )
        ],
        return_doc_ir=True,
    )
)

print(result.output_path)
print(result.modified_target_ids)
```

The removed low-level edit engine names are documented in
[Removed Legacy Edit API](removed-legacy-edit-api.md).

The removed low-level annotation names are documented in
[Removed Legacy Annotation API](removed-legacy-annotation-api.md).

## 11. Add Custom Metadata

All IR nodes expose a `.meta` field for Pydantic-based metadata.

```python
from pydantic import BaseModel

from document_processor import DocIR


class ReviewMeta(BaseModel):
    risk_level: str
    reviewer_note: str


doc = DocIR.from_mapping({"s1.p1.r1": "Clause text"})
doc.paragraphs[0].meta = ReviewMeta(
    risk_level="medium",
    reviewer_note="Needs legal review",
)

print(doc.paragraphs[0].meta)
```

## 12. Current Limits

- `pdf` parsing is not implemented.
- External PDF parsers should build `DocIR` with stable `node_id` values as described in
  [PDF Parser DocIR Integration](pdf-parser-docir-integration.md).
- Native write-back is same-format only for `docx`, `hwpx`, and `hwp -> hwpx`.
- Paragraph edits are rejected when the paragraph contains tables or images.
- Table structure edits are out of scope.
- Annotation selection is exact-text based; use `occurrence_index` when the same substring repeats in a target.

## Suggested Workflow

For LLM or review tooling:

1. Read source through `read_document(...)` or parse source into `DocIR`.
2. Use returned `node_id` values, `get_document_context(...)`, or `list_editable_targets(...)`.
3. Emit exact `TextEdit` objects.
4. Call `validate_text_edits(...)`.
5. Call `apply_text_edits(...)`.
6. Call `render_review_html(...)` for human review.
