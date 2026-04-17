# PDF Table Split Enrichment Design

## Goal

ODL이 이미 `TableIR`로 인식한 PDF 표에 한해, 점선 내부 경계를 근거로 `TableIR.cells`를 실제로 다시 분할해 정보 추출용 구조를 보강한다. 목표는 빈 셀까지 완전 복원하는 것이 아니라, 텍스트가 있는 셀의 row/column 구조를 더 정확하게 복원하는 것이다.

## Sample Input

초기 설계와 검증은 다음 샘플 PDF를 기준으로 한다.

- `/Users/yoonseo/Developer/External/document-processor/out/pdf_docir_demo/03-_-_AX_-_LLM_-_-_/모두의_챌린지_AX_-_LLM_분야_참여기업_모집공고.pdf`

이 문서에는 ODL이 테이블 자체는 잡지만 점선 내부 경계를 충분히 셀 분할로 반영하지 못하는 사례가 존재한다고 가정한다.

## Current Problem

현재 canonical PDF 파이프라인은 ODL raw의 `rows[]` / `cells[]`를 그대로 `TableIR`로 옮긴다. 이 경로는 ODL이 cell topology를 잘 잡으면 충분히 좋지만, 점선 경계처럼 분절된 내부 divider에는 취약하다.

이미 코드베이스에는 두 종류의 보강 로직이 있다.

- `enrich_pdf_table_borders(...)`
  - 셀이 이미 존재한다는 전제에서 셀 외곽 border 스타일을 래스터 기반으로 보정한다.
  - `TableIR.cells`의 topology 자체는 바꾸지 않는다.
- preview visual analysis
  - `pypdfium2` path primitive를 보고 짧은 선 fragment를 `segmented_horizontal_rule` / `segmented_vertical_rule`로 합성한다.
  - 이후 preview candidate(`axis_box`, `open_frame`, `semantic_line`) 구성에 사용된다.

문제는 점선 내부 경계에 대한 가장 유용한 신호가 preview 쪽 primitive analysis에 있는데, canonical `DocIR` 보강에는 연결되어 있지 않다는 점이다.

## Scope

이번 작업 범위는 다음으로 제한한다.

- ODL이 이미 만든 `TableIR`만 보강한다.
- `pypdfium2` 기반 primitive analysis를 재사용한다.
- 점선 내부 경계를 근거로 `TableIR.cells`를 실제로 다시 분할한다.
- 분할 결과는 canonical `DocIR`에 반영한다.
- 빈 셀은 생성하지 않는다.
- 텍스트가 있는 칸만 보수적으로 materialize한다.

## Non-Goals

- ODL이 table로 인식하지 못한 영역에서 새 `TableIR`를 생성하지 않는다.
- preview candidate(`axis_box`, `open_frame`, `semantic_line`) 전체를 canonical 경로로 끌어오지 않는다.
- 빈 셀 복원이나 완전한 시각적 grid 재현을 목표로 하지 않는다.
- 1차 구현에서 `rowspan` / `colspan` 완전 복원을 목표로 하지 않는다.
- HTML preview 전용 layout-table 승격 경로를 canonical path와 통합하지 않는다.

## Chosen Approach

선택한 방향은 `primitive-first canonical split enrichment`다.

핵심 아이디어는 다음과 같다.

1. preview 경로에서 이미 사용하는 `pypdfium2` visual primitive 추출을 재사용한다.
2. 그 중 `segmented_horizontal_rule` / `segmented_vertical_rule` 생성까지는 canonical enrichment에서도 공유한다.
3. preview candidate 생성은 사용하지 않는다.
4. 각 기존 `TableIR.bbox` 내부에 들어오는 segmented rule만 추려서 내부 split axis 후보를 만든다.
5. 그 축을 기준으로 기존 셀을 실제로 다시 쪼개되, 텍스트가 있는 칸만 새 `TableCellIR`로 materialize한다.

이 접근을 고른 이유는 다음과 같다.

- 점선 대응의 핵심 로직이 이미 primitive layer에 존재한다.
- preview candidate는 바깥 박스/프레임 추론 성격이 강해 canonical path에 그대로 들이기엔 과하다.
- `enrich_pdf_table_borders(...)`만 확장하는 방식보다, 실제 cell topology 변경이라는 목표에 더 직접적이다.
- 범위를 `기존 TableIR 내부`로 제한하면 false positive 리스크를 통제하기 쉽다.

## Architecture

### New Enrichment Step

새 parse-time 보강 단계 `enrich_pdf_table_splits(...)`를 추가한다.

- 입력: `DocIR`, `pdf_path`
- 출력: 같은 `DocIR` 인스턴스를 수정해 반환
- 역할: 기존 `TableIR` 내부 cell 재분할

### Reused Analysis Layer

`src/document_processor/pdf/preview/analyze.py`의 전부를 공유하지는 않는다. 다음 레이어만 canonical path에 재사용 가능하게 만든다.

- PDFium path object -> `PdfPreviewVisualPrimitive`
- fragmented short line -> `segmented_horizontal_rule` / `segmented_vertical_rule`
- primitive orientation / line span / axis center 계산 helper

preview 전용으로 남기는 것은 다음이다.

- `axis_box`, `open_frame`, `semantic_line` candidate 생성
- candidate dedupe / suppression
- candidate assignment
- layout-table promotion

### Proposed Module Boundaries

- `src/document_processor/pdf/enhancement/table_split_inference.py`
  - canonical table split enrichment orchestration
  - table bbox 내부 split axis 계산
  - cell repartition 적용
- `src/document_processor/pdf/preview/analyze.py`
  - low-level primitive / segmented-rule helper를 export 가능 구조로 정리
  - preview candidate 구성은 그대로 유지
- `src/document_processor/pdf/config.py`
  - opt-in parse config 추가
- `src/document_processor/pdf/pipeline.py`
  - parse-time enrichment 연결

## Data Flow

canonical PDF parse 흐름은 아래 순서를 따른다.

1. `ODL raw -> TableIR`
2. `enrich_pdf_table_splits(...)`
3. 선택적으로 `enrich_pdf_table_borders(...)`
4. 문서 메타 정리 후 반환

이 순서를 선택한 이유는 split을 먼저 해야 이후 border/background 보강도 새로 쪼개진 셀 단위로 적용할 수 있기 때문이다.

`enrich_pdf_table_splits(...)` 내부 흐름은 다음과 같다.

1. `DocIR` 안의 기존 `TableIR`만 순회한다.
2. 필요한 페이지만 대상으로 `pypdfium2` primitive를 한 번 추출한다.
3. primitive에서 segmented rule을 합성한다.
4. 각 `TableIR.bbox` 내부에 들어오는 horizontal / vertical rule만 추린다.
5. 외곽 테두리와 겹치는 rule은 제거하고 내부 divider 후보만 남긴다.
6. 가까운 rule을 clustering해 `x split axes`, `y split axes`를 만든다.
7. 기존 셀/문단/런 bbox를 이용해 실제로 양쪽에 텍스트가 존재하는 축만 유지한다.
8. 최종 축 집합으로 새 셀 partition을 만든다.
9. 텍스트가 들어가는 partition만 `TableCellIR`로 materialize한다.
10. `row_index`, `col_index`, `cell.text`, `table.row_count`, `table.col_count`를 갱신한다.

## Split Rules

### Rule Filtering

- table bbox와 충분히 겹치는 rule만 사용한다.
- table 외곽 경계와 거의 같은 위치의 rule은 outer border로 보고 split axis 후보에서 제외한다.
- rule이 너무 짧거나 bbox 내부에서 span이 부족하면 제외한다.

### Conservative Split Policy

- 선이 보인다는 이유만으로 분할하지 않는다.
- 해당 축 양쪽에 실제 텍스트 bbox 중심점이 나뉘어 존재할 때만 분할한다.
- 증거가 약하면 원래 셀을 유지한다.
- 기본 동작은 `no-op`이며, 명확한 split 근거가 있을 때만 topology를 변경한다.

### Materialization Policy

- 빈 partition은 materialize하지 않는다.
- 각 새 셀은 원래 셀의 하위 문단/런 중 bbox가 속한 쪽으로 배정해 구성한다.
- `cell.text`는 새 `paragraphs`로부터 다시 계산한다.
- 중첩 table이 있는 경우는 1차 구현에서 원래 구조를 우선 보존하고, ambiguous split이면 no-op로 둔다.

## Configuration

새 parse config를 추가한다.

- `PdfParseConfig.infer_table_splits: bool = False`
- `PdfParseConfig.table_split_strategy: Literal["segmented-rules"] = "segmented-rules"`는 1차 구현에서는 선택 사항이다.

기본값은 `False`로 둔다. 이유는 다음과 같다.

- `pypdfium2` 분석은 추가 비용이 있다.
- canonical topology 변경은 사용자 영향이 크다.
- 샘플 PDF와 회귀 테스트가 충분히 쌓이기 전에는 opt-in이 안전하다.

공개 함수도 별도로 둔다.

- `enrich_pdf_table_splits(doc_ir, pdf_path=None) -> DocIR`

이 함수는 parse-time opt-in 외에도 수동 실험과 debugging에 유용하다.

## Error Handling

- `pypdfium2` import 실패 시 no-op로 반환한다.
- `pdf_path`가 없거나 파일이 존재하지 않으면 no-op로 반환한다.
- table bbox 또는 page number가 없으면 해당 table은 건너뛴다.
- split 결과가 비정상적이면 원래 `table.cells`를 유지한다.

canonical enrichment는 실패 시 문서를 망가뜨리면 안 된다. 따라서 이 기능은 “best effort, safe fallback”을 기본 정책으로 둔다.

## Testing Strategy

### Unit Tests

- segmented vertical dotted divider가 있는 테이블에서 1셀이 2셀로 분할되는 케이스
- segmented horizontal dotted divider가 있는 테이블에서 1셀이 2행으로 분할되는 케이스
- divider는 있지만 텍스트가 한쪽에만 있어 no-op인 케이스
- table 외곽 점선을 내부 split으로 잘못 해석하지 않는 케이스
- 분할 후 `cell.text`, `row_index`, `col_index`, `row_count`, `col_count`가 기대값과 일치하는 케이스

### Integration Tests

- 샘플 PDF 기반 regression test
- `infer_table_splits=True`일 때 canonical `DocIR`에서 기대 cell count가 증가하는지 검증
- 이후 `enrich_pdf_table_borders(...)`를 적용해도 새 셀 구조가 유지되는지 검증

## Risks

### False Split

점선, 하이라이트 라인, 장식용 path를 내부 divider로 잘못 해석할 수 있다.

대응:

- table bbox 내부 rule만 사용
- outer border 제외
- 양쪽 텍스트 존재 조건 추가
- 불확실하면 no-op

### Weak BBoxes

ODL이 준 cell/paragraph bbox 또는 PDFium primitive bbox가 부정확하면 잘못된 partition이 생길 수 있다.

대응:

- bbox 기반 판단을 절대값이 아니라 tolerance/clustering 기반으로 사용
- 셀 재구성 전에 sanity check를 통과한 경우에만 반영

### Coupling With Preview Internals

preview 전용 candidate 로직에 canonical path가 과도하게 결합될 수 있다.

대응:

- candidate 레이어는 공유하지 않음
- primitive / segmented-rule helper만 명시적으로 공유

## Success Criteria

- 기존 `TableIR` 내부에서 점선 divider를 근거로 text-bearing cell split이 가능해진다.
- 샘플 PDF에서 점선 표의 canonical `table.cells` 구조가 현재보다 정보 추출 친화적으로 개선된다.
- 새 기능은 opt-in이며, 실패 시 안전하게 no-op로 떨어진다.
- preview candidate 전체를 canonical path에 들이지 않고도 목적을 달성한다.
