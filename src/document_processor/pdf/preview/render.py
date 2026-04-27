"""PDF preview HTML 렌더링 진입점.

외부 호출자는 이 파일의 함수만 사용하면 된다. 여기서는 PDF 파싱 결과
DocIR와 preview sidecar context를 받아 `prepare_pdf_for_html()`로 PDF 전용
보정을 적용한 뒤, 마지막 출력은 공통 `render_html_document()`에 맡긴다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...html_exporter import render_html_document
from ...models import DocIR
from .normalize import prepare_pdf_for_html
from .models import PdfPreviewContext


def render_pdf_html(
    path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    title: str | None = None,
    doc_id: str | None = None,
    doc_cls: type[DocIR] | None = None,
    **doc_kwargs: Any,
) -> str:
    """PDF 파일 경로 하나를 받아 파싱부터 HTML preview 렌더까지 수행한다."""
    from ..pipeline import _parse_pdf_to_doc_ir_with_preview

    doc_ir, preview_context = _parse_pdf_to_doc_ir_with_preview(
        path,
        config=config,
        doc_id=doc_id,
        doc_cls=doc_cls,
        **doc_kwargs,
    )
    return render_pdf_preview_html(doc_ir, preview_context=preview_context, title=title)


def render_pdf_preview_html(
    doc_ir: DocIR,
    *,
    preview_context: PdfPreviewContext | None = None,
    title: str | None = None,
) -> str:
    """이미 DocIR와 preview context를 가진 호출자가 쓰는 내부 렌더 헬퍼."""
    prepared_doc = doc_ir.model_copy(deep=True)
    prepared_context = preview_context.model_copy(deep=True) if preview_context is not None else None
    prepare_pdf_for_html(prepared_doc, preview_context=prepared_context)
    return render_html_document(prepared_doc, title=title)


def render_pdf_preview_html_from_file(
    path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    title: str | None = None,
    doc_id: str | None = None,
    doc_cls: type[DocIR] | None = None,
    **doc_kwargs: Any,
) -> str:
    """기존 API 이름을 유지하기 위한 호환용 alias."""
    return render_pdf_html(
        path,
        config=config,
        title=title,
        doc_id=doc_id,
        doc_cls=doc_cls,
        **doc_kwargs,
    )


__all__ = [
    "render_pdf_html",
    "render_pdf_preview_html",
    "render_pdf_preview_html_from_file",
]
