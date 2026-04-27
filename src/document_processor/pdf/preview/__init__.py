"""PDF HTML preview 진입점.

이 패키지는 PDF를 DocIR로 만든 뒤 HTML preview에 맞게 약간 보정하는
전용 레이어다. 공통 HTML 렌더러는 그대로 쓰고, PDF에서만 얻을 수 있는
ODL layout region, bbox, pdfium 시각 primitive 같은 힌트를 여기서 해석한다.
"""

from __future__ import annotations

from .normalize import prepare_pdf_for_html
from .render import render_pdf_html, render_pdf_preview_html, render_pdf_preview_html_from_file

__all__ = [
    "prepare_pdf_for_html",
    "render_pdf_html",
    "render_pdf_preview_html",
    "render_pdf_preview_html_from_file",
]
