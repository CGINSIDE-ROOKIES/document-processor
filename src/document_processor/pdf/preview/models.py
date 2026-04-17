"""Preview model and constant definitions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from pydantic import BaseModel, Field

from ...models import ImageIR, ParagraphIR, RunIR, TableIR
from ..meta import PdfBoundingBox

_VISUAL_TOUCH_TOLERANCE_PT = 1.0
_VISUAL_DIVIDER_SPAN_RATIO = 0.80
_VISUAL_MIN_LINE_SEGMENT_PT = 5.0
_VISUAL_SEGMENTED_AXIS_TOLERANCE_PT = 1.0
_VISUAL_SEGMENTED_GAP_TOLERANCE_PT = 3.0
_VISUAL_SEGMENTED_MIN_PARTS = 3
_VISUAL_SEGMENTED_MAX_FRAGMENT_PT = 18.0
_VISUAL_SEGMENTED_MIN_SPAN_PT = 10.0
_VISUAL_FRAME_MIN_SIZE_PT = 10.0
_VISUAL_OPEN_FRAME_PRIMITIVE_LIMIT = 500
_CANDIDATE_ASSIGN_TOLERANCE_PT = 2.0
_VISUAL_BOX_SEED_MIN_SIZE_PT = 4.0
_VISUAL_BOUNDARY_SUPPRESSION_TOLERANCE_PT = 2.0
_VISUAL_BOUNDARY_SUPPRESSION_OVERLAP_RATIO = 0.85
_VISUAL_LINE_JOIN_TOLERANCE_PT = 1.5
_LAYOUT_TABLE_GROUP_GAP_TOLERANCE_PT = 18.0
_LAYOUT_TABLE_ALIGNMENT_OVERLAP_RATIO = 0.75
_LAYOUT_TABLE_BOUNDARY_TOLERANCE_PT = 4.0
_LOGICAL_PAGE_NUMBER_FOOTER_TOP_RATIO = 0.14
_LOGICAL_PAGE_NUMBER_MAX_WIDTH_RATIO = 0.18
_LOGICAL_PAGE_NUMBER_TEXT_RE = re.compile(r"^\s*[-–—]?\s*[\(\[\{]?\s*(\d{1,4})\s*[\)\]\}]?\s*[-–—]?\s*$")
_COLUMN_BAND_CENTER_OFFSET_RATIO = 0.08
_COLUMN_BAND_GUTTER_MIN_WIDTH_PT = 12.0
_COLUMN_BAND_GUTTER_MIN_WIDTH_RATIO = 0.03
_COLUMN_BAND_MIN_HEIGHT_PT = 24.0
_COLUMN_BAND_SPLIT_MERGE_TOLERANCE_PT = 24.0
_IMAGE_STRIP_MIN_GROUP_SIZE = 3
_IMAGE_STRIP_MAX_HEIGHT_RATIO = 0.20
_IMAGE_STRIP_MIN_SPAN_OVERLAP_RATIO = 0.92
_IMAGE_STRIP_MAX_CENTER_DELTA_RATIO = 0.03
_IMAGE_STRIP_MAX_GAP_PT = 6.0


class PdfLayoutRegion(BaseModel):
    region_id: str
    region_type: str
    page_number: int
    bounding_box: PdfBoundingBox | None = None


class PdfPreviewTableContext(BaseModel):
    page_number: int | None = None
    bounding_box: PdfBoundingBox | None = None
    layout_region_id: str | None = None
    reading_order_index: int | None = None
    grid_row_boundaries: list[float] = Field(default_factory=list)
    grid_column_boundaries: list[float] = Field(default_factory=list)
    serialized_cell_count: int | None = None
    logical_cell_count: int | None = None
    covered_logical_cell_count: int | None = None
    non_empty_cell_count: int | None = None
    empty_cell_count: int | None = None
    spanning_cell_count: int | None = None
    line_art_boxes: list[PdfBoundingBox] = Field(default_factory=list)


class PdfPreviewVisualPrimitive(BaseModel):
    page_number: int
    draw_order: int
    object_type: str
    bounding_box: PdfBoundingBox
    fill_color: str | None = None
    stroke_color: str | None = None
    stroke_width_pt: float | None = None
    has_fill: bool = False
    has_stroke: bool = False
    is_axis_aligned_box: bool = False
    candidate_roles: list[str] = Field(default_factory=list)


class PdfPreviewVisualBlockCandidate(BaseModel):
    page_number: int
    candidate_type: str
    bounding_box: PdfBoundingBox
    primitive_draw_orders: list[int] = Field(default_factory=list)
    source_roles: list[str] = Field(default_factory=list)
    child_cells: list[PdfBoundingBox] = Field(default_factory=list)


class PdfPreviewContext(BaseModel):
    layout_regions: list[PdfLayoutRegion] = Field(default_factory=list)
    tables: list[PdfPreviewTableContext] = Field(default_factory=list)
    visual_block_candidates: list[PdfPreviewVisualBlockCandidate] = Field(default_factory=list)


@dataclass(slots=True)
class _PreviewRenderNode:
    kind: str
    unit_id: str
    bbox: PdfBoundingBox
    order_key: tuple[float, float, int, int]
    parent_paragraph_id: str | None = None
    parent_para_style: Any = None
    paragraph: ParagraphIR | None = None
    table: TableIR | None = None
    image: ImageIR | None = None
    run: RunIR | None = None


@dataclass(slots=True)
class _AssignedCandidate:
    candidate: PdfPreviewVisualBlockCandidate
    region_type: str
    top_offset_pt: float | None
    bottom_offset_pt: float | None
    order_key: tuple[float, float, int, int]
    paragraph_nodes: list[_PreviewRenderNode]
    table_nodes: list[_PreviewRenderNode]
    image_nodes: list[_PreviewRenderNode]
    run_nodes: list[_PreviewRenderNode]


@dataclass(slots=True)
class _AssignedCandidateGroup:
    candidates: list[_AssignedCandidate]
    region_type: str
    top_offset_pt: float | None
    bottom_offset_pt: float | None
    order_key: tuple[float, float, int, int]
    bounding_box: PdfBoundingBox


@dataclass(slots=True)
class _LogicalPage:
    page_number: int
    physical_page_number: int
    logical_page_type: str
    bounding_box: PdfBoundingBox
    source_region_ids: list[str]
    scale_factor: float = 1.0
    target_width_pt: float | None = None
    target_height_pt: float | None = None


@dataclass(slots=True)
class _PreviewCompositionEntry:
    item_type: str
    region_type: str
    top_offset_pt: float | None
    bottom_offset_pt: float | None
    order_key: tuple[float, float, int, int]
    bounding_box: PdfBoundingBox | None
    paragraph: ParagraphIR | None = None


@dataclass(slots=True)
class _LogicalPageComposition:
    ordered_paragraphs: list[ParagraphIR]
    assigned_candidates: list[_AssignedCandidate]
    promoted_candidate_ids: set[int]


__all__ = [
    "PdfLayoutRegion",
    "PdfPreviewTableContext",
    "PdfPreviewVisualPrimitive",
    "PdfPreviewVisualBlockCandidate",
    "PdfPreviewContext",
    "_PreviewRenderNode",
    "_AssignedCandidate",
    "_AssignedCandidateGroup",
    "_LogicalPage",
    "_PreviewCompositionEntry",
    "_LogicalPageComposition",
]
