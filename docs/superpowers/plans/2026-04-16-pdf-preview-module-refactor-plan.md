# PDF Preview Module Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `src/document_processor/pdf/preview.py`의 대형 단일 구현을 `src/document_processor/pdf/preview/` 내부 패키지로 분해하고, PDF HTML 경로를 `DocIR.from_file(...).to_html()` 중심으로 이해할 수 있게 정리한다.

**Architecture:** 먼저 `document_processor.pdf.preview`를 파일 모듈에서 패키지로 바꾸되 동작은 그대로 유지한다. 그 다음 preview 전용 모델, context 수집, primitive/candidate 추출, layout normalization, flow composition, residual render를 작은 내부 모듈로 순차 분리하고, 마지막에 dead path와 과도한 공개 helper를 제거한다.

**Tech Stack:** Python, Pydantic, existing `document_processor.pdf.*` pipeline, shared `html_exporter`, `uv run python -m pytest`

---

### Task 1: Add refactor safety-net tests

**Files:**
- Create: `tests/test_pdf_preview_module_api.py`
- Modify: `tests/test_pdf_preview.py`
- Test: `tests/test_pdf_preview_module_api.py`, `tests/test_pdf_preview.py`

- [ ] **Step 1: Write the failing module-shape tests**

```python
from __future__ import annotations

import importlib
import unittest


class PdfPreviewModuleApiTests(unittest.TestCase):
    def test_preview_submodules_are_importable(self) -> None:
        for module_name in (
            "document_processor.pdf.preview.models",
            "document_processor.pdf.preview.context",
            "document_processor.pdf.preview.primitives",
            "document_processor.pdf.preview.candidates",
            "document_processor.pdf.preview.layout",
            "document_processor.pdf.preview.compose",
            "document_processor.pdf.preview.render",
            "document_processor.pdf.preview.prepare",
        ):
            module = importlib.import_module(module_name)
            self.assertIsNotNone(module)
```

- [ ] **Step 2: Run the new test to verify RED**

Run:

```bash
uv run python -m pytest tests/test_pdf_preview_module_api.py -q
```

Expected: `ModuleNotFoundError` for `document_processor.pdf.preview.<submodule>`

- [ ] **Step 3: Add a public-path characterization test in `tests/test_pdf_preview.py`**

```python
def test_render_pdf_html_public_entrypoint_still_matches_preview_render(self) -> None:
    raw_document = {
        "file name": "sample.pdf",
        "number of pages": 1,
        "pages": [{"page number": 1, "width pt": 200, "height pt": 120}],
        "kids": [{"type": "paragraph", "content": "hello", "page number": 1, "bounding box": [10, 90, 40, 100]}],
    }
    doc = build_doc_ir_from_odl_result(raw_document, source_path="sample.pdf")
    context = build_pdf_preview_context(raw_document)

    self.assertEqual(
        render_pdf_html(doc, preview_context=context, title="Preview"),
        render_pdf_preview_html(doc, preview_context=context, title="Preview"),
    )
```

- [ ] **Step 4: Run the targeted preview tests**

Run:

```bash
uv run python -m pytest tests/test_pdf_preview.py -q
```

Expected: new module-shape test still fails; existing preview behavior tests stay green

- [ ] **Step 5: Commit the safety-net tests**

```bash
git add tests/test_pdf_preview_module_api.py tests/test_pdf_preview.py
git commit -m "test: add PDF preview refactor safety net"
```

### Task 2: Convert `preview.py` into a package without changing behavior

**Files:**
- Delete: `src/document_processor/pdf/preview.py`
- Create: `src/document_processor/pdf/preview/__init__.py`
- Modify: `src/document_processor/pdf/pipeline.py`
- Modify: `src/document_processor/models.py`
- Modify: `src/document_processor/render_prep.py`
- Test: `tests/test_pdf_preview_module_api.py`, `tests/test_pdf_preview.py`, `tests/test_pdf_enrichment.py`

- [ ] **Step 1: Copy the current module into a package entrypoint**

Create `src/document_processor/pdf/preview/__init__.py` with the current contents of `src/document_processor/pdf/preview.py` unchanged at first:

```python
"""PDF HTML preview implementation."""

# Copy the entire contents of the current preview.py here first.
# Do not split functions yet.
```

- [ ] **Step 2: Remove the old module file**

Delete:

```text
src/document_processor/pdf/preview.py
```

- [ ] **Step 3: Update imports that reference the preview module file path**

Make sure these imports still point at the package path:

```python
# src/document_processor/models.py
from .pdf.preview import render_pdf_preview_html

# src/document_processor/pdf/pipeline.py
from .preview import PdfPreviewContext, build_pdf_preview_context

# src/document_processor/render_prep.py
from .pdf.preview import prepare_pdf_for_html
```

- [ ] **Step 4: Run the package smoke tests and existing preview tests**

Run:

```bash
uv run python -m pytest tests/test_pdf_preview_module_api.py tests/test_pdf_preview.py tests/test_pdf_enrichment.py -q
```

Expected: package-import test passes, preview behavior remains green

- [ ] **Step 5: Commit the module-to-package conversion**

```bash
git add src/document_processor/pdf/preview/__init__.py src/document_processor/models.py src/document_processor/pdf/pipeline.py src/document_processor/render_prep.py tests/test_pdf_preview_module_api.py tests/test_pdf_preview.py tests/test_pdf_enrichment.py
git commit -m "refactor(pdf): convert preview module to package"
```

### Task 3: Extract preview models and constants

**Files:**
- Create: `src/document_processor/pdf/preview/models.py`
- Modify: `src/document_processor/pdf/preview/__init__.py`
- Test: `tests/test_pdf_preview_module_api.py`, `tests/test_pdf_preview.py`

- [ ] **Step 1: Create `models.py` with preview-only models and constants**

Move the unchanged definitions for these types from `preview/__init__.py` into `preview/models.py`:

- `PdfLayoutRegion`
- `PdfPreviewTableContext`
- `PdfPreviewVisualPrimitive`
- `PdfPreviewVisualBlockCandidate`
- `PdfPreviewContext`
- `_PreviewRenderNode`
- `_AssignedCandidate`
- `_AssignedCandidateGroup`
- `_LogicalPage`
- `_PreviewCompositionEntry`
- `_LogicalPageComposition`

Also move all preview-specific constants at the top of the file, including:

```python
_VISUAL_TOUCH_TOLERANCE_PT
_VISUAL_DIVIDER_SPAN_RATIO
_VISUAL_MIN_LINE_SEGMENT_PT
_LAYOUT_TABLE_GROUP_GAP_TOLERANCE_PT
_LOGICAL_PAGE_NUMBER_FOOTER_TOP_RATIO
_COLUMN_BAND_CENTER_OFFSET_RATIO
_IMAGE_STRIP_MIN_GROUP_SIZE
```

- [ ] **Step 2: Re-export only the names still needed by sibling modules**

Add explicit exports:

```python
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
```

- [ ] **Step 3: Update `preview/__init__.py` imports**

At the top of `preview/__init__.py`, replace inline definitions with imports:

```python
from .models import (
    PdfLayoutRegion,
    PdfPreviewContext,
    PdfPreviewTableContext,
    PdfPreviewVisualBlockCandidate,
    PdfPreviewVisualPrimitive,
    _AssignedCandidate,
    _AssignedCandidateGroup,
    _LogicalPage,
    _LogicalPageComposition,
    _PreviewCompositionEntry,
    _PreviewRenderNode,
)
```

- [ ] **Step 4: Run targeted tests**

Run:

```bash
uv run python -m pytest tests/test_pdf_preview_module_api.py tests/test_pdf_preview.py -q
```

Expected: model moves are behavior-neutral

- [ ] **Step 5: Commit the model extraction**

```bash
git add src/document_processor/pdf/preview/models.py src/document_processor/pdf/preview/__init__.py
git commit -m "refactor(pdf): extract preview models"
```

### Task 4: Extract context, primitive, and candidate builders

**Files:**
- Create: `src/document_processor/pdf/preview/context.py`
- Create: `src/document_processor/pdf/preview/primitives.py`
- Create: `src/document_processor/pdf/preview/candidates.py`
- Modify: `src/document_processor/pdf/preview/__init__.py`
- Modify: `tests/test_pdf_preview.py`
- Test: `tests/test_pdf_preview_module_api.py`, `tests/test_pdf_preview.py`, `tests/test_pdf_pipeline.py`

- [ ] **Step 1: Move raw context collection into `context.py`**

Move these functions unchanged first:

```python
build_pdf_preview_context
_layout_regions_from_raw
_collect_table_preview_context
_table_preview_context_from_node
_float_list
_line_art_boxes
_augment_layout_regions_with_pdfium
_detect_pdfium_split_regions
```

- [ ] **Step 2: Move PDFium primitive extraction into `primitives.py`**

Move these functions:

```python
_extract_pdfium_visual_primitives
_build_segmented_rule_primitives
_build_axis_box_edge_primitives
_segmented_rule_can_extend
_build_segmented_rule_primitive
_pdfium_object_type_name
_pdfium_color
_pdfium_stroke_width
_pdfium_has_fill
_pdfium_has_stroke
_pdfium_is_axis_aligned_box
_pdfium_path_points
_candidate_roles_for_visual_primitive
```

- [ ] **Step 3: Move candidate graphing into `candidates.py`**

Move these functions:

```python
_build_visual_block_candidates
_connected_line_components
_dedupe_line_primitives_for_graph
_line_primitives_are_graph_duplicates
_build_axis_box_candidates_from_component
_find_axis_box_seed_bboxes_from_component
_build_non_box_line_candidates
_component_has_box_outline
_dedupe_visual_block_candidates
_suppress_boundary_semantic_lines
_semantic_line_matches_structure_boundary
```

Update `tests/test_pdf_preview.py` so internal tests import from the new submodules:

```python
from document_processor.pdf.preview.candidates import _build_visual_block_candidates, _connected_line_components
from document_processor.pdf.preview.context import build_pdf_preview_context
from document_processor.pdf.preview.primitives import _extract_pdfium_visual_primitives
```

- [ ] **Step 4: Run the targeted tests**

Run:

```bash
uv run python -m pytest tests/test_pdf_preview_module_api.py tests/test_pdf_preview.py tests/test_pdf_pipeline.py -q
```

Expected: preview context and candidate extraction tests stay green

- [ ] **Step 5: Commit the extraction**

```bash
git add src/document_processor/pdf/preview/context.py src/document_processor/pdf/preview/primitives.py src/document_processor/pdf/preview/candidates.py src/document_processor/pdf/preview/__init__.py tests/test_pdf_preview.py tests/test_pdf_pipeline.py
git commit -m "refactor(pdf): extract preview context and candidate builders"
```

### Task 5: Extract layout normalization helpers

**Files:**
- Create: `src/document_processor/pdf/preview/layout.py`
- Modify: `src/document_processor/pdf/preview/__init__.py`
- Modify: `tests/test_pdf_preview.py`
- Test: `tests/test_pdf_preview.py`

- [ ] **Step 1: Move logical-page and band-detection helpers into `layout.py`**

Move these functions:

```python
_build_logical_pages_for_page
_region_split_x
_footer_page_number_candidates
_has_footer_page_number_pair
_spread_split_x
_score_logical_page_for_bbox
_best_logical_page_for_bbox
_column_band_split_x
_detect_intra_page_column_regions
_flow_regions_for_logical_page
```

- [ ] **Step 2: Move rebasing and image-strip grouping into `layout.py`**

Move these functions:

```python
_rebase_bbox
_rebase_meta_bbox
_rebase_table_for_logical_page
_rebase_paragraph_content_node
_rebase_paragraph_for_logical_page
_rebase_candidate_for_logical_page
_logical_page_page_info
_logical_page_paragraphs
_logical_page_preview_context
_is_image_only_paragraph
_image_strip_paragraphs_can_merge
_merged_image_strip_paragraph
_collapse_image_strip_paragraphs
```

- [ ] **Step 3: Point internal tests at the new module**

Change `tests/test_pdf_preview.py` imports:

```python
from document_processor.pdf.preview.layout import _build_logical_pages_for_page
```

Keep public behavior tests using `prepare_pdf_for_html(...)` unchanged.

- [ ] **Step 4: Run the layout-focused tests**

Run:

```bash
uv run python -m pytest tests/test_pdf_preview.py -q
```

Expected: logical-page, band, and image-strip tests remain green

- [ ] **Step 5: Commit the layout extraction**

```bash
git add src/document_processor/pdf/preview/layout.py src/document_processor/pdf/preview/__init__.py tests/test_pdf_preview.py
git commit -m "refactor(pdf): extract preview layout normalization"
```

### Task 6: Extract flow composition and layout-table promotion

**Files:**
- Create: `src/document_processor/pdf/preview/compose.py`
- Modify: `src/document_processor/pdf/preview/__init__.py`
- Modify: `tests/test_pdf_preview.py`
- Test: `tests/test_pdf_preview.py`, `tests/test_html_exporter.py`

- [ ] **Step 1: Move flow-entry and materialization helpers into `compose.py`**

Move these functions:

```python
_compose_logical_page
_normalize_pdf_doc_for_flow
_build_preview_entries
_preview_region_rank
_preview_entry_sort_key
_primary_region_bbox
_column_band_cell_style
_build_column_band_paragraph
_materialize_flow_paragraphs
```

- [ ] **Step 2: Move candidate assignment and grouping into `compose.py`**

Move these functions:

```python
_collect_page_render_nodes
_page_box_candidates
_page_long_rule_candidates
_candidate_matches_table_bbox
_assign_page_nodes_to_candidates
_filter_page_flow_paragraphs
_build_candidate_groups
_assigned_candidate_cell_paragraphs
_layout_table_cell_style
_build_layout_table_paragraph_for_group
_promote_assigned_candidates_to_layout_tables
```

- [ ] **Step 3: Keep only orchestration imports in `preview/__init__.py`**

At this point `preview/__init__.py` should import the core compose entrypoints instead of defining them inline:

```python
from .compose import _normalize_pdf_doc_for_flow
```

- [ ] **Step 4: Run the compose-oriented tests**

Run:

```bash
uv run python -m pytest tests/test_pdf_preview.py tests/test_html_exporter.py -q
```

Expected: layout-table promotion and multi-image paragraph rendering remain green

- [ ] **Step 5: Commit the composition extraction**

```bash
git add src/document_processor/pdf/preview/compose.py src/document_processor/pdf/preview/__init__.py tests/test_pdf_preview.py tests/test_html_exporter.py
git commit -m "refactor(pdf): extract preview flow composition"
```

### Task 7: Extract residual render and prepare orchestration, then remove dead paths

**Files:**
- Create: `src/document_processor/pdf/preview/render.py`
- Create: `src/document_processor/pdf/preview/prepare.py`
- Modify: `src/document_processor/pdf/preview/__init__.py`
- Modify: `tests/test_pdf_enrichment.py`
- Test: `tests/test_pdf_preview.py`, `tests/test_pdf_enrichment.py`

- [ ] **Step 1: Move residual render helpers into `render.py`**

Move these functions:

```python
_render_preview_body
_render_preview_page_content
_page_content_bbox
_render_page_positioned_candidates
_render_preview_entry
_render_positioned_candidate
_render_auxiliary_nodes
_render_candidate_child_cell_overlays
_page_content_margins
_render_long_rule_overlays
render_pdf_html
render_pdf_preview_html
render_pdf_preview_html_from_file
```

- [ ] **Step 2: Move `prepare_pdf_for_html(...)` into `prepare.py`**

Move these functions:

```python
prepare_pdf_for_html
_apply_preview_table_geometry
_match_preview_table_context
_bbox_distance
_apply_table_context
_span_extent
```

Delete the no-op hooks if they still do nothing and no test requires them:

```python
_prepare_pdf_caption_groups
_prepare_pdf_list_groups
```

- [ ] **Step 3: Update patch targets in enrichment tests**

Replace package-root patching with submodule patching where needed:

```python
with patch("document_processor.pdf.preview.prepare.enrich_pdf_table_borders") as enrich_borders, patch(
    "document_processor.pdf.preview.prepare.enrich_pdf_table_backgrounds"
) as enrich_backgrounds:
    prepare_pdf_for_html(doc)
```

- [ ] **Step 4: Run targeted tests**

Run:

```bash
uv run python -m pytest tests/test_pdf_preview.py tests/test_pdf_enrichment.py -q
```

Expected: `to_html()` reuse and residual render behavior stay green

- [ ] **Step 5: Commit the render/prepare extraction**

```bash
git add src/document_processor/pdf/preview/render.py src/document_processor/pdf/preview/prepare.py src/document_processor/pdf/preview/__init__.py tests/test_pdf_enrichment.py tests/test_pdf_preview.py
git commit -m "refactor(pdf): extract preview prepare and render stages"
```

### Task 8: Narrow the preview package surface and finish verification

**Files:**
- Modify: `src/document_processor/pdf/preview/__init__.py`
- Modify: `src/document_processor/pdf/pipeline.py`
- Modify: `src/document_processor/models.py`
- Modify: `src/document_processor/render_prep.py`
- Modify: `tests/test_pdf_preview_module_api.py`
- Test: `tests/test_pdf_preview.py`, `tests/test_pdf_pipeline.py`, `tests/test_pdf_enrichment.py`, `tests/test_html_exporter.py`

- [ ] **Step 1: Restrict `preview/__init__.py` exports**

Keep only the names still needed by package-internal callers and compatibility imports:

```python
from .context import build_pdf_preview_context
from .models import PdfPreviewContext
from .prepare import prepare_pdf_for_html
from .render import render_pdf_html, render_pdf_preview_html, render_pdf_preview_html_from_file

__all__ = [
    "PdfPreviewContext",
    "build_pdf_preview_context",
    "prepare_pdf_for_html",
    "render_pdf_html",
    "render_pdf_preview_html",
    "render_pdf_preview_html_from_file",
]
```

After this step, do not re-export internal helper names like `_build_logical_pages_for_page` from the package root.

- [ ] **Step 2: Update the module-shape test to enforce the narrower surface**

```python
def test_preview_package_root_only_exposes_compatibility_entrypoints(self) -> None:
    module = importlib.import_module("document_processor.pdf.preview")
    self.assertTrue(hasattr(module, "PdfPreviewContext"))
    self.assertTrue(hasattr(module, "build_pdf_preview_context"))
    self.assertTrue(hasattr(module, "prepare_pdf_for_html"))
    self.assertTrue(hasattr(module, "render_pdf_html"))
    self.assertFalse(hasattr(module, "_build_logical_pages_for_page"))
    self.assertFalse(hasattr(module, "_build_visual_block_candidates"))
```

- [ ] **Step 3: Run the full verification command**

Run:

```bash
uv run python -m pytest tests/test_pdf_preview_module_api.py tests/test_pdf_preview.py tests/test_pdf_pipeline.py tests/test_pdf_enrichment.py tests/test_html_exporter.py -q
```

Expected: all tests pass

- [ ] **Step 4: Regenerate representative sample outputs and inspect them**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/render_pdf_review_batch.py --output-dir out/pdf-review/text-pdf
```

Manually inspect:

```text
out/pdf-review/text-pdf/index.html
out/pdf-review/text-pdf/005-RAG_science-63518ccb/full.html
out/pdf-review-check/001-source-fd6b62c7/analysis-pdf-flow-shared.html
```

Check:

- `RAG_science` page 1 keeps the mid-page two-column band
- grouped image strips still render as one visual block
- `001` keeps spread split only when footer page numbers justify it

- [ ] **Step 5: Commit the API narrowing and final refactor**

```bash
git add src/document_processor/pdf/preview/__init__.py src/document_processor/pdf/pipeline.py src/document_processor/models.py src/document_processor/render_prep.py tests/test_pdf_preview_module_api.py tests/test_pdf_preview.py tests/test_pdf_pipeline.py tests/test_pdf_enrichment.py tests/test_html_exporter.py
git commit -m "refactor(pdf): split preview internals into focused modules"
```
