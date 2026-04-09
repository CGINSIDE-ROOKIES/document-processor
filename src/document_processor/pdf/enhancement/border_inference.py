"""Infer approximate PDF table borders from rasterized page content."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median

from ..meta import PdfBoundingBox

_DARK_PIXEL_THRESHOLD = 200
_EDGE_COVERAGE_THRESHOLD = 0.6
_MAX_BORDER_SCAN_PX = 4


@dataclass(slots=True)
class RenderedPdfPage:
    width_px: int
    height_px: int
    stride: int
    pixels: bytes


def render_pdf_pages_to_grayscale(
    pdf_path: str | Path,
    *,
    page_numbers: set[int],
    dpi: int,
) -> dict[int, RenderedPdfPage]:
    import pypdfium2 as pdfium

    scale = dpi / 72.0
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        rendered_pages: dict[int, RenderedPdfPage] = {}
        for page_number in sorted(page_numbers):
            page = doc[page_number - 1]
            bitmap = page.render(scale=scale, grayscale=True, no_smoothpath=True)
            try:
                rendered_pages[page_number] = RenderedPdfPage(
                    width_px=bitmap.width,
                    height_px=bitmap.height,
                    stride=bitmap.stride,
                    pixels=bytes(bitmap.buffer),
                )
            finally:
                bitmap.close()
        return rendered_pages
    finally:
        doc.close()


def infer_cell_borders_from_rendered_page(
    page: RenderedPdfPage,
    *,
    bbox: PdfBoundingBox,
    page_height_pt: float,
    dpi: int,
) -> dict[str, str | None]:
    scale = dpi / 72.0
    left_px = _clamp_px(int(bbox.left_pt * scale), upper=page.width_px - 1)
    right_px = _clamp_px(int(round(bbox.right_pt * scale)), upper=page.width_px)
    top_px = _clamp_px(int((page_height_pt - bbox.top_pt) * scale), upper=page.height_px - 1)
    bottom_px = _clamp_px(
        int(round((page_height_pt - bbox.bottom_pt) * scale)) - 1,
        upper=page.height_px - 1,
    )
    if right_px <= left_px or bottom_px < top_px:
        return {"top": None, "bottom": None, "left": None, "right": None}

    width_px = right_px - left_px
    height_px = (bottom_px - top_px) + 1
    trim_x = min(max(int(width_px * 0.05), 2), 8)
    trim_y = min(max(int(height_px * 0.05), 2), 8)

    x0 = left_px + trim_x if width_px > trim_x * 2 else left_px
    x1 = right_px - trim_x if width_px > trim_x * 2 else right_px
    y0 = top_px + trim_y if height_px > trim_y * 2 else top_px
    y1 = bottom_px - trim_y if height_px > trim_y * 2 else bottom_px

    return {
        "top": _infer_horizontal_border(page, x0=x0, x1=x1, row_candidates=range(top_px, min(bottom_px + 1, top_px + _MAX_BORDER_SCAN_PX))),
        "bottom": _infer_horizontal_border(
            page,
            x0=x0,
            x1=x1,
            row_candidates=range(bottom_px, max(top_px - 1, bottom_px - _MAX_BORDER_SCAN_PX), -1),
        ),
        "left": _infer_vertical_border(page, y0=y0, y1=y1, col_candidates=range(left_px, min(right_px, left_px + _MAX_BORDER_SCAN_PX))),
        "right": _infer_vertical_border(
            page,
            y0=y0,
            y1=y1,
            col_candidates=range(right_px - 1, max(left_px - 1, right_px - _MAX_BORDER_SCAN_PX - 1), -1),
        ),
    }


def _infer_horizontal_border(
    page: RenderedPdfPage,
    *,
    x0: int,
    x1: int,
    row_candidates: range,
) -> str | None:
    if x1 <= x0:
        return None

    dark_values: list[int] = []
    thickness = 0
    found_active = False
    for y in row_candidates:
        ratio, row_dark_values = _row_dark_stats(page, y=y, x0=x0, x1=x1)
        if ratio >= _EDGE_COVERAGE_THRESHOLD:
            found_active = True
            thickness += 1
            dark_values.extend(row_dark_values)
            continue
        if found_active:
            break

    return _to_css_border(thickness=thickness, dark_values=dark_values)


def _infer_vertical_border(
    page: RenderedPdfPage,
    *,
    y0: int,
    y1: int,
    col_candidates: range,
) -> str | None:
    if y1 < y0:
        return None

    dark_values: list[int] = []
    thickness = 0
    found_active = False
    for x in col_candidates:
        ratio, col_dark_values = _col_dark_stats(page, x=x, y0=y0, y1=y1)
        if ratio >= _EDGE_COVERAGE_THRESHOLD:
            found_active = True
            thickness += 1
            dark_values.extend(col_dark_values)
            continue
        if found_active:
            break

    return _to_css_border(thickness=thickness, dark_values=dark_values)


def _row_dark_stats(page: RenderedPdfPage, *, y: int, x0: int, x1: int) -> tuple[float, list[int]]:
    row_offset = y * page.stride
    row = memoryview(page.pixels)[row_offset + x0 : row_offset + x1]
    dark_values = [value for value in row if value < _DARK_PIXEL_THRESHOLD]
    ratio = len(dark_values) / len(row) if row else 0.0
    return ratio, dark_values


def _col_dark_stats(page: RenderedPdfPage, *, x: int, y0: int, y1: int) -> tuple[float, list[int]]:
    dark_values: list[int] = []
    total = 0
    for y in range(y0, y1 + 1):
        value = page.pixels[(y * page.stride) + x]
        total += 1
        if value < _DARK_PIXEL_THRESHOLD:
            dark_values.append(value)
    ratio = len(dark_values) / total if total else 0.0
    return ratio, dark_values


def _to_css_border(*, thickness: int, dark_values: list[int]) -> str | None:
    if thickness <= 0 or not dark_values:
        return None
    grayscale = max(0, min(int(median(dark_values)), 255))
    color = f"#{grayscale:02x}{grayscale:02x}{grayscale:02x}"
    return f"{min(thickness, 3)}px solid {color}"


def _clamp_px(value: int, *, upper: int) -> int:
    return max(0, min(value, upper))


__all__ = [
    "RenderedPdfPage",
    "infer_cell_borders_from_rendered_page",
    "render_pdf_pages_to_grayscale",
]
