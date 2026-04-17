# PDF Logical Page Design

## Goal

PDF preview를 physical page 기반 절대 배치 렌더에서 logical page 기반 flow-friendly 렌더로 옮긴다. 단일 physical page는 최소 1개의 logical page를 만들고, spread로 판단되면 2개의 logical page로 나눈다. 이후 paragraph/table/image/candidate는 해당 logical page 안에서만 배정하고, `axis_box/open_frame` 승격도 logical page 범위 안에서만 수행한다.

## Current Problem

- HWP/HWPX는 shared paged renderer를 사용하고 `min-height` 기반 flow로 렌더돼 clipping이 거의 없다.
- PDF는 preview 전용 경로에서 fixed `height`와 `overflow:hidden`을 사용한다.
- PDF의 `left/right/full/main` region은 physical page 안에서만 배치 힌트로 사용되고, 실제 logical page abstraction은 없다.
- candidate overlay와 candidate-to-table 승격도 physical page 단위로 이루어져 spread와 단일 페이지의 flow model이 섞여 있다.

## Chosen Approach

1. 모든 physical PDF page에서 logical page descriptor를 만든다.
2. ODL이 준 `layout_region_id` / `region_type`를 우선 사용한다.
3. side-region이 없으면 PDFium split 추론을 사용한다.
4. side-region이 끝내 없으면 physical page 전체를 하나의 logical page로 사용한다.
5. `full/main`은 independent logical page가 아니라 assignment/routing signal로 유지한다.
6. preview render는 physical page body를 직접 그리지 않고 logical page body를 조립한다.

## Logical Page Model

각 logical page는 다음 정보를 가진다.

- source physical page number
- stable logical page id
- logical page type: `single`, `left`, `right`
- source region ids
- bounding box
- width/height/margins

`single`은 physical page 전체 콘텐츠 영역 또는 full/main region을 대표한다. `left/right`는 side-region bounding box를 대표한다.

## Region Signals

### Priority

1. raw ODL `layout regions[]`
2. paragraph/table `layout_region_id`
3. PDFium split inference (`left/right`)
4. bbox-based `full` classification
5. fallback single full-page logical page

### Interpretation

- `left/right`: 독립 logical page 후보
- `main`: side-region이 없을 때 single logical page의 기본 signal
- `full`: side-region이 있을 때 gutter를 가로지르는 wide content signal

`full`은 spread physical page 전체를 의미하지 않는다. spread에서는 두 logical page 중 어느 하나의 flow에 넣지 않고 별도 full-width block처럼 다룰 수 있어야 한다.

## Rendering Direction

- logical page 렌더는 shared HTML renderer와 비슷하게 flow 중심으로 간다.
- page wrapper는 `min-height`를 유지하되 `height` 고정과 `overflow:hidden`은 피한다.
- long rule overlay 같은 purely visual artifact만 별도 overlay를 유지한다.
- `axis_box/open_frame` 승격은 logical page 내부 candidate 집합에서만 수행한다.
- 승격된 synthetic `TableIR`는 logical page flow 안에 삽입한다.

## Legacy Cleanup

다음 코드는 정리 대상이다.

- physical page 기준 `left/right` band 조립 로직
- preview 전용 fixed-height page wrapper 의존성
- candidate group entry/overlay 경로 중 logical page flow에 흡수 가능한 부분

유지할 코드는 다음이다.

- ODL/PDFium region signal 수집
- table geometry application
- candidate extraction, suppression, promotion
- long rule overlay

## Testing

- logical page builder가 single page와 spread page를 모두 올바르게 구성하는지
- logical page assignment가 `left/right/full/main`과 fallback single page를 올바르게 처리하는지
- logical page render가 fixed-height clipping 없이 `<section class="document-page">`를 조립하는지
- candidate promotion이 logical page 안에서만 일어나는지
- 기존 table geometry / long rule behavior가 깨지지 않는지

## Non-Goals

- canonical PDF parsing 단계에서 DocIR page model 자체를 다시 쓰는 것
- OCR or reading-order 알고리즘 교체
- 모든 overlay 제거
