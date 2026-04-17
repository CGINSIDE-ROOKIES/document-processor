# PDF Codebase Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PDF 외부 동작은 유지하면서 preview/analyze/normalize/render/pipeline 책임을 재정렬하고 PDF 전용 코드 면적을 크게 줄인다.

**Architecture:** adapter는 canonical DocIR만 담당하고, preview는 `analyze`와 `normalize` 두 축으로 압축한다. render는 thin wrapper로 줄이고 pipeline은 explicit parse surface만 남긴다. 줄 수를 줄이기보다 중복 책임을 제거하는 방향으로 리팩토링한 뒤, dead/fallback path를 걷어낸다.

**Tech Stack:** Python, Pydantic models, pypdfium2, Java ODL runner, pytest

---

## File Map

### Keep and shrink

- `src/document_processor/pdf/odl/adapter.py`
  - canonical DocIR 생성만 유지
- `src/document_processor/pdf/pipeline.py`
  - 공개 parse surface 유지, 내부 preview fallback 정리
- `src/document_processor/pdf/preview/render.py`
  - residual-only thin render layer

### Merge into analyze subsystem

- `src/document_processor/pdf/preview/context.py`
- `src/document_processor/pdf/preview/primitives.py`
- `src/document_processor/pdf/preview/candidates.py`
- `src/document_processor/pdf/preview/shared.py`

### Merge into normalize subsystem

- `src/document_processor/pdf/preview/layout.py`
- `src/document_processor/pdf/preview/compose.py`
- `src/document_processor/pdf/preview/prepare.py`

### Surface updates

- `src/document_processor/pdf/preview/__init__.py`
- `src/document_processor/models.py`

### Verification

- `tests/test_pdf_preview.py`
- `tests/test_pdf_pipeline.py`
- `tests/test_pdf_enrichment.py`
- `tests/test_html_exporter.py`

---

### Task 1: Freeze current public behavior

**Files:**
- Modify: `tests/test_pdf_pipeline.py`
- Modify: `tests/test_pdf_preview.py`

- [ ] **Step 1: Add explicit surface assertions**

Add or extend tests to assert:
- `DocIR.from_file(..., doc_type="pdf").to_html(...)` works
- preview context remains attached on PDF parse result
- current review-path representative pages still produce expected structural markers

- [ ] **Step 2: Run targeted tests**

Run: `uv run python -m pytest tests/test_pdf_pipeline.py tests/test_pdf_preview.py -q`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_pdf_pipeline.py tests/test_pdf_preview.py
git commit -m "test: freeze pdf public behavior before reduction"
```

### Task 2: Merge preview analysis helpers

**Files:**
- Create: `src/document_processor/pdf/preview/analyze.py`
- Modify: `src/document_processor/pdf/preview/context.py`
- Modify: `src/document_processor/pdf/preview/__init__.py`
- Delete or reduce: `src/document_processor/pdf/preview/shared.py`
- Delete or reduce: `src/document_processor/pdf/preview/primitives.py`
- Delete or reduce: `src/document_processor/pdf/preview/candidates.py`

- [ ] **Step 1: Move primitive/candidate/shared helpers into one analysis unit**

Create `analyze.py` and move:
- primitive extraction
- line/candidate role logic
- bbox/overlap helper logic used only by analysis
- candidate dedupe/suppression logic

- [ ] **Step 2: Reduce context.py to preview-context assembly**

Leave `context.py` responsible only for:
- raw layout region collection
- raw table preview collection
- calling analysis helpers

- [ ] **Step 3: Remove duplicated re-exports/import layers**

Make `preview/__init__.py` expose only the minimal entrypoints still needed externally.

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_pdf_preview.py tests/test_pdf_pipeline.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/document_processor/pdf/preview/__init__.py src/document_processor/pdf/preview/context.py src/document_processor/pdf/preview/analyze.py src/document_processor/pdf/preview/shared.py src/document_processor/pdf/preview/primitives.py src/document_processor/pdf/preview/candidates.py tests/test_pdf_preview.py tests/test_pdf_pipeline.py
git commit -m "refactor(pdf): merge preview analysis helpers"
```

### Task 3: Merge preview normalize helpers

**Files:**
- Create: `src/document_processor/pdf/preview/normalize.py`
- Modify: `src/document_processor/pdf/preview/render.py`
- Delete or reduce: `src/document_processor/pdf/preview/layout.py`
- Delete or reduce: `src/document_processor/pdf/preview/compose.py`
- Delete or reduce: `src/document_processor/pdf/preview/prepare.py`

- [ ] **Step 1: Move logical-page and flow normalization into one module**

`normalize.py` should own:
- logical page split
- scaling/rebase
- image strip collapse
- flow/band materialization
- candidate assignment/group/promotion
- preview table geometry apply
- enrichment orchestration

- [ ] **Step 2: Collapse duplicate bbox/order/group helpers**

Keep one local helper set for:
- bbox ordering
- overlap scoring
- grouping proximity
- rebase/scaling

- [ ] **Step 3: Update render.py to consume normalized output only**

`render.py` should stop owning normalization decisions and just:
- call normalize
- dispatch to shared renderer
- render minimal residuals if still needed

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_pdf_preview.py tests/test_pdf_enrichment.py tests/test_html_exporter.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/document_processor/pdf/preview/normalize.py src/document_processor/pdf/preview/render.py src/document_processor/pdf/preview/layout.py src/document_processor/pdf/preview/compose.py src/document_processor/pdf/preview/prepare.py tests/test_pdf_preview.py tests/test_pdf_enrichment.py tests/test_html_exporter.py
git commit -m "refactor(pdf): merge preview normalization helpers"
```

### Task 4: Simplify pipeline surface

**Files:**
- Modify: `src/document_processor/pdf/pipeline.py`
- Modify: `src/document_processor/models.py`

- [ ] **Step 1: Make parse contract explicit**

Remove misleading comments and ensure one canonical story:
- parse returns PDF DocIR
- preview context attach is explicit and internal behavior is consistent

- [ ] **Step 2: Remove or internalize preview-only fallback rebuild path**

If `DocIR.to_html()` currently rebuilds preview context from `source_path`, either:
- remove it, or
- move it behind a clearly internal-only helper with no public surface confusion

- [ ] **Step 3: Reduce wrapper layers**

Keep only the minimal call chain needed for:
- parse
- to_html

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_pdf_pipeline.py tests/test_pdf_preview.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/document_processor/pdf/pipeline.py src/document_processor/models.py tests/test_pdf_pipeline.py tests/test_pdf_preview.py
git commit -m "refactor(pdf): simplify pipeline surface"
```

### Task 5: Trim render-specific stale paths

**Files:**
- Modify: `src/document_processor/pdf/preview/render.py`
- Modify: `src/document_processor/html_exporter.py`

- [ ] **Step 1: Remove no-longer-used residual render branches**

Delete any render helper path that normalize no longer feeds, especially logic preserved only for old candidate/long-rule cases.

- [ ] **Step 2: Keep shared renderer as the default end-state**

Ensure the common path always ends in shared HTML rendering and preview-specific markup is only used for truly residual cases.

- [ ] **Step 3: Run tests**

Run: `uv run python -m pytest tests/test_pdf_preview.py tests/test_html_exporter.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/document_processor/pdf/preview/render.py src/document_processor/html_exporter.py tests/test_pdf_preview.py tests/test_html_exporter.py
git commit -m "refactor(pdf): trim residual render paths"
```

### Task 6: Batch review verification

**Files:**
- Verify only: `scripts/render_pdf_review_batch.py`

- [ ] **Step 1: Regenerate review batch**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/render_pdf_review_batch.py --input-dir "/Users/yoonseo/Developer/External/RAGBuilder-test/Dataset/Text pdf" --output-dir out/pdf-review/text-pdf
```

Expected:
- `17` documents processed
- `195` pages total in index
- no runtime errors

- [ ] **Step 2: Manually inspect representative docs**

Inspect:
- `out/pdf-review/text-pdf/001-2026-_-_-_-52eedcbb/full.html`
- `out/pdf-review/text-pdf/005-RAG_science-63518ccb/full.html`
- `out/pdf-review/text-pdf/013-table-in-table-54122f76/full.html`

Expected:
- no major regression in structural layout
- known pages still render equivalently

- [ ] **Step 3: Commit**

```bash
git add out/pdf-review/text-pdf/index.html
git commit -m "test: verify pdf batch review after reduction"
```

## Self-Review

Spec coverage:
- adapter stays canonical: covered by Task 3 boundaries and no adapter expansion
- analyze merge: Task 2
- normalize merge: Task 3
- pipeline simplification: Task 4
- render thin path: Task 5
- external behavior verification: Tasks 1 and 6

Placeholder scan:
- no TODO/TBD placeholders
- every task has files, concrete goal, command, expected result

Type consistency:
- `analyze.py` and `normalize.py` are named consistently throughout
- public parse surface remains `parse_pdf_to_doc_ir`

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-17-pdf-codebase-reduction-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**

