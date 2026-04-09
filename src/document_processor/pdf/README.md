# PDF Module

This package contains the PDF-only path added on top of the existing `document_processor`
core parsers.

The important design choice is that PDF parsing is separate, but rendering is not:

- `DocIR.from_file(..., doc_type="pdf")` uses the dedicated PDF pipeline in this directory
- `DocIR.to_html()` still uses the shared `src/document_processor/html_exporter.py`
- PDF-specific render behavior is limited to metadata interpretation and optional table-border
  enrichment before the shared renderer runs

## Current Flow

1. `probe.py`
   Cheap page profiling with `pypdfium2`
2. `triage.py`
   Classifies pages as `structured` or `scan_like`
3. `odl/runner.py`
   Runs the vendored OpenDataLoader CLI JAR
4. `odl/adapter.py`
   Converts ODL JSON into `DocIR`
5. `enhancement/enrichment.py`
   Optionally rasterizes PDF pages again to infer missing table borders

The main entrypoint is:

- `parse_pdf_to_doc_ir()` in [pipeline.py](./pipeline.py)

Native ODL artifacts are exposed separately through:

- `export_pdf_local_outputs()` in [local_outputs.py](./local_outputs.py)

## What Lives Here

- `config.py`
  PDF parse config, ODL config, and triage config
- `parsing/probe.py`
  Lightweight PDF page profiling
- `parsing/triage.py`
  Scan-like vs structured routing rules
- `odl/runner.py`
  Local Java CLI wrapper around the vendored ODL JAR
- `odl/adapter.py`
  ODL JSON to `DocIR`
- `meta.py`
  PDF-specific metadata models and normalization helpers
- `local_outputs.py`
  Typed handles for native ODL `json` / `html` / `markdown` outputs
- `enhancement/border_inference.py`
  Grayscale raster sampling for cell-border inference
- `enhancement/enrichment.py`
  Applies inferred borders back onto `DocIR`

## Important Behaviors

### Shared HTML renderer stays in charge

There is no separate PDF HTML exporter in the current design.

Instead:

- `meta_render.py` reads PDF metadata and produces node-local render hints
- `html_exporter.py` remains the single HTML renderer for every format
- `DocIR.to_html()` optionally enriches PDF table borders before handing off to the shared renderer

This keeps the PDF work isolated from DOCX/HWP/HWPX parsing while avoiding a second
renderer codepath.

### Embedded images are preferred for the `DocIR` path

`parse_pdf_to_doc_ir()` defaults ODL `image_output` to `embedded` when no explicit value is
provided.

Reason:

- `DocIR` wants `ImageAsset` data available immediately
- embedded `data:` URIs can be turned into `DocIR.assets` directly
- native local output export remains the place where sidecar files are expected

### Table borders are best-effort

ODL JSON usually gives table structure but not full border CSS.

So the PDF path does two things:

- marks table metadata with `render_table_grid=True`
- optionally infers missing cell borders from rasterized page pixels

The enrichment step only fills edges that are still missing. It does not overwrite explicit
styles already present on `CellStyleInfo`.

## Touchpoints Outside This Directory

- `document_processor.models.DocIR.from_file(...)`
- `document_processor.models.DocIR.to_html(...)`
- `document_processor.meta_render`
- `document_processor.html_exporter`
- `document_processor.__init__`
- `tests/test_pdf_pipeline.py`
- `tests/test_pdf_enrichment.py`

## Current Limits

- Probe currently runs serially to keep the page-classification path simple and deterministic
- Table border inference is heuristic and focused on missing grid lines, not full visual fidelity
- `DocIR` parsing and native ODL local outputs intentionally stay as separate codepaths
