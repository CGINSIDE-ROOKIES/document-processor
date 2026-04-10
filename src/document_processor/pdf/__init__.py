"""Public PDF API.

Keep the package surface intentionally small:
- ``parse_pdf_to_doc_ir`` for canonical structured parsing
- ``export_pdf_local_outputs`` for native ODL artifact export

Lower-level helpers still live under submodules, but are not re-exported here.
"""

from .config import PdfParseConfig
from .local_outputs import DEFAULT_LOCAL_FORMATS, PdfLocalOutputs, export_pdf_local_outputs
from .pipeline import parse_pdf_to_doc_ir

__all__ = [
    "DEFAULT_LOCAL_FORMATS",
    "PdfParseConfig",
    "PdfLocalOutputs",
    "export_pdf_local_outputs",
    "parse_pdf_to_doc_ir",
]
