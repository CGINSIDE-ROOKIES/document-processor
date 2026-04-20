"""ODL raw JSON to DocIR conversion."""

from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from typing import Any

from ...models import DocIR, ImageAsset, ImageIR, PageInfo, ParagraphIR, RunIR, TableCellIR, TableIR
from ...style_types import CellStyleInfo, ParaStyleInfo, RunStyleInfo, TableStyleInfo
from ..meta import (
    PdfBoundingBox,
    PdfDocumentMeta,
    PdfNodeMeta,
    build_pdf_document_meta,
    build_pdf_node_meta,
    coerce_bbox,
    coerce_float,
    coerce_int,
    extract_text_from_odl_children,
    extract_text_from_odl_node,
    node_value,
    normalize_align,
    pixels_to_points,
    sanitize_css_color,
)
from .table_reconstruct import (
    MergeGroup,
    TableGrid,
    TableNodeKey,
    assign_fragments_to_groups,
    table_node_key,
)
from .table_split_plan import (
    BoundaryEvent,
    CellKey,
    TableNodeKey as SplitPlanTableNodeKey,
    TableSplitPlan,
    table_node_key as split_plan_table_node_key,
)

_STRIP_CONNECTOR_CHARS = frozenset({"➡", "→", "➜", "➝", "←", "↑", "↓", "↔", "↕", ""})
_STRIP_ROW_TOLERANCE_PT = 18.0


# ---------------------------------------------------------------------------
# Style extraction helpers
# These functions map raw ODL node fields into format-agnostic DocIR style
# models. They are intentionally small and side-effect free so the structural
# conversion helpers below can stay readable.
# ---------------------------------------------------------------------------


def _para_style_from_node(node: dict[str, Any]) -> ParaStyleInfo | None:
    render_tag: str | None = None
    if node.get("type") == "heading":
        level = coerce_int(node.get("heading level"))
        render_tag = f"h{level}" if level is not None and 1 <= level <= 6 else "h2"

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
        render_tag=render_tag,
    )
    return style if style.model_dump(exclude_defaults=True, exclude_none=True) else None


def _run_style_from_node(node: dict[str, Any]) -> RunStyleInfo | None:
    text_format = node_value(node, "text format")
    format_tokens = (
        {token for token in text_format.strip().lower().replace("-", " ").split() if token}
        if isinstance(text_format, str)
        else set()
    )
    font_weight = coerce_float(node_value(node, "font weight"))
    italic_angle = coerce_float(node_value(node, "italic angle"))
    bold = _coerce_bool(node_value(node, "bold"))
    if bold is None:
        bold = (font_weight is not None and font_weight >= 600.0) or ("bold" in format_tokens)
    italic = _coerce_bool(node_value(node, "italic"))
    if italic is None:
        italic = (
            (italic_angle is not None and abs(italic_angle) > 0.01)
            or ("italic" in format_tokens)
            or ("oblique" in format_tokens)
        )
    underline = _coerce_bool(node_value(node, "underline"))
    if underline is None:
        underline = "underline" in format_tokens
    strikethrough = _coerce_bool(node_value(node, "strikethrough"))
    if strikethrough is None:
        strikethrough = "strikethrough" in format_tokens or "strike" in format_tokens

    style = RunStyleInfo(
        font_family=node.get("font") if isinstance(node.get("font"), str) else None,
        bold=bold or False,
        italic=italic or False,
        underline=underline or False,
        strikethrough=strikethrough or False,
        superscript=_coerce_bool(node_value(node, "superscript")) or False,
        subscript=_coerce_bool(node_value(node, "subscript")) or False,
        size_pt=coerce_float(node.get("font size")),
        color=sanitize_css_color(node.get("text color")),
        highlight=sanitize_css_color(node_value(node, "highlight color", "background color")),
        hidden=bool(node.get("hidden text", False)),
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
        border_top=_coarse_border_css_from_node(node, "has top border", "border top"),
        border_bottom=_coarse_border_css_from_node(node, "has bottom border", "border bottom"),
        border_left=_coarse_border_css_from_node(node, "has left border", "border left"),
        border_right=_coarse_border_css_from_node(node, "has right border", "border right"),
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
        preview_grid=True,
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


def _border_css(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    return normalized or None


def _coarse_border_css_from_node(
    node: dict[str, Any],
    bool_key: str,
    legacy_key: str,
) -> str | None:
    has_border = _coerce_bool(node.get(bool_key))
    if has_border is True:
        return "1px solid"
    if has_border is False:
        return None
    return _border_css(node.get(legacy_key))


# ---------------------------------------------------------------------------
# Text node conversion
# ODL semantic text nodes become ParagraphIR + RunIR here. This is the main
# place where we preserve run-level styling while still keeping a stable
# paragraph-level `.text` for downstream chunking/RAG paths.
# ---------------------------------------------------------------------------

def _paragraph_from_text_node(
    node: dict[str, Any],
    *,
    unit_id: str,
    paragraph_meta: PdfNodeMeta | None = None,
    run_meta: PdfNodeMeta | None = None,
    style_node: dict[str, Any] | None = None,
    default_page_number: int | None = None,
) -> ParagraphIR | None:
    text = extract_text_from_odl_node(node)
    if not text and node.get("type") not in {"caption", "header", "footer", "formula"}:
        return None
    if not text.strip() and node.get("type") not in {"caption", "header", "footer", "formula"}:
        return None
    style_source = style_node or node
    resolved_meta = paragraph_meta if paragraph_meta is not None else build_pdf_node_meta(node)
    resolved_run_meta = run_meta if run_meta is not None else resolved_meta
    content = _runs_from_text_node(
        node,
        unit_id=unit_id,
        style_node=style_source,
        run_meta=resolved_run_meta,
    ) if text else []
    return ParagraphIR(
        unit_id=unit_id,
        text=text,
        page_number=_page_number_from_node(style_source) or _page_number_from_node(node) or default_page_number,
        bbox=resolved_meta.bounding_box if resolved_meta is not None else None,
        para_style=_para_style_from_node(style_source),
        meta=resolved_meta,
        content=content,
    )


def _paragraphs_from_container_node(
    node: dict[str, Any],
    *,
    unit_prefix: str,
    assets: dict[str, ImageAsset],
    table_grids: dict[TableNodeKey, TableGrid] | None = None,
    table_split_plans: dict[SplitPlanTableNodeKey, TableSplitPlan] | None = None,
) -> list[ParagraphIR]:
    """Flatten header/footer-like wrapper nodes into paragraph units.

    DocIR stays intentionally flat at the top level, so wrapper containers do
    not survive as dedicated nodes. Their children are converted into regular
    paragraphs/tables/images and appended in reading order.
    """
    paragraphs: list[ParagraphIR] = []
    container_meta = build_pdf_node_meta(node)
    default_page_number = _page_number_from_node(node)

    for child_index, child in enumerate(node.get("kids", []), start=1):
        child_type = child.get("type")
        child_unit_id = f"{unit_prefix}.c{child_index}"
        if child_type == "table":
            paragraphs.append(
                ParagraphIR(
                    unit_id=child_unit_id,
                    text="",
                    page_number=_page_number_from_node(child) or default_page_number,
                    para_style=_para_style_from_node(child),
                    meta=build_pdf_node_meta(child) or container_meta,
                    content=[
                        _table_node_to_ir(
                            child,
                            unit_id=f"{child_unit_id}.tbl1",
                            assets=assets,
                            table_grids=table_grids,
                            table_split_plans=table_split_plans,
                        )
                    ],
                )
            )
            continue
        if child_type == "image":
            paragraph = _image_paragraph(child, unit_id=child_unit_id, assets=assets)
            if paragraph.page_number is None:
                paragraph.page_number = default_page_number
            if paragraph.meta is None:
                paragraph.meta = container_meta
            paragraphs.append(paragraph)
            continue
        if child_type == "list":
            paragraphs.extend(
                _paragraphs_from_list_node(
                    child,
                    unit_prefix=child_unit_id,
                    assets=assets,
                    table_grids=table_grids,
                    table_split_plans=table_split_plans,
                )
            )
            continue

        paragraph = _paragraph_from_text_node(
            child,
            unit_id=child_unit_id,
            paragraph_meta=container_meta,
            run_meta=build_pdf_node_meta(child),
            style_node=child,
            default_page_number=default_page_number,
        )
        if paragraph is not None:
            paragraphs.append(paragraph)

    if paragraphs:
        return paragraphs

    paragraph = _paragraph_from_text_node(
        node,
        unit_id=unit_prefix,
        paragraph_meta=container_meta,
        default_page_number=default_page_number,
    )
    return [paragraph] if paragraph is not None else []


def _compose_pdf_node_meta(
    primary: PdfNodeMeta | None,
    fallback: PdfNodeMeta | None,
) -> PdfNodeMeta | None:
    if primary is None:
        return fallback
    if fallback is None:
        return primary
    merged = PdfNodeMeta(
        **{
            **fallback.model_dump(exclude_defaults=True, exclude_none=True),
            **primary.model_dump(exclude_defaults=True, exclude_none=True),
        }
    )
    return merged if merged.model_dump(exclude_defaults=True, exclude_none=True) else None


def _merged_style_node(
    primary: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(fallback)
    merged.update({key: value for key, value in primary.items() if value is not None})
    return merged


def _text_spans_from_node(node: dict[str, Any]) -> list[dict[str, Any]]:
    spans = node.get("spans")
    if not isinstance(spans, list):
        return []
    return [span for span in spans if isinstance(span, dict) and isinstance(span.get("content"), str)]


def _runs_from_text_node(
    node: dict[str, Any],
    *,
    unit_id: str,
    style_node: dict[str, Any],
    run_meta: PdfNodeMeta | None,
) -> list[RunIR]:
    """Convert ODL spans into RunIR and merge adjacent identical runs.

    ODL span output can be very fine-grained, including whitespace chunks.
    We preserve the information first, then merge only immediately adjacent
    runs whose effective style/meta signatures are identical.
    """
    text = extract_text_from_odl_node(node)
    spans = _text_spans_from_node(node)
    # Current ODL span output flattens per-line chunks but does not emit explicit
    # newline spans. When node content already contains line breaks, prefer the
    # node-level text so preview fidelity does not regress.
    if text and "\n" in text and not any("\n" in span.get("content", "") for span in spans):
        return [
            RunIR(
                unit_id=f"{unit_id}.r1",
                text=text,
                bbox=run_meta.bounding_box if run_meta is not None else None,
                run_style=_run_style_from_node(style_node),
                meta=run_meta,
            )
        ]

    runs: list[RunIR] = []
    for index, span in enumerate(spans, start=1):
        span_text = span.get("content")
        if not isinstance(span_text, str):
            continue
        span_style_node = _merged_style_node(span, style_node)
        span_meta = _compose_pdf_node_meta(build_pdf_node_meta(span), run_meta)
        runs.append(
            RunIR(
                unit_id=f"{unit_id}.r{index}",
                text=span_text,
                bbox=span_meta.bounding_box if span_meta is not None else None,
                run_style=_run_style_from_node(span_style_node),
                meta=span_meta,
            )
        )
    if runs:
        return _merge_adjacent_runs(runs)
    if not text:
        return []
    return [
        RunIR(
            unit_id=f"{unit_id}.r1",
            text=text,
            bbox=run_meta.bounding_box if run_meta is not None else None,
            run_style=_run_style_from_node(style_node),
            meta=run_meta,
        )
    ]


# ---------------------------------------------------------------------------
# Run post-processing helpers
# ---------------------------------------------------------------------------

def _merge_adjacent_runs(runs: list[RunIR]) -> list[RunIR]:
    if not runs:
        return []
    merged_runs: list[RunIR] = [runs[0].model_copy(deep=True)]
    for run in runs[1:]:
        current = merged_runs[-1]
        if _can_merge_runs(current, run):
            current.text += run.text
            current.bbox = _merge_bounding_boxes(current.bbox, run.bbox)
            current.meta = _merge_run_meta(current.meta, run.meta)
            continue
        merged_runs.append(run.model_copy(deep=True))
    return merged_runs


def _can_merge_runs(left: RunIR, right: RunIR) -> bool:
    return _run_style_signature(left.run_style) == _run_style_signature(
        right.run_style
    ) and _run_meta_signature(left.meta) == _run_meta_signature(right.meta)


def _run_style_signature(style: RunStyleInfo | None) -> dict[str, Any] | None:
    if style is None:
        return None
    return style.model_dump(exclude_defaults=True, exclude_none=True)


def _run_meta_signature(meta: PdfNodeMeta | None) -> dict[str, Any] | None:
    if meta is None:
        return None
    signature = meta.model_dump(exclude_defaults=True, exclude_none=True)
    signature.pop("bounding_box", None)
    return signature


def _merge_run_meta(left: PdfNodeMeta | None, right: PdfNodeMeta | None) -> PdfNodeMeta | None:
    if left is None:
        return right
    if right is None:
        return left
    merged = left.model_copy(deep=True)
    merged.bounding_box = _merge_bounding_boxes(left.bounding_box, right.bounding_box)
    return merged if merged.model_dump(exclude_defaults=True, exclude_none=True) else None


def _merge_bounding_boxes(
    left: PdfBoundingBox | None,
    right: PdfBoundingBox | None,
) -> PdfBoundingBox | None:
    if left is None:
        return right
    if right is None:
        return left
    return PdfBoundingBox(
        left_pt=min(left.left_pt, right.left_pt),
        bottom_pt=min(left.bottom_pt, right.bottom_pt),
        right_pt=max(left.right_pt, right.right_pt),
        top_pt=max(left.top_pt, right.top_pt),
    )


# ---------------------------------------------------------------------------
# Non-text block conversion
# Images, tables, table cells, and list containers become normal DocIR content
# nodes here. The goal is still a flat top-level paragraph stream, with tables
# nested only where DocIR already supports them.
# ---------------------------------------------------------------------------

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
    image_meta = build_pdf_node_meta(node)
    return ParagraphIR(
        unit_id=unit_id,
        text="",
        page_number=_page_number_from_node(node),
        bbox=image_meta.bounding_box if image_meta is not None else None,
        para_style=_para_style_from_node(node),
        meta=image_meta,
        content=[
            ImageIR(
                unit_id=f"{unit_id}.img1",
                image_id=f"odl-img-{unit_id}",
                alt_text=node_value(node, "alt text"),
                title=node_value(node, "title", "name"),
                bbox=image_meta.bounding_box if image_meta is not None else None,
                display_width_pt=display_width_pt,
                display_height_pt=display_height_pt,
            )
        ],
    )


def _cell_paragraphs(
    children: list[dict[str, Any]],
    *,
    cell_unit_id: str,
    default_page_number: int | None,
    assets: dict[str, ImageAsset],
    table_grids: dict[TableNodeKey, TableGrid] | None = None,
    table_split_plans: dict[SplitPlanTableNodeKey, TableSplitPlan] | None = None,
) -> list[ParagraphIR]:
    """Build the paragraph stream for a table cell.

    Cells can still contain nested tables/images, but from the caller's point
    of view they always become a list of ParagraphIR entries.
    """
    paragraphs: list[ParagraphIR] = []
    child_index = 0
    for child in children:
        child_type = child.get("type")
        unit_id = f"{cell_unit_id}.p{child_index + 1}"
        if child_type == "table":
            child_index += 1
            child_meta = build_pdf_node_meta(child)
            paragraphs.append(
                ParagraphIR(
                    unit_id=unit_id,
                    text="",
                    page_number=_page_number_from_node(child) or default_page_number,
                    bbox=child_meta.bounding_box if child_meta is not None else None,
                    para_style=_para_style_from_node(child),
                    meta=child_meta,
                    content=[
                        _table_node_to_ir(
                            child,
                            unit_id=f"{unit_id}.tbl1",
                            assets=assets,
                            table_grids=table_grids,
                            table_split_plans=table_split_plans,
                        )
                    ],
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
                bbox=None,
                meta=PdfNodeMeta(page_number=default_page_number),
            )
        )
    return paragraphs


def _iter_raw_table_cells(node: dict[str, Any]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for row in node.get("rows", []) or []:
        if not isinstance(row, dict):
            continue
        for cell in row.get("cells", []) or []:
            if isinstance(cell, dict):
                cells.append(cell)
    return cells


def _cell_key_from_raw_cell(cell: dict[str, Any]) -> CellKey:
    return CellKey(
        row_index=coerce_int(cell.get("row number")) or 1,
        col_index=coerce_int(cell.get("column number")) or 1,
        rowspan=max(coerce_int(cell.get("row span")) or 1, 1),
        colspan=max(coerce_int(cell.get("column span")) or 1, 1),
    )


def _resolved_raw_cells_from_split_plan(
    raw_cells: list[dict[str, Any]],
    plan: TableSplitPlan,
) -> list[dict[str, Any]]:
    resolved_cells: list[dict[str, Any]] = []
    for cell in raw_cells:
        split = plan.cell_splits.get(_cell_key_from_raw_cell(cell))
        if split is None:
            resolved_cells.append(
                _shift_or_expand_raw_cell(
                    cell,
                    row_events=plan.row_events,
                    column_events=plan.column_events,
                )
            )
            continue
        resolved_cells.extend(
            _split_raw_cell(
                cell,
                split_orientation=split.orientation,
                split_axis_pt=split.axis_pt,
                row_events=plan.row_events,
                column_events=plan.column_events,
            )
        )
    return resolved_cells


def _shift_or_expand_raw_cell(
    cell: dict[str, Any],
    *,
    row_events: list[BoundaryEvent],
    column_events: list[BoundaryEvent],
) -> dict[str, Any]:
    cell_key = _cell_key_from_raw_cell(cell)
    shifted_cell = dict(cell)
    shifted_cell["row number"] = cell_key.row_index + _row_shift_before(cell_key.row_index, row_events)
    shifted_cell["column number"] = cell_key.col_index + _col_shift_before(
        cell_key.col_index,
        column_events,
    )
    shifted_cell["row span"] = cell_key.rowspan + _row_expansion_inside_cell(
        cell_key.row_index,
        cell_key.rowspan,
        row_events,
    )
    shifted_cell["column span"] = cell_key.colspan + _col_expansion_inside_cell(
        cell_key.col_index,
        cell_key.colspan,
        column_events,
    )
    return shifted_cell


def _split_raw_cell(
    cell: dict[str, Any],
    *,
    split_orientation: str,
    split_axis_pt: float,
    row_events: list[BoundaryEvent],
    column_events: list[BoundaryEvent],
) -> list[dict[str, Any]]:
    shifted_cell = _shift_or_expand_raw_cell(cell, row_events=row_events, column_events=column_events)
    kids_before, kids_after = _split_raw_cell_kids(
        cell.get("kids", []),
        orientation=split_orientation,
        axis_pt=split_axis_pt,
    )
    first_piece = deepcopy(shifted_cell)
    second_piece = deepcopy(shifted_cell)
    cell_bbox = coerce_bbox(cell.get("bounding box"))

    if split_orientation == "horizontal":
        if cell_bbox is not None:
            first_piece["bounding box"] = [
                cell_bbox.left_pt,
                split_axis_pt,
                cell_bbox.right_pt,
                cell_bbox.top_pt,
            ]
            second_piece["bounding box"] = [
                cell_bbox.left_pt,
                cell_bbox.bottom_pt,
                cell_bbox.right_pt,
                split_axis_pt,
            ]
            first_piece["display width pt"] = cell_bbox.right_pt - cell_bbox.left_pt
            first_piece["display height pt"] = cell_bbox.top_pt - split_axis_pt
            second_piece["display width pt"] = cell_bbox.right_pt - cell_bbox.left_pt
            second_piece["display height pt"] = split_axis_pt - cell_bbox.bottom_pt
        first_piece["row span"] = 1
        second_piece["row number"] = (coerce_int(first_piece.get("row number")) or 1) + 1
        second_piece["row span"] = 1
        first_piece["kids"] = kids_after
        second_piece["kids"] = kids_before
    else:
        if cell_bbox is not None:
            first_piece["bounding box"] = [
                cell_bbox.left_pt,
                cell_bbox.bottom_pt,
                split_axis_pt,
                cell_bbox.top_pt,
            ]
            second_piece["bounding box"] = [
                split_axis_pt,
                cell_bbox.bottom_pt,
                cell_bbox.right_pt,
                cell_bbox.top_pt,
            ]
            first_piece["display width pt"] = split_axis_pt - cell_bbox.left_pt
            first_piece["display height pt"] = cell_bbox.top_pt - cell_bbox.bottom_pt
            second_piece["display width pt"] = cell_bbox.right_pt - split_axis_pt
            second_piece["display height pt"] = cell_bbox.top_pt - cell_bbox.bottom_pt
        first_piece["column span"] = 1
        second_piece["column number"] = (coerce_int(first_piece.get("column number")) or 1) + 1
        second_piece["column span"] = 1
        first_piece["kids"] = kids_before
        second_piece["kids"] = kids_after

    return [first_piece, second_piece]


def _split_raw_cell_kids(
    kids: Any,
    *,
    orientation: str,
    axis_pt: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    before: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    for kid in kids or []:
        if not isinstance(kid, dict):
            continue
        bbox = _effective_bbox_from_descendants(kid)
        if bbox is None:
            before.append(kid)
            continue
        center = (
            (bbox.bottom_pt + bbox.top_pt) / 2.0
            if orientation == "horizontal"
            else (bbox.left_pt + bbox.right_pt) / 2.0
        )
        if center < axis_pt:
            before.append(kid)
        else:
            after.append(kid)
    return before, after


def _effective_bbox_from_descendants(node: Any) -> PdfBoundingBox | None:
    bbox = coerce_bbox(node.get("bounding box")) if isinstance(node, dict) else None
    merged_bbox = bbox

    def visit(value: Any) -> None:
        nonlocal merged_bbox
        if isinstance(value, dict):
            child_bbox = coerce_bbox(value.get("bounding box"))
            if child_bbox is not None:
                merged_bbox = _merge_bounding_boxes(merged_bbox, child_bbox)
            for child in value.values():
                visit(child)
            return
        if isinstance(value, list):
            for item in value:
                visit(item)

    if isinstance(node, dict):
        for child in node.values():
            visit(child)
    return merged_bbox


def _row_shift_before(row_index: int, row_events: list[BoundaryEvent]) -> int:
    return sum(1 for event in row_events if event.source_index < row_index)


def _col_shift_before(col_index: int, column_events: list[BoundaryEvent]) -> int:
    return sum(1 for event in column_events if event.source_index < col_index)


def _row_expansion_inside_cell(
    row_index: int,
    rowspan: int,
    row_events: list[BoundaryEvent],
) -> int:
    row_end = row_index + max(rowspan, 1) - 1
    return sum(1 for event in row_events if row_index <= event.source_index <= row_end)


def _col_expansion_inside_cell(
    col_index: int,
    colspan: int,
    column_events: list[BoundaryEvent],
) -> int:
    col_end = col_index + max(colspan, 1) - 1
    return sum(1 for event in column_events if col_index <= event.source_index <= col_end)


def _table_node_to_ir(
    node: dict[str, Any],
    *,
    unit_id: str,
    assets: dict[str, ImageAsset],
    table_grids: dict[TableNodeKey, TableGrid] | None = None,
    table_split_plans: dict[SplitPlanTableNodeKey, TableSplitPlan] | None = None,
) -> TableIR:
    """Convert one raw ODL table node into TableIR."""
    raw_cells = _iter_raw_table_cells(node)
    grid = table_grids.get(table_node_key(node)) if table_grids else None
    if grid is None:
        return _table_node_to_ir_from_raw_topology(
            node,
            unit_id=unit_id,
            assets=assets,
            table_grids=table_grids,
            table_split_plans=table_split_plans,
            raw_cells=raw_cells,
        )
    if _raw_cells_have_unsupported_reconstruct_content(raw_cells):
        return _table_node_to_ir_from_raw_topology(
            node,
            unit_id=unit_id,
            assets=assets,
            table_grids=table_grids,
            table_split_plans=table_split_plans,
            raw_cells=raw_cells,
        )

    fragments_by_group = assign_fragments_to_groups(raw_cells=raw_cells, grid=grid)
    if not fragments_by_group and _raw_cells_have_table_fragments(raw_cells):
        return _table_node_to_ir_from_raw_topology(
            node,
            unit_id=unit_id,
            assets=assets,
            table_grids=table_grids,
            table_split_plans=table_split_plans,
            raw_cells=raw_cells,
        )

    table_meta = build_pdf_node_meta(node)
    table_style = _table_style_from_node(node)
    if table_style is not None:
        table_style.row_count = grid.row_count
        table_style.col_count = grid.col_count

    table = TableIR(
        unit_id=unit_id,
        row_count=grid.row_count,
        col_count=grid.col_count,
        bbox=table_meta.bounding_box if table_meta is not None else None,
        table_style=table_style,
        meta=table_meta,
    )
    first_raw_cell_by_group = _first_raw_cell_by_group(raw_cells, grid)
    for group in sorted(grid.merge_groups, key=lambda item: (item.min_row, item.min_col)):
        row_index = group.min_row + 1
        col_index = group.min_col + 1
        group_bbox = grid.group_bbox(group)
        source_cell = first_raw_cell_by_group.get(group)
        cell_meta = build_pdf_node_meta(source_cell) if source_cell is not None else None
        if cell_meta is None:
            cell_meta = PdfNodeMeta(page_number=_page_number_from_node(node), bounding_box=group_bbox)
        else:
            cell_meta = cell_meta.model_copy(deep=True)
            cell_meta.bounding_box = group_bbox
        _append_table_cell(
            table,
            row_index=row_index,
            col_index=col_index,
            rowspan=group.max_row - group.min_row + 1,
            colspan=group.max_col - group.min_col + 1,
            cell_bbox=group_bbox,
            cell_meta=cell_meta,
            cell_style=_reconstructed_cell_style(source_cell, rowspan=group.rowspan, colspan=group.colspan),
            children=fragments_by_group.get(group, []),
            unit_id=unit_id,
            assets=assets,
            default_page_number=(
                _page_number_from_node(source_cell) if source_cell is not None else None
            ) or _page_number_from_node(node),
            table_grids=table_grids,
            table_split_plans=table_split_plans,
        )
    return table


def _table_node_to_ir_from_raw_topology(
    node: dict[str, Any],
    *,
    unit_id: str,
    assets: dict[str, ImageAsset],
    table_grids: dict[TableNodeKey, TableGrid] | None = None,
    table_split_plans: dict[SplitPlanTableNodeKey, TableSplitPlan] | None = None,
    raw_cells: list[dict[str, Any]] | None = None,
) -> TableIR:
    table_meta = build_pdf_node_meta(node)
    resolved_raw_cells = raw_cells if raw_cells is not None else _iter_raw_table_cells(node)
    plan = _lookup_split_plan(node, table_split_plans)
    resolved_cells = (
        _resolved_raw_cells_from_split_plan(resolved_raw_cells, plan)
        if plan is not None
        else resolved_raw_cells
    )
    row_count = (coerce_int(node.get("number of rows")) or 0) + (len(plan.row_events) if plan else 0)
    col_count = (coerce_int(node.get("number of columns")) or 0) + (len(plan.column_events) if plan else 0)
    table_style = _table_style_from_node(node)
    if table_style is not None:
        table_style.row_count = row_count
        table_style.col_count = col_count

    table = TableIR(
        unit_id=unit_id,
        row_count=row_count,
        col_count=col_count,
        bbox=table_meta.bounding_box if table_meta is not None else None,
        table_style=table_style,
        meta=table_meta,
    )
    ordered_cells = (
        sorted(
            resolved_cells,
            key=lambda item: (
                coerce_int(item.get("row number")) or 1,
                coerce_int(item.get("column number")) or 1,
            ),
        )
        if plan is not None
        else resolved_cells
    )
    for cell in ordered_cells:
        row_index = coerce_int(cell.get("row number")) or 1
        col_index = coerce_int(cell.get("column number")) or 1
        cell_meta = build_pdf_node_meta(cell)
        cell_style = _cell_style_from_node(cell)
        if cell_style is not None:
            cell_style.rowspan = max(coerce_int(cell.get("row span")) or 1, 1)
            cell_style.colspan = max(coerce_int(cell.get("column span")) or 1, 1)
        _append_table_cell(
            table,
            row_index=row_index,
            col_index=col_index,
            rowspan=max(coerce_int(cell.get("row span")) or 1, 1),
            colspan=max(coerce_int(cell.get("column span")) or 1, 1),
            cell_bbox=cell_meta.bounding_box if cell_meta is not None else None,
            cell_meta=cell_meta,
            cell_style=cell_style,
            children=cell.get("kids", []),
            unit_id=unit_id,
            assets=assets,
            default_page_number=_page_number_from_node(cell),
            table_grids=table_grids,
            table_split_plans=table_split_plans,
        )
    return table


def _append_table_cell(
    table: TableIR,
    *,
    row_index: int,
    col_index: int,
    rowspan: int,
    colspan: int,
    cell_bbox: PdfBoundingBox | None,
    cell_meta: PdfNodeMeta | None,
    cell_style: CellStyleInfo | None,
    children: list[dict[str, Any]],
    unit_id: str,
    assets: dict[str, ImageAsset],
    default_page_number: int | None,
    table_grids: dict[TableNodeKey, TableGrid] | None = None,
    table_split_plans: dict[SplitPlanTableNodeKey, TableSplitPlan] | None = None,
) -> None:
    cell_unit_id = f"{unit_id}.tr{row_index}.tc{col_index}"
    if cell_style is not None:
        cell_style.rowspan = rowspan
        cell_style.colspan = colspan
    table.cells.append(
        TableCellIR(
            unit_id=cell_unit_id,
            row_index=row_index,
            col_index=col_index,
            text=extract_text_from_odl_children(children),
            bbox=cell_bbox,
            cell_style=cell_style,
            meta=cell_meta,
            paragraphs=_cell_paragraphs(
                children,
                cell_unit_id=cell_unit_id,
                default_page_number=default_page_number,
                assets=assets,
                table_grids=table_grids,
                table_split_plans=table_split_plans,
            ),
        )
    )


def _table_key_signature(
    key: Any,
) -> tuple[int | None, int | None, float | None, float | None, float | None, float | None] | None:
    if key is None:
        return None
    try:
        return (
            getattr(key, "page_number"),
            getattr(key, "reading_order_index"),
            getattr(key, "left_pt"),
            getattr(key, "bottom_pt"),
            getattr(key, "right_pt"),
            getattr(key, "top_pt"),
        )
    except AttributeError:
        return None


def _lookup_split_plan(
    node: dict[str, Any],
    table_split_plans: dict[Any, TableSplitPlan] | None,
) -> TableSplitPlan | None:
    if not table_split_plans:
        return None

    split_key = split_plan_table_node_key(node)
    plan = table_split_plans.get(split_key)
    if plan is not None:
        return plan

    reconstruct_key = table_node_key(node)
    plan = table_split_plans.get(reconstruct_key)
    if plan is not None:
        return plan

    expected_signature = _table_key_signature(split_key)
    for candidate_key, candidate_plan in table_split_plans.items():
        if _table_key_signature(candidate_key) == expected_signature:
            return candidate_plan
    return None


def _iter_raw_cell_fragments(raw_cell: dict[str, Any]):
    def visit(node: Any):
        if not isinstance(node, dict):
            return
        if node.get("type") == "paragraph":
            yield node
        kids = node.get("kids")
        if isinstance(kids, list):
            for kid in kids:
                yield from visit(kid)

    yield from visit(raw_cell)


def _raw_cells_have_table_fragments(raw_cells: list[dict[str, Any]]) -> bool:
    return any(True for raw_cell in raw_cells for _ in _iter_raw_cell_fragments(raw_cell))


def _raw_cells_have_unsupported_reconstruct_content(raw_cells: list[dict[str, Any]]) -> bool:
    for raw_cell in raw_cells:
        for child in raw_cell.get("kids", []) or []:
            if not isinstance(child, dict):
                continue
            if child.get("type") != "paragraph":
                return True
    return False


def _first_raw_cell_by_group(
    raw_cells: list[dict[str, Any]],
    grid: TableGrid,
) -> dict[MergeGroup, dict[str, Any]]:
    first_raw_cell: dict[MergeGroup, dict[str, Any]] = {}
    for raw_cell in raw_cells:
        fragment_mapping = assign_fragments_to_groups(raw_cells=[raw_cell], grid=grid)
        if fragment_mapping:
            for group in fragment_mapping:
                first_raw_cell.setdefault(group, raw_cell)
        raw_cell_bbox = coerce_bbox(raw_cell.get("bounding box"))
        if raw_cell_bbox is None:
            continue
        for group in _groups_with_centers_inside_bbox(raw_cell_bbox, grid):
            first_raw_cell.setdefault(group, raw_cell)
    return first_raw_cell


def _reconstructed_cell_style(
    raw_cell: dict[str, Any] | None,
    *,
    rowspan: int,
    colspan: int,
) -> CellStyleInfo | None:
    if raw_cell is None:
        return None
    cell_style = _cell_style_from_node(raw_cell)
    if cell_style is None:
        return None
    cell_style.rowspan = rowspan
    cell_style.colspan = colspan
    return cell_style


def _groups_with_centers_inside_bbox(
    bbox: PdfBoundingBox,
    grid: TableGrid,
) -> list[MergeGroup]:
    matches: list[MergeGroup] = []
    for group in grid.merge_groups:
        group_bbox = grid.group_bbox(group)
        center_x = (group_bbox.left_pt + group_bbox.right_pt) / 2.0
        center_y = (group_bbox.bottom_pt + group_bbox.top_pt) / 2.0
        if (
            bbox.left_pt <= center_x <= bbox.right_pt
            and bbox.bottom_pt <= center_y <= bbox.top_pt
        ):
            matches.append(group)
    return matches


def _paragraph_is_table_box(paragraph: ParagraphIR) -> bool:
    return (
        paragraph.bbox is not None
        and len(paragraph.content) == 1
        and isinstance(paragraph.content[0], TableIR)
    )


def _paragraph_is_connector(paragraph: ParagraphIR) -> bool:
    if paragraph.bbox is None or not paragraph.content:
        return False
    if not all(isinstance(node, RunIR) for node in paragraph.content):
        return False
    text = "".join(run.text for run in paragraph.content).strip()
    return bool(text) and all(char in _STRIP_CONNECTOR_CHARS for char in text)


def _group_strip_rows(paragraphs: list[ParagraphIR]) -> list[list[ParagraphIR]]:
    rows: list[list[ParagraphIR]] = []
    row_tops: list[float] = []
    for paragraph in paragraphs:
        bbox = paragraph.bbox
        if bbox is None:
            continue
        assigned_row_index: int | None = None
        for row_index, row_top in enumerate(row_tops):
            if abs(bbox.top_pt - row_top) <= _STRIP_ROW_TOLERANCE_PT:
                assigned_row_index = row_index
                break
        if assigned_row_index is None:
            rows.append([paragraph])
            row_tops.append(bbox.top_pt)
            continue
        rows[assigned_row_index].append(paragraph)
        row_tops[assigned_row_index] = sum(
            member.bbox.top_pt for member in rows[assigned_row_index] if member.bbox is not None
        ) / len(rows[assigned_row_index])
    rows.sort(
        key=lambda row: (
            -max(member.bbox.top_pt for member in row if member.bbox is not None),
            min(member.bbox.left_pt for member in row if member.bbox is not None),
        )
    )
    for row in rows:
        row.sort(key=lambda member: member.bbox.left_pt if member.bbox is not None else float("inf"))
    return rows


def _build_strip_table_paragraph(
    paragraphs: list[ParagraphIR],
    *,
    unit_id: str,
) -> ParagraphIR | None:
    if not paragraphs:
        return None

    bboxes = [paragraph.bbox for paragraph in paragraphs if paragraph.bbox is not None]
    if not bboxes:
        return None

    group_bbox = bboxes[0]
    for bbox in bboxes[1:]:
        group_bbox = _merge_bounding_boxes(group_bbox, bbox)
    if group_bbox is None:
        return None

    rows = _group_strip_rows(paragraphs)
    if not rows:
        return None
    col_count = max(len(row) for row in rows)

    cells: list[TableCellIR] = []
    cell_index = 0
    for row_index, row in enumerate(rows, start=1):
        for col_index, paragraph in enumerate(row, start=1):
            bbox = paragraph.bbox
            if bbox is None:
                continue
            cell_index += 1
            cells.append(
                TableCellIR(
                    unit_id=f"{unit_id}.cell.{cell_index}",
                    row_index=row_index,
                    col_index=col_index,
                    text=paragraph.text,
                    bbox=bbox,
                    cell_style=CellStyleInfo(
                        width_pt=max(bbox.right_pt - bbox.left_pt, 0.0),
                        height_pt=max(bbox.top_pt - bbox.bottom_pt, 0.0),
                        horizontal_align="center" if _paragraph_is_connector(paragraph) else None,
                        vertical_align="middle" if _paragraph_is_connector(paragraph) else None,
                    ),
                    paragraphs=[paragraph.model_copy(deep=True)],
                )
            )
    if not cells:
        return None

    table = TableIR(
        unit_id=f"{unit_id}.tbl1",
        row_count=max(len(rows), 1),
        col_count=max(col_count, 1),
        bbox=group_bbox,
        table_style=TableStyleInfo(
            row_count=max(len(rows), 1),
            col_count=max(col_count, 1),
            width_pt=max(group_bbox.right_pt - group_bbox.left_pt, 0.0),
            height_pt=max(group_bbox.top_pt - group_bbox.bottom_pt, 0.0),
            preview_grid=False,
        ),
        cells=cells,
    )
    paragraph = ParagraphIR(
        unit_id=unit_id,
        text="",
        page_number=paragraphs[0].page_number,
        bbox=group_bbox,
        content=[table],
    )
    paragraph.recompute_text()
    return paragraph


def _collapse_table_connector_sequences(
    paragraphs: list[ParagraphIR],
    *,
    unit_prefix: str,
) -> list[ParagraphIR]:
    collapsed: list[ParagraphIR] = []
    index = 0
    strip_index = 0
    while index < len(paragraphs):
        kind = (
            "box"
            if _paragraph_is_table_box(paragraphs[index])
            else "connector" if _paragraph_is_connector(paragraphs[index]) else None
        )
        if kind is None:
            collapsed.append(paragraphs[index])
            index += 1
            continue

        end = index
        box_count = 0
        connector_count = 0
        while end < len(paragraphs):
            current = paragraphs[end]
            if _paragraph_is_table_box(current):
                box_count += 1
                end += 1
                continue
            if _paragraph_is_connector(current):
                connector_count += 1
                end += 1
                continue
            break

        block = paragraphs[index:end]
        if (
            len(block) >= 3
            and box_count >= 3
            and connector_count >= 1
            and _paragraph_is_table_box(block[0])
            and _paragraph_is_table_box(block[-1])
        ):
            strip_index += 1
            strip_paragraph = _build_strip_table_paragraph(
                block,
                unit_id=f"{unit_prefix}.strip{strip_index}",
            )
            if strip_paragraph is not None:
                collapsed.append(strip_paragraph)
                index = end
                continue

        collapsed.extend(block)
        index = end

    return collapsed


def _paragraphs_from_list_node(
    node: dict[str, Any],
    *,
    unit_prefix: str,
    assets: dict[str, ImageAsset],
    table_grids: dict[TableNodeKey, TableGrid] | None = None,
    table_split_plans: dict[SplitPlanTableNodeKey, TableSplitPlan] | None = None,
) -> list[ParagraphIR]:
    """Flatten list items into normal paragraph units.

    DocIR currently does not keep a dedicated list tree, so list items are
    emitted as ordinary paragraphs in reading order. Nested tables/images are
    still preserved inside paragraph content where supported.
    """
    paragraphs: list[ParagraphIR] = []
    for index, item in enumerate(node.get("list items", []), start=1):
        unit_id = f"{unit_prefix}.li{index}"
        item_paragraphs: list[ParagraphIR] = []
        paragraph = _paragraph_from_text_node(
            item,
            unit_id=unit_id,
            paragraph_meta=build_pdf_node_meta(item),
        )
        if paragraph is not None:
            item_paragraphs.append(paragraph)
        child_paragraphs: list[ParagraphIR] = []
        for child_index, child in enumerate(item.get("kids", []), start=1):
            child_type = child.get("type")
            child_unit_id = f"{unit_id}.c{child_index}"
            if child_type == "list":
                child_paragraphs.extend(
                    _paragraphs_from_list_node(
                        child,
                        unit_prefix=child_unit_id,
                        assets=assets,
                        table_grids=table_grids,
                        table_split_plans=table_split_plans,
                    )
                )
            elif child_type == "table":
                child_meta = build_pdf_node_meta(child)
                child_paragraphs.append(
                    ParagraphIR(
                        unit_id=child_unit_id,
                        text="",
                        page_number=_page_number_from_node(child),
                        bbox=child_meta.bounding_box if child_meta is not None else None,
                        para_style=_para_style_from_node(child),
                        meta=child_meta,
                        content=[
                            _table_node_to_ir(
                                child,
                                unit_id=f"{child_unit_id}.tbl1",
                                assets=assets,
                                table_grids=table_grids,
                                table_split_plans=table_split_plans,
                            )
                        ],
                    )
                )
            elif child_type == "image":
                child_paragraphs.append(_image_paragraph(child, unit_id=child_unit_id, assets=assets))
            else:
                nested_paragraph = _paragraph_from_text_node(child, unit_id=child_unit_id)
                if nested_paragraph is not None:
                    child_paragraphs.append(nested_paragraph)
        item_paragraphs.extend(
            _collapse_table_connector_sequences(child_paragraphs, unit_prefix=unit_id)
        )
        paragraphs.extend(item_paragraphs)
    return paragraphs


# ---------------------------------------------------------------------------
# Page/document assembly
# Raw ODL output is assembled into one flat DocIR paragraph list here. Page and
# layout provenance stay in metadata; the top-level content model remains flat.
# ---------------------------------------------------------------------------

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
    table_grids: dict[TableNodeKey, TableGrid] | None = None,
    table_split_plans: dict[SplitPlanTableNodeKey, TableSplitPlan] | None = None,
    **doc_kwargs: Any,
) -> DocIR:
    """Build canonical DocIR from one ODL raw JSON document.

    Important design choice:
    - top-level content stays flat in ``DocIR.paragraphs``
    - page/region/layout information remains metadata
    - preview-only raw fields are intentionally *not* mirrored into DocIR
    """
    assets: dict[str, ImageAsset] = {}
    paragraphs: list[ParagraphIR] = []

    order = 0
    for node in raw_document.get("kids", []):
        node_type = node.get("type")
        unit_id = f"p{order + 1}"
        if node_type == "table":
            order += 1
            node_meta = build_pdf_node_meta(node)
            paragraphs.append(
                ParagraphIR(
                    unit_id=unit_id,
                    text="",
                    page_number=_page_number_from_node(node),
                    bbox=node_meta.bounding_box if node_meta is not None else None,
                    para_style=_para_style_from_node(node),
                    meta=node_meta,
                    content=[
                        _table_node_to_ir(
                            node,
                            unit_id=f"{unit_id}.tbl1",
                            assets=assets,
                            table_grids=table_grids,
                            table_split_plans=table_split_plans,
                        )
                    ],
                )
            )
            continue
        if node_type == "image":
            order += 1
            paragraphs.append(_image_paragraph(node, unit_id=unit_id, assets=assets))
            continue
        if node_type == "list":
            list_paragraphs = _paragraphs_from_list_node(
                node,
                unit_prefix=unit_id,
                assets=assets,
                table_grids=table_grids,
                table_split_plans=table_split_plans,
            )
            if list_paragraphs:
                order += len(list_paragraphs)
                paragraphs.extend(list_paragraphs)
            continue
        if node_type in {"header", "footer"}:
            # Header/footer wrappers are flattened into ordinary paragraphs so
            # downstream consumers do not need a PDF-only container type.
            container_paragraphs = _paragraphs_from_container_node(
                node,
                unit_prefix=unit_id,
                assets=assets,
                table_grids=table_grids,
                table_split_plans=table_split_plans,
            )
            if container_paragraphs:
                order += len(container_paragraphs)
                paragraphs.extend(container_paragraphs)
            continue
        if node_type == "text block":
            # `text block` is another wrapper-like construct in ODL output.
            # Its children are emitted directly into the flat paragraph stream.
            for child in node.get("kids", []):
                child_unit_id = f"p{order + 1}"
                if child.get("type") == "table":
                    order += 1
                    child_meta = build_pdf_node_meta(child)
                    paragraphs.append(
                        ParagraphIR(
                            unit_id=child_unit_id,
                            text="",
                            page_number=_page_number_from_node(child),
                            bbox=child_meta.bounding_box if child_meta is not None else None,
                            para_style=_para_style_from_node(child),
                            meta=child_meta,
                            content=[
                                _table_node_to_ir(
                                    child,
                                    unit_id=f"{child_unit_id}.tbl1",
                                    assets=assets,
                                    table_grids=table_grids,
                                    table_split_plans=table_split_plans,
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
                        table_grids=table_grids,
                        table_split_plans=table_split_plans,
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
    document_meta: PdfDocumentMeta = build_pdf_document_meta(raw_document)
    return resolved_doc_cls(
        doc_id=resolved_doc_id,
        meta=document_meta if document_meta.model_dump(exclude_defaults=True, exclude_none=True) else None,
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
