# LLM Editing And Annotation Notes

This note captures the current assessment for future implementation work on
LLM-driven document edits and annotations.

## Scope

- Same-format native write-back only:
  - `docx -> docx`
  - `hwpx -> hwpx`
- Table structure edits are out of scope.
- Planned table support is limited to editing text already inside existing
  cells / cell paragraphs / runs.

## Core Principle

`DocIR` should remain the structural read model.

Do not treat `DocIR` as a fully round-trippable source document model.
Instead:

1. Parse source document into `DocIR`
2. Let the LLM read reduced / derived views of the IR
3. Have the LLM emit constrained edit or annotation commands
4. Validate those commands deterministically
5. Apply validated commands back to the native source document
6. Re-parse to verify the result

## Safe Editing Pattern

Do not ask the LLM to return fully rewritten paragraph text, table markdown,
or full-document replacements.

Prefer structured edit commands such as:

```python
class RunTextEdit(BaseModel):
    run_unit_id: str
    old_text: str
    new_text: str
```

Possible future extensions:

```python
class CellParagraphEdit(BaseModel):
    paragraph_unit_id: str
    old_text: str
    new_text: str
```

Validation rules:

- target unit must exist
- target unit must be writable for the requested operation
- `old_text` must match the current source text exactly
- if hashes/version guards are added later, they must also match

## Why Table Markdown Must Not Be The Edit Source

`TableIR.markdown` is useful for LLM reading only.

It is intentionally lossy:

- merged cells are repeated
- nested tables are emitted by reference (`[tbl:...]`)
- rich styling is not represented

So:

- read tables as markdown
- write edits back only through run / paragraph / cell targets

## Cell Editing Assessment

Cell text edits are feasible with the current structural IR because cells,
cell paragraphs, and nested tables are explicit nodes.

Safe scope:

- editing text in existing runs inside cells
- possibly editing existing cell paragraph text and decomposing to run edits

Unsafe scope:

- adding/removing rows or columns
- changing row/col spans
- changing merge structure
- editing nested-table structure through markdown

## Missing Piece For Native Write-Back

The current package does not yet store native source anchors.

Future implementation likely needs a resolver layer such as:

- DOCX: block/paragraph/run path for each writable run
- HWPX: section/paragraph/run path or XML path for each writable run

This layer should be used only by the apply step, not exposed as the main LLM
interface.

## Annotation Assessment

HTML/UI annotations are a good fit.

Recommended future annotation schema:

```python
class Annotation(BaseModel):
    target_unit_id: str
    start: int | None = None
    end: int | None = None
    label: str
    color: str = "#FFFF00"
    note: str = ""
```

Recommended anchoring:

- primary: `unit_id`
- optional: offsets within `RunIR.text` or `ParagraphIR.text`

This is preferable to global exact-text matching because repeated text is less
ambiguous.

Native source-document comments/highlights are a separate format-specific
problem and should not be coupled to the initial HTML annotation feature.

## Cross-Format Conversion Assessment

`DocIR` may be usable as a lossy structural export layer for:

- `docx -> IR -> hwpx`
- `hwpx -> IR -> docx`

But it should not be treated as a fidelity-preserving round-trip model.

The current IR does not fully preserve many source-specific semantics, such as:

- full layout/page settings
- numbering definitions
- comments/revisions
- floating drawing/shapes
- many format-specific XML constructs

So cross-format generation should be treated as export, not safe round-trip
conversion.

## Recommended Future Work Order

1. Add native source-anchor metadata or a resolver layer for writable runs
2. Define structured LLM edit DTOs
3. Implement deterministic validation
4. Implement same-format run write-back
5. Extend to cell run edits
6. Add annotation DTOs anchored by `unit_id` + offsets
7. Keep table markdown as read-only LLM context
