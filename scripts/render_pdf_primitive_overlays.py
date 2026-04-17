from __future__ import annotations

import argparse
from collections import Counter
from html import escape
from pathlib import Path

import pypdfium2 as pdfium

from document_processor.pdf.preview import PdfPreviewVisualPrimitive, _extract_pdfium_visual_primitives


ROLE_COLORS = {
    "axis_box": "#a855f7",
    "closed_shape": "#f97316",
    "segmented_horizontal_rule": "#ec4899",
    "segmented_vertical_rule": "#06b6d4",
    "horizontal_line_segment": "#ef4444",
    "vertical_line_segment": "#3b82f6",
    "long_horizontal_rule": "#dc2626",
    "long_vertical_rule": "#1d4ed8",
    "box_attached_rule": "#10b981",
}


def _primary_role(primitive: PdfPreviewVisualPrimitive) -> str:
    for role in (
        "axis_box",
        "closed_shape",
        "segmented_horizontal_rule",
        "segmented_vertical_rule",
        "long_horizontal_rule",
        "long_vertical_rule",
        "horizontal_line_segment",
        "vertical_line_segment",
        "box_attached_rule",
    ):
        if role in primitive.candidate_roles:
            return role
    return primitive.candidate_roles[0] if primitive.candidate_roles else "unknown"


def _render_doc_page(doc_dir: Path, page_number: int) -> Path:
    source_pdf = doc_dir / "source.pdf"
    source_png = doc_dir / "review" / "source-pages-png" / f"page-{page_number:03d}.png"
    source_bmp = doc_dir / "review" / "source-pages" / f"page-{page_number:03d}.bmp"
    source_image = source_png if source_png.exists() else source_bmp
    output_dir = doc_dir / "review" / "primitive-overlays"
    output_dir.mkdir(parents=True, exist_ok=True)

    with pdfium.PdfDocument(str(source_pdf)) as pdf:
        page = pdf[page_number - 1]
        page_width = page.get_width() or 1.0
        page_height = page.get_height() or 1.0
        primitives = _extract_pdfium_visual_primitives(page, page_number=page_number)

    role_counts = Counter()
    boxes: list[str] = []
    for index, primitive in enumerate(primitives, start=1):
        bbox = primitive.bounding_box
        role = _primary_role(primitive)
        role_counts[role] += 1
        color = ROLE_COLORS.get(role, "#111827")
        left = bbox.left_pt / page_width * 100.0
        width = (bbox.right_pt - bbox.left_pt) / page_width * 100.0
        top = (page_height - bbox.top_pt) / page_height * 100.0
        height = (bbox.top_pt - bbox.bottom_pt) / page_height * 100.0
        boxes.append(
            f'<div class="box" style="left:{left:.4f}%;top:{top:.4f}%;width:{width:.4f}%;height:{height:.4f}%;border-color:{color};"></div>'
        )

    legend_rows = []
    for role, count in sorted(role_counts.items()):
        color = ROLE_COLORS.get(role, "#111827")
        legend_rows.append(
            f'<div class="legend-item"><span class="swatch" style="background:{color};"></span>{escape(role)}: {count}</div>'
        )

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>Primitive Overlay p{page_number}</title>
<style>
  body {{
    margin: 0;
    padding: 16px;
    background: #f3f3f3;
    font-family: monospace;
  }}
  .legend {{
    margin-bottom: 12px;
    line-height: 1.5;
  }}
  .legend-item {{
    display: inline-flex;
    align-items: center;
    margin-right: 12px;
    margin-bottom: 6px;
    gap: 6px;
  }}
  .swatch {{
    width: 12px;
    height: 12px;
    display: inline-block;
    border-radius: 2px;
  }}
  .wrap {{
    position: relative;
    display: inline-block;
    box-shadow: 0 2px 10px rgba(0,0,0,.12);
    background: white;
  }}
  .wrap img {{
    display: block;
    max-width: min(96vw, 1600px);
    height: auto;
  }}
  .box {{
    position: absolute;
    box-sizing: border-box;
    border: 1px solid;
    opacity: 0.72;
  }}
</style>
</head>
<body>
  <div class="legend">
    <div>page {page_number} primitives: {len(primitives)}</div>
    {''.join(legend_rows)}
  </div>
  <div class="wrap">
    <img src="../{escape(source_image.parent.name)}/{escape(source_image.name)}" alt="page {page_number}">
    {''.join(boxes)}
  </div>
</body>
</html>
"""

    out_path = output_dir / f"page-{page_number:03d}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("doc_dir", type=Path)
    parser.add_argument("--page", type=int, required=True)
    args = parser.parse_args()
    print(_render_doc_page(args.doc_dir.resolve(), args.page))


if __name__ == "__main__":
    main()
