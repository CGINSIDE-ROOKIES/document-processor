# document-processor

Installable structural document parser for `hwp`, `hwpx`, and `docx`.

```python
from document_processor import DocIR

doc = DocIR.from_file("/path/to/file.docx")
```

The package focuses on:

- document parsing
- style extraction
- structural IR creation
- embedded image extraction for `docx` and `hwpx`


## Images in the IR

Parsed image binaries are stored once on `DocIR.assets`, and paragraph-like nodes keep ordered
`content` entries so text, tables, and images can be rendered in source order.

```python
from document_processor import DocIR

doc = DocIR.from_file("/path/to/file.docx")
first_asset = next(iter(doc.assets.values()))
html = doc.to_html()
```


## Exporting HTML

Render a parsed document to styled HTML:

```python
from document_processor import DocIR

doc = DocIR.from_file("/path/to/file.docx")
html = doc.to_html(title="Preview")
```


## Visualizing the Models

Install the visualization extra first:

```bash
pip install "document-processor[viz]"
```

Erdantic also needs Graphviz available on the system.

Render the default `DocIR` model diagram:

```bash
document-processor-diagram --out docir.svg
```

Render a custom model by dotted import path:

```bash
document-processor-diagram --model document_processor.DocIR --out docir.png
```

Or use the Python helper:

```python
from document_processor import draw_model_diagram

draw_model_diagram(out="docir.svg")
```
