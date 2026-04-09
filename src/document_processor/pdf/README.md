# PDF 모듈

이 디렉터리는 기존 `document_processor` 코어 파서 위에 추가된 PDF 전용 경로를 담고 있습니다.

현재 설계의 중요한 기준은 다음과 같습니다.

- PDF 파싱은 별도 pipeline으로 분리
- 렌더링은 별도 PDF renderer를 만들지 않고 shared `html_exporter.py` 사용
- PDF 쪽은 shared renderer 앞단의 `render_prep` 계층에서 필요한 보강만 수행

즉, 바깥에서는 `DocIR`를 공통 계약으로 유지하면서도, PDF만 `probe -> triage -> ODL -> DocIR`
경로를 타도록 정리한 구조입니다.

## 현재 흐름

1. `parsing/probe.py`
   - `pypdfium2`로 페이지를 가볍게 프로파일링
2. `parsing/triage.py`
   - 페이지를 `structured` / `scan_like`로 분류
3. `odl/runner.py`
   - vendored OpenDataLoader CLI JAR 실행
4. `odl/adapter.py`
   - ODL JSON을 `DocIR`로 변환
   - 렌더에 필요한 PDF style 정보도 함께 채움
   - formula, font family, list metadata, 문서 메타데이터도 여기서 흡수
5. `enhancement/enrichment.py`
   - 필요한 경우 페이지 raster를 다시 읽어 table border를 보강
6. `render_prep.py`
   - PDF 전용 보강을 shared renderer 밖에서 수행

메인 진입점:

- `parse_pdf_to_doc_ir()` in [pipeline.py](./pipeline.py)

ODL native 산출물은 별도 경로로 노출합니다.

- `export_pdf_local_outputs()` in [local_outputs.py](./local_outputs.py)

## 디렉터리 구성

- `config.py`
  - PDF parse config, ODL config, triage config
- `parsing/probe.py`
  - lightweight PDF page profiling
- `parsing/triage.py`
  - scan-like / structured 분기 규칙
- `odl/runner.py`
  - vendored ODL JAR를 감싼 local CLI wrapper
- `odl/adapter.py`
  - ODL JSON -> `DocIR`
- `meta.py`
  - PDF provenance / 좌표 정보용 metadata 모델
  - 문서 레벨 메타데이터(author/title/date 등) 정규화
- `local_outputs.py`
  - native ODL `json` / `html` / `markdown` output handle
- `enhancement/border_inference.py`
  - grayscale raster 기반 cell border 추론
- `enhancement/enrichment.py`
  - 추론된 border를 `DocIR`에 다시 반영

## 중요한 동작

### shared HTML renderer를 계속 사용합니다

현재 구조에는 별도 PDF HTML exporter가 없습니다.

대신:

- `DocIR.to_html()`는 다른 포맷과 같은 shared `html_exporter.py`를 사용
- PDF 쪽은 `render_prep.py`에서 필요한 전처리만 먼저 수행
- 즉 렌더러가 PDF metadata를 직접 해석하기보다, adapter/render_prep가 `style_types`를 채우는 쪽으로 정리됨

현재 raw에서 바로 흡수하는 대표 정보는 다음과 같습니다.

- `RunStyleInfo`
  - `font_family`, `font_size`, `text_color`, `hidden`
- `ParaStyleInfo`
  - heading 기반 `render_tag`
- `PdfNodeMeta`
  - `bbox`, `page_number`, `source_id`, `linked_content_id`
  - list `numbering style`, `previous/next list id`
- `PdfDocumentMeta`
  - `file_name`, `number_of_pages`, `author`, `title`, `creation_date`, `modification_date`

이 방식으로 DOCX/HWP/HWPX 렌더 경로를 건드리지 않으면서 PDF만 별도 파싱할 수 있습니다.

### `DocIR` 경로에서는 embedded image를 기본으로 사용합니다

`parse_pdf_to_doc_ir()`는 ODL `image_output`이 명시되지 않으면 기본적으로 `embedded`를 사용합니다.

이유:

- `DocIR`는 `ImageAsset` 데이터를 바로 들고 있는 편이 자연스럽고
- embedded `data:` URI는 `DocIR.assets`로 바로 변환 가능하며
- sidecar 파일 기반 output은 `export_pdf_local_outputs()` 경로에서 다루는 편이 더 명확하기 때문입니다

### table border는 best-effort 보강입니다

ODL JSON은 표 구조는 잘 주지만, 셀 border CSS는 충분히 주지 않는 경우가 많습니다.

그래서 PDF 경로는 두 단계로 처리합니다.

- adapter 단계에서 `TableStyleInfo.preview_grid=True` 설정
- enrichment 단계에서 raster 기반으로 비어 있는 `CellStyleInfo.border_*` 추론

중요한 점은, enrichment는 이미 채워진 border를 덮어쓰지 않고
비어 있는 edge만 보강한다는 점입니다.

## 이 디렉터리 밖과 연결되는 지점

- `document_processor.models.DocIR.from_file(...)`
- `document_processor.models.DocIR.to_html(...)`
- `document_processor.html_exporter`
- `document_processor.__init__`
- `tests/test_pdf_pipeline.py`
- `tests/test_pdf_enrichment.py`

## 현재 한계

- probe는 단순성과 안정성을 위해 현재 직렬 실행
- table border inference는 missing grid line 보강 중심의 heuristic이며 full visual fidelity를 보장하지 않음
- `DocIR` 경로와 native ODL local output 경로는 의도적으로 분리되어 있음
