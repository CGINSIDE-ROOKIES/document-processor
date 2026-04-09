"""Format-aware meta-derived render information for HTML output."""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from typing import Any

from .models import DocIR


@dataclass(slots=True)
class MetaRenderInfo:
    tag: str | None = None
    classes: list[str] = field(default_factory=list)
    data_attrs: list[str] = field(default_factory=list)
    render_table_grid: bool = False
    suppress: bool = False


def resolve_meta_render_info(
    doc_ir: DocIR,
    node: object,
    *,
    default_tag: str | None = None,
) -> MetaRenderInfo:
    # This stays intentionally node-local. Larger PDF layout decisions belong in
    # the parse/enrichment pipeline, not in the shared HTML exporter.
    resolved_doc_type = (doc_ir.source_doc_type or "").lower()
    if resolved_doc_type == "pdf":
        return _resolve_pdf_meta_render_info(node, default_tag=default_tag)
    return MetaRenderInfo(tag=default_tag)


def _resolve_pdf_meta_render_info(
    node: object,
    *,
    default_tag: str | None = None,
) -> MetaRenderInfo:
    meta = getattr(node, "meta", None)
    render_info = MetaRenderInfo(tag=default_tag)
    if meta is None:
        return render_info

    if _meta_value(meta, "hidden_text") is True:
        render_info.suppress = True

    if (node_type := _meta_value(meta, "source_type")):
        render_info.classes.append(f'semantic-{str(node_type).strip().lower().replace(" ", "-")}')
        render_info.data_attrs.append(f'data-source-type="{escape(str(node_type), quote=True)}"')

    if (source_id := _meta_value(meta, "source_id")) is not None:
        render_info.data_attrs.append(f'data-source-id="{source_id}"')
    if (page_number := _meta_value(meta, "page_number")) is not None:
        render_info.data_attrs.append(f'data-page-number="{page_number}"')
    if (heading_level := _meta_value(meta, "heading_level")) is not None:
        render_info.data_attrs.append(f'data-heading-level="{heading_level}"')
    if (linked_content_id := _meta_value(meta, "linked_content_id")) is not None:
        render_info.data_attrs.append(f'data-linked-content-id="{linked_content_id}"')
    if (previous_table_id := _meta_value(meta, "previous_table_id")) is not None:
        render_info.data_attrs.append(f'data-previous-table-id="{previous_table_id}"')
    if (next_table_id := _meta_value(meta, "next_table_id")) is not None:
        render_info.data_attrs.append(f'data-next-table-id="{next_table_id}"')
    if _meta_value(meta, "render_table_grid") is True:
        render_info.render_table_grid = True
        render_info.classes.append("render-table-grid")
    if (bounding_box := _meta_value(meta, "bounding_box")) is not None:
        left = _meta_value(bounding_box, "left_pt")
        bottom = _meta_value(bounding_box, "bottom_pt")
        right = _meta_value(bounding_box, "right_pt")
        top = _meta_value(bounding_box, "top_pt")
        if None not in (left, bottom, right, top):
            render_info.data_attrs.append(f'data-bbox="{left},{bottom},{right},{top}"')

    if _meta_value(meta, "source_type") == "heading":
        level = _meta_value(meta, "heading_level")
        render_info.tag = f"h{level}" if isinstance(level, int) and 1 <= level <= 6 else "h2"

    return render_info


def _meta_value(meta: Any, field_name: str) -> Any:
    if meta is None:
        return None
    return getattr(meta, field_name, None)


__all__ = [
    "MetaRenderInfo",
    "resolve_meta_render_info",
]
