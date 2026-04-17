"""Render structural document IR as styled HTML."""

from __future__ import annotations

import base64
from html import escape
import re

from .models import DocIR, ImageIR, PageInfo, ParagraphContentNode, ParagraphIR, RunIR, TableCellIR, TableIR
from .style_types import CellStyleInfo, ParaStyleInfo, RunStyleInfo


# ---------------------------------------------------------------------------
# Shared format-agnostic rendering helpers
# ---------------------------------------------------------------------------

def _run_css(style: RunStyleInfo) -> str:
    parts: list[str] = []
    if style.font_family:
        parts.append(f"font-family:{style.font_family}")
    if style.color:
        parts.append(f"color:{style.color}")
    if style.size_pt:
        parts.append(f"font-size:{style.size_pt:.1f}pt")
    if style.highlight:
        parts.append(f"background-color:{style.highlight}")

    decorations = []
    if style.underline:
        decorations.append("underline")
    if style.strikethrough:
        decorations.append("line-through")
    if decorations:
        parts.append(f"text-decoration:{' '.join(decorations)}")

    return ";".join(parts)


def _style_wrap(html: str, style: RunStyleInfo | None) -> str:
    if style is None:
        return html

    if style.superscript:
        html = f"<sup>{html}</sup>"
    elif style.subscript:
        html = f"<sub>{html}</sub>"
    if style.bold:
        html = f"<b>{html}</b>"
    if style.italic:
        html = f"<i>{html}</i>"

    css = _run_css(style)
    if css:
        html = f'<span style="{css}">{html}</span>'

    return html


def _escape_whitespace(html: str) -> str:
    html = html.replace("\t", "&nbsp;&nbsp;&nbsp;&nbsp;")
    html = re.sub(r"  +", lambda m: "&nbsp;" * len(m.group(0)), html)
    html = html.replace("\n", "<br>")
    return html


def _wrap_run(doc_ir: DocIR, run: RunIR) -> str:
    if run.run_style is not None and run.run_style.hidden:
        return ""
    html = escape(run.text)
    if not html:
        return ""
    html = _escape_whitespace(html)
    return _style_wrap(html, run.run_style)


def _render_image(doc_ir: DocIR, image: ImageIR, *, block: bool = False) -> str:
    asset = doc_ir.assets.get(image.image_id)
    if asset is None:
        return ""

    style_parts = ["max-width:100%"]
    if block:
        style_parts.append("display:block")
    else:
        style_parts.append("vertical-align:middle")
    if image.display_width_pt is not None:
        style_parts.append(f"width:{image.display_width_pt:.1f}pt")
    if image.display_height_pt is not None:
        style_parts.append(f"height:{image.display_height_pt:.1f}pt")
    elif image.display_width_pt is None:
        style_parts.append("height:auto")

    extra_attrs = [
        f'src="{escape(asset.as_data_url(), quote=True)}"',
        f'alt="{escape(image.alt_text or asset.filename or "")}"',
    ]
    attrs = _html_attrs(style=";".join(style_parts), extra_attrs=extra_attrs)
    return f"<img {attrs} />"


def _paragraph_css(
    style: ParaStyleInfo | None,
    *,
    clamp_negative_first_line_indent: bool = False,
) -> str:
    parts: list[str] = ["margin:0"]
    if style is not None:
        if style.align:
            parts.append(f"text-align:{style.align}")
        if style.left_indent_pt is not None:
            parts.append(f"padding-left:{style.left_indent_pt:.1f}pt")
        if style.right_indent_pt is not None:
            parts.append(f"padding-right:{style.right_indent_pt:.1f}pt")
        if style.first_line_indent_pt is not None:
            text_indent = style.first_line_indent_pt
            if clamp_negative_first_line_indent and text_indent < 0:
                text_indent = 0.0
            parts.append(f"text-indent:{text_indent:.1f}pt")
    return ";".join(parts)


def _html_attrs(
    *,
    style: str | None = None,
    extra_attrs: list[str] | None = None,
) -> str:
    attrs: list[str] = []
    if style:
        attrs.append(f'style="{style}"')

    if extra_attrs:
        attrs.extend(extra_attrs)
    return " ".join(attrs)


def _flush_paragraph(
    run_fragments: list[str],
    para_style: ParaStyleInfo | None,
    *,
    clamp_negative_first_line_indent: bool = False,
) -> str:
    content = "".join(run_fragments)
    if not content.strip():
        content = "&nbsp;"
    tag = para_style.render_tag if para_style is not None and para_style.render_tag else "p"
    attrs = _html_attrs(
        style=_paragraph_css(
            para_style,
            clamp_negative_first_line_indent=clamp_negative_first_line_indent,
        ),
    )
    return f"<{tag} {attrs}>{content}</{tag}>"


def _cell_css(style: CellStyleInfo | None, *, render_table_grid: bool = False) -> str:
    parts: list[str] = []
    # PDF tables often arrive with structure but incomplete border CSS. Fall back
    # to a conservative grid only when the PDF pipeline explicitly asks for it.
    fallback_border = "1px solid #4a4f57" if render_table_grid else "none"
    if style is not None:
        if style.background:
            parts.append(f"background-color:{style.background}")
        if style.vertical_align:
            parts.append(f"vertical-align:{style.vertical_align}")
        if style.horizontal_align:
            parts.append(f"text-align:{style.horizontal_align}")
        if style.width_pt is not None:
            parts.append(f"width:{style.width_pt:.1f}pt")
        if style.height_pt is not None:
            parts.append(f"height:{style.height_pt:.1f}pt")
        parts.append(f"border-top:{style.border_top or fallback_border}")
        parts.append(f"border-bottom:{style.border_bottom or fallback_border}")
        parts.append(f"border-left:{style.border_left or fallback_border}")
        parts.append(f"border-right:{style.border_right or fallback_border}")
        diagonal_background = _cell_diagonal_background(style)
        if diagonal_background:
            parts.append(f"background-image:url({diagonal_background})")
            parts.append("background-repeat:no-repeat")
            parts.append("background-size:100% 100%")
    else:
        parts.extend(
            [
                f"border-top:{fallback_border}",
                f"border-bottom:{fallback_border}",
                f"border-left:{fallback_border}",
                f"border-right:{fallback_border}",
            ]
        )
    parts.append("padding:4px 6px")
    return ";".join(parts)


def _parse_border_css(border_css: str | None) -> tuple[int, str, str] | None:
    if not border_css:
        return None
    match = re.fullmatch(r"\s*(\d+)px\s+([a-zA-Z-]+)\s+(#[0-9A-Fa-f]{3,8})\s*", border_css)
    if not match:
        return None
    return int(match.group(1)), match.group(2), match.group(3)


def _svg_dasharray(style_name: str, stroke_width: int) -> str | None:
    if style_name == "dashed":
        return f"{max(stroke_width * 4, 4)} {max(stroke_width * 2, 2)}"
    if style_name == "dotted":
        return f"{max(stroke_width, 1)} {max(stroke_width * 2, 2)}"
    return None


def _svg_diagonal_lines(style: CellStyleInfo) -> str | None:
    diagonals: list[tuple[str, int, str, str]] = []
    for direction, border_css in (
        ("tl_br", style.diagonal_tl_br),
        ("tr_bl", style.diagonal_tr_bl),
    ):
        parsed = _parse_border_css(border_css)
        if parsed is None:
            continue
        stroke_width, style_name, color = parsed
        diagonals.append((direction, stroke_width, style_name, color))

    if not diagonals:
        return None

    svg_parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" preserveAspectRatio="none">'
    ]
    for direction, stroke_width, style_name, color in diagonals:
        dasharray = _svg_dasharray(style_name, stroke_width)
        if direction == "tl_br":
            coords = (0, 0, 100, 100)
        else:
            coords = (100, 0, 0, 100)

        if style_name == "double":
            offset = min(max(stroke_width * 1.2, 1.5), 5.0)
            for delta in (-offset, offset):
                if direction == "tl_br":
                    x1, y1, x2, y2 = 0, max(0.0, delta), 100 - max(0.0, delta), 100
                else:
                    x1, y1, x2, y2 = 100, max(0.0, delta), max(0.0, delta), 100
                svg_parts.append(
                    f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{max(stroke_width / 2, 1)}" />'
                )
            continue

        attrs = [
            f'x1="{coords[0]}"',
            f'y1="{coords[1]}"',
            f'x2="{coords[2]}"',
            f'y2="{coords[3]}"',
            f'stroke="{color}"',
            f'stroke-width="{stroke_width}"',
        ]
        if dasharray:
            attrs.append(f'stroke-dasharray="{dasharray}"')
        svg_parts.append(f"<line {' '.join(attrs)} />")

    svg_parts.append("</svg>")
    return "".join(svg_parts)


def _cell_diagonal_background(style: CellStyleInfo) -> str | None:
    svg = _svg_diagonal_lines(style)
    if svg is None:
        return None
    svg_base64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{svg_base64}"


def _table_css(table: TableIR, para_style: ParaStyleInfo | None) -> str:
    align = para_style.align if para_style is not None else None
    parts = ["border-collapse:collapse", "margin-top:8px", "margin-bottom:12px"]
    if table.table_style is not None and table.table_style.width_pt is not None:
        parts.append(f"width:{table.table_style.width_pt:.1f}pt")
    if table.table_style is not None and table.table_style.height_pt is not None:
        parts.append(f"height:{table.table_style.height_pt:.1f}pt")
    if align == "center":
        parts.extend(["margin-left:auto", "margin-right:auto"])
    elif align == "right":
        parts.extend(["margin-left:auto", "margin-right:0"])
    else:
        parts.extend(["margin-left:0", "margin-right:auto"])
    return ";".join(parts)


def _render_paragraph_like(
    doc_ir: DocIR,
    content: list[ParagraphContentNode],
    para_style: ParaStyleInfo | None,
    *,
    clamp_negative_first_line_indent: bool = False,
) -> str:
    if content and all(isinstance(node, ImageIR) for node in content) and len(content) > 1:
        image_fragments = [
            _render_image(doc_ir, node, block=True)
            for node in content
            if isinstance(node, ImageIR)
        ]
        attrs = _html_attrs(style="margin:0;line-height:0")
        return f"<div {attrs}>{''.join(fragment for fragment in image_fragments if fragment)}</div>"

    parts: list[str] = []
    inline_fragments: list[str] = []

    for node in content:
        if isinstance(node, RunIR):
            inline_fragments.append(_wrap_run(doc_ir, node))
            continue
        if isinstance(node, ImageIR):
            image_html = _render_image(doc_ir, node)
            if image_html:
                inline_fragments.append(image_html)
            continue
        if isinstance(node, TableIR):
            if inline_fragments:
                parts.append(
                    _flush_paragraph(
                        inline_fragments,
                        para_style,
                        clamp_negative_first_line_indent=clamp_negative_first_line_indent,
                    )
                )
                inline_fragments = []
            parts.append(_render_table(doc_ir, node, para_style))

    if inline_fragments:
        parts.append(
            _flush_paragraph(
                inline_fragments,
                para_style,
                clamp_negative_first_line_indent=clamp_negative_first_line_indent,
            )
        )
    elif not parts:
        parts.append(
            _flush_paragraph(
                [],
                para_style,
                clamp_negative_first_line_indent=clamp_negative_first_line_indent,
            )
        )

    return "\n".join(parts)


def _render_cell_paragraph(doc_ir: DocIR, paragraph: ParagraphIR) -> str:
    return _render_paragraph_like(
        doc_ir,
        paragraph.content,
        paragraph.para_style,
        clamp_negative_first_line_indent=True,
    )


def _cell_span_attrs(cell: TableCellIR) -> list[str]:
    if cell.cell_style is None:
        return []

    attrs: list[str] = []
    if cell.cell_style.colspan > 1:
        attrs.append(f'colspan="{cell.cell_style.colspan}"')
    if cell.cell_style.rowspan > 1:
        attrs.append(f'rowspan="{cell.cell_style.rowspan}"')
    return attrs


def _render_cell(doc_ir: DocIR, cell: TableCellIR, *, render_table_grid: bool = False) -> str:
    cell_attrs = _html_attrs(
        style=_cell_css(cell.cell_style, render_table_grid=render_table_grid),
        extra_attrs=_cell_span_attrs(cell),
    )

    if cell.paragraphs:
        content = "".join(_render_cell_paragraph(doc_ir, paragraph) for paragraph in cell.paragraphs)
    else:
        content = "&nbsp;"

    return f"<td {cell_attrs}>{content}</td>"


def _render_table(doc_ir: DocIR, table: TableIR, para_style: ParaStyleInfo | None = None) -> str:
    render_table_grid = bool(table.table_style and table.table_style.preview_grid)
    # `render_table_grid` is a rendering hint, not a layout model change: explicit
    # cell borders still win, and the shared table renderer stays in one place.
    table_style = _table_css(table, para_style)
    table_attrs = _html_attrs(
        style=table_style,
    )

    if not table.cells:
        return f"<table {table_attrs}></table>"

    covered: set[tuple[int, int]] = set()
    cells_by_pos = {(cell.row_index, cell.col_index): cell for cell in table.cells}
    max_row = max(cell.row_index for cell in table.cells)
    max_col = max(cell.col_index for cell in table.cells)

    lines = [f"<table {table_attrs}>"]
    for row in range(1, max_row + 1):
        lines.append("  <tr>")
        for col in range(1, max_col + 1):
            if (row, col) in covered:
                continue

            cell = cells_by_pos.get((row, col))
            if cell is None:
                lines.append(f'    <td style="{_cell_css(None, render_table_grid=render_table_grid)}">&nbsp;</td>')
                continue

            if cell.cell_style is not None:
                rowspan = max(cell.cell_style.rowspan, 1)
                colspan = max(cell.cell_style.colspan, 1)
            else:
                rowspan = 1
                colspan = 1

            for covered_row in range(row, row + rowspan):
                for covered_col in range(col, col + colspan):
                    if covered_row == row and covered_col == col:
                        continue
                    covered.add((covered_row, covered_col))

            lines.append(f"    {_render_cell(doc_ir, cell, render_table_grid=render_table_grid)}")
        lines.append("  </tr>")
    lines.append("</table>")
    return "\n".join(lines)


def _render_paragraph(doc_ir: DocIR, paragraph: ParagraphIR) -> str:
    return _render_paragraph_like(
        doc_ir,
        paragraph.content,
        paragraph.para_style,
    )


def _page_style(page: PageInfo) -> str:
    parts = [
        "box-sizing:border-box",
        "background:#fff",
        "border:1px solid #222",
        "box-shadow:0 1px 3px rgba(0,0,0,0.08)",
        "margin:0 auto 24px auto",
    ]
    if page.width_pt is not None:
        parts.append(f"width:{page.width_pt:.1f}pt")
    else:
        parts.append("max-width:900px")
    if page.height_pt is not None:
        parts.append(f"min-height:{page.height_pt:.1f}pt")
    return ";".join(parts)


def _page_content_style(page: PageInfo) -> str:
    margin_top = page.margin_top_pt if page.margin_top_pt is not None else 48.0
    margin_right = page.margin_right_pt if page.margin_right_pt is not None else 42.0
    margin_bottom = page.margin_bottom_pt if page.margin_bottom_pt is not None else 48.0
    margin_left = page.margin_left_pt if page.margin_left_pt is not None else 42.0
    return (
        "box-sizing:border-box;"
        f"padding:{margin_top:.1f}pt {margin_right:.1f}pt {margin_bottom:.1f}pt {margin_left:.1f}pt"
    )


def _render_paged_body(doc_ir: DocIR) -> str:
    paragraphs_by_page: dict[int, list[ParagraphIR]] = {}
    unpaged: list[ParagraphIR] = []

    for paragraph in doc_ir.paragraphs:
        if paragraph.page_number is None:
            unpaged.append(paragraph)
            continue
        paragraphs_by_page.setdefault(paragraph.page_number, []).append(paragraph)

    parts: list[str] = []
    for page in doc_ir.pages:
        page_paragraphs = paragraphs_by_page.get(page.page_number, [])
        content_html = "\n\n".join(_render_paragraph(doc_ir, paragraph) for paragraph in page_paragraphs)
        parts.append(
            f'<section class="document-page" data-page-number="{page.page_number}" style="{_page_style(page)}">'
            f'<div class="document-page__content" style="{_page_content_style(page)}">{content_html or "&nbsp;"}</div>'
            "</section>"
        )

    if unpaged:
        parts.append(
            '<section class="document-unpaged">'
            + "\n\n".join(_render_paragraph(doc_ir, paragraph) for paragraph in unpaged)
            + "</section>"
        )

    return "\n".join(parts)


def _render_html_document_shell(*, title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
  body {{
    max-width: 1100px;
    margin: 2em auto;
    padding: 0 1rem;
    line-height: 1.6;
    color: #1a1a1a;
    font-family: serif;
    background:#f5f5f2;
  }}
  p {{
    margin: 0 0 0.45em 0;
  }}
  table {{
    border-collapse: collapse;
    margin: 8px 0 12px 0;
  }}
  img {{
    max-width: 100%;
    height: auto;
  }}
  .document-unpaged {{
    max-width: 900px;
    margin: 0 auto;
  }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def render_html_document(doc_ir: DocIR, *, title: str | None = None) -> str:
    """Render a document IR tree as a complete HTML document."""
    resolved_title = title or doc_ir.doc_id or "Document"
    body = (
        _render_paged_body(doc_ir)
        if doc_ir.pages
        else "\n\n".join(_render_paragraph(doc_ir, paragraph) for paragraph in doc_ir.paragraphs)
    )
    return _render_html_document_shell(title=resolved_title, body=body)


__all__ = ["render_html_document"]
