from .builder import build_doc_ir_from_mapping
from .diagram import create_model_diagram, draw_model_diagram
from .models import DocIR, ImageAsset, ImageIR, PageInfo, ParagraphContentNode, ParagraphIR, RunIR, TableCellIR, TableIR

# Keep top-level PDF exports narrow. Advanced preview/context helpers remain in
# `document_processor.pdf.*` submodules so the main package stays easy to scan.
from .pdf import (
    DEFAULT_LOCAL_FORMATS,
    PdfLocalOutputs,
    PdfParseConfig,
    export_pdf_local_outputs,
    parse_pdf_to_doc_ir,
)
from .style_types import CellStyleInfo, ParaStyleInfo, RunStyleInfo, StyleMap, TableStyleInfo

__all__ = [
    "DEFAULT_LOCAL_FORMATS",
    "CellStyleInfo",
    "DocIR",
    "ImageAsset",
    "ImageIR",
    "PageInfo",
    "ParagraphContentNode",
    "ParagraphIR",
    "ParaStyleInfo",
    "PdfLocalOutputs",
    "PdfParseConfig",
    "export_pdf_local_outputs",
    "RunIR",
    "RunStyleInfo",
    "StyleMap",
    "TableCellIR",
    "TableIR",
    "TableStyleInfo",
    "build_doc_ir_from_mapping",
    "create_model_diagram",
    "draw_model_diagram",
    "parse_pdf_to_doc_ir",
]
