# document-processor PDF

PDF parsing uses the same public `DocIR` API as DOCX/HWP/HWPX.

## Parse PDF

```python
from document_processor import DocIR

doc = DocIR.from_file("/path/to/file.pdf", doc_type="pdf")
```

Optional PDF config is intentionally small:

```python
doc = DocIR.from_file(
    "/path/to/file.pdf",
    doc_type="pdf",
    config={
        "pages": "1,3,5-7",
        "include_header_footer": False,
        "image_quality": "high",  # standard, high, max
        "image_output": "embedded",  # embedded, external, off
    },
)
```

## Semantic Output

Use semantic output for parsing, chunking, RAG, and downstream processing.

```python
semantic = doc.to_semantic(format="dict")
semantic_json = doc.to_semantic(format="json", indent=2)
```

## HTML Preview

Use HTML output for preview rendering.

```python
html = doc.to_html(title="PDF Preview")
```
