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


for specific uses, you can add metadata for processing (eg. feeding LLMs, RAG, analysis and such)

All IR models include a `.meta` field for this purpose.
```python
for file_ in files:
    doc = DocIR.from_file(file_)

    class MyMetaData(BaseModel):
        a: int = 1
        b: str = "test"

    # add your processing logic
    metainfo = MyMetaData(a=2)
    doc.paragraphs[0].runs[0].meta = metainfo

    with \
        open((out_dir / file_.stem).with_suffix(".json"), "w", encoding="utf-8") as json_f, \
        open((out_dir / file_.stem).with_suffix(".html"), "w", encoding="utf-8") as html_f:
        
        json.dump(doc.model_dump(mode="json"), json_f, indent=4, ensure_ascii=False)
        html_f.write(doc.to_html())

    print(f"completed: {file_}")
```
> **! Note !**
>
> Metadata obj. needs to extend Pydantic BaseModels. If not, it'll thow a validation error.


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

Render a package-scope diagram with IR fields/methods plus the main `core/`
modules:

```bash
document-processor-diagram --kind package --out package.svg
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

---

ERD for the pydantic models

![diagram](docir.svg)