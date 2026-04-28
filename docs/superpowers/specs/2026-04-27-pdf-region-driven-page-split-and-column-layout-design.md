# PDF Region-Driven Page Split And Column Layout Design

## Goal

ODL raw JSON already carries layout region hints such as `left-page`, `right-page`, `left-column`, and `right-column`.
Today those hints only affect PDF preview candidate grouping.

This change makes the hints visible in the rendered HTML:

- `left-page` / `right-page` split one original PDF page into two logical rendered pages.
- `left-column` / `right-column` drive `ColumnLayoutInfo` so the shared HTML exporter renders a 2-column block.

The shared HTML renderer should stay unchanged as much as possible. The PDF preview pipeline should transform raw geometry into `DocIR` shapes that the current renderer already understands.

## Current Problem

The current PDF preview path does this:

- `build_pdf_preview_context(...)` reads layout regions from raw ODL JSON.
- `prepare_pdf_for_html(...)` uses those regions only for candidate assignment and grouping.
- `DocIR.to_html()` then passes the normalized document to the shared HTML exporter.

That means:

- `left-page` / `right-page` do not create separate rendered pages.
- `left-column` / `right-column` do not become `ParagraphIR.column_layout`.
- The existing HTML exporter can already render 2-column groups, but the PDF preview path does not feed it the right metadata.

So the current system sees the region hints, but does not turn them into layout.

## Chosen Approach

Add a PDF preview normalization stage that converts layout regions into document structure before the shared renderer runs.

The stage has two responsibilities:

1. Page split mode
   - If a page has meaningful `left-page` and `right-page` regions, create two synthetic rendered pages from one physical page.
   - Reassign page-bound paragraphs and other render nodes to the best matching half.
   - Rebase bbox coordinates into the local half-page coordinate space.

2. Column mode
   - If a page has meaningful `left-column` and `right-column` regions, annotate the relevant paragraph sequence with a shared `ColumnLayoutInfo(count=2, ...)`.
   - Let the existing HTML exporter render the block through its `document-column-group` path.

The renderer remains the same. The PDF preview normalization becomes responsible for turning raw region hints into `DocIR.pages` and `ParagraphIR.column_layout`.

## Scope

- PDF preview only.
- ODL raw layout regions are the source of truth for region-driven layout hints.
- Existing DOCX/HWPX column parsing stays as-is.
- Shared HTML exporter behavior stays as-is except for consuming the metadata already present in `DocIR`.

## Non-Goals

- Do not implement raster cutting of PDFs.
- Do not add a new rendering engine.
- Do not change the general HTML exporter contract.
- Do not make `left-page` / `right-page` affect annotation or table enrichment logic beyond the required bbox rebasing.
- Do not infer column layout from arbitrary reading order when the raw region hints are absent.

## Layout Modes

### Page Split Mode

Triggered when a page has both side regions needed to represent a split page:

- `left-page`
- `right-page`

Behavior:

- Create two synthetic `PageInfo` entries for the rendered document.
- Each synthetic page uses the bounding box of its matching region as its local canvas.
- Paragraphs, tables, and images whose bbox falls inside a side region are assigned to that synthetic page.
- Bboxes are rebased so that rendering and later geometry checks happen in the local page coordinate system.

Fallback:

- If only one side exists, or the regions are too small / malformed, keep the original page unchanged.
- If a node cannot be assigned confidently, keep it on the original page rather than inventing a split.

### Column Mode

Triggered when a page has both:

- `left-column`
- `right-column`

Behavior:

- Keep the page itself intact.
- Derive one shared `ColumnLayoutInfo` for the paragraph sequence that belongs to the column region span.
- Use the region bboxes to derive:
  - `count = 2`
  - `gap_pt` from the horizontal space between regions
  - `widths_pt` from the region widths
  - `equal_width` when both regions are comparable in width
- Attach that layout to the affected paragraphs so `html_exporter` renders a `document-column-group`.

Fallback:

- If the region geometry does not support a stable 2-column reading, leave the page in single-column flow.

## Data Flow

1. Raw ODL JSON is parsed into `PdfPreviewContext`.
2. Preview normalization reads `layout_regions` per page.
3. A region planning step classifies the page as:
   - split page
   - 2-column page
   - plain page
4. The document is rewritten in place:
   - page split rewrites `DocIR.pages` and `ParagraphIR.page_number`
   - column mode writes `ParagraphIR.column_layout`
5. Existing table geometry enrichment and visual-block promotion continue.
6. `DocIR.to_html()` renders the normalized document through the shared HTML exporter.

## Implementation Shape

The smallest useful implementation is a new internal planning layer inside the PDF preview normalization path.

Likely responsibilities:

- region classification
- page split planning
- paragraph/node reassignment
- bbox rebasing
- column layout derivation

The current `prepare_pdf_for_html(...)` orchestration can call this layer before or alongside the existing table geometry and visual-block promotion stages.

The key constraint is that the shared renderer should continue to see ordinary `DocIR`, `PageInfo`, `ParagraphIR`, `TableIR`, and `ColumnLayoutInfo` objects.

## Precedence Rules

When both page-split and column hints exist on the same physical page:

1. Page split wins first.
2. Column layout is then considered inside each resulting logical page segment.

That keeps the page-level split deterministic and prevents a single physical page from trying to act like both a duplex spread and a 2-column article at the same time.

## Failure Model

The pipeline should fail open.

Conditions that should skip region-driven layout:

- missing region bboxes
- only one side of a split is present
- regions overlap too much to define a stable split
- a paragraph/table/image cannot be assigned with enough confidence
- the derived column gap or widths are clearly nonsensical

In those cases, the current flow should remain unchanged rather than producing broken page structure.

## Testing Strategy

### Page Split Tests

- A page with `left-page` and `right-page` regions produces two rendered pages.
- Paragraphs on the left and right halves land on different synthetic pages.
- Bboxes are rebased into local page coordinates.
- The shared HTML exporter renders two `document-page` sections.

### Column Layout Tests

- A page with `left-column` and `right-column` regions produces `ParagraphIR.column_layout.count == 2`.
- The rendered HTML contains `document-column-group`.
- The generated `column-gap` and page-local widths are derived from the raw region geometry.

### Regression Tests

- No region hints means the current PDF preview output stays unchanged.
- Malformed or incomplete region data falls back to the current single-page flow.
- Table geometry and visual-block promotion still run after the region-driven layout step.

### Integration Tests

- Add a PDF preview case that exercises a split-page source.
- Add a PDF preview case that exercises a 2-column source.
- Keep existing PDF preview tests that assert the shared renderer path still works for plain pages.

## Non-Goals For This Change

- Reworking the shared HTML exporter.
- Changing DOCX or HWPX column parsing.
- Rebuilding OCR or raster extraction.
- Expanding the candidate grouping rules beyond region-aware side separation.

## Acceptance Criteria

This work is done when:

- PDF `left-page` / `right-page` regions create separate rendered pages.
- PDF `left-column` / `right-column` regions produce 2-column HTML output.
- The shared HTML exporter is still the only HTML renderer.
- Existing PDF preview behavior for pages without region hints is preserved.
