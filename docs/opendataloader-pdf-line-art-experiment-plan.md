# ODL Table Grid And Background Preservation Plan

## Objective

The original phase-1 goal was to expose `LineArtChunk` and decide whether raw line geometry was good enough for downstream border recovery.

That phase is complete.

The revised goal is narrower and more useful:

1. preserve table grid topology well enough to populate downstream `TableIR` and `CellStyleInfo` reliably
2. preserve cell background color when the PDF uses filled rectangles or filled paths for table shading

This plan is now about reaching those two outcomes.

## Scope

### In Scope

- table row/column/span fidelity
- cell-level border-presence fidelity
- cell background color preservation for flat fills
- debug JSON needed to evaluate both
- upstream patch planning across:
  - `opendataloader-pdf`
  - veraPDF dependency code paths

### Out Of Scope For This Phase

- dotted vs solid vs double-line fidelity
- exact stroke-style reproduction
- arbitrary vector artwork styling
- gradients, patterns, transparency-heavy fills
- production `document-processor` integration before the upstream signal is validated

## What Phase 1 Proved

### Completed Work

- branch: `feat/TableStyle`
- `LineArtChunk` exposure was added to `opendataloader-pdf`
- modified CLI JAR was built and run against the 4-PDF dataset
- results were written to:
  - `/Users/yoonseo/Developer/External/document-processor/docs/opendataloader-pdf-line-art-experiment-results.md`

### Dataset

- `/Users/yoonseo/Developer/External/RAGBuilder-test/dataset-2`

Files:

1. `창업기업.pdf`
2. `모두의_챌린지_AX_-_LLM_분야_참여기업_모집공고.pdf`
3. `지구과학Ⅰ_문제.pdf`
4. `2026년_전통시장_육성사업(백년시장)_모집공고.pdf`

### Key Findings

- `LineArtChunk` wrappers are now emitted in JSON.
- `line chunks` were empty in all 4 PDFs.
- therefore, the current exposed line-art path is not enough for stroke-style fidelity
- but some cell-level bbox hints are still useful as weak border-presence hints

### Dependency Trace Findings

Observed runtime dependency versions in the shaded CLI JAR:

- `wcag-validation`: `1.31.36`
- `wcag-algorithms`: `1.31.16`

The most important technical finding is:

- the serializer is not the main reason `line chunks` are empty
- the real bottleneck is upstream parsing in `org.verapdf.gf.model.factory.chunks.ChunkParser`

What happens:

- `GraphicsState` tracks `fillColor` and `fillColorSpace`
- `ChunkParser.processS()` and `ChunkParser.processB()` can preserve line segments more directly
- `ChunkParser.processf()` often reduces filled geometry to bbox-only artifacts through `processBoundingBox(...)`
- therefore many visually meaningful table fills and filled border-like rectangles never survive as segment geometry

This changes the strategy:

- grid fidelity should not depend primarily on raw `LineArtChunk`
- background color preservation cannot be solved only in JSON serializers

## Revised Technical Thesis

### Thesis A: Table Grid Should Use `TableBorder*` As Primary Truth

For table structure, the strongest signal is already inside the recognized border-table model:

- `TableBorder`
- `TableBorderRow`
- `TableBorderCell`
- `TableBordersCollection`
- `TableBorderProcessor`
- `TableStructureNormalizer`

This path already knows about:

- rows
- columns
- spans
- normalized table structure
- assignment of content into cells

So the right direction for “기가막힌 격자” is:

- use `TableBorder*` as the primary topology source
- use raw line art only as a weak supporting hint

### Thesis B: Background Color Requires Fill-Aware Visual Objects

For color preservation, table structure is not enough.

We need the fill information that currently exists in `GraphicsState` to survive into emitted visual objects.

That means:

- `ChunkParser.processf()`
- `GraphicsState.fillColor`
- `GraphicsState.fillColorSpace`
- filled rectangle/path serialization

must all be part of the solution.

### Thesis C: Background Filtering Currently Conflicts With Color Preservation

`ContentFilterProcessor.processBackgrounds(...)` removes large `LineArtChunk` backgrounds.

That may be correct for page-level decoration removal, but it is also a direct risk for:

- table-wide shaded header rows
- cell fills
- large merged-cell backgrounds

So background filtering itself must become table-aware before color preservation can work reliably.

## Success Criteria

The project is successful only if both of the following become true on the 4-PDF dataset.

### A. Grid Success

- most visible tables have correct row and column counts
- merged cells and spans are preserved well enough for downstream `TableIR`
- cell bbox coverage is stable enough to assign content and later background style
- the emitted JSON remains understandable and debug-friendly

### B. Background Success

- obvious header-row or cell shading is emitted as structured data
- background color is recoverable for flat filled cells with low false positives
- page-level backgrounds and decorative fills do not dominate cell assignments

## Planned Output Shape

Do not overload existing semantic `kids` arrays with visual-debug objects.

Keep debug structures separate.

The concrete JSON contract for downstream `CellStyleInfo` / `TableStyleInfo`
mapping now lives in:

- `/Users/yoonseo/Developer/External/document-processor/docs/opendataloader-pdf-table-style-schema.md`

That schema document is the source of truth for:

- which keys should be emitted flat for easy `document-processor` mapping
- which keys are debug-only
- which fields are feasible in `opendataloader-pdf` alone
- which fields require a veraPDF-side patch

### Proposed Table JSON Additions

At the table level:

```json
{
  "type": "table",
  "bounding box": [ ... ],
  "number of rows": 5,
  "number of columns": 4,
  "rows": [ ... ],
  "grid source": "table-border",
  "grid normalized": true
}
```

At the cell level:

```json
{
  "type": "table cell",
  "bounding box": [ ... ],
  "row number": 1,
  "column number": 1,
  "row span": 1,
  "column span": 1,
  "kids": [ ... ],
  "line arts": [ ... ],
  "fills": [
    {
      "type": "fill region",
      "bounding box": [ ... ],
      "fill color": [0.9, 0.9, 0.9],
      "fill color space": "DeviceRGB",
      "source": "filled-rectangle"
    }
  ],
  "background": {
    "fill color": [0.9, 0.9, 0.9],
    "fill color space": "DeviceRGB",
    "source": "cell-matched-fill",
    "confidence": 0.94
  }
}
```

Notes:

- `fills` is a debug/analysis field
- `background` is a higher-confidence derived field
- `line arts` remains optional weak evidence

## Workstreams

## Workstream A: Grid Fidelity Through `TableBorder*`

### Goal

Make the emitted table model trustworthy enough that downstream style assignment can target stable cells.

### Why This Comes First

Without strong cell topology:

- background matching becomes brittle
- row/header shading cannot be attached consistently
- later `document-processor` mapping will remain heuristic-heavy

### Primary Patch Targets In `opendataloader-pdf`

- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/processors/DocumentProcessor.java`
- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/processors/TableBorderProcessor.java`
- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/processors/TableStructureNormalizer.java`
- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/serializers/TableSerializer.java`
- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/serializers/TableRowSerializer.java`
- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/serializers/TableCellSerializer.java`
- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/JsonName.java`

### Detailed Tasks

#### A1. Verify Table Source Of Truth

Confirm which tables in JSON are already coming from normalized `TableBorder` objects rather than weaker raw structures.

Questions:

- Is every serialized `table` backed by `TableBorderProcessor.normalizeAndProcessTableBorder(...)`?
- Are there cases where the JSON table falls back to a weaker structure?
- Are one-cell “text block tables” affecting analysis?

#### A2. Emit Grid Provenance

Add explicit debug fields to the table JSON:

- `grid source`
- `grid normalized`
- optional `previous table id`
- optional `next table id`

This is not a style feature by itself.
It is needed to understand whether topology quality comes from recognition, normalization, or fallback behavior.

#### A3. Audit Cell Coverage

Measure, per table:

- number of rows
- number of columns
- number of serialized cells
- number of logical covered cells due to span
- count of cells with content
- count of empty cells

This gives a hard baseline before color matching starts.

#### A4. Keep Raw Visual Hints Available At Cell Level

Retain:

- `line arts`
- later `fills`

at table-cell scope so that style assignment is cell-driven, not page-driven.

### Grid Decision Gate

Proceed to serious color work only if the normalized table-cell grid is stable enough across the 4 PDFs.

If grid quality is not strong:

- do not attempt background color assignment yet
- fix topology first

## Workstream B: Background Color Preservation

### Goal

Recover flat table-cell fills such as:

- gray header shading
- alternating row shading when implemented as filled rectangles
- emphasis cells drawn with flat colored fills

### Why This Requires More Than JSON Serializer Work

The current serializer only sees already-created content objects.

But the color is currently trapped earlier:

- `GraphicsState` knows the fill color
- `ChunkParser.processf()` decides how filled paths become visual objects
- those objects do not currently preserve fill metadata in emitted JSON

So this workstream is split into:

- ODL-local work
- dependency-level work

### Workstream B1: ODL-Local Feasibility Pass

#### Primary Patch Targets

- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/processors/ContentFilterProcessor.java`
- existing JSON serializer files under `json/serializers`

#### Tasks

1. Make background filtering table-aware for experiments.

Current risk:

- `ContentFilterProcessor.processBackgrounds(...)` removes large `LineArtChunk` backgrounds too early.

Experimental change:

- do not remove a large line-art background if it overlaps a recognized table region significantly
- keep these objects for analysis runs

2. Measure whether useful fill-like objects already survive to the table-cell level after filtering is relaxed.

If yes:

- some background recovery might be possible without immediate veraPDF patching

If no:

- dependency patching is mandatory

### Workstream B2: veraPDF Fill-Aware Object Path

This is the most important upstream technical work.

#### Primary Dependency Classes

- `org.verapdf.gf.model.factory.chunks.ChunkParser`
- `org.verapdf.gf.model.factory.chunks.GraphicsState`
- `org.verapdf.gf.model.factory.chunks.Rectangle`
- `org.verapdf.gf.model.factory.chunks.Path`
- `org.verapdf.wcag.algorithms.entities.content.LineArtChunk`

#### Required Outcome

When `processf()` handles filled table geometry, the emitted object must preserve:

- bbox
- fill color
- fill color space
- source type such as:
  - filled rectangle
  - filled path
  - filled curve-derived region

#### Likely Model Change

There are two plausible design directions:

1. extend `LineArtChunk`

- add fill metadata fields directly
- cheaper for downstream reuse
- but changes shared model semantics

2. introduce a new visual chunk type such as `FilledShapeChunk`

- cleaner semantics
- larger schema and serializer change

At planning time, option 2 is conceptually cleaner.
Option 1 may be faster if the goal is experimental validation.

#### Required Logic Change

`ChunkParser.processf()` must stop collapsing all useful filled table geometry into bbox-only artifacts that lose fill metadata.

At minimum, simple filled rectangles must preserve:

- their bbox
- their fill color

This is the minimum viable signal for cell background recovery.

### Workstream B3: ODL JSON Serialization For Fills

#### Primary Patch Targets In `opendataloader-pdf`

- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/JsonName.java`
- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/ObjectMapperHolder.java`
- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/serializers/SerializerUtil.java`
- new serializer such as:
  - `FilledShapeSerializer.java`

#### Proposed JSON Keys

- `fills`
- `fill color`
- `fill color space`
- `fill source`
- `background`
- `confidence`
- optional `grid source`
- optional `grid normalized`

#### Output Strategy

- keep raw fills separate from `kids`
- emit `fills` as debug evidence
- emit `background` only after a cell-level matching step says it is trustworthy

### Workstream B4: Match Fill Regions To Table Cells

#### Primary Patch Targets

- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/processors/TableBorderProcessor.java`
- possibly a new helper such as:
  - `TableBackgroundProcessor.java`

#### Matching Rules To Implement

Assign a fill to a cell only when most of the following hold:

1. fill bbox overlaps the cell bbox strongly
2. fill bbox is not page-scale decoration
3. fill bbox is not obviously spanning unrelated nearby cells
4. fill color is stable
5. competing fills do not create ambiguity

Special handling should be added for:

- row-wide header fills spanning multiple cells
- merged cells
- repeated row shading

### Workstream B5: Downstream Mapping

This is not the first patch, but the plan must end here.

Once upstream signal is good enough:

- `document-processor` can map cell background to `CellStyleInfo.background`
- table-grid confidence can influence border reconstruction confidence

## Risks

### Grid Risks

- `TableBorder` recognition may still fail on documents whose visible grid is weak
- normalized topology may differ from user-visible visual grouping in some PDFs

### Background Risks

- many PDFs draw table backgrounds as large page-level fills or decorative artifacts
- `ContentFilterProcessor` may remove useful fills unless made table-aware
- some fills may use unsupported color spaces or patterns
- some table shading may be raster image-based rather than vector fill-based

### Dependency Risks

- real color preservation likely requires patching veraPDF classes outside this repo
- model changes may span more than one dependency module
- keeping the experimental fork buildable may take more effort than the JSON patch itself

## Validation Plan

For each of the 4 PDFs:

1. run current baseline JSON
2. run table-grid enhanced JSON
3. run fill-preserving JSON
4. inspect:
   - table count
   - row and column counts
   - span correctness
   - cells with raw fill candidates
   - cells with derived background assignments
   - false positives from decoration or page backgrounds

### Required Evidence Per PDF

- one good table example
- one weak or failure example
- one JSON snippet showing:
  - cell bbox
  - raw fills
  - final background assignment

## HWPX Oracle Validation

The grid debug fields are useful, but they are not the actual target.

They only answer:

- is the PDF-side table topology internally consistent enough to support style matching?

They do **not** answer:

- are border/background styles correct?

For style fidelity, the better validation source is the HWP/HWPX side parsed through `document-processor`,
because HWPX can populate richer `CellStyleInfo` directly.

### Why HWPX Comparison Matters

For the same or near-identical source document:

- HWPX `DocIR` can serve as a practical style oracle
- PDF-side recovered table styles can be compared against:
  - cell background
  - border presence
  - row/col spans
  - cell occupancy

This is a much better test than looking at PDF JSON in isolation.

### Dataset Status

Current files in `/Users/yoonseo/Developer/External/RAGBuilder-test/dataset-2`:

- exact pair:
  - `모두의_챌린지_AX_-_LLM_분야_참여기업_모집공고.pdf`
  - `모두의_챌린지_AX_-_LLM_분야_참여기업_모집공고.hwpx`
- oracle pair (filename suffix differs):
  - `2026년_전통시장_육성사업(백년시장)_모집공고.pdf`
  - `2026년_전통시장_육성사업(백년시장)_모집공고(수정).hwpx`
- PDF-only:
  - `창업기업.pdf`
  - `지구과학Ⅰ_문제.pdf`
- HWPX-only:
  - `2026년도_민관공동기술사업화R&D(TRL점프업)_상반기_2차_시행계획_공고.hwpx`

### Revised Validation Strategy

Use two tracks:

1. internal PDF debug validation
   - grid stats
   - raw line art
   - raw fills
2. HWPX oracle comparison
   - compare recovered PDF cell styles against HWPX `CellStyleInfo`

### Oracle Metrics To Compare

For matched table/cell pairs:

- border presence agreement
  - top
  - bottom
  - left
  - right
- background agreement
  - background present vs absent
  - optional normalized color distance later
- span agreement
  - row span
  - column span
- occupancy agreement
  - empty vs non-empty cell

### Oracle Matching Strategy

Do not try to compare full documents only by page number.

Match in this order:

1. table shape
   - row count
   - column count
   - span pattern
2. table text fingerprint
   - sampled cell text
   - header row content
3. cell position within the matched table
   - row index
   - column index

For the `2026...` pair, the HWPX filename includes `(수정)`, but the working assumption for this project is:

- the suffix is only a filename artifact
- the pair is still valid for oracle comparison unless concrete content drift is observed during matching

If a later comparison run shows real table-level mismatch, downgrade that pair from oracle to qualitative-only.

### Immediate Oracle Priority

Start with both oracle pairs:

- `모두의_챌린지_AX_-_LLM_분야_참여기업_모집공고.pdf`
- `모두의_챌린지_AX_-_LLM_분야_참여기업_모집공고.hwpx`
- `2026년_전통시장_육성사업(백년시장)_모집공고.pdf`
- `2026년_전통시장_육성사업(백년시장)_모집공고(수정).hwpx`

Reason:

- both pairs are currently treated as valid style-oracle candidates
- both contain enough table structure to make border/background validation meaningful

### Practical Next Step

Before deeper veraPDF fill patching, add an evaluation step that:

1. parses the HWPX file through `document-processor`
2. extracts table/cell style summaries from HWPX `DocIR`
3. extracts comparable summaries from PDF-side JSON / future PDF `DocIR`
4. writes a pairwise comparison report

This will make future work measurable instead of purely visual.

## Go / No-Go Gates

### Gate 1: Grid

Continue only if the normalized table grid is stable enough to support cell-level reasoning.

### Gate 2: Raw Fill Signal

Continue only if meaningful table-adjacent filled regions can be kept through parsing and filtering.

### Gate 3: Cell Background Assignment

Continue toward downstream integration only if header or shaded-cell recovery works on at least some of the dataset without unacceptable false positives.

## Immediate Execution Order

1. update JSON/table debug fields for normalized `TableBorder` output
2. measure grid quality on the 4-PDF dataset
3. relax or instrument `ContentFilterProcessor.processBackgrounds(...)` for experiment runs
4. determine whether useful fill-like regions survive without dependency changes
5. if not, start the veraPDF-side fill-aware object patch
6. serialize raw fills
7. implement cell background matching
8. rerun the dataset and record results

## Practical Decision Tree

### If Grid Is Strong But Fill Signal Is Weak

- keep using `TableBorder*` for topology
- patch veraPDF parsing next

### If Grid Is Weak

- pause color work
- improve table normalization first

### If Fill Signal Exists But Color Does Not Survive

- dependency patch is mandatory
- serializer-only work is not enough

## Current Status

### Already Done

- line-art exposure experiment completed
- root cause for empty `line chunks` traced to upstream parse behavior rather than serializer omission
- key patch candidates identified in both `opendataloader-pdf` and veraPDF dependency code

### Ready For Next Step

The next sensible implementation step is:

- start with Workstream A
- do not begin full background-color rollout until grid quality and filtering behavior are measured on the current branch
