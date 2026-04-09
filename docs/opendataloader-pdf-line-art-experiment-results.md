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
