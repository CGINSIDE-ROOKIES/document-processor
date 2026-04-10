# ODL Table Style JSON Schema Contract

## Goal

Define the smallest useful JSON contract that can be emitted from
`opendataloader-pdf` and mapped into `document-processor` table styles with
minimal downstream guesswork.

This document separates three things:

1. fields that should map directly into `CellStyleInfo` / `TableStyleInfo`
2. debug fields that help evaluate or tune the extractor
3. fields that are blocked on a veraPDF patch rather than an
   `opendataloader-pdf` serializer change

## Downstream Targets

Current downstream style targets are:

- [`CellStyleInfo`](/Users/yoonseo/Developer/External/document-processor/src/document_processor/style_types.py#L35)
- [`TableStyleInfo`](/Users/yoonseo/Developer/External/document-processor/src/document_processor/style_types.py#L53)
- PDF adapter: [`adapter.py`](/Users/yoonseo/Developer/External/document-processor/src/document_processor/pdf/odl/adapter.py#L66)

Important current adapter behavior:

- already reads `background color`
- already reads `row span`
- already reads `column span`
- does not yet read `border top/bottom/left/right`

So the schema should prefer flat keys that can be consumed without a large
adapter rewrite.

## Contract Layers

### Layer 1: Direct Style Fields

These are the fields we actually want to map into `DocIR`.

At the table level:

```json
{
  "type": "table",
  "number of rows": 5,
  "number of columns": 4,
  "grid source": "table-border",
  "grid normalized": true
}
```

At the table-cell level:

```json
{
  "type": "table cell",
  "row number": 1,
  "column number": 1,
  "row span": 1,
  "column span": 1,
  "background color": "#dfe6f7",
  "background source": "cell-matched-fill",
  "background confidence": 0.94,
  "border top": "1px solid",
  "border bottom": "1px solid",
  "border left": "1px solid",
  "border right": "1px solid"
}
```

Interpretation:

- `background color` is the only background key downstream needs for first-pass
  rendering
- `border top/bottom/left/right` should be present only when the extractor is
  confident that the edge exists
- first pass does not require exact color/width/dash for borders; a generic CSS
  token like `"1px solid"` is enough to light up `CellStyleInfo`

### Layer 2: Debug / Provenance Fields

These fields are not required for rendering, but they are needed to understand
why a style was emitted.

At the table level:

```json
{
  "grid row boundaries": [0.0, 18.2, 36.4, 54.6],
  "grid column boundaries": [0.0, 91.0, 182.0, 273.0],
  "logical cell count": 12,
  "covered logical cell count": 12,
  "serialized cell count": 10,
  "spanning cell count": 2
}
```

At the cell level:

```json
{
  "has content": true,
  "content count": 1,
  "fills": [
    {
      "type": "fill region",
      "bounding box": [0, 0, 100, 50],
      "fill color": "#dfe6f7",
      "fill color space": "DeviceRGB",
      "source": "filled-rectangle",
      "confidence": 0.94
    }
  ],
  "border evidence": {
    "top": {"source": "table-grid", "confidence": 0.88},
    "bottom": {"source": "table-grid", "confidence": 0.88},
    "left": {"source": "table-grid", "confidence": 0.88},
    "right": {"source": "table-grid", "confidence": 0.88}
  }
}
```

Interpretation:

- `fills` is for analysis and oracle comparison
- `border evidence` explains why a flat `border top` field exists
- debug fields should stay separate from `kids`

### Layer 3: Deferred Fidelity Fields

These are useful, but not required for first success.

```json
{
  "border top color": "#7f7f7f",
  "border top width pt": 0.5,
  "border top style": "dashed"
}
```

These should be treated as future fields. They are not required to prove that
table styles can be carried into `DocIR`.

## Feasibility Matrix

### Can be done in `opendataloader-pdf` without veraPDF patch

- `number of rows`
- `number of columns`
- `row span`
- `column span`
- `grid source`
- `grid normalized`
- `grid row boundaries`
- `grid column boundaries`
- `logical/covered/serialized cell counts`
- coarse `border top/bottom/left/right` presence derived from normalized
  `TableBorder*` geometry

Reason:

- `TableBorder` already exposes row/column boundaries and normalized cell
  topology
- `TableBorderCell` already exposes row/col/span and cell bounding boxes

This means the first border-presence implementation should stay entirely inside
`opendataloader-pdf`.

### Might be possible in `opendataloader-pdf` only, but must be tested first

- `background color` from already surviving fill-like objects
- `fills` debug array populated from current `LineArtChunk` / bbox artifacts

Reason:

- current filtering may be removing useful large fills in
  [`ContentFilterProcessor.java`](/Users/yoonseo/Developer/External/opendataloader-pdf/java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/processors/ContentFilterProcessor.java#L83)
- before patching veraPDF, we should first test whether table-local fills can be
  recovered by relaxing or making that filter table-aware

### Requires veraPDF patch if the ODL-only experiment is insufficient

- robust `background color`
- reliable `fills` with precise source geometry
- anything that depends on preserving fill primitives instead of bbox-only
  artifacts
- border color / border width / dashed vs solid fidelity

Reason:

- `GraphicsState` carries `fillColor`, `fillColorSpace`, `lineWidth`, `lineCap`
  but that information is not reaching downstream JSON objects in a usable form
- `ChunkParser.processf()` is the likely loss point for filled rectangles and
  filled paths
- current dependency inspection showed bbox-only collapse is happening upstream

## Implementation Order

### Step 1: Border Presence From `TableBorder*`

Owner: `opendataloader-pdf`

Emit:

- `grid row boundaries`
- `grid column boundaries`
- `border top`
- `border bottom`
- `border left`
- `border right`
- optional `border evidence`

Initial policy:

- emit `"1px solid"` for present edges
- do not emit edge color or dash style yet
- prefer missing field over weak false positive

Success condition:

- oracle rerun should show non-zero border recall

### Step 2: Re-run Oracle Comparison

Owner: `document-processor`

Use:

- [`compare_table_style_oracles.py`](/Users/yoonseo/Developer/External/document-processor/scripts/compare_table_style_oracles.py)

Success condition:

- border recall moves above `0.0`
- table matching quality does not regress badly

### Step 3: ODL-only Background Survival Experiment

Owner: `opendataloader-pdf`

Patch candidates:

- make `processBackgrounds(...)` table-aware instead of page-wide destructive
  removal
- add table-cell-local `fills` debug output if surviving objects exist

Success condition:

- at least some oracle-shaded cells produce non-empty `fills`

### Step 4: If Step 3 Fails, Patch veraPDF

Owner: veraPDF dependency fork

Primary target:

- `ChunkParser.processf()`

Likely supporting targets:

- filled rectangle or filled path objects carrying `fillColor`
- serializer path for those objects after they survive parsing

Success condition:

- shaded cells begin producing usable `background color`

## Decision Rule

Do not patch veraPDF for border presence first.

Use this decision ladder:

1. border presence: `opendataloader-pdf` only
2. background preservation: try `opendataloader-pdf` filter changes first
3. only then escalate to veraPDF `processf()` work

This keeps the expensive dependency fork focused on the part that truly needs it:
fill-color preservation.
