"""PDF parsing pipeline.

Public surface:
- ``parse_pdf_to_doc_ir()`` for canonical DocIR construction

Internal helpers:
- preview-sidecar parsing used only by the PDF HTML preview path
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import DocIR, PageInfo
from .config import PdfParseConfig
from .enhancement import enrich_pdf_table_borders
from .meta import PdfDocumentMeta
from .odl import build_doc_ir_from_odl_result, run_odl_json
from .parsing import PageClass, PdfProfile, decide_page, probe_pdf
from .preview import PdfPreviewContext, build_pdf_preview_context


def parse_pdf_to_doc_ir(
    path: str | Path,
    *,
    config: PdfParseConfig | dict[str, Any] | None = None,
    doc_id: str | None = None,
    doc_cls: type[DocIR] | None = None,
    **doc_kwargs: Any,
) -> DocIR:
    # Canonical PDF parse path:
    # raw ODL JSON -> DocIR
    # Preview-only sidecar data is intentionally dropped here.
    doc_ir, _preview_context = _parse_pdf_with_optional_preview(
        path,
        config=config,
        doc_id=doc_id,
        doc_cls=doc_cls,
        **doc_kwargs,
    )
    return doc_ir


def _build_pdf_preview_context_for_path(
    path: str | Path,
    *,
    config: PdfParseConfig | dict[str, Any] | None = None,
) -> PdfPreviewContext:
    """Internal helper for preview-only ODL sidecar loading."""
    resolved_config = (
        config
        if isinstance(config, PdfParseConfig)
        else PdfParseConfig.model_validate(config or {})
    )
    source_path = Path(path)
    profile = probe_pdf(source_path)
    if profile is None:
        raise RuntimeError("PDF probe failed before ODL parsing.")

    structured_pages = [
        decision.page_number
        for decision in (
            decide_page(page_profile, resolved_config.triage)
            for page_profile in profile.page_profiles
        )
        if decision.page_class == PageClass.STRUCTURED
    ]
    if not structured_pages:
        return PdfPreviewContext()

    raw_document = run_odl_json(
        source_path,
        {
            **resolved_config.odl.model_dump(),
            "pages": structured_pages,
            "image_output": resolved_config.odl.image_output or "embedded",
        },
    )
    return build_pdf_preview_context(raw_document)


def _parse_pdf_to_doc_ir_with_preview(
    path: str | Path,
    *,
    config: PdfParseConfig | dict[str, Any] | None = None,
    doc_id: str | None = None,
    doc_cls: type[DocIR] | None = None,
    **doc_kwargs: Any,
) -> tuple[DocIR, PdfPreviewContext]:
    # Internal preview parse path:
    # raw ODL JSON -> DocIR + PdfPreviewContext sidecar
    return _parse_pdf_with_optional_preview(
        path,
        config=config,
        doc_id=doc_id,
        doc_cls=doc_cls,
        **doc_kwargs,
    )


def _parse_pdf_with_optional_preview(
    path: str | Path,
    *,
    config: PdfParseConfig | dict[str, Any] | None = None,
    doc_id: str | None = None,
    doc_cls: type[DocIR] | None = None,
    **doc_kwargs: Any,
) -> tuple[DocIR, PdfPreviewContext]:
    resolved_config = (
        config
        if isinstance(config, PdfParseConfig)
        else PdfParseConfig.model_validate(config or {})
    )
    source_path = Path(path)
    profile = probe_pdf(source_path)
    if profile is None:
        raise RuntimeError("PDF probe failed before ODL parsing.")

    page_decisions = [
        decide_page(page_profile, resolved_config.triage)
        for page_profile in profile.page_profiles
    ]
    structured_pages = [
        decision.page_number
        for decision in page_decisions
        if decision.page_class == PageClass.STRUCTURED
    ]

    resolved_doc_cls = doc_cls or DocIR
    preview_context = PdfPreviewContext()
    if structured_pages:
        raw_document = run_odl_json(
            source_path,
            {
                **resolved_config.odl.model_dump(),
                "pages": structured_pages,
                # Prefer embedded image data for the DocIR path so ImageAsset entries
                # can be materialized without depending on sidecar files on disk.
                "image_output": resolved_config.odl.image_output or "embedded",
            },
        )
        # The same raw document feeds two outputs:
        # 1. canonical DocIR
        # 2. preview-only sidecar context
        preview_context = build_pdf_preview_context(raw_document)
        doc_ir = build_doc_ir_from_odl_result(
            raw_document,
            source_path=str(source_path),
            doc_id=doc_id,
            doc_cls=resolved_doc_cls,
            **doc_kwargs,
        )
    else:
        resolved_doc_id = doc_id or source_path.stem
        doc_ir = resolved_doc_cls(
            doc_id=resolved_doc_id,
            source_path=str(source_path),
            source_doc_type="pdf",
            pages=[],
            paragraphs=[],
            assets={},
            **doc_kwargs,
        )

    _apply_probe_page_sizes(doc_ir, profile=profile)
    if resolved_config.infer_table_borders:
        # Parse-time border inference is optional because it rasterizes pages and
        # is noticeably more expensive than the base ODL conversion path.
        enrich_pdf_table_borders(
            doc_ir,
            pdf_path=source_path,
            dpi=resolved_config.table_border_dpi,
        )
    document_meta = (
        doc_ir.meta.model_copy(deep=True)
        if isinstance(doc_ir.meta, PdfDocumentMeta)
        else PdfDocumentMeta()
    )
    document_meta.structured_pages = structured_pages
    document_meta.scan_like_pages = [
        decision.page_number
        for decision in page_decisions
        if decision.page_class == PageClass.SCAN_LIKE
    ]
    doc_ir.meta = document_meta
    return doc_ir, preview_context


def _apply_probe_page_sizes(doc_ir: DocIR, *, profile: PdfProfile) -> None:
    page_map = {page.page_number: page for page in doc_ir.pages}
    for page_profile in profile.page_profiles:
        page = page_map.get(page_profile.page_number)
        if page is None:
            page = PageInfo(page_number=page_profile.page_number)
            doc_ir.pages.append(page)
            page_map[page.page_number] = page
        if page.width_pt is None:
            page.width_pt = page_profile.page_width_pt
        if page.height_pt is None:
            page.height_pt = page_profile.page_height_pt
    doc_ir.pages.sort(key=lambda page: page.page_number)


__all__ = ["parse_pdf_to_doc_ir"]
