# PDF Parser DocIR Integration

This package's LLM-facing API uses stable `node_id` values as edit and annotation
targets. A PDF parser that builds `DocIR` should therefore generate `node_id`
directly for every addressable paragraph, run, table, cell, and image node.
Structured public calls should use `TextEdit`, `StructuralEdit`, and
`TextAnnotation` with those `node_id` values as `target_id`.

Core IR nodes no longer carry a separate structural ID field. Parser/native paths
belong in `NativeAnchor`; public operations use `node_id`.

## Identity Rules

- `node_id` is the public, stable identifier.
- `NativeAnchor.debug_path` is a readable diagnostic/source path, not a public target.
- `NativeAnchor.structural_path` should be the parser's best native locator.
- `NativeAnchor.text_hash` is for drift checks only. Do not derive identity from text alone.
- Existing nodes keep their `node_id` across edits.
- Inserted nodes get new `node_id` values.
- Split or merged nodes should carry explicit edit provenance in parser metadata if the PDF
  writer needs to reconcile them later.

Use a deterministic ID seed that includes a document fingerprint, node kind, and the
best available structural anchor. Avoid hashing only page/block ordinals unless the
PDF extraction order is stable for the parser version.

```python
import hashlib


PREFIX_BY_KIND = {
    "paragraph": "p",
    "run": "r",
    "table": "tbl",
    "cell": "cell",
    "image": "img",
}


def make_node_id(kind: str, doc_fingerprint: str, anchor: str) -> str:
    digest = hashlib.sha1(f"{doc_fingerprint}:{kind}:{anchor}".encode("utf-8")).hexdigest()[:16]
    return f"{PREFIX_BY_KIND[kind]}_{digest}"


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()
```

## Anchor Shape

Recommended `NativeAnchor` values for PDF-built nodes:

- `source_doc_type="pdf"`
- `node_kind`: the matching DocIR node kind.
- `debug_path`: readable virtual path such as `page[3]/block[12]/line[1]/span[2]`.
- `parent_debug_path`: containing paragraph, cell, table, or page path.
- `part_name`: source segment such as `page:3`.
- `structural_path`: parser-native locator, ideally stable across repeated extraction.
- `text_hash`: SHA-1 of the node text when the node has text.

For DOCX/HWPX/HWP work, keep using the local format docs under
`docs/document-spec-docs`: OOXML package parts such as `word/document.xml` should
populate `part_name` for DOCX, and OWPML body/section XML files such as
`Contents/section*.xml` should populate `part_name` for HWPX. PDF parsers should
follow the same principle with page or object-stream locators.

## Minimal Build Pattern

```python
from document_processor import DocIR, NativeAnchor, ParagraphIR, RunIR


def pdf_paragraph_to_ir(doc_fingerprint: str, page_index: int, block_index: int, spans: list[str]) -> ParagraphIR:
    paragraph_anchor = f"page[{page_index}]/block[{block_index}]"
    paragraph_text = "".join(spans)
    runs: list[RunIR] = []

    for span_index, span_text in enumerate(spans):
        span_anchor = f"{paragraph_anchor}/span[{span_index}]"
        runs.append(
            RunIR(
                node_id=make_node_id("run", doc_fingerprint, span_anchor),
                text=span_text,
                native_anchor=NativeAnchor(
                    source_doc_type="pdf",
                    node_kind="run",
                    debug_path=span_anchor,
                    parent_debug_path=paragraph_anchor,
                    part_name=f"page:{page_index}",
                    structural_path=span_anchor,
                    text_hash=text_hash(span_text),
                ),
            )
        )

    return ParagraphIR(
        node_id=make_node_id("paragraph", doc_fingerprint, paragraph_anchor),
        text=paragraph_text,
        page_number=page_index + 1,
        content=runs,
        native_anchor=NativeAnchor(
            source_doc_type="pdf",
            node_kind="paragraph",
            debug_path=paragraph_anchor,
            part_name=f"page:{page_index}",
            structural_path=paragraph_anchor,
            text_hash=text_hash(paragraph_text),
        ),
    )


doc = DocIR(
    source_doc_type="pdf",
    paragraphs=[
        pdf_paragraph_to_ir(
            doc_fingerprint="sha1-of-original-pdf-bytes",
            page_index=0,
            block_index=0,
            spans=["Hello ", "World"],
        )
    ],
).ensure_node_identity()
```

## Edit, Annotation, And Re-map Flow

1. The LLM reads through `read_document(...)` or `get_document_context(...)`.
2. The LLM emits `TextEdit(target_id=<node_id>, expected_text=..., new_text=...)`
   for exact text replacement, or `StructuralEdit(target_id=<node_id>, operation=...)`
   for insert/remove/table operations.
3. The API validates the request against the current `DocIR`.
4. The edit layer resolves `target_id` directly to the DocIR node for in-memory mutation.
5. Existing node IDs remain stable. Inserted nodes receive new IDs, while
   `NativeAnchor.structural_path` can be refreshed to the new parser/native path.
6. For annotations, the LLM emits `TextAnnotation(target_id=<node_id>, target_kind=..., selected_text=..., occurrence_index=...)`.
7. The annotation API validates exact selected text and returns resolved offsets through `validate_text_annotations(...)` or `render_review_html(...)`.
8. The PDF writer or external synchronizer uses `NativeAnchor.structural_path`,
   `NativeAnchor.debug_path`, and `text_hash` to find the original extracted object or detect drift.

For extensive edits, do not re-ID unchanged nodes. Preserve IDs for nodes whose source
anchor still represents the same logical content. New content receives new IDs, and style
inheritance should be explicit: copy the style from the edited run/paragraph or from the
nearest containing paragraph, then record that provenance in metadata if the downstream
PDF writer needs it.
