"""Render structural document IR as styled HTML."""

from __future__ import annotations

from html import escape
import re

from .models import DocIR, ImageIR, ParagraphIR, RunIR, TableCellIR, TableCellParagraphIR, TableIR
from .style_types import CellStyleInfo, ParaStyleInfo, RunStyleInfo


def _run_css(style: RunStyleInfo) -> str:
    parts: list[str] = []
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
    return html


def _wrap_run(run: RunIR) -> str:
    html = escape(run.text)
    if not html:
        return ""
    html = _escape_whitespace(html)
    return _style_wrap(html, run.run_style)


def _render_image(doc_ir: DocIR, image: ImageIR) -> str:
    asset = doc_ir.assets.get(image.image_id)
    if asset is None:
        return ""

    style_parts = ["max-width:100%", "vertical-align:middle"]
    if image.display_width_pt is not None:
        style_parts.append(f"width:{image.display_width_pt:.1f}pt")
    if image.display_height_pt is not None:
        style_parts.append(f"height:{image.display_height_pt:.1f}pt")
    elif image.display_width_pt is None:
        style_parts.append("height:auto")

    attrs = [
        f'src="{escape(asset.as_data_url(), quote=True)}"',
        f'alt="{escape(image.alt_text or asset.filename or "")}"',
        f'style="{";".join(style_parts)}"',
    ]
    return f"<img {' '.join(attrs)} />"


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


def _flush_paragraph(
    run_fragments: list[str],
    para_style: ParaStyleInfo | None,
    *,
    clamp_negative_first_line_indent: bool = False,
) -> str:
    content = "".join(run_fragments)
    if not content.strip():
        content = "&nbsp;"
    return f'<p style="{_paragraph_css(para_style, clamp_negative_first_line_indent=clamp_negative_first_line_indent)}">{content}</p>'


def _cell_css(style: CellStyleInfo | None) -> str:
    parts: list[str] = []
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
        parts.append(f"border-top:{style.border_top or 'none'}")
        parts.append(f"border-bottom:{style.border_bottom or 'none'}")
        parts.append(f"border-left:{style.border_left or 'none'}")
        parts.append(f"border-right:{style.border_right or 'none'}")
    else:
        parts.extend(
            [
                "border-top:none",
                "border-bottom:none",
                "border-left:none",
                "border-right:none",
            ]
        )
    parts.append("padding:4px 6px")
    return ";".join(parts)


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
    content: list[object],
    para_style: ParaStyleInfo | None,
    *,
    clamp_negative_first_line_indent: bool = False,
) -> str:
    parts: list[str] = []
    inline_fragments: list[str] = []

    for node in content:
        if isinstance(node, RunIR):
            inline_fragments.append(_wrap_run(node))
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


def _render_cell_paragraph(doc_ir: DocIR, paragraph: TableCellParagraphIR) -> str:
    paragraph.sync_content()
    return _render_paragraph_like(
        doc_ir,
        paragraph.content,
        paragraph.para_style,
        clamp_negative_first_line_indent=True,
    )


def _render_cell(doc_ir: DocIR, cell: TableCellIR) -> str:
    attrs = [f'style="{_cell_css(cell.cell_style)}"']
    if cell.cell_style is not None:
        if cell.cell_style.colspan > 1:
            attrs.append(f'colspan="{cell.cell_style.colspan}"')
        if cell.cell_style.rowspan > 1:
            attrs.append(f'rowspan="{cell.cell_style.rowspan}"')

    if cell.paragraphs:
        content = "".join(_render_cell_paragraph(doc_ir, paragraph) for paragraph in cell.paragraphs)
    else:
        content = "&nbsp;"

    return f"<td {' '.join(attrs)}>{content}</td>"


def _render_table(doc_ir: DocIR, table: TableIR, para_style: ParaStyleInfo | None = None) -> str:
    if not table.cells:
        return f'<table style="{_table_css(table, para_style)}"></table>'

    covered: set[tuple[int, int]] = set()
    cells_by_pos = {(cell.row_index, cell.col_index): cell for cell in table.cells}
    max_row = max(cell.row_index for cell in table.cells)
    max_col = max(cell.col_index for cell in table.cells)

    lines = [f'<table style="{_table_css(table, para_style)}">']
    for row in range(1, max_row + 1):
        lines.append("  <tr>")
        for col in range(1, max_col + 1):
            if (row, col) in covered:
                continue

            cell = cells_by_pos.get((row, col))
            if cell is None:
                lines.append('    <td style="padding:4px 6px;border:none">&nbsp;</td>')
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

            lines.append(f"    {_render_cell(doc_ir, cell)}")
        lines.append("  </tr>")
    lines.append("</table>")
    return "\n".join(lines)


def _render_paragraph(doc_ir: DocIR, paragraph: ParagraphIR) -> str:
    paragraph.sync_content()
    return _render_paragraph_like(doc_ir, paragraph.content, paragraph.para_style)


def render_html_document(doc_ir: DocIR, *, title: str | None = None) -> str:
    """Render a document IR tree as a complete HTML document."""
    resolved_title = title or doc_ir.doc_id or "Document"
    body = "\n\n".join(
        _render_paragraph(doc_ir, paragraph)
        for paragraph in doc_ir.paragraphs
    )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(resolved_title)}</title>
<style>
  body {{
    max-width: 900px;
    margin: 2em auto;
    padding: 0 1rem;
    line-height: 1.6;
    color: #1a1a1a;
    font-family: serif;
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
</style>
</head>
<body>
{body}
</body>
</html>
"""


__all__ = ["render_html_document"]
