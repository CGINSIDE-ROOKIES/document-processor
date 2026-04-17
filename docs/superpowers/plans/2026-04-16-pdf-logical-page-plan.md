# PDF Logical Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PDF preview를 physical page 기반 preview body에서 logical page 기반 flow renderer로 전환한다.

**Architecture:** 기존 region/candidate extraction은 유지하되, preview render 직전에 logical page descriptors를 만든다. paragraph/table/image/candidate assignment와 candidate-to-table promotion은 logical page 경계 안에서만 수행하고, final HTML은 shared paged renderer와 가까운 flow 구조로 조립한다.

**Tech Stack:** Python, Pydantic models, existing `document_processor.pdf.preview` renderer/tests

---

### Task 1: Add logical page tests

**Files:**
- Modify: `tests/test_pdf_preview.py`

- [ ] Add failing tests for single logical page fallback.
- [ ] Run the targeted tests and verify they fail for the expected missing logical-page behavior.
- [ ] Add failing tests for spread physical page splitting into left/right logical pages.
- [ ] Add failing tests for logical-page-scoped candidate promotion and flow rendering.

### Task 2: Introduce logical page model and builders

**Files:**
- Modify: `src/document_processor/pdf/preview.py`

- [ ] Add a logical page data structure in `preview.py`.
- [ ] Build logical pages from ODL regions, PDFium inferred split regions, and single-page fallback.
- [ ] Add helpers that classify nodes/candidates into logical pages using `layout_region_id`, `region_type`, and bbox fallback.

### Task 3: Rework preview render around logical pages

**Files:**
- Modify: `src/document_processor/pdf/preview.py`

- [ ] Replace physical-page-only preview body assembly with logical-page-aware assembly.
- [ ] Scope candidate assignment and promotion to each logical page.
- [ ] Remove now-redundant physical-page band/group rendering code that logical pages supersede.
- [ ] Drop fixed-height/overflow clipping rules from PDF preview wrappers where logical-page flow makes them unnecessary.

### Task 4: Verify behavior on sample files

**Files:**
- Modify: `src/document_processor/pdf/preview.py`
- Modify: `tests/test_pdf_preview.py`

- [ ] Run `tests/test_pdf_preview.py` and the relevant renderer tests.
- [ ] Regenerate representative sample outputs (`001-source-fd6b62c7`, `005-RAG_science-63518ccb`).
- [ ] Check the regenerated HTML for logical page count, candidate promotion, and clipping changes.
