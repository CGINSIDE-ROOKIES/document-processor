# PDF Codebase Reduction Design

## Goal

PDF 파싱과 HTML 렌더 결과는 유지하면서, PDF 전용 코드 면적을 크게 줄인다. 이번 작업의 성공 기준은 외부 계약과 리뷰 배치 결과를 유지하는 것이다.

유지 대상:
- `DocIR.from_file(..., doc_type="pdf")`
- `DocIR.to_html(...)`
- `scripts/render_pdf_review_batch.py`가 만드는 review/full HTML 결과의 시각적/구조적 동등성

비유지 대상:
- 내부 helper 함수명
- 내부 모듈 구조
- preview 내부 데이터 흐름
- paragraph/unit_id 같은 중간 표현의 세부 구성

## Current Problems

현재 PDF 구현은 기능이 없는 dead code가 많다기보다, 같은 책임이 여러 레이어에 중복되어 있다.

핵심 문제:
- `preview/shared.py`, `preview/primitives.py`, `preview/candidates.py`, `preview/context.py` 사이에 bbox/line/candidate 관련 책임이 분산되어 있다.
- `preview/layout.py`, `preview/compose.py`, `preview/prepare.py`가 모두 “logical page normalize”의 일부를 담당하면서 경계가 흐리다.
- `pipeline.py`에 canonical parse와 preview sidecar attach, preview-only rebuild 경로가 같이 있어 계약이 불명확하다.
- `render.py`는 실제로는 대부분 shared renderer로 끝나는데도 preview residual path를 별도 계층으로 오래 끌고 간다.
- `adapter.py`가 커진 이유 중 일부는 parse 단계에서 해야 할 canonical normalize와 preview 보조 normalize가 섞여 있기 때문이다.

즉 현재 목표는 “코드를 삭제한다”보다 “중복 책임을 하나로 모으고, 남은 공개 표면을 줄인다”에 가깝다.

## Design Principles

1. Canonical parse와 preview normalize를 명확히 분리한다.
2. 분석 단계와 재배치 단계의 책임을 섞지 않는다.
3. 같은 bbox/ordering/grouping 유틸은 한 군데에만 둔다.
4. render는 마지막 표현 계층으로만 유지하고, normalize 책임을 다시 끌어오지 않는다.
5. 새로운 기능을 추가하지 않는다. 기존 동작을 유지하는 범위에서만 구조를 줄인다.
6. residual preview 경로는 실제 필요 범위만 남기고, 거의 쓰이지 않는 특수 처리들은 제거 대상에 둔다.

## Proposed Architecture

### 1. Adapter stays canonical

`src/document_processor/pdf/odl/adapter.py`

책임:
- raw ODL -> canonical `DocIR`
- PDF 특화 style/meta/bbox 채우기
- canonical parse 단계에서만 정당화되는 구조 normalize
  - 예: 현재 list child strip collapse 같은 로직

비책임:
- preview candidate 분석
- logical page split
- residual overlay 판단

즉 adapter는 “PDF 원문에서 바로 설명 가능한 canonical 구조”만 만든다.

### 2. Preview analyze is one subsystem

새 구조:
- `preview/analyze.py` 또는 `preview/analyze/`

통합 대상:
- `shared.py`
- `primitives.py`
- `candidates.py`
- `context.py` 중 primitive/candidate/context 수집 관련 부분

책임:
- raw preview context 수집
- pdfium primitive 추출
- primitive role 판정
- candidate 생성/정리
- preview context 최종 assemble

중요:
- bbox overlap, line orientation, interval merge, boundary proximity 같은 helper는 여기서만 소유한다.
- 다른 preview 단계는 이 helper를 직접 구현하지 않는다.

### 3. Preview normalize is one subsystem

새 구조:
- `preview/normalize.py` 또는 `preview/normalize/`

통합 대상:
- `layout.py`
- `compose.py`
- `prepare.py`

책임:
- logical page split
- scale-up/rebase
- flow paragraph normalize
- image strip collapse
- region/band 배치
- candidate assignment/group/promotion
- preview table geometry 적용
- enrichment orchestration

중요:
- “prepare”, “layout”, “compose”를 따로 부르는 대신, 하나의 normalize 파이프라인으로 읽히게 만든다.
- ordering/bbox/group 유틸은 여기서만 소유한다.

### 4. Render becomes thin

`preview/render.py`

책임:
- normalize된 `DocIR`를 shared renderer로 내보낸다.
- residual candidate가 정말 남는 경우만 최소한의 positioned render를 한다.

축소 원칙:
- long rule path는 유지하지 않는다.
- normalize 단계에서 흡수 가능한 것은 render에 남기지 않는다.
- page shell/body assembly는 최대한 shared HTML renderer를 재사용한다.

### 5. Pipeline surface becomes explicit

`src/document_processor/pdf/pipeline.py`

공개 표면:
- `parse_pdf_to_doc_ir(...)`

내부 표면:
- canonical parse
- optional preview analyze attach

정리 원칙:
- preview-only rebuild/fallback 경로는 없애거나 내부 전용으로 강하게 숨긴다.
- parse 결과가 preview context를 attach하는 계약이면 그것을 명시한다.
- 주석과 실제 동작이 어긋나는 상태를 정리한다.

## Reduction Strategy

이번 축소는 세 단계로 나눈다.

### Phase 1: Structural merge without behavior change

목표:
- 파일 간 책임을 재배치하되, 함수 본문은 최대한 유지

작업:
- analyze 계층 통합
- normalize 계층 통합
- render thin wrapper화
- pipeline surface 정리

예상 효과:
- 중복 import/wrapper/helper 제거
- 파일 간 왕복 감소

### Phase 2: Utility deduplication

목표:
- bbox/order/overlap/group helper를 한 군데로 수렴

작업:
- preview analyze 내부 중복 helper 제거
- preview normalize 내부 중복 helper 제거
- shared-like helper를 각 축 내부의 local helper로 흡수

예상 효과:
- 가장 체감되는 line 수 감소
- 읽기 난이도 감소

### Phase 3: Remove stale/fallback paths

목표:
- 지금 구조를 복잡하게 만드는 fallback/compat path 제거

작업:
- preview rebuild fallback 제거 또는 강한 internal화
- 사실상 안 쓰는 render 특수 처리 제거
- thin re-export surface만 남기고 나머지 internal화

## What Will Not Change

- PDF spread split/scale-up의 현재 동작 목표
- image strip collapse
- empty candidate suppression
- long rule overlay 제거 상태
- bbox top-left ordering 기준
- adapter 단계 strip collapse 패턴 자체

즉 지금까지 맞춰놓은 사용자-visible 동작은 유지하고, 구조만 압축한다.

## Testing Strategy

정답 기준은 내부 함수 단위가 아니라 외부 결과다.

필수 검증:
- `tests/test_pdf_preview.py`
- `tests/test_pdf_pipeline.py`
- `tests/test_pdf_enrichment.py`
- `tests/test_html_exporter.py`

추가 검증:
- `scripts/render_pdf_review_batch.py`로 17개/195페이지 배치 재생성
- 대표 문서 수동 점검:
  - `001-2026-...`
  - `005-RAG_science`
  - `013-table-in-table`

비목표:
- HTML 바이트 단위 identical
- 중간 unit_id identical

검증 기준:
- 주요 페이지에서 구조와 시각 결과가 동등
- known regression이 없을 것

## Expected Outcome

현실적인 1차 목표는 PDF 코드 총량 **25~35% 축소**다.

50% 축소는 이번 단계의 hard target으로 두지 않는다. 그 수준은 기능 제거 없이 달성하기 어렵다. 대신 이번 설계는:
- 가장 큰 중복 레이어를 통합하고
- 내부 경계를 다시 세우고
- 다음 축소 단계에서 더 줄일 수 있는 기반을 만드는 데 초점을 둔다.

즉 이번 작업의 성공은:
- 외부 동작 유지
- preview 계층의 책임 재정렬
- PDF 코드 면적의 유의미한 축소
- 이후 유지보수 비용 감소

