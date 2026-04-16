"""PDF preview HTML rendering helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...html_exporter import (
    _page_content_style,
    _page_style,
    _render_html_document_shell,
    _render_paragraph,
    render_html_document,
)
from ...models import DocIR, PageInfo, ParagraphIR
from ..meta import PdfBoundingBox
from .layout import (
    _build_logical_pages_for_page,
    _logical_page_page_info,
    _logical_page_paragraphs,
    _logical_page_preview_context,
)
from .models import (
    PdfPreviewContext,
    PdfPreviewVisualBlockCandidate,
    _AssignedCandidate,
    _PreviewRenderNode,
)
from .shared import _bbox_area, _shared_page_content_margins


def _page_content_margins(page: PageInfo) -> tuple[float, float, float, float]:
    return _shared_page_content_margins(page)


def _render_preview_body(doc_ir: DocIR, *, preview_context: PdfPreviewContext) -> str:
    if not doc_ir.pages:
        return "\n\n".join(_render_paragraph(doc_ir, paragraph) for paragraph in doc_ir.paragraphs)

    paragraphs_by_page: dict[int, list[ParagraphIR]] = {}
    unpaged: list[ParagraphIR] = []

    for paragraph in doc_ir.paragraphs:
        if paragraph.page_number is None:
            unpaged.append(paragraph)
            continue
        paragraphs_by_page.setdefault(paragraph.page_number, []).append(paragraph)

    parts: list[str] = []
    next_logical_page_number = 1
    for page in doc_ir.pages:
        page_paragraphs = paragraphs_by_page.get(page.page_number, [])
        page_regions = [region for region in preview_context.layout_regions if region.page_number == page.page_number]
        logical_pages = _build_logical_pages_for_page(
            page,
            page_regions,
            page_paragraphs=page_paragraphs,
            starting_page_number=next_logical_page_number,
        )
        next_logical_page_number += len(logical_pages)

        for logical_page in logical_pages:
            logical_page_info = _logical_page_page_info(logical_page, source_page=page)
            logical_page_paragraphs = _logical_page_paragraphs(
                page,
                page_paragraphs,
                page_regions=page_regions,
                logical_pages=logical_pages,
                logical_page=logical_page,
            )
            logical_preview_context = _logical_page_preview_context(
                page,
                preview_context,
                page_regions=page_regions,
                logical_pages=logical_pages,
                logical_page=logical_page,
            )
            content_html = _render_preview_page_content(
                doc_ir,
                logical_page_info,
                logical_page_paragraphs,
                preview_context=logical_preview_context,
            )
            parts.append(
                f'<section class="document-page" data-page-number="{logical_page.page_number}" '
                f'data-physical-page-number="{logical_page.physical_page_number}" '
                f'data-logical-page-type="{logical_page.logical_page_type}" '
                f'style="{_page_style(logical_page_info)}">'
                f'<div class="document-page__content" style="{_page_content_style(logical_page_info)}">{content_html or "&nbsp;"}</div>'
                "</section>"
            )

    if unpaged:
        parts.append(
            '<section class="document-unpaged">'
            + "\n\n".join(_render_paragraph(doc_ir, paragraph) for paragraph in unpaged)
            + "</section>"
        )

    return "\n".join(parts)


def _render_preview_page_content(
    doc_ir: DocIR,
    page: PageInfo,
    page_paragraphs: list[ParagraphIR],
    *,
    preview_context: PdfPreviewContext,
) -> str:
    from .compose import _compose_logical_page, _page_long_rule_candidates

    long_rule_overlays = _render_long_rule_overlays(
        page,
        _page_long_rule_candidates(preview_context, page_number=page.page_number),
    )
    if not page_paragraphs and not long_rule_overlays:
        return ""

    composition = _compose_logical_page(
        page,
        page_paragraphs,
        page_regions=[],
        preview_context=preview_context,
    )
    candidate_overlays = _render_page_positioned_candidates(
        doc_ir,
        page,
        [
            assigned_candidate
            for assigned_candidate in composition.assigned_candidates
            if id(assigned_candidate.candidate) not in composition.promoted_candidate_ids
        ],
    )
    if not composition.ordered_paragraphs:
        return (
            '<div class="pdf-preview-page-layer" style="position:relative;flex:1 1 auto;height:100%">'
            f"{long_rule_overlays}{candidate_overlays}"
            "</div>"
        )

    flow_html = "\n\n".join(
        _render_paragraph(doc_ir, paragraph)
        for paragraph in composition.ordered_paragraphs
    )
    return (
        '<div class="pdf-preview-page-layer" style="position:relative;flex:1 1 auto;height:100%">'
        f"{long_rule_overlays}{candidate_overlays}"
        '<div class="pdf-preview-page-flow" style="position:relative;z-index:2">'
        f"{flow_html}"
        "</div></div>"
    )


def _page_content_bbox(page: PageInfo) -> PdfBoundingBox | None:
    if page.width_pt is None or page.height_pt is None:
        return None
    margin_top, margin_right, margin_bottom, margin_left = _page_content_margins(page)
    return PdfBoundingBox(
        left_pt=margin_left,
        bottom_pt=margin_bottom,
        right_pt=max(page.width_pt - margin_right, margin_left),
        top_pt=max(page.height_pt - margin_top, margin_bottom),
    )


def _render_page_positioned_candidates(
    doc_ir: DocIR,
    page: PageInfo,
    assigned_candidates: list[_AssignedCandidate],
) -> str:
    if not assigned_candidates:
        return ""

    content_bbox = _page_content_bbox(page)
    if content_bbox is None:
        return ""

    ordered_candidates = sorted(
        assigned_candidates,
        key=lambda item: (
            -_bbox_area(item.candidate.bounding_box),
            item.order_key,
        ),
    )
    parts = [
        _render_positioned_candidate(
            doc_ir,
            assigned_candidate,
            group_bbox=content_bbox,
        )
        for assigned_candidate in ordered_candidates
    ]
    parts = [part for part in parts if part]
    if not parts:
        return ""
    return (
        '<div class="pdf-preview-page-candidates" '
        'style="position:absolute;inset:0;z-index:1;pointer-events:none">'
        + "".join(parts)
        + "</div>"
    )


def _render_positioned_candidate(
    doc_ir: DocIR,
    assigned_candidate: _AssignedCandidate,
    *,
    group_bbox: PdfBoundingBox,
) -> str:
    candidate = assigned_candidate.candidate
    bbox = candidate.bounding_box
    width_pt = max(bbox.right_pt - bbox.left_pt, 0.0)
    height_pt = max(bbox.top_pt - bbox.bottom_pt, 0.0)
    left_offset_pt = max(bbox.left_pt - group_bbox.left_pt, 0.0)
    top_offset_pt = max(group_bbox.top_pt - bbox.top_pt, 0.0)

    content_blocks: list[tuple[tuple[int, int, int], str]] = []
    for paragraph_node in assigned_candidate.paragraph_nodes:
        if paragraph_node.paragraph is None:
            continue
        content_blocks.append((paragraph_node.order_key, _render_paragraph(doc_ir, paragraph_node.paragraph)))

    auxiliary_nodes = sorted(
        assigned_candidate.table_nodes + assigned_candidate.image_nodes + assigned_candidate.run_nodes,
        key=lambda node: node.order_key,
    )
    content_blocks.extend(_render_auxiliary_nodes(doc_ir, auxiliary_nodes))
    content_blocks.sort(key=lambda item: item[0])
    inner_html = "\n\n".join(html for _, html in content_blocks if html) or "&nbsp;"

    wrapper_styles = [
        "position:absolute",
        "box-sizing:border-box",
        f"left:{left_offset_pt:.1f}pt",
        f"top:{top_offset_pt:.1f}pt",
        "padding:6pt 8pt",
        "border:1px solid #4a4f57",
        "background:transparent",
        f"width:{width_pt:.1f}pt",
    ]
    if height_pt > 0.0:
        wrapper_styles.append(f"min-height:{height_pt:.1f}pt")

    overlay_html = _render_candidate_child_cell_overlays(candidate)
    content_html = (
        '<div class="pdf-preview-candidate__content" '
        'style="position:relative;z-index:1">'
        f"{inner_html}</div>"
    )
    return (
        f'<div class="pdf-preview-candidate pdf-preview-candidate--{candidate.candidate_type}" '
        f'data-candidate-type="{candidate.candidate_type}" '
        f'style="{";".join(wrapper_styles)}">'
        f"{overlay_html}{content_html}</div>"
    )


def _render_auxiliary_nodes(
    doc_ir: DocIR,
    nodes: list[_PreviewRenderNode],
) -> list[tuple[tuple[int, int, int], str]]:
    if not nodes:
        return []

    grouped: dict[str, list[_PreviewRenderNode]] = {}
    group_order: dict[str, tuple[int, int, int]] = {}
    group_para_style: dict[str, Any] = {}
    for node in nodes:
        group_key = node.parent_paragraph_id or node.unit_id
        grouped.setdefault(group_key, []).append(node)
        group_order[group_key] = min(group_order.get(group_key, node.order_key), node.order_key)
        if group_key not in group_para_style:
            group_para_style[group_key] = node.parent_para_style

    rendered: list[tuple[tuple[int, int, int], str]] = []
    for group_key, group_nodes in grouped.items():
        content_nodes: list[Any] = []
        for node in sorted(group_nodes, key=lambda item: item.order_key):
            if node.table is not None:
                content_nodes.append(node.table)
            elif node.image is not None:
                content_nodes.append(node.image)
            elif node.run is not None:
                content_nodes.append(node.run)
        if not content_nodes:
            continue
        wrapper_paragraph = ParagraphIR(
            unit_id=group_key,
            text="",
            bbox=None,
            para_style=group_para_style.get(group_key),
            content=content_nodes,
        )
        wrapper_paragraph.recompute_text()
        rendered.append((group_order[group_key], _render_paragraph(doc_ir, wrapper_paragraph)))
    return rendered


def _render_candidate_child_cell_overlays(candidate: PdfPreviewVisualBlockCandidate) -> str:
    return ""


def _render_long_rule_overlays(page: PageInfo, long_rules: list[PdfPreviewVisualBlockCandidate]) -> str:
    if not long_rules:
        return ""
    if page.height_pt is None:
        return ""

    margin_top, _margin_right, _margin_bottom, margin_left = _page_content_margins(page)
    parts: list[str] = []
    for candidate in long_rules:
        bbox = candidate.bounding_box
        left = max(bbox.left_pt - margin_left, 0.0)
        top = max(page.height_pt - bbox.top_pt - margin_top, 0.0)
        width = max(bbox.right_pt - bbox.left_pt, 0.0)
        height = max(bbox.top_pt - bbox.bottom_pt, 0.0)
        if width <= 0.0 or height <= 0.0:
            continue
        parts.append(
            '<div class="pdf-preview-long-rule" '
            f'style="position:absolute;left:{left:.1f}pt;top:{top:.1f}pt;'
            f'width:{width:.1f}pt;height:{height:.1f}pt;'
            'background:#4a4f57;opacity:0.8"></div>'
        )
    if not parts:
        return ""
    return (
        '<div class="pdf-preview-long-rules" '
        'style="position:absolute;inset:0;z-index:0;pointer-events:none">'
        + "".join(parts)
        + "</div>"
    )


def render_pdf_html(
    path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    title: str | None = None,
    doc_id: str | None = None,
    doc_cls: type[DocIR] | None = None,
    **doc_kwargs: Any,
) -> str:
    """Public PDF -> HTML preview entrypoint."""
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
    preview_context: PdfPreviewContext,
    title: str | None = None,
) -> str:
    """Internal helper for callers that already hold preview sidecar data."""
    from .prepare import prepare_pdf_for_html

    prepared_doc = doc_ir.model_copy(deep=True)
    prepared_context = preview_context.model_copy(deep=True)
    prepare_pdf_for_html(prepared_doc, preview_context=prepared_context)
    if not prepared_context.visual_block_candidates:
        return render_html_document(prepared_doc, title=title)
    body = _render_preview_body(prepared_doc, preview_context=prepared_context)
    resolved_title = title or prepared_doc.doc_id or "Document"
    return _render_html_document_shell(title=resolved_title, body=body)


def render_pdf_preview_html_from_file(
    path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    title: str | None = None,
    doc_id: str | None = None,
    doc_cls: type[DocIR] | None = None,
    **doc_kwargs: Any,
) -> str:
    """Backward-compatible alias around :func:`render_pdf_html`."""
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
