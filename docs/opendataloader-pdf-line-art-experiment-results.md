# ODL Line Art Experiment Results

## Setup

- Modified branch: `feat/TableStyle`
- Built artifact:
  - `/Users/yoonseo/Developer/External/opendataloader-pdf/java/opendataloader-pdf-cli/target/opendataloader-pdf-cli-0.0.0.jar`
- Dataset:
  - `/Users/yoonseo/Developer/External/RAGBuilder-test/dataset-2`
- Command used:

```bash
java -Djava.awt.headless=true \
  -jar /Users/yoonseo/Developer/External/opendataloader-pdf/java/opendataloader-pdf-cli/target/opendataloader-pdf-cli-0.0.0.jar \
  -o /Users/yoonseo/Developer/External/document-processor/out/odl-line-art-experiment \
  -f json \
  --image-output off \
  --include-header-footer \
  /Users/yoonseo/Developer/External/RAGBuilder-test/dataset-2
```

Output directory:

- `/Users/yoonseo/Developer/External/document-processor/out/odl-line-art-experiment`

## Files Tested

1. `창업기업.pdf`
2. `모두의_챌린지_AX_-_LLM_분야_참여기업_모집공고.pdf`
3. `지구과학Ⅰ_문제.pdf`
4. `2026년_전통시장_육성사업(백년시장)_모집공고.pdf`

## Key Result

`LineArtChunk` wrappers are now emitted, but `line chunks` are empty in all 4 documents.

That means:

- line-art bbox exposure works
- detailed line-segment geometry is still unavailable
- dotted/double-line fidelity is not achievable from the current emitted data

This is the single most important result from phase 1.

## Counts

### 2026년_전통시장_육성사업(백년시장)_모집공고

- root `line art` nodes: 74
- tables: 50
- table cells: 1892
- cells with `line arts`: 22
- cell coverage: about 1.2%
- list items with `line arts`: 21

BBox shape breakdown:

- root box-like: 68
- root horizontal-like: 2
- root other: 4
- cell horizontal-like: 21
- cell other: 2
- list-item horizontal-like: 14
- list-item box-like: 6

Assessment:

- root output is dominated by large box-like regions
- cell-level useful signal exists, but coverage is too low for broad border recovery

### 모두의 챌린지 AX - LLM 분야 참여기업 모집공고

- root `line art` nodes: 32
- tables: 35
- table cells: 481
- cells with `line arts`: 92
- cell coverage: about 19.1%
- list items with `line arts`: 55

BBox shape breakdown:

- root box-like: 23
- root horizontal-like: 9
- cell box-like: 264
- cell horizontal-like: 45
- cell other: 41
- list-item box-like: 39
- list-item horizontal-like: 34

Assessment:

- this is the strongest of the 4 documents for cell-level signal
- but a lot of cell-attached line art is box-like rather than narrow line-like geometry
- list-item contamination is also non-trivial

### 지구과학Ⅰ 문제

- root `line art` nodes: 26
- tables: 5
- table cells: 58
- cells with `line arts`: 0
- cell coverage: 0%
- list items with `line arts`: 12

BBox shape breakdown:

- root box-like: 14
- root horizontal-like: 8
- root vertical-like: 4
- list-item box-like: 12
- list-item horizontal-like: 2

Assessment:

- table recovery value is effectively absent in this sample
- emitted line-art signal is mostly outside table-cell attachment

### 창업기업

- root `line art` nodes: 35
- tables: 23
- table cells: 346
- cells with `line arts`: 53
- cell coverage: about 15.3%
- list items with `line arts`: 21

BBox shape breakdown:

- root box-like: 29
- root horizontal-like: 6
- cell horizontal-like: 57
- cell box-like: 3
- cell other: 2
- list-item horizontal-like: 40
- list-item box-like: 5

Assessment:

- this is the cleanest example of cell-level thin horizontal signal
- still, root-level output remains coarse and list-item noise is substantial

## Qualitative Samples

### Useful case

In `모두의_챌린지_AX_-_LLM_분야_참여기업_모집공고.json`, a table cell had:

- cell bbox: `[59.788, 431.468, 150.999, 445.073]`
- first line-art bbox: `[59.848, 430.749, 150.999, 432.187]`

This looks like a thin border-like segment near the bottom edge of the cell.

### Weak case

In `창업기업.json`, a root line-art bbox was:

- `[59.308, 60.174, 535.632, 545.224]`

This is too coarse to use as a border segment directly. It behaves more like a grouped visual region than a precise line.

### Noise example

List-item line art repeatedly appeared under long bullet paragraphs. Those are likely underline/separator-like artifacts, not table borders.

## Interpretation

### What worked

- exposing `LineArtChunk` at all
- attaching bbox-level line-art hints to some table cells
- revealing that some documents really do contain thin horizontal signals near cell edges

### What did not work

- `line chunks` remained empty everywhere
- root-level line art is often too coarse
- table-level direct signal is weak because most useful attachment happens at cell level
- list-item contamination is real
- one of the 4 PDFs produced no cell-level table signal at all

## Dependency Trace

The deeper upstream trace narrowed the cause further than the phase-1 summary.

Observed dependency versions in the shaded CLI JAR:

- `wcag-validation`: `1.31.36`
- `wcag-algorithms`: `1.31.16`

Key classes:

- `org.verapdf.gf.model.factory.chunks.ChunkParser`
- `org.verapdf.gf.model.factory.chunks.LineArtContainer`
- `org.verapdf.wcag.algorithms.entities.content.LineArtChunk`

Important behavior:

- `LineArtContainer.add(Long, LineChunk)` stores both:
  - the `LineChunk` in `lineArtLines`
  - the chunk bbox in `lineArtBBoxes`
- `ChunkParser.processLineChunk(...)` calls that path.
- `ChunkParser.parseLineArts()` later serializes:
  - bbox unions from `lineArtBBoxes`
  - segment lists from `lineArtLines`

So empty `line chunks` does **not** mean the serializer dropped them.
It means the upstream parse path often never stored segment geometry in the first place.

The strongest signal is in `ChunkParser.processf()`:

- when a filled path is recognized as a clean rectangle, it may still recover a `LineChunk`
- but when a filled path is seen as a standalone `LineChunk` and does **not** collapse into a rectangle, the code calls `processBoundingBox(...)`, not `processLineChunk(...)`
- `processBoundingBox(...)` stores only bbox information
- `CurveChunk` paths also go through bbox-only handling

Practical implication:

- stroked line art can preserve segment geometry
- filled thin rectangles / filled paths often collapse to bbox-only line-art wrappers
- if the sample PDFs draw many borders as fills rather than strokes, empty `line chunks` is expected

That matches the experiment:

- many emitted `line arts` are thin or box-like bboxes
- but no actual `type: "line"` segment payload is present anywhere in the output

Revised conclusion:

- this is likely not “our serializer forgot to emit line chunks”
- it is much closer to “the veraPDF parse path reduces many visual rules to bbox-only artifacts before JSON serialization”

## Bottom Line

Current verdict:

- bbox-level `LineArtChunk` exposure is worth keeping for further inspection
- current output is **not sufficient** to justify a production JAR swap for border-style fidelity
- current output **might** still help a downstream “border-present vs not-present” heuristic in selected PDFs
- current output is **not enough** for dotted/double-line fidelity

## Recommended Next Step

Before investing further in a custom JAR rollout, inspect why `LineArtChunk.getLineChunks()` is empty at runtime.

That is the critical fork point.

If the underlying veraPDF path can be made to populate real line segments:

- continue

If `LineArtChunk` is inherently only a coarse wrapper in this pipeline:

- stop pursuing it for border-style fidelity
- keep raster-based border inference as the main strategy
- treat bbox-level line-art only as an optional weak hint

## Recommended Next Action Items

1. Trace where `LineArtChunk` objects are created upstream and why `getLineChunks()` is empty.
2. Check whether another veraPDF object carries the actual segment list earlier in the pipeline.
3. If real segments exist upstream, expose them before they collapse into empty wrappers.
4. If not, do not over-invest in this path for style fidelity.

## Workstream A2: Border-Presence JSON Baseline

The next patch moved from grid-debug-only output to the first direct style signal.

Changes:

- `opendataloader-pdf` table JSON now emits:
  - `grid row boundaries`
  - `grid column boundaries`
- `opendataloader-pdf` table-cell JSON now emits:
  - `border top`
  - `border bottom`
  - `border left`
  - `border right`
- `document-processor` PDF adapter now maps those flat keys into
  `CellStyleInfo.border_*`

Implementation note:

- this is intentionally coarse
- each detected edge is emitted as the generic CSS token `1px solid`
- this phase is about edge-presence recall, not stroke-style fidelity

Validation path:

- rebuilt CLI JAR:
  - `/Users/yoonseo/Developer/External/opendataloader-pdf/java/opendataloader-pdf-cli/target/opendataloader-pdf-cli-0.0.0.jar`
- reran the HWPX oracle comparison with:
  - `/Users/yoonseo/Developer/External/document-processor/scripts/compare_table_style_oracles.py`

Updated oracle metrics:

### 모두의 챌린지 AX - LLM 분야 참여기업 모집공고

- matched tables: `34`
- compared cells: `700`
- span exact ratio: `0.43`
- occupancy exact ratio: `0.5757`
- border top: precision `0.7745`, recall `0.6330`, f1 `0.6967`
- border bottom: precision `0.8043`, recall `0.6321`, f1 `0.7079`
- border left: precision `0.8085`, recall `0.6452`, f1 `0.7177`
- border right: precision `0.7787`, recall `0.6365`, f1 `0.7005`
- background presence: still `0.0` recall

### 2026년 전통시장 육성사업(백년시장) 모집공고

- matched tables: `44`
- compared cells: `1335`
- span exact ratio: `0.8719`
- occupancy exact ratio: `0.8869`
- border top: precision `0.9078`, recall `0.9329`, f1 `0.9202`
- border bottom: precision `0.9347`, recall `0.9302`, f1 `0.9325`
- border left: precision `0.8825`, recall `0.9458`, f1 `0.9131`
- border right: precision `0.8948`, recall `0.9328`, f1 `0.9134`
- background presence: still `0.0` recall

Interpretation:

- the border-presence path is now validated as viable
- border recall moved from `0.0` to meaningful non-zero values immediately after
  exposing and mapping coarse edge fields
- this confirms that `TableBorder*` is a practical first source for
  `CellStyleInfo.border_*`
- the remaining major gap is background color preservation, not border presence

## Workstream B1: Raster Refinement And Background Sampling

The next step stayed downstream in `document-processor` rather than immediately
forking veraPDF further.

Two render-time enhancements were added:

1. parser-provided coarse borders like `1px solid` are now refined with raster
   evidence when a visible edge exists
2. table-cell background color is inferred from rasterized color pages by
   sampling dominant interior cell color

Implementation points:

- raster border refinement:
  - `/Users/yoonseo/Developer/External/document-processor/src/document_processor/pdf/enhancement/enrichment.py`
- grayscale and color page rendering / sampling:
  - `/Users/yoonseo/Developer/External/document-processor/src/document_processor/pdf/enhancement/border_inference.py`
- render-prep hookup:
  - `/Users/yoonseo/Developer/External/document-processor/src/document_processor/pdf/render_prep.py`
- oracle comparison now evaluates the prepared PDF `DocIR`:
  - `/Users/yoonseo/Developer/External/document-processor/scripts/compare_table_style_oracles.py`

Validation:

- tests:
  - `uv run python -m pytest tests/test_pdf_pipeline.py tests/test_pdf_enrichment.py`
  - result: `13 passed`

Updated oracle metrics after render-prep enrichment:

### 모두의 챌린지 AX - LLM 분야 참여기업 모집공고

- background precision `0.7301`
- background recall `0.6395`
- background f1 `0.6818`
- border f1 remained:
  - top `0.6967`
  - bottom `0.7079`
  - left `0.7177`
  - right `0.7005`

### 2026년 전통시장 육성사업(백년시장) 모집공고

- background precision `0.8667`
- background recall `0.8426`
- background f1 `0.8545`
- border f1 remained:
  - top `0.9202`
  - bottom `0.9325`
  - left `0.9131`
  - right `0.9134`

Interpretation:

- coarse border presence is now practical enough to keep
- actual visible edge styling is better handled as a raster refinement layer on
  top of parser topology
- cell background preservation is no longer blocked; a first useful baseline is
  now working without an immediate veraPDF fork
- if later work needs exact fill provenance rather than render-time color
  sampling, that is the point where veraPDF `processf()` work becomes necessary

## Workstream A Progress

After the dependency trace, the first grid-focused patch was implemented in `opendataloader-pdf`:

- table JSON now emits:
  - `grid source`
  - `grid normalized`
  - `serialized cell count`
  - `logical cell count`
  - `covered logical cell count`
  - `non-empty cell count`
  - `empty cell count`
  - `spanning cell count`
- cell JSON now emits:
  - `has content`
  - `content count`

The modified CLI JAR was rerun against the same 4-PDF dataset and the new JSON outputs were written to:

- `/Users/yoonseo/Developer/External/document-processor/out/odl-grid-debug-experiment`

Aggregate observations:

- `2026년_전통시장_육성사업(백년시장)_모집공고.json`
  - tables: `50`
  - coverage mismatch: `0`
  - tables with spans: `24`
  - non-empty ratio: `797 / 2309`
- `모두의_챌린지_AX_-_LLM_분야_참여기업_모집공고.json`
  - tables: `35`
  - coverage mismatch: `0`
  - tables with spans: `25`
  - non-empty ratio: `459 / 690`
- `지구과학Ⅰ_문제.json`
  - tables: `5`
  - coverage mismatch: `0`
  - tables with spans: `0`
  - non-empty ratio: `56 / 58`
- `창업기업.json`
  - tables: `23`
  - coverage mismatch: `0`
  - tables with spans: `6`
  - non-empty ratio: `344 / 387`

Interpretation:

- for the current dataset, `TableBorder*` topology looks internally consistent
- `covered logical cell count == logical cell count` for all tables in all 4 PDFs
- this is strong evidence that grid restoration should continue to use `TableBorder*` as the primary structure source
- the next meaningful experiment is fill/background preservation, not more `LineArtChunk`-only work
