# ODL Line Art Exposure Experiment Plan

## Goal

Expose `LineArtChunk` data from `opendataloader-pdf` JSON output, inspect whether the emitted geometry is useful for downstream table-border recovery, and only then decide whether to build and adopt a custom JAR in `document-processor`.

## Current Facts

- `LineArtChunk` is currently filtered out in multiple serializers before JSON is written.
- `LineChunk` has a serializer, but `LineArtChunk` does not.
- Because of that, downstream code cannot evaluate whether raw line geometry is good enough for border-style or border-presence recovery.
- The right first step is not “ship a new JAR”, but “instrument JSON output and inspect the signal quality”.

## Decision Criteria

I will judge the experiment successful only if most of the following hold across the 4 target PDFs:

1. Table-adjacent line art is emitted consistently enough to recover border presence for a large share of table cells.
2. The amount of obvious noise is manageable.
   Noise examples:
   - underline strokes
   - decorative separators
   - header/footer lines
   - layout guides unrelated to tables
3. Table-cell-level association looks feasible from emitted geometry.
   Feasible means cell bbox and line-art bbox overlap patterns are stable enough that downstream heuristics do not become brittle.
4. The emitted schema is understandable and does not destabilize existing consumers.
5. The JSON volume increase is acceptable for debugging and later optional production use.

## Non-Goals For Phase 1

- Perfect dotted/double-line fidelity
- Production-grade border classification
- Immediate `document-processor` integration
- Schema finalization for public release

Phase 1 is strictly about exposure and evaluation.

## Dataset

Requested dataset path:

- `/Users/yoonseo/Downloads/dataset-2/`

Expected contents:

- 4 PDF files

Current blocker:

- The sandbox cannot read `Downloads/dataset-2`; access currently fails with `Operation not permitted`.
- Until that path is accessible, exact file enumeration and actual sample runs against those 4 PDFs are blocked.

## Proposed Output Shape

For phase 1, do **not** mix line art into existing `kids`.

Instead emit dedicated arrays so current downstream parsing remains stable.

Recommended JSON additions:

```json
{
  "type": "table cell",
  "bounding box": [ ... ],
  "kids": [ ... ],
  "line arts": [
    {
      "type": "line art",
      "bounding box": [ ... ],
      "line chunks": [
        {
          "type": "line",
          "bounding box": [ ... ]
        }
      ]
    }
  ]
}
```

Possible locations:

- root document object: `line arts`
- table: `line arts`
- table cell: `line arts`
- header/footer: `line arts`
- list item: `line arts`

## Why Separate Fields Instead Of `kids`

- avoids breaking downstream assumptions about semantic content order
- keeps debug output readable
- lets us compare text/image/table content against line data side by side
- allows later opt-in support in downstream adapters

## Implementation Plan

### Step 1. Add Phase-1 JSON Keys

Files:

- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/JsonName.java`

Add keys such as:

- `LINE_ART_TYPE`
- `LINE_ARTS`
- `LINE_CHUNKS`

### Step 2. Add `LineArtChunkSerializer`

Files:

- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/serializers/LineArtChunkSerializer.java`
- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/ObjectMapperHolder.java`

Serializer requirements:

- write essential info
- write `type: "line art"`
- write nested `line chunks`
- keep phase 1 output simple and geometry-focused

Nice-to-have later:

- orientation summary
- stroke count
- enclosing-box hint

### Step 3. Stop Throwing Away `LineArtChunk`

Files:

- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/JsonWriter.java`
- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/serializers/TableSerializer.java`
- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/serializers/TableCellSerializer.java`
- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/serializers/HeaderFooterSerializer.java`
- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/json/serializers/ListItemSerializer.java`

Required behavior:

- keep non-line content in `kids`
- collect `LineArtChunk` separately into `line arts`

Root writer should also optionally emit document-level orphan line art, because some useful table/grid lines may not be neatly attached to the most specific semantic parent.

### Step 4. Optional Feature Flag

Only add this if the raw output looks useful.

Files:

- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/api/Config.java`
- `java/opendataloader-pdf-cli/src/main/java/org/opendataloader/pdf/cli/CLIOptions.java`
- `options.json`

Suggested flag:

- `--emit-line-art`

Default:

- `false`

Reason:

- limits schema churn
- keeps baseline outputs unchanged for existing users

For the very first local experiment, hard-coded exposure is acceptable. Feature-flagging can come immediately after validation.

## Evaluation Procedure

For each of the 4 PDFs:

1. Run baseline ODL JSON.
2. Run modified ODL JSON with line-art exposure.
3. Inspect:
   - document-level `line arts`
   - table-level `line arts`
   - table-cell-level `line arts`
4. Record:
   - number of tables
   - number of cells
   - number of cells with nearby line art
   - obvious false positives
   - whether cell-border inference appears feasible

## What I Will Measure

### Quantitative

- count of `line art` nodes per document
- count of `line art` nodes per table
- count of cells whose bbox intersects at least one line-art bbox
- count of orphan line-art nodes at document root
- JSON size delta before/after exposure

### Qualitative

- are table outlines actually captured?
- are internal row/column separators captured?
- do underlines dominate the output?
- do decorative lines pollute table-adjacent regions?
- are lines emitted close enough to cell edges to support deterministic heuristics?

## Success Thresholds

Proceed toward custom JAR adoption only if:

- line art is present for a meaningful share of real table borders
- noise does not overwhelm true table structure
- table-cell association looks implementable with reasonable heuristics

Do **not** proceed yet if:

- most emitted line art is decorative noise
- table borders are too incomplete
- emitted geometry is too coarse to associate with cells
- JSON explosion is disproportionate to utility

## Expected Follow-Up If Phase 1 Succeeds

1. Build a shaded CLI JAR from the modified branch.
2. Run the same 4-PDF dataset again via the built CLI.
3. Add temporary support in `document-processor` to preserve raw `line arts`.
4. Prototype table-border recovery from exposed line geometry.
5. Compare:
   - current raster border inference
   - raw line-art-based inference
   - combined strategy

## Expected Follow-Up If Phase 1 Fails

If exposed line art is too noisy or too weak:

- abandon raw line-art path for border fidelity
- keep current raster-based border enrichment
- focus upstream effort on other recoverable metadata:
  - formula
  - document metadata
  - font family
  - list metadata
  - caption linkage

## Immediate Execution Order

1. Gain access to `/Users/yoonseo/Downloads/dataset-2/`.
2. Implement phase-1 serializer changes in `opendataloader-pdf`.
3. Build local CLI artifact.
4. Run the 4 target PDFs.
5. Write a short result note per PDF with screenshots or JSON snippets.
6. Decide whether to continue toward JAR adoption.

## Current Progress

Completed on `feat/TableStyle` in local `opendataloader-pdf`:

- added `LineArtChunkSerializer`
- registered `LineArtChunk` in `ObjectMapperHolder`
- added `line arts` / `line chunks` JSON keys
- expanded `LineChunk` JSON with start/end/width/orientation hints
- stopped dropping `LineArtChunk` from:
  - root `JsonWriter`
  - `TableCellSerializer`
  - `TableSerializer` text-block path
  - `HeaderFooterSerializer`
  - `ListItemSerializer`
- built local CLI successfully with:
  - `mvn -pl opendataloader-pdf-cli -am -DskipTests package`

Current blocker:

- dataset path under `Downloads` is still not readable from this environment
- actual 4-PDF experiment run is therefore pending filesystem access

## Notes

- This plan is intentionally conservative.
- The main question is not “can we emit line art?” but “does emitted line art materially improve downstream border reasoning?”
- If the answer is weak, we should stop early instead of over-investing in a custom JAR path.
