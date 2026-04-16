# PDF Preview Module Refactor Design

## Goal

PDF HTML 경로를 기능적으로는 유지하면서, 현재 `src/document_processor/pdf/preview.py`에 몰린 책임을 작은 내부 모듈로 분해한다. 사용자는 다른 확장자와 마찬가지로 `DocIR.from_file(...)`와 `DocIR.to_html(...)`만 보면 되게 만들고, PDF 전용 `preview` 보조 함수와 모델은 내부 구현으로 숨긴다.

## Context

현재 상태는 다음 특징을 가진다.

- PDF만 raw ODL에서 `canonical DocIR`와 `preview sidecar`를 동시에 만든다.
- `preview.py`가 context 수집, pdfium primitive 추출, candidate graph 생성, logical page 정규화, band detection, candidate-to-layout-table 승격, residual overlay 렌더를 한 파일에서 모두 담당한다.
- 최근 변경으로 PDF도 `preview_context`를 먼저 사용해 `DocIR.pages`와 `DocIR.paragraphs`를 flow-friendly하게 보정한 뒤 shared renderer로 최대한 내려가게 바뀌었지만, 구현 경계는 여전히 한 파일에 응집돼 있다.

이 구조는 기능 추가에는 유리했지만, 다음 문제가 생겼다.

- 파일이 너무 커서 새로 들어온 사람이 맥락을 잡기 어렵다.
- “page/region 계산”, “flow 재조립”, “candidate 승격”, “최종 residual render” 경계가 섞여 있다.
- helper가 public API처럼 노출돼 테스트와 타 모듈이 preview 내부 구조에 과도하게 의존한다.
- dead path와 no-op stage의 존재 여부를 파악하기 어렵다.

## Refactor Constraints

- 동작 보존이 최우선이다.
- `scripts/`와 실험용 `docs/superpowers/` 산출물은 이번 리팩토링 범위에서 제외한다.
- 기존 PDF 동작을 설명하는 integration test는 유지한다.
- 테스트는 기대값을 억지로 구현에 맞춰 비트는 용도가 아니라, 공개 동작 보호용으로 쓴다.
- 공개 표면은 가능한 한 다른 확장자 수준으로 단순화한다.

## Public Surface

리팩토링 후 외부에서 의미 있게 유지할 표면은 다음으로 제한한다.

- `DocIR.from_file(...)`
- `DocIR.to_html(...)`
- 선택적 PDF 엔트리포인트: `parse_pdf_to_doc_ir(...)`

다음은 내부 구현으로 낮춘다.

- `build_pdf_preview_context(...)`
- `prepare_pdf_for_html(...)`
- `render_pdf_preview_html(...)`
- `render_pdf_preview_html_from_file(...)`
- `PdfPreviewContext` 및 관련 preview 전용 모델

호환성을 위해 `document_processor.pdf.preview` import 경로는 한동안 남길 수 있지만, 이 모듈은 더 이상 public contract가 아니라 internal façade로 본다.

## Chosen Approach

`src/document_processor/pdf/preview.py`를 유지 보수용 façade로 축소하고, 실제 책임을 `src/document_processor/pdf/preview/` 패키지 아래의 내부 모듈로 나눈다.

이 접근을 고른 이유는 다음과 같다.

- 현재 코드가 이미 `context -> primitives -> candidates -> layout -> compose -> render` 흐름으로 자연스럽게 뭉쳐 있다.
- 파일 분리만으로도 결합도를 낮출 수 있고, 큰 동작 변경 없이 읽기 난이도를 줄일 수 있다.
- 더 공격적인 “prepare/render 완전 분리”보다 리스크가 낮고, 단순 섹션 정리보다 효과가 크다.

## Target Package Structure

### `src/document_processor/pdf/preview/__init__.py`

- 내부 구현 진입점만 재-export한다.
- 외부 호환성이 필요하다면 최소 alias만 둔다.
- 장기적으로는 `document_processor.pdf.preview` 자체를 internal package로 간주한다.

### `src/document_processor/pdf/preview/models.py`

- preview 전용 모델과 내부 composition model을 둔다.
- 예:
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
- preview 상수도 이 모듈로 이동한다.

### `src/document_processor/pdf/preview/context.py`

- raw ODL에서 preview sidecar를 만든다.
- layout region/table context를 수집한다.
- PDFium 보강 진입점을 제공한다.
- 책임:
  - `build_pdf_preview_context`
  - `_layout_regions_from_raw`
  - `_collect_table_preview_context`
  - `_augment_layout_regions_with_pdfium`

### `src/document_processor/pdf/preview/primitives.py`

- PDFium object를 primitive로 변환한다.
- segmented rule, axis-box edge primitive 같은 전처리를 담당한다.
- primitive role 판정과 low-level PDFium 유틸을 모은다.

### `src/document_processor/pdf/preview/candidates.py`

- line primitive를 graph/component로 묶는다.
- `axis_box`, `open_frame`, `semantic_line`, `long_rule`로 분류한다.
- dedupe와 boundary suppression을 담당한다.

### `src/document_processor/pdf/preview/layout.py`

- logical page split을 담당한다.
- split 허용 규칙은 “footer에 페이지 번호쌍이 있는 경우만” 유지한다.
- single logical page 내부에서는 band-level `left/right` detection을 담당한다.
- bbox rebasing과 image strip grouping도 이 레이어 책임으로 둔다.

### `src/document_processor/pdf/preview/compose.py`

- logical page 단위 flow 재조립을 담당한다.
- paragraph region type 계산, preview entry 정렬, column band materialization을 둔다.
- candidate assignment, candidate grouping, layout-table promotion도 이 레이어에서 담당한다.
- `_normalize_pdf_doc_for_flow(...)`는 이 모듈의 orchestration 함수가 된다.

### `src/document_processor/pdf/preview/render.py`

- residual preview body 렌더만 담당한다.
- positioned candidate render, long-rule overlay, preview body 조립만 둔다.
- shared renderer로 내려갈 수 있는 경우는 이 레이어가 아니라 prepare 쪽에서 결정한다.

### `src/document_processor/pdf/preview/prepare.py`

- `prepare_pdf_for_html(...)`의 orchestration만 둔다.
- 실제 작업은 table geometry 적용, enrichment 호출, normalize 호출을 순서대로 위임한다.
- no-op placeholder가 계속 필요하지 않다면 이 레이어에서 제거한다.

## Boundary Rules

리팩토링 중 지켜야 할 경계는 다음과 같다.

### 1. `layout`과 `compose`를 분리한다

- `layout`은 “페이지를 어떻게 나누고, bbox를 어떤 지역에 귀속시킬지”까지다.
- `compose`는 “그 지역 정보로 flow와 candidate를 어떻게 조립할지”부터다.

이 경계가 무너지면 지금과 같은 파일 비대화가 다시 생긴다.

### 2. shared HTML renderer는 PDF primitive/candidate를 몰라야 한다

- shared renderer는 `DocIR`만 받아야 한다.
- PDF 특수 구조는 prepare 단계에서 최대한 `ParagraphIR`, `TableIR`, `ImageIR`, `PageInfo`로 환원한다.

### 3. residual render는 residual artifact만 담당한다

- normal case는 shared renderer 경로로 내려보낸다.
- overlay 전용 artifact가 남은 경우에만 preview render를 쓴다.
- residual render가 다시 main layout engine처럼 커지는 것을 막는다.

## Deletion Policy

다음은 제거 대상이다.

- 새 패키지로 옮긴 뒤 `preview.py`에 남는 중복 wrapper/helper
- 더 이상 호출되지 않는 private helper
- 현재와 미래 계획 모두 없는 no-op stage
- public contract가 아닌데 테스트가 직접 patch/import하던 internal 이름

다음은 유지 대상이다.

- PDF 전용 logical page split heuristic
- band-level column detection
- image strip grouping
- candidate extraction, grouping, promotion
- long-rule residual overlay

## Migration Strategy

리팩토링은 한 번에 다 옮기지 않고 아래 순서로 간다.

1. façade 보호 테스트를 추가한다.
   - `DocIR.from_file(...).to_html()` 기준 동작
   - PDF prepare/render public path 기준 동작
2. `models.py`를 먼저 만든다.
   - 데이터 구조와 상수를 이동한다.
3. `primitives.py`와 `candidates.py`를 분리한다.
   - pdfium/candidate 쪽은 비교적 독립적이라 먼저 분리하기 쉽다.
4. `layout.py`를 분리한다.
   - logical page, band detection, rebasing, image strip grouping 이동
5. `compose.py`를 분리한다.
   - flow 재조립, candidate assignment, layout-table promotion 이동
6. `render.py`와 `prepare.py`를 분리한다.
7. 마지막에 `preview.py`를 얇은 façade로 줄이거나 package entrypoint로 대체한다.
8. 사용하지 않는 wrapper/no-op stage를 삭제한다.

이 순서를 지키면 큰 integration behavior를 깨지 않고 단계별로 diff를 읽을 수 있다.

## Test Strategy

### Keep

- 기존 `tests/test_pdf_preview.py`의 integration 성격 테스트
- `tests/test_pdf_pipeline.py`의 preview context attach/path 검증
- `tests/test_pdf_enrichment.py`의 `to_html()`/preview context reuse 검증
- `tests/test_html_exporter.py`의 multi-image paragraph render 검증

### Add

- façade export 보호 테스트
  - public path에서 필요한 함수만 노출되는지
- internal module smoke test
  - `layout`과 `compose`가 독립적으로 import 가능한지
- no-op stage 제거 시 관련 dead patch 제거 테스트

### Do Not Do

- 리팩토링 편의를 위해 기존 기대값을 광범위하게 다시 쓰지 않는다.
- internal helper 이름 변경을 이유로 의미 없는 snapshot churn을 만들지 않는다.

## Non-Goals

- PDF parsing algorithm 자체를 교체하지 않는다.
- ODL adapter를 완전히 다시 설계하지 않는다.
- shared renderer를 PDF 전용으로 오염시키지 않는다.
- `scripts/`의 리뷰 도구를 이번 리팩토링에 포함하지 않는다.
- VLM/compare 실험 코드를 이번 커밋에 섞지 않는다.

## Success Criteria

- `preview.py` 단일 대형 파일이 내부 패키지로 분해된다.
- PDF 관련 책임이 이름만 봐도 이해되는 모듈 경계로 나뉜다.
- 사용자는 `DocIR.from_file(...).to_html()`만으로 PDF를 다룰 수 있다.
- 기존 PDF HTML 결과와 테스트가 유지된다.
- preview 전용 helper/model이 더 이상 사실상의 public API처럼 다뤄지지 않는다.
