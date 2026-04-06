"""Render structural document IR as styled HTML."""

from __future__ import annotations

from html import escape
import re

from .models import DocIR, ParagraphIR, RunIR, TableCellIR, TableCellParagraphIR, TableIR
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


def _paragraph_css(style: ParaStyleInfo | None) -> str:
    parts: list[str] = ["margin:0"]
    if style is not None:
        if style.align:
            parts.append(f"text-align:{style.align}")
        if style.left_indent_pt is not None:
            parts.append(f"padding-left:{style.left_indent_pt:.1f}pt")
        if style.right_indent_pt is not None:
            parts.append(f"padding-right:{style.right_indent_pt:.1f}pt")
        if style.first_line_indent_pt is not None:
            parts.append(f"text-indent:{style.first_line_indent_pt:.1f}pt")
    return ";".join(parts)


def _flush_paragraph(run_fragments: list[str], para_style: ParaStyleInfo | None) -> str:
    content = "".join(run_fragments)
    if not content.strip():
        content = "&nbsp;"
    return f'<p style="{_paragraph_css(para_style)}">{content}</p>'


def _cell_css(style: CellStyleInfo | None) -> str:
    parts: list[str] = []
    if style is not None:
        if style.background:
            parts.append(f"background-color:{style.background}")
        if style.vertical_align:
            parts.append(f"vertical-align:{style.vertical_align}")
        if style.horizontal_align:
            parts.append(f"text-align:{style.horizontal_align}")
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


def _table_css(para_style: ParaStyleInfo | None) -> str:
    align = para_style.align if para_style is not None else None
    if align in (None, "justify", ""):
        align = "center"

    parts = ["border-collapse:collapse", "margin-top:8px", "margin-bottom:12px"]
    if align == "center":
        parts.extend(["margin-left:auto", "margin-right:auto"])
    elif align == "right":
        parts.extend(["margin-left:auto", "margin-right:0"])
    else:
        parts.extend(["margin-left:0", "margin-right:auto"])
    return ";".join(parts)


def _render_cell_paragraph(paragraph: TableCellParagraphIR) -> str:
    return _flush_paragraph([_wrap_run(run) for run in paragraph.runs], paragraph.para_style)


def _render_cell(cell: TableCellIR) -> str:
    attrs = [f'style="{_cell_css(cell.cell_style)}"']
    if cell.cell_style is not None:
        if cell.cell_style.colspan > 1:
            attrs.append(f'colspan="{cell.cell_style.colspan}"')
        if cell.cell_style.rowspan > 1:
            attrs.append(f'rowspan="{cell.cell_style.rowspan}"')

    if cell.paragraphs:
        content = "".join(_render_cell_paragraph(paragraph) for paragraph in cell.paragraphs)
    else:
        content = "&nbsp;"

    return f"<td {' '.join(attrs)}>{content}</td>"


def _render_table(table: TableIR, para_style: ParaStyleInfo | None = None) -> str:
    if not table.cells:
        return f'<table style="{_table_css(para_style)}"></table>'

    covered: set[tuple[int, int]] = set()
    cells_by_pos = {(cell.row_index, cell.col_index): cell for cell in table.cells}
    max_row = max(cell.row_index for cell in table.cells)
    max_col = max(cell.col_index for cell in table.cells)

    lines = [f'<table style="{_table_css(para_style)}">']
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

            lines.append(f"    {_render_cell(cell)}")
        lines.append("  </tr>")
    lines.append("</table>")
    return "\n".join(lines)


def _render_paragraph(paragraph: ParagraphIR) -> str:
    parts: list[str] = []
    if paragraph.runs:
        parts.append(_flush_paragraph([_wrap_run(run) for run in paragraph.runs], paragraph.para_style))
    elif not paragraph.tables:
        parts.append(_flush_paragraph([], paragraph.para_style))

    for table in paragraph.tables:
        parts.append(_render_table(table, paragraph.para_style))

    return "\n".join(parts)


def export_html(doc_ir: DocIR, *, title: str | None = None) -> str:
    """Export a document IR tree as a complete HTML document."""
    resolved_title = title or doc_ir.doc_id or "Document"
    body = "\n\n".join(
        _render_paragraph(paragraph)
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
</style>
</head>
<body>
{body}
</body>
</html>
"""


__all__ = ["export_html"]
