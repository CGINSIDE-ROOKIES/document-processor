# PDF Raw-Table Split Plan Design

## Goal

ODL raw가 이미 table로 인식한 PDF 표에 한해, `segmented_horizontal_rule` / `segmented_vertical_rule`을 이용해 점선 내부 경계를 보강하고, 그 결과를 adapter가 `TableIR`로 변환할 때부터 반영한다. 목표는 post-pass로 `TableIR`를 다시 재작성하는 것이 아니라, raw table node를 기준으로 split plan을 만들고 그 plan으로 더 안정적인 canonical `TableIR`를 생성하는 것이다.

## Current Problem

현재 구현된 split enrichment는 `DocIR` 생성 후 `TableIR.cells`를 다시 분해하는 post-pass 방식이다. 이 방식은 다음 문제가 있다.

- split 신호가 `TableIR` 생성 이후에 적용되어 raw table context와 어긋난다.
- 잘못된 축 후보가 들어오면 table 전체 topology를 흔들기 쉽다.
- HTML preview와 canonical `TableIR`이 서로 다른 기하 정보를 쓰게 되어 결과가 불안정해진다.
- 점선을 실제로 잘 못 잡는 경우에는 split 자체가 시작되지 않는다.

반면 adapter는 raw ODL table node의 `rows[*].cells[*]`, `row number`, `column number`, `row span`, `column span`, `kids`, `bounding box`를 그대로 읽어 `TableIR`를 만든다. 즉 점선 보강은 `TableIR` 후처리보다 raw table -> adapter 경계에서 처리하는 편이 더 자연스럽다.

## Scope

- ODL이 이미 table로 인식한 raw table node만 보강한다.
- 점선 후보는 `pypdfium2` primitive analysis가 만든 `segmented_horizontal_rule` / `segmented_vertical_rule`만 사용한다.
- 점선 후보는 해당 raw table `bounding box` 내부에 있을 때만 사용한다.
- 분할 판단은 raw table의 기존 cell을 기준으로 한다.
- 승인된 분할만 adapter가 `TableIR`로 만들 때 반영한다.
- direct split은 보수적으로 `rowspan == 1` 또는 `colspan == 1` 셀부터 시작한다.

## Non-Goals

- ODL이 table로 인식하지 못한 영역에서 새 table을 만들지 않는다.
- raw JSON 원본 자체를 mutate하지 않는다.
- `line arts`를 canonical split 신호로 쓰지 않는다.
- preview candidate(`axis_box`, `open_frame`, `semantic_line`)를 canonical 경로에 끌어오지 않는다.
- 빈 셀 완전 복원이나 시각적 grid 재현을 1차 목표로 두지 않는다.
- merged cell을 임의의 geometry 추론으로 강제로 쪼개지 않는다.

## Chosen Approach

선택한 방향은 `raw-table split plan` 방식이다.

1. ODL raw에서 table node를 읽는다.
2. 페이지별 PDFium primitive에서 `segmented_*_rule`을 만든다.
3. 각 raw table node에 대해 table bbox 내부의 segmented rule만 추린다.
4. 각 rule이 실제로 지나는 기존 raw cell만 조사한다.
5. 그 cell 내부 텍스트가 rule 기준으로 실제로 양쪽으로 갈릴 때만 split proposal을 승인한다.
6. 승인된 proposal을 `row boundary insertion` / `column boundary insertion` 이벤트로 승격한다.
7. adapter가 이 split plan을 consume해서 최종 `TableIR.cells`의 `row_index`, `col_index`, `rowspan`, `colspan`을 계산한다.

이 접근을 고른 이유는 다음과 같다.

- 점선 후보는 PDFium segmented rule만으로 제한되어 신호가 단순하다.
- split 판단은 raw table/cell 문맥 안에서 일어나므로 table 외부 primitive에 덜 흔들린다.
- row/col 계산을 geometry 재추론이 아니라 기존 logical band에 slot을 삽입하는 방식으로 처리할 수 있다.
- raw JSON 원본은 유지되므로 디버깅과 regression 비교가 쉽다.

## Architecture

### Remove Existing Post-Pass

기존 post-pass split enrichment는 제거한다.

- `src/document_processor/pdf/enhancement/table_split_inference.py` 삭제
- `PdfParseConfig.infer_table_splits` 삭제
- `pipeline.py`의 parse-time post-pass wiring 삭제
- 관련 post-pass 테스트 삭제 또는 교체

### New Split Planning Layer

새 모듈을 추가한다.

- `src/document_processor/pdf/odl/table_split_plan.py`

책임은 다음과 같다.

- 페이지별 `segmented_*_rule` 준비
- raw table node별 후보 rule 필터링
- cell-local split proposal 계산
- approved proposal을 table-level split plan으로 정리

### Adapter Integration

adapter는 raw table node를 `TableIR`로 바꾸기 직전에 split plan을 조회해 반영한다.

- `_table_node_to_ir(...)`는 raw table node와 대응되는 split plan을 선택한다.
- split plan이 없으면 현재와 동일하게 raw `rows/cells`를 그대로 `TableIR`로 변환한다.
- split plan이 있으면 plan에 따라 synthetic logical row/column slot을 삽입하고 최종 `TableCellIR`를 생성한다.

### Primitive Source

primitive source는 기존 preview 분석 코드에서 재사용한다.

- `extract_pdfium_table_rule_primitives(...)`를 사용해 페이지별 line-like primitive를 추출한다.
- 그중 `candidate_roles`에 `segmented_horizontal_rule` 또는 `segmented_vertical_rule`가 있는 primitive만 split planning에 사용한다.

preview candidate 생성 자체는 재사용하지 않는다.

## Data Model

split planning은 raw JSON을 직접 수정하지 않고 별도 plan 객체로 표현한다.

### `TableSplitPlan`

- `table_bbox`
- `page_number`
- `row_events: list[RowBoundaryEvent]`
- `column_events: list[ColumnBoundaryEvent]`
- `cell_splits: dict[CellKey, CellSplitPlan]`

### `CellKey`

raw cell을 stable하게 가리키는 키다.

- `row_index`
- `col_index`
- `rowspan`
- `colspan`
- 필요 시 raw bbox를 포함해 ambiguous collision을 막는다.

### `RowBoundaryEvent` / `ColumnBoundaryEvent`

새 boundary 삽입 이벤트다.

- `source_row` 또는 `source_col`
- `axis_pt`
- `supporting_cells: set[CellKey]`

의미는 “기존 logical band 내부에 새 경계 하나를 삽입한다”이다.

### `CellSplitPlan`

개별 셀의 direct split 정보다.

- `orientation`
- `axis_pt`
- `leading_text_boxes`
- `trailing_text_boxes`

1차 구현에서는 한 cell당 한 개 split만 허용한다. 복수 split은 후속 범위다.

## Split Candidate Rules

점선 후보는 다음 조건을 모두 만족해야 한다.

- primitive가 `segmented_horizontal_rule` 또는 `segmented_vertical_rule`여야 한다.
- primitive bbox가 raw table bbox 내부에 완전히 들어와야 한다.
- primitive 중심축이 table outer border 근처면 제외한다.
- primitive span이 table span의 최소 비율 이상이어야 한다.

즉 split planning의 입력은 “page 전체 primitive”가 아니라 “해당 table bbox 내부 segmented rule”로 좁혀진다.

## Cell-Local Split Decision

각 후보 rule에 대해 rule이 실제로 지나는 raw cell만 조사한다.

- vertical rule이면 `cell.left < axis < cell.right`
- horizontal rule이면 `cell.bottom < axis < cell.top`

그다음 각 crossed cell에 대해 다음을 본다.

1. direct split 가능한 셀인지 확인
   - vertical은 우선 `colspan == 1`
   - horizontal은 우선 `rowspan == 1`
2. cell 내부 텍스트 bbox를 rule 기준으로 leading/trailing 두 쪽으로 나눈다.
3. 양쪽 모두 텍스트가 존재할 때만 split proposal을 승인한다.

이 단계에서는 table 전체 topology를 바꾸지 않는다. 단지 “이 cell은 이 축에서 쪼개도 된다”는 proposal만 생성한다.

## Row/Col Calculation

핵심 원칙은 `row/col을 새로 추론하지 않고, 기존 logical band 안에 slot을 삽입한다`는 것이다.

### Horizontal Split

예를 들어 기존 cell이 `(row=5, col=2, rowspan=1)`이고, 이 cell 내부의 horizontal dotted rule이 승인되면:

1. `source_row = 5`인 `RowBoundaryEvent`를 하나 만든다.
2. 원래 row 5 band는 두 개 sub-row로 확장된다.
3. split된 cell은:
   - top piece: `row_index = 5`
   - bottom piece: `row_index = 6`
4. 원래 `row >= 6`이던 셀은 `row_index + 1`
5. 같은 old row 5 band를 가로지르지만 split되지 않은 셀은 `rowspan + 1`

### Vertical Split

vertical도 동일하다.

1. `source_col = c`인 `ColumnBoundaryEvent` 생성
2. old column band를 두 개 sub-col로 확장
3. split된 cell은 left/right 두 piece로 분해
4. 원래 `col >= c + 1`이던 셀은 `col_index + 1`
5. same band를 가로지르는 unsplit cell은 `colspan + 1`

### Merged Cells

merged cell은 다음 규칙으로 단순 처리한다.

- direct split 가능한 조건을 만족하지 않으면 그 merged cell 자체를 쪼개지 않는다.
- 대신 새 boundary가 그 cell을 가로지르면 `rowspan` 또는 `colspan`만 늘린다.
- 즉 merged cell은 우선 “split 대상”이 아니라 “new band를 덮는 spanning cell”로 취급한다.

이 규칙이면 row/col topology는 안정적으로 갱신되면서도, 점선으로 실제 텍스트가 갈리는 단일-span cell만 보수적으로 복원할 수 있다.

## Adapter Consumption

`_table_node_to_ir(...)`는 split plan을 받아 다음 순서로 동작한다.

1. raw table의 original cells를 읽는다.
2. row/column insertion event를 source row/source col 기준으로 정렬한다.
3. original logical band -> final logical band 매핑을 계산한다.
4. split plan이 있는 cell은 두 조각의 synthetic cell로 변환한다.
5. split plan이 없는 cell은:
   - boundary 뒤쪽이면 shifted index 적용
   - boundary를 가로지르면 expanded span 적용
6. 최종 `TableCellIR`를 생성하고 `row_count`, `col_count`를 재계산한다.

text, paragraphs, bbox, meta는 split piece 기준으로 다시 구성한다.

## Error Handling

- `pypdfium2` import 실패 시 split planning은 no-op
- table bbox 또는 page number가 없으면 no-op
- cell bbox 또는 text bbox가 부족하면 그 cell proposal은 폐기
- split plan이 table topology를 모순되게 만들면 해당 table은 원본 adapter 경로로 fallback

새 기능의 기본 원칙은 “실패해도 기존 ODL adapter 결과보다 나빠지지 않는다”이다.

## Testing Strategy

### Unit Tests

- bbox 내부 segmented horizontal rule이 single-row cell을 두 row로 나누는 케이스
- bbox 내부 segmented vertical rule이 single-col cell을 두 col로 나누는 케이스
- 점선이 crossed cell을 지나지만 텍스트가 한쪽만 있어 no-op인 케이스
- merged cell이 split되지 않고 `rowspan/colspan`만 늘어나는 케이스
- table bbox 바깥 rule은 무시되는 케이스

### Integration Tests

- raw table node + segmented rule fixture -> `_table_node_to_ir()` 결과 검증
- 샘플 PDF에서 기존 과분할 regression이 사라지는지 검증
- split plan이 없는 table은 기존 adapter 결과와 동일한지 검증

## Success Criteria

- 점선 후보는 `segmented_*_rule`만 사용한다.
- split 판단은 raw table의 existing cell 기준으로만 일어난다.
- row/col 계산은 기존 logical band에 boundary slot을 삽입하는 방식으로 일관되게 동작한다.
- merged cell이 있어도 table 전체 topology가 깨지지 않는다.
- post-pass `TableIR` 재작성 없이 adapter 단계에서 더 안정적인 canonical `TableIR`를 만든다.

## Migration Notes

이 설계는 `2026-04-17-pdf-table-split-enrichment-design.md`의 post-pass enrichment 방향을 대체한다. 기존 설계 문서는 참고 기록으로 남기되, 구현 기준은 이 문서를 따른다.
