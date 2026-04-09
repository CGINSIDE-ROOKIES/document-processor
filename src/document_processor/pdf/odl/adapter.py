"""ODL raw JSON to DocIR conversion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...models import DocIR, ImageAsset, ImageIR, PageInfo, ParagraphIR, RunIR, TableCellIR, TableIR
from ...style_types import CellStyleInfo, ParaStyleInfo, RunStyleInfo, TableStyleInfo
from ..meta import (
    PdfNodeMeta,
    build_pdf_node_meta,
    coerce_float,
    coerce_int,
    extract_text_from_odl_children,
    extract_text_from_odl_node,
    node_value,
    normalize_align,
    pixels_to_points,
    sanitize_css_color,
)


def _para_style_from_node(node: dict[str, Any]) -> ParaStyleInfo | None:
    style = ParaStyleInfo(
        align=normalize_align(
            node_value(node, "align", "alignment", "text align", "horizontal align")
        ),
        left_indent_pt=coerce_float(node_value(node, "left indent pt", "left indent")),
        right_indent_pt=coerce_float(node_value(node, "right indent pt", "right indent")),
        first_line_indent_pt=coerce_float(
            node_value(node, "first line indent pt", "first line indent")
        ),
        hanging_indent_pt=coerce_float(
            node_value(node, "hanging indent pt", "hanging indent")
        ),
    )
    return style if style.model_dump(exclude_defaults=True, exclude_none=True) else None


def _run_style_from_node(node: dict[str, Any]) -> RunStyleInfo | None:
    style = RunStyleInfo(
        bold=_coerce_bool(node_value(node, "bold")) or False,
        italic=_coerce_bool(node_value(node, "italic")) or False,
        underline=_coerce_bool(node_value(node, "underline")) or False,
        strikethrough=_coerce_bool(node_value(node, "strikethrough")) or False,
        superscript=_coerce_bool(node_value(node, "superscript")) or False,
        subscript=_coerce_bool(node_value(node, "subscript")) or False,
        size_pt=coerce_float(node.get("font size")),
        color=sanitize_css_color(node.get("text color")),
        highlight=sanitize_css_color(node_value(node, "highlight color", "background color")),
    )
    return style if style.model_dump(exclude_defaults=True, exclude_none=True) else None


def _cell_style_from_node(node: dict[str, Any]) -> CellStyleInfo | None:
    width_pt, height_pt = _display_size_from_node(node)
    style = CellStyleInfo(
        background=sanitize_css_color(node.get("background color")),
        vertical_align=_normalize_vertical_align(node_value(node, "vertical align")),
        horizontal_align=normalize_align(node_value(node, "horizontal align", "text align")),
        width_pt=width_pt,
        height_pt=height_pt,
        rowspan=coerce_int(node.get("row span")) or 1,
        colspan=coerce_int(node.get("column span")) or 1,
    )
    return style if style.model_dump(exclude_defaults=True, exclude_none=True) else None


def _table_style_from_node(node: dict[str, Any]) -> TableStyleInfo | None:
    width_pt, height_pt = _display_size_from_node(node)
    style = TableStyleInfo(
        row_count=coerce_int(node.get("number of rows")) or 0,
        col_count=coerce_int(node.get("number of columns")) or 0,
        width_pt=width_pt,
        height_pt=height_pt,
    )
    return style if style.model_dump(exclude_defaults=True, exclude_none=True) else None


def _display_size_from_node(node: dict[str, Any]) -> tuple[float | None, float | None]:
    width_pt = coerce_float(node_value(node, "display width pt", "width pt"))
    height_pt = coerce_float(node_value(node, "display height pt", "height pt"))
    if width_pt is not None or height_pt is not None:
        return width_pt, height_pt
    dpi = node_value(node, "dpi")
    return (
        pixels_to_points(node_value(node, "width px", "image width"), dpi),
        pixels_to_points(node_value(node, "height px", "image height"), dpi),
    )


def _page_number_from_node(node: dict[str, Any]) -> int | None:
    return coerce_int(node.get("page number"))


def _paragraph_from_text_node(
    node: dict[str, Any],
    *,
    unit_id: str,
) -> ParagraphIR | None:
    text = extract_text_from_odl_node(node).strip()
    if not text and node.get("type") not in {"caption", "header", "footer"}:
        return None
    content = [
        RunIR(
            unit_id=f"{unit_id}.r1",
            text=text,
            run_style=_run_style_from_node(node),
            meta=build_pdf_node_meta(node),
        )
    ] if text else []
    return ParagraphIR(
        unit_id=unit_id,
        text=text,
        page_number=_page_number_from_node(node),
        para_style=_para_style_from_node(node),
        meta=build_pdf_node_meta(node),
        content=content,
    )


def _append_image_asset(
    assets: dict[str, ImageAsset],
    *,
    node: dict[str, Any],
    unit_id: str,
) -> None:
    data_uri = node.get("data")
    if not isinstance(data_uri, str):
        return
    mime_type = "application/octet-stream"
    data_base64: str | None = None
    if data_uri.startswith("data:") and ";base64," in data_uri:
        mime_type = data_uri[5:].split(";base64,", 1)[0] or mime_type
        data_base64 = data_uri.split(";base64,", 1)[1]
    if not data_base64:
        return

    image_id = f"odl-img-{unit_id}"
    assets[image_id] = ImageAsset(
        mime_type=mime_type,
        filename=None,
        data_base64=data_base64,
        intrinsic_width_px=coerce_int(node_value(node, "width px", "image width")),
        intrinsic_height_px=coerce_int(node_value(node, "height px", "image height")),
        meta=build_pdf_node_meta(node),
    )


def _image_paragraph(
    node: dict[str, Any],
    *,
    unit_id: str,
    assets: dict[str, ImageAsset],
) -> ParagraphIR:
    _append_image_asset(assets, node=node, unit_id=unit_id)
    display_width_pt, display_height_pt = _display_size_from_node(node)
    return ParagraphIR(
        unit_id=unit_id,
        text="",
        page_number=_page_number_from_node(node),
        para_style=_para_style_from_node(node),
        meta=build_pdf_node_meta(node),
        content=[
            ImageIR(
                unit_id=f"{unit_id}.img1",
                image_id=f"odl-img-{unit_id}",
                alt_text=node_value(node, "alt text"),
                title=node_value(node, "title", "name"),
                display_width_pt=display_width_pt,
                display_height_pt=display_height_pt,
                meta=build_pdf_node_meta(node),
            )
        ],
    )


def _cell_paragraphs(
    children: list[dict[str, Any]],
    *,
    cell_unit_id: str,
    default_page_number: int | None,
    assets: dict[str, ImageAsset],
) -> list[ParagraphIR]:
    paragraphs: list[ParagraphIR] = []
    child_index = 0
    for child in children:
        child_type = child.get("type")
        unit_id = f"{cell_unit_id}.p{child_index + 1}"
        if child_type == "table":
            child_index += 1
            paragraphs.append(
                ParagraphIR(
                    unit_id=unit_id,
                    text="",
                    page_number=_page_number_from_node(child) or default_page_number,
                    para_style=_para_style_from_node(child),
                    meta=build_pdf_node_meta(child),
                    content=[_table_node_to_ir(child, unit_id=f"{unit_id}.tbl1", assets=assets)],
                )
            )
            continue
        if child_type == "image":
            child_index += 1
            paragraph = _image_paragraph(child, unit_id=unit_id, assets=assets)
            if paragraph.page_number is None:
                paragraph.page_number = default_page_number
            paragraphs.append(paragraph)
            continue
        paragraph = _paragraph_from_text_node(child, unit_id=unit_id)
        if paragraph is None:
            continue
        child_index += 1
        if paragraph.page_number is None:
            paragraph.page_number = default_page_number
        paragraphs.append(paragraph)
    if not paragraphs:
        paragraphs.append(
            ParagraphIR(
                unit_id=f"{cell_unit_id}.p1",
                text="",
                page_number=default_page_number,
                meta=PdfNodeMeta(page_number=default_page_number),
            )
        )
    return paragraphs


def _table_node_to_ir(
    node: dict[str, Any],
    *,
    unit_id: str,
    assets: dict[str, ImageAsset],
) -> TableIR:
    table_meta = build_pdf_node_meta(node)
    if table_meta is None:
        table_meta = PdfNodeMeta(source_type="table")
    # ODL JSON usually carries table structure but not full cell-border CSS. Mark
    # PDF tables up front so the shared HTML renderer can add a conservative grid
    # fallback when explicit or inferred borders are still missing.
    table_meta.render_table_grid = True

    table = TableIR(
        unit_id=unit_id,
        row_count=coerce_int(node.get("number of rows")) or 0,
        col_count=coerce_int(node.get("number of columns")) or 0,
        table_style=_table_style_from_node(node),
        meta=table_meta,
    )
    for row in node.get("rows", []):
        for cell in row.get("cells", []):
            row_index = coerce_int(cell.get("row number")) or 1
            col_index = coerce_int(cell.get("column number")) or 1
            cell_unit_id = f"{unit_id}.tr{row_index}.tc{col_index}"
            table.cells.append(
                TableCellIR(
                    unit_id=cell_unit_id,
                    row_index=row_index,
                    col_index=col_index,
                    text=extract_text_from_odl_children(cell.get("kids", [])).strip(),
                    cell_style=_cell_style_from_node(cell),
                    meta=build_pdf_node_meta(cell),
                    paragraphs=_cell_paragraphs(
                        cell.get("kids", []),
                        cell_unit_id=cell_unit_id,
                        default_page_number=_page_number_from_node(cell),
                        assets=assets,
                    ),
                )
            )
    return table


def _paragraphs_from_list_node(
    node: dict[str, Any],
    *,
    unit_prefix: str,
    assets: dict[str, ImageAsset],
) -> list[ParagraphIR]:
    paragraphs: list[ParagraphIR] = []
    for index, item in enumerate(node.get("list items", []), start=1):
        unit_id = f"{unit_prefix}.li{index}"
        paragraph = _paragraph_from_text_node(item, unit_id=unit_id)
        if paragraph is not None:
            paragraphs.append(paragraph)
        for child_index, child in enumerate(item.get("kids", []), start=1):
            child_type = child.get("type")
            child_unit_id = f"{unit_id}.c{child_index}"
            if child_type == "list":
                paragraphs.extend(
                    _paragraphs_from_list_node(child, unit_prefix=child_unit_id, assets=assets)
                )
            elif child_type == "table":
                paragraphs.append(
                    ParagraphIR(
                        unit_id=child_unit_id,
                        text="",
                        page_number=_page_number_from_node(child),
                        para_style=_para_style_from_node(child),
                        meta=build_pdf_node_meta(child),
                        content=[_table_node_to_ir(child, unit_id=f"{child_unit_id}.tbl1", assets=assets)],
                    )
                )
            elif child_type == "image":
                paragraphs.append(_image_paragraph(child, unit_id=child_unit_id, assets=assets))
            else:
                nested_paragraph = _paragraph_from_text_node(child, unit_id=child_unit_id)
                if nested_paragraph is not None:
                    paragraphs.append(nested_paragraph)
    return paragraphs


def _collect_page_numbers(value: Any, page_numbers: set[int]) -> None:
    if isinstance(value, dict):
        page_number = coerce_int(value.get("page number"))
        if page_number is not None:
            page_numbers.add(page_number)
        for child in value.values():
            _collect_page_numbers(child, page_numbers)
        return
    if isinstance(value, list):
        for child in value:
            _collect_page_numbers(child, page_numbers)


def _page_infos_from_odl(raw_document: dict[str, Any]) -> list[PageInfo]:
    page_layouts: dict[int, dict[str, Any]] = {}
    for page in raw_document.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        page_number = coerce_int(page.get("page number"))
        if page_number is None:
            continue
        page_layouts[page_number] = {
            "width_pt": coerce_float(node_value(page, "width pt", "page width pt")),
            "height_pt": coerce_float(node_value(page, "height pt", "page height pt")),
            "margin_left_pt": coerce_float(page.get("margin left pt")),
            "margin_right_pt": coerce_float(page.get("margin right pt")),
            "margin_top_pt": coerce_float(page.get("margin top pt")),
            "margin_bottom_pt": coerce_float(page.get("margin bottom pt")),
        }

    page_numbers: set[int] = set()
    page_count = coerce_int(raw_document.get("number of pages"))
    if page_count is not None and page_count > 0:
        page_numbers.update(range(1, page_count + 1))
    _collect_page_numbers(raw_document.get("kids", []), page_numbers)

    return [
        PageInfo(page_number=page_number, **page_layouts.get(page_number, {}))
        for page_number in sorted(page_numbers)
    ]


def build_doc_ir_from_odl_result(
    raw_document: dict[str, Any],
    *,
    source_path: str | Path | None = None,
    doc_id: str | None = None,
    doc_cls: type[DocIR] | None = None,
    **doc_kwargs: Any,
) -> DocIR:
    assets: dict[str, ImageAsset] = {}
    paragraphs: list[ParagraphIR] = []

    order = 0
    for node in raw_document.get("kids", []):
        node_type = node.get("type")
        unit_id = f"p{order + 1}"
        if node_type == "table":
            order += 1
            paragraphs.append(
                ParagraphIR(
                    unit_id=unit_id,
                    text="",
                    page_number=_page_number_from_node(node),
                    para_style=_para_style_from_node(node),
                    meta=build_pdf_node_meta(node),
                    content=[_table_node_to_ir(node, unit_id=f"{unit_id}.tbl1", assets=assets)],
                )
            )
            continue
        if node_type == "image":
            order += 1
            paragraphs.append(_image_paragraph(node, unit_id=unit_id, assets=assets))
            continue
        if node_type == "list":
            list_paragraphs = _paragraphs_from_list_node(node, unit_prefix=unit_id, assets=assets)
            if list_paragraphs:
                order += len(list_paragraphs)
                paragraphs.extend(list_paragraphs)
            continue
        if node_type == "text block":
            for child in node.get("kids", []):
                child_unit_id = f"p{order + 1}"
                if child.get("type") == "table":
                    order += 1
                    paragraphs.append(
                        ParagraphIR(
                            unit_id=child_unit_id,
                            text="",
                            page_number=_page_number_from_node(child),
                            para_style=_para_style_from_node(child),
                            meta=build_pdf_node_meta(child),
                            content=[
                                _table_node_to_ir(
                                    child,
                                    unit_id=f"{child_unit_id}.tbl1",
                                    assets=assets,
                                )
                            ],
                        )
                    )
                    continue
                if child.get("type") == "list":
                    list_paragraphs = _paragraphs_from_list_node(
                        child,
                        unit_prefix=child_unit_id,
                        assets=assets,
                    )
                    if list_paragraphs:
                        order += len(list_paragraphs)
                        paragraphs.extend(list_paragraphs)
                    continue
                paragraph = _paragraph_from_text_node(child, unit_id=child_unit_id)
                if paragraph is None:
                    continue
                order += 1
                paragraphs.append(paragraph)
            continue
        paragraph = _paragraph_from_text_node(node, unit_id=unit_id)
        if paragraph is None:
            continue
        order += 1
        paragraphs.append(paragraph)

    resolved_doc_cls = doc_cls or DocIR
    resolved_source_path = str(source_path) if source_path is not None else raw_document.get("file name")
    resolved_doc_id = doc_id or raw_document.get("file name")
    if resolved_doc_id and "." in resolved_doc_id:
        resolved_doc_id = Path(resolved_doc_id).stem
    return resolved_doc_cls(
        doc_id=resolved_doc_id,
        source_path=resolved_source_path,
        source_doc_type="pdf",
        assets=assets,
        pages=_page_infos_from_odl(raw_document),
        paragraphs=paragraphs,
        **doc_kwargs,
    )


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in {"true", "1", "yes", "y", "on"}:
            return True
        if stripped in {"false", "0", "no", "n", "off"}:
            return False
    return None


def _normalize_vertical_align(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip().lower()
    mapping = {
        "top": "top",
        "middle": "middle",
        "center": "middle",
        "centre": "middle",
        "bottom": "bottom",
    }
    return mapping.get(stripped)


__all__ = ["build_doc_ir_from_odl_result"]
