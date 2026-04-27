# document-processor PDF

Installable local-mode PDF parser for `document-processor`.

```python
from document_processor import DocIR

doc = DocIR.from_file("/path/to/file.pdf", doc_type="pdf")
```

PDF support is available as an optional local-mode extra:

```bash
pip install "document-processor[pdf-local]"
```

The PDF package focuses on:

- canonical PDF -> `DocIR` conversion
- PDF HTML preview rendering
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

For PDF documents, `DocIR.to_html()` automatically takes the higher-fidelity PDF
preview path when `source_path` is available.

That means the HTML path can reflect PDF-only preview hints such as:

- `layout regions`
- `reading order index`
- raw table geometry

If a PDF `DocIR` has no usable `source_path`, `to_html()` falls back to the shared
canonical HTML renderer.


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

PDF uses a dedicated pipeline:

- `probe -> triage -> ODL -> adapter -> DocIR`

When HTML is requested, the PDF path adds one more preview step:

- `DocIR -> pdf.preview.normalize/render -> shared html exporter`

The adapter preserves and normalizes:

- paragraph text
- `spans[]` -> `RunIR[]`
- table/cell/span structure
- run style fields such as font family, size, color, underline, strikethrough
- first-class DocIR page numbers and bounding boxes copied from ODL geometry

The preview path additionally uses raw ODL-derived hints such as:

- `layout regions[]`
- `grid row boundaries`
- `grid column boundaries`
- visual block candidates

The core `DocIR` model stays shared. Current PDF-specific state is split into:

- canonical structure in `DocIR`
- style-projectable results in `StyleMap`
- preview-only layout hints in the runtime `PdfPreviewContext`

The preview renderer uses explicit runtime preview context. Preview layout hints are not persisted in `DocIR.meta`.


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
  - canonical / chunking / RAG-safe path
- `DocIR.to_html()`
  - preview-oriented HTML path for parsed PDFs
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
