# document-processor PDF

PDF parser for `document-processor`.

```python
from document_processor import DocIR

doc = DocIR.from_file("/path/to/file.pdf", doc_type="pdf")
```

The PDF package focuses on:

- canonical PDF -> `DocIR` conversion
- shared `DocIR.to_html()` rendering
- OpenDataLoader local output export
- table/style fidelity improvements on top of shared `DocIR`


## Canonical PDF Parsing

Build a canonical `DocIR` from a PDF:

```python
from document_processor import DocIR

doc = DocIR.from_file("/path/to/file.pdf", doc_type="pdf")
```

This is the structured path used for:

- chunking
- RAG
- downstream structured processing

The canonical path keeps `DocIR` flat and format-agnostic.


## Exporting HTML

Render a parsed PDF to styled HTML:

```python
from document_processor import DocIR

doc = DocIR.from_file("/path/to/file.pdf", doc_type="pdf")
html = doc.to_html(title="PDF Preview")
```

PDF-specific extraction evidence is consumed during parsing/enrichment and written
back into `DocIR`. `DocIR.to_html()` then uses the same shared HTML renderer as
other document types.


## PDF Local Outputs

Expose native OpenDataLoader outputs side-by-side when needed:

```python
from document_processor.pdf import export_pdf_local_outputs

outputs = export_pdf_local_outputs(
    "/path/to/file.pdf",
    output_dir="./out/pdf-native",
)

raw_json = outputs.read_json()
native_html = outputs.read_text("html")
native_markdown = outputs.read_text("markdown")
```

This is useful when:

- you want the raw ODL JSON directly
- you want native ODL HTML/Markdown for comparison
- you are debugging the adapter or preview path


## PDF Notes

PDF uses a dedicated parse/enrich pipeline:

- `probe -> triage -> ODL -> dotted-rule preprocessing -> adapter -> preview context -> DocIR enrichment`

HTML rendering stays on the shared path:

- `DocIR -> shared html exporter`

The adapter preserves and normalizes:

- paragraph text
- `spans[]` -> `RunIR[]`
- table/cell/span structure
- run style fields such as font family, size, color, underline, strikethrough
- first-class DocIR page numbers and bounding boxes copied from ODL geometry

The enrichment path additionally uses raw ODL/pdfium-derived hints such as:

- `layout regions[]`
- `grid row boundaries`
- `grid column boundaries`
- visual block candidates

The core `DocIR` model stays shared. PDF-specific intermediate state is consumed
before rendering and projected into common DocIR/style fields where possible.


## Preview Fidelity Options

For visual preview experiments, you can opt into a more permissive ODL profile:

```python
from document_processor import DocIR

doc = DocIR.from_file(
    "/path/to/file.pdf",
    doc_type="pdf",
    config={
        "odl": {
            "keep_line_breaks": True,
            "preserve_whitespace": True,
        }
    },
)
html = doc.to_html(title="Preview Fidelity")
```

Recommended split:

- default `DocIR.from_file(..., doc_type="pdf")`
  - canonical parsing plus PDF layout/style enrichment
- `DocIR.to_html()`
  - shared HTML rendering path
- `keep_line_breaks`, `preserve_whitespace`
  - preview-fidelity options only

`preserve_whitespace` is best-effort. Some PDFs lose spacing before ODL normalization,
so this does not guarantee that double spaces will appear in raw JSON.


## Custom ODL JAR

You can point the PDF path at a custom OpenDataLoader CLI JAR:

```bash
export DOCUMENT_PROCESSOR_ODL_JAR=/abs/path/opendataloader-pdf-cli.jar
```

This is useful when testing a local `opendataloader-pdf` fork without replacing
the vendored JAR.
