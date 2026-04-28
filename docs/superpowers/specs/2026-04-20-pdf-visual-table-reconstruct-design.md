# PDF Visual-First Table Reconstruction Design

## Goal

ODL raw가 이미 `table`로 인식한 PDF 표에 대해, ODL의 row/col topology를 부분 보정하는 대신 `pypdfium2` line primitive를 이용해 표 격자를 시각적으로 다시 구성하고, 그 결과를 adapter가 `TableIR`로 만들 때 바로 반영한다.

핵심 목표는 다음 두 가지다.

- 점선 또는 짧은 선 조각으로만 구분된 표에서, ODL이 큰 merged cell로 과병합한 구조를 시각 선 기준으로 되돌린다.
- 최종 출력은 계속 `TableIR`를 유지하되, row/col/rowspan/colspan은 visual-first grid에서 오고, 텍스트/paragraph/run/style/meta는 ODL raw에서 가져온다.

## Current Problem

현재 경로는 `table_split_plan` 기반이다.

- ODL raw cell 구조를 기본 truth로 두고,
- `segmented_horizontal_rule` / `segmented_vertical_rule`를 이용해 일부 cell만 split/expand 한다.

이 방식은 다음 한계를 가진다.

- ODL이 이미 하나의 큰 merged cell로 본 경우, 가장 중요한 점선 표 케이스를 근본적으로 뒤집기 어렵다.
- 같은 source band에 여러 내부 점선이 있으면 conflicting band 처리나 N-way split 문제 때문에 설계가 빠르게 복잡해진다.
- row/col topology를 geometry로 재구성하지 않고 기존 logical band를 늘리는 식이라, ODL topology가 크게 틀린 표에서는 항상 보수적 no-op에 가깝다.

즉 문제의 본질은 “점선 감지”가 아니라 “감지된 선을 어떤 표 topology로 해석하느냐”다.

## Chosen Approach

선택한 방식은 visual-first grid reconstruction이다.

1. ODL raw의 `table` node에서 `bounding box`, `kids`, cell paragraphs, style, meta를 읽는다.
2. 같은 페이지의 PDFium line primitive에서 해당 table bbox 내부 수평/수직 선 조각을 모은다.
3. 이 선 조각들로 row/column boundary를 재구성한다.
4. 각 격자 셀의 아래/오른쪽 경계가 실제로 그려졌는지 검사해 merge relation을 계산한다.
5. merge relation을 BFS로 묶어 logical merge group을 만든다.
6. ODL raw cell/paragraph를 merge group에 배정한다.
7. 각 merge group을 하나의 `TableCellIR`로 만들어 `row_index`, `col_index`, `rowspan`, `colspan`을 visual-first grid 기준으로 출력한다.

이 접근에서는 시각적으로 선이 지나가는 셀은 실제로 나뉜 것으로 본다. 따라서 `개인기업/법인기업` 같은 왼쪽 라벨 셀도 중간에 실선이 지나가면 분리되는 것이 정상 동작이다. 이 설계는 ODL의 의미적 그룹핑보다 시각 구조를 우선한다.

## Scope

- ODL이 이미 `table`로 인식한 raw table node만 다룬다.
- 기존 PDFium 기반 선 감지 경로는 재사용한다.
- 최종 출력 형식은 그대로 `TableIR`다.
- `DocIR.to_html()`, preview renderer, editor API 같은 후단 API는 바꾸지 않는다.

## Non-Goals

- ODL이 table로 인식하지 못한 영역에서 새 table을 찾지 않는다.
- OCR이나 raster-based line detection을 새로 넣지 않는다.
- semantic merge heuristic으로 시각 선을 무시하지 않는다.
- “ODL이 원래 이렇게 봤으니 rowspan을 유지한다” 같은 hybrid 규칙은 1차 목표가 아니다.

## Primitive Source

선 primitive는 기존 PDFium 분석 경로에서 가져온다.

- 수평 후보: `horizontal_line_segment`, `segmented_horizontal_rule`, `long_horizontal_rule`, `axis_box_edge_horizontal`
- 수직 후보: `vertical_line_segment`, `segmented_vertical_rule`, `long_vertical_rule`, `axis_box_edge_vertical`

테이블 외곽 경계는 ODL table bbox를 이용해 항상 4변을 synthetic border로 추가한다. 이로써 내부 선이 부족하더라도 격자는 닫힌 형태를 유지한다.

주의:

- 입력 primitive는 table bbox 내부에 있는 것만 쓴다.
- nested table의 내부 선이 외부 table primitive에 섞일 수 있으므로, table bbox filtering 외에 nested child table bbox exclusion이 필요하다.

## Grid Reconstruction

### 1. Line Collection

table bbox 내부 primitive에서 다음 두 집합을 만든다.

- horizontal lines: `(axis_y, x_start, x_end)`
- vertical lines: `(axis_x, y_start, y_end)`

짧은 선 노이즈를 줄이기 위해 최소 길이 threshold를 둔다.

### 2. Boundary Extraction

horizontal line의 `axis_y`를 모아 row boundary 후보를 만들고, vertical line의 `axis_x`를 모아 column boundary 후보를 만든다.

- 가까운 좌표는 tolerance 내에서 병합한다.
- 최종 boundary 수로 `rows = len(h_y) - 1`, `cols = len(v_x) - 1`를 계산한다.

### 3. Border Coverage Check

각 grid slot `(i, j)`에 대해:

- 아래 경계는 `h_y[i + 1]`
- 오른쪽 경계는 `v_x[j + 1]`

이 경계를 실제 line segment가 충분히 덮는지 본다.

- 충분히 덮이면 border exists
- 덮지 않으면 neighbor와 merge되어야 하는 것으로 본다

이 판정은 coverage ratio로 한다. 짧은 점선 조각이더라도 전체 셀 폭/높이 대비 일정 비율 이상이면 해당 border가 존재하는 것으로 본다.

## Merge Group Construction

각 grid slot별로 `merge-down`, `merge-right` flag를 만든 뒤 BFS로 connected component를 구한다.

출력은 rectangular merge group이다.

- `min_row`
- `min_col`
- `max_row`
- `max_col`

주의:

- connected component가 비직사각형이면 단순 bbox 승격은 잘못된 merge cell을 만들 수 있다.
- 따라서 component가 직사각형을 채우는지 검사해야 한다.
- 직사각형이 아니면 해당 table reconstruct는 unsafe로 보고 fallback한다.

이 검사는 필수다. visual-first reconstruction이더라도 invalid topology를 `TableIR`로 내보내면 안 된다.

## ODL Content Mapping

시각 grid를 truth로 쓰더라도 텍스트와 스타일은 ODL raw를 재사용한다.

### Mapping Unit

배정 단위는 raw cell 전체보다 paragraph 우선이다.

- paragraph bbox가 있으면 paragraph 중심점 기준
- paragraph bbox가 없으면 descendant span/run bbox union을 사용
- 그래도 없으면 raw cell bbox fallback

이유:

- raw cell 중심점 하나만 쓰면 ODL이 큰 merged cell로 준 경우 텍스트 전체가 한 group으로 쏠린다.
- paragraph 단위가 visual-first grid와 가장 자연스럽게 맞는다.

### Mapping Rule

각 paragraph는 중심점이 포함되는 grid slot을 찾고, 그 slot이 속한 merge group에 배정한다.

- 경계선 위에 걸친 경우는 overlap 최대 rule을 사용한다.
- 어떤 group에도 안전하게 배정되지 않으면 해당 table reconstruct를 fallback한다.

### Style / Meta Rule

group 안에 모인 raw cell 중 reading order상 첫 cell을 representative cell로 둔다.

- `cell_style`은 representative cell 기준
- `background`, `alignment`, `border style`도 representative 기준
- `meta.bounding_box`는 merge group bbox 기준
- paragraph/run 자체의 style과 meta는 원래 ODL 것을 유지

## TableIR Emission

merge group 하나가 최종 `TableCellIR` 하나가 된다.

- `row_index = min_row + 1`
- `col_index = min_col + 1`
- `rowspan = max_row - min_row + 1`
- `colspan = max_col - min_col + 1`
- `bbox = merge group bbox`
- `paragraphs = 해당 group에 배정된 paragraph들을 reading order 유지해서 concat`
- `text = paragraphs flattened text`

`TableIR.row_count`와 `TableIR.col_count`는 reconstructed grid 기준이다.

## Adapter Integration

통합 지점은 `_table_node_to_ir(...)`다.

현재:

- raw cells를 읽고
- optional `table_split_plan`을 적용한 뒤
- shifted/split synthetic cell을 `TableCellIR`로 만든다

변경 후:

1. raw table node를 받는다.
2. page-level primitive cache에서 해당 table의 visual grid를 찾는다.
3. grid reconstruct가 성공하면 raw cells/paragraphs를 merge group으로 다시 배정한다.
4. group 기준으로 `TableCellIR`를 바로 생성한다.
5. reconstruct가 실패하면 현재 ODL raw topology fallback 경로를 사용한다.

즉 adapter는 최종적으로 두 경로를 가진다.

- reconstruct success → visual-first `TableIR`
- reconstruct failure → 기존 raw-cell `TableIR`

## Pipeline Integration

pipeline은 current split-plan builder 대신 page-level table grid builder를 호출한다.

- old: `build_table_split_plans(...)`
- new: `build_table_grids(...)`

threading shape는 기존과 비슷하게 유지한다.

- key: `TableNodeKey`
- value: `TableGrid`

이렇게 하면 adapter wiring과 page-level PDFium open/close 구조를 크게 바꾸지 않아도 된다.

## Failure Model

다음 경우에는 reconstruct를 버리고 기존 ODL table adapter 경로로 fallback한다.

- table bbox 없음
- 충분한 boundary를 만들 수 없음
- nested table exclusion 후 primitive가 너무 적음
- merge component가 비직사각형
- paragraph 배정이 과도하게 실패
- reconstructed row/col 수가 지나치게 커서 noise로 보임

핵심 원칙은 fail-open이다.

- visual-first가 확실할 때만 적용
- 불안정하면 기존 ODL raw topology를 그대로 쓴다

## Testing Strategy

### Unit Tests

- 점선 horizontal rows가 여러 개 있는 merged table에서 visual-first grid가 여러 row를 만든다
- vertical dotted divider가 column을 나누는 케이스
- 실선이 column 0까지 이어질 때 왼쪽 라벨 셀도 분리되는 케이스
- border coverage가 부족하면 merge group으로 묶이는 케이스
- 비직사각형 merge component는 fallback 되는 케이스

### Adapter Tests

- reconstructed group -> `TableCellIR` row/col/span 생성 검증
- paragraph bbox 기준 재배정 검증
- representative cell style 보존 검증
- nested table이 있는 raw cell에서도 outer table grid가 오염되지 않는지 검증

### Pipeline Tests

- pipeline이 `build_table_grids(...)` 결과를 adapter로 넘기는지 검증
- PDFium open 실패 시 empty grids로 fail-open 하는지 검증
- legacy `infer_table_splits` key rejection은 유지

### Sample Verification

샘플 PDF 기준으로 최소 확인 항목은 다음이다.

- 점선 표에서 `개인기업/법인기업` 같은 왼쪽 라벨 셀이 visual-first 규칙대로 분리되는지
- 점선 행 분리가 ODL merged cell보다 촘촘하게 복원되는지
- noise 때문에 열 수가 과도하게 폭증하지 않는지

## Migration

이 설계는 기존 `2026-04-18-pdf-raw-table-split-plan-design.md`를 대체한다.

삭제 대상:

- `src/document_processor/pdf/odl/table_split_plan.py`
- adapter 내 split-plan specific helper
- 관련 테스트 `tests/test_pdf_odl_table_split_plan.py`

추가 대상:

- `src/document_processor/pdf/odl/table_reconstruct.py`
- visual-first grid adapter tests

단, migration은 단계적으로 한다.

1. 새 reconstruct 경로 추가
2. adapter를 reconstruct-first + raw fallback 구조로 변경
3. 기존 split-plan 경로 삭제

## Success Criteria

- 점선 표에서 ODL merged cell을 visual grid 기준으로 실제 row/col로 복원할 수 있다.
- final output은 기존과 동일한 `TableIR` interface를 유지한다.
- 시각 선이 지나가면 왼쪽 라벨 셀도 분리되는 visual-first 정책이 일관되게 적용된다.
- reconstruct가 불안정한 표에서는 기존 ODL raw topology로 안전하게 fallback한다.
