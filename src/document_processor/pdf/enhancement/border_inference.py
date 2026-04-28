"""Infer approximate PDF table borders from rasterized page content."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median

from ..meta import PdfBoundingBox

_DARK_PIXEL_THRESHOLD = 200
_EDGE_COVERAGE_THRESHOLD = 0.6
_MAX_BORDER_SCAN_PX = 4
_BACKGROUND_BUCKET_SIZE = 16
_BACKGROUND_DOMINANCE_THRESHOLD = 0.35
_MAX_BACKGROUND_SAMPLE_STEP = 3


@dataclass(slots=True)
class RenderedPdfPage:
    width_px: int
    height_px: int
    stride: int
    pixels: bytes


@dataclass(slots=True)
class RenderedPdfColorPage:
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


def render_pdf_pages_to_color(
    pdf_path: str | Path,
    *,
    page_numbers: set[int],
    dpi: int,
) -> dict[int, RenderedPdfColorPage]:
    import pypdfium2 as pdfium

    scale = dpi / 72.0
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        rendered_pages: dict[int, RenderedPdfColorPage] = {}
        for page_number in sorted(page_numbers):
            page = doc[page_number - 1]
            bitmap = page.render(scale=scale, grayscale=False, no_smoothpath=True)
            try:
                rendered_pages[page_number] = RenderedPdfColorPage(
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
    left_px, right_px, top_px, bottom_px = _bbox_to_pixel_bounds(
        bbox=bbox,
        page_width_px=page.width_px,
        page_height_px=page.height_px,
        page_height_pt=page_height_pt,
        dpi=dpi,
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


def infer_cell_background_from_rendered_page(
    page: RenderedPdfColorPage,
    *,
    bbox: PdfBoundingBox,
    page_height_pt: float,
    dpi: int,
) -> str | None:
    left_px, right_px, top_px, bottom_px = _bbox_to_pixel_bounds(
        bbox=bbox,
        page_width_px=page.width_px,
        page_height_px=page.height_px,
        page_height_pt=page_height_pt,
        dpi=dpi,
    )
    if right_px <= left_px or bottom_px < top_px:
        return None

    width_px = right_px - left_px
    height_px = (bottom_px - top_px) + 1
    trim_x = min(max(int(width_px * 0.12), 3), 16)
    trim_y = min(max(int(height_px * 0.12), 3), 16)

    x0 = left_px + trim_x if width_px > trim_x * 2 else left_px
    x1 = right_px - trim_x if width_px > trim_x * 2 else right_px
    y0 = top_px + trim_y if height_px > trim_y * 2 else top_px
    y1 = bottom_px - trim_y if height_px > trim_y * 2 else bottom_px
    if x1 <= x0 or y1 < y0:
        return None

    sample_step = max(1, min(max(width_px, height_px) // 40, _MAX_BACKGROUND_SAMPLE_STEP))
    buckets: dict[tuple[int, int, int], list[int]] = {}
    total = 0
    for y in range(y0, y1 + 1, sample_step):
        row_offset = y * page.stride
        for x in range(x0, x1, sample_step):
            idx = row_offset + (x * 3)
            blue = page.pixels[idx]
            green = page.pixels[idx + 1]
            red = page.pixels[idx + 2]
            bucket = (
                red // _BACKGROUND_BUCKET_SIZE,
                green // _BACKGROUND_BUCKET_SIZE,
                blue // _BACKGROUND_BUCKET_SIZE,
            )
            stats = buckets.setdefault(bucket, [0, 0, 0, 0])
            stats[0] += 1
            stats[1] += red
            stats[2] += green
            stats[3] += blue
            total += 1

    if total <= 0:
        return None

    dominant_bucket, dominant_stats = max(buckets.items(), key=lambda item: item[1][0])
    dominant_count, red_sum, green_sum, blue_sum = dominant_stats
    dominance_ratio = dominant_count / total
    if dominance_ratio < _BACKGROUND_DOMINANCE_THRESHOLD:
        return None

    red = red_sum // dominant_count
    green = green_sum // dominant_count
    blue = blue_sum // dominant_count
    if _is_near_white((red, green, blue)):
        return None
    return f"#{red:02x}{green:02x}{blue:02x}"


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


def _bbox_to_pixel_bounds(
    *,
    bbox: PdfBoundingBox,
    page_width_px: int,
    page_height_px: int,
    page_height_pt: float,
    dpi: int,
) -> tuple[int, int, int, int]:
    scale = dpi / 72.0
    left_px = _clamp_px(int(bbox.left_pt * scale), upper=page_width_px - 1)
    right_px = _clamp_px(int(round(bbox.right_pt * scale)), upper=page_width_px)
    top_px = _clamp_px(int((page_height_pt - bbox.top_pt) * scale), upper=page_height_px - 1)
    bottom_px = _clamp_px(
        int(round((page_height_pt - bbox.bottom_pt) * scale)) - 1,
        upper=page_height_px - 1,
    )
    return left_px, right_px, top_px, bottom_px


def _is_near_white(rgb: tuple[int, int, int]) -> bool:
    red, green, blue = rgb
    return red >= 245 and green >= 245 and blue >= 245


__all__ = [
    "RenderedPdfColorPage",
    "RenderedPdfPage",
    "infer_cell_background_from_rendered_page",
    "infer_cell_borders_from_rendered_page",
    "render_pdf_pages_to_color",
    "render_pdf_pages_to_grayscale",
]
