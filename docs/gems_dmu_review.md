# GeMS DMU profile and prompt review

Reviewed 2026-10-28 against the [GeMS standard](https://scgeology.github.io/GeMS/)
and the toolbox snapshot documented in [vocabulary provenance](../profiles/vocabularies/README.md).

The new [DMU profile](../profiles/stratigraphic_column_gems_dmu.yml) produces a
reviewable **15-column DMU staging table** from either a stratigraphic column or
a description-of-map-units panel. It does not certify database compliance.
The [main extraction prompt](../prompts/extraction_prompt.md) is now version 2.
The original six-column stratigraphic profile remains available, with clearer
shared-age, heading, and coal-bed instructions.

## Findings in the existing profiles

| Existing behavior | Consequence | Change or recommendation |
| --- | --- | --- |
| Stratigraphic profile contains MapUnit, Name, FullName, Age, Thickness, Description | Useful transcription, but missing DMU structure and technical columns | Use the new DMU profile for GeMS preparation; retain the old profile for stratigraphic supplements |
| Coal-bed guidance exists only in optional notes | Normal runs can omit beds | Move essential guidance into the profile; distinguish unmapped beds from mapped units |
| Main prompt merges headings and neighboring text broadly | Parent units, headings, and headnotes can collapse into one row | Classify row function and follow bracket scope before assigning text |
| Profile booleans were not inserted into the prompt | Turning normalization or standalone text off had no reliable effect | Render all four switches explicitly and describe both states |
| FullName has minimal guidance; unit-explanation profiles request semicolon lists | Output does not consistently identify containing units | New profile requests full lithostratigraphic identification and excludes province/age headings from parent names |
| No controlled vocabularies | Plausible rock names can fail dictionary matching | Supply pinned terms and definitions; constrain API-compatible enums and validate all vocabulary values locally |
| State map profile requests dash-delimited RGB and model-estimated averages | Format differs from GeMS and the numbers are unmeasured | New DMU profile uses comma-delimited, padded RGB only when supplied; separately measure colors before import |

The original unit-explanation/state-map profiles retain their existing column
contracts. They should not be described as ready-to-import GeMS DMUs. The new
profile also handles their DMU-panel use case; provincial headings remain heading
rows rather than an extra DMU column.

## Four fields that need different kinds of evidence

| Field | Implemented extraction policy | Required review |
| --- | --- | --- |
| `HierarchyKey` | Provisional source-order tree with uniformly padded segments, e.g. `001-002`; every row receives a unique key | Parentage, uniqueness, segment width, and lexical sort must agree with the full panel |
| `GeoMaterial` | One exact, case-sensitive term selected using the definitions of all 101 toolbox terms | Check the choice against the full unit description, including subordinate lithologies |
| `GeoMaterialConfidence` | `High`, `Medium`, or `Low`, describing the fit of the selected material category | Keep transcription uncertainty in warnings; it is a separate issue |
| `ParagraphStyle` | Actual toolbox template names such as `DMU-Heading2`, `DMU Unit 1`, and `DMU Headnote Paragraph` | Check row function and indentation rank; supply matching Glossary definitions |

The names are singular `GeoMaterial` and `GeoMaterialConfidence`, and the spelling
is `ParagraphStyle`. The standard's example style names are not a universal enum:
publishers can define styles. This profile deliberately targets the toolbox's
current Word conventions from [GeMS_Definition.py](https://github.com/DOI-USGS/gems-tools-pro/blob/f6008e7a5d0c10a0a56ed631a88d704d43a47c71/Scripts/GeMS_Definition.py)
and [GeMS_DMUtoDocx.py](https://github.com/DOI-USGS/gems-tools-pro/blob/f6008e7a5d0c10a0a56ed631a88d704d43a47c71/Scripts/GeMS_DMUtoDocx.py).

For material confidence, High indicates adequate overall fit; Medium permits
significant minor lithologies not represented by the category; Low indicates poor
fit or insufficient lithologic knowledge. A low-confidence assignment must still
have evidence. An unreadable unit is not automatically a Low material assignment.
For example, `Sandstone and mudstone` is an available composite term, while `Granite`
is not an exact toolbox term. The material decision must use definitions rather
than surface similarity of names. These semantics come from the toolbox's
`GeoMatConfDict`, not a generic AI confidence scale.

### Hierarchy and styles

Three digits per segment is this profile's convention, consistent with the
[DocxToDMU tool's default](https://github.com/DOI-USGS/gems-tools-pro/blob/f6008e7a5d0c10a0a56ed631a88d704d43a47c71/Scripts/GeMS_DocxToDMU.py).
GeMS permits other uniform widths. Headings cannot descend from units; unit rank
is distinct from heading rank and from the total number of key segments.
[GeMS Appendix 3](https://scgeology.github.io/GeMS/appendix-three.html) explains
the order/parentage model and the need for database nulls in unused fields.

This synthetic example illustrates the convention, not extracted source data:

| HierarchyKey | Row | ParagraphStyle |
| --- | --- | --- |
| `001` | Bedrock heading | `DMU-Heading2` |
| `001-001` | A described, unmapped formation | `DMU Unit 1 (1st after heading)` |
| `001-001-001` | Its mapped upper member | `DMU Unit 2` |
| `001-001-002` | Its mapped lower member | `DMU Unit 2` |
| `001-002` | Next formation | `DMU Unit 1` |

The panel title is omitted. An independent headnote gets a separate row in its
visible position; unit-specific notes stay with the unit. Age brackets are not
automatically formation parents. Ambiguous branches remain provisional siblings
at the nearest supported level and generate a warning.

There is a real compatibility wrinkle: the standard says Name is non-null, but
the current Word conversion tools recognize standalone headnotes by blank MapUnit
and Name plus nonblank Description. The profile preserves those headnotes and
flags them for reconciliation. Do not invent a heading just to silence a check;
review whether the publication should attach the headnote to its real heading.

## Completing the table for publication

The complete field list follows the toolbox schema plus its table primary key.
Thickness is preserved in Description, including measurements attached to coal
beds. No source text should be discarded to achieve a narrower column set.
See [GeMS required elements, table 14](https://scgeology.github.io/GeMS/required.html#descriptionofmapunits-nonspatial-table)
for field meaning and nullability.

1. Review Name, FullName, inherited Age, text, brackets, and source order. A column
   cannot supply descriptions that occur only in a separate pamphlet. Reconcile
   map, cross-section, overlay, and other represented units against the full report.
2. Reconcile MapUnit identifiers with the GIS feature classes. Preserve special
   printed glyphs in Label; do not guess an ASCII or font-code conversion.
3. Review all provisional keys and styles, including unmapped parent units and
   named beds. Keep HierarchyKey as text when importing CSV to avoid date conversion.
4. Assign database-unique DescriptionOfMapUnits_ID values and actual
   DescriptionSourceID references into DataSources. Supply the report citation
   there and record extraction/review provenance. A model must not invent these IDs.
5. Reconcile Symbol to the supplied symbol library. Sample scan color numerically
   or use the publisher's palette for AreaFillRGB; the syntax is `nnn,nnn,nnn`,
   with channels from 000 to 255. Lithology hatching is not necessarily map symbology.
6. Review material classification and confidence; provide the unchanged
   GeoMaterialDict and definitions for confidence/style terms in Glossary, with
   source references. Style definitions need the publication's formatting details.
7. Convert staging empty strings to true database nulls where allowed, resolve
   missing mandatory values, check target field lengths without truncation, and
   run the toolbox's Validate Database tool on the assembled database.

These last steps require database and publication context absent from a scan.
The profile intentionally leaves unresolved IDs and unsampled colors blank with
warnings. It cannot guarantee a complete database merely by populating every cell.

## Prompt and API updates

Version 2 uses a layout-first reading pass, source-bound transcription, explicit
permission for profile-requested derivations, and a coverage check. It retains
paragraph boundaries, lexical hyphens, uncertainty, and numeric units. The output
contains concise evidence notes and localized warnings, without a reasoning trace.
All essential field instructions and vocabulary definitions load automatically;
the optional profile-notes checkbox is not required.

The new profile selects `gpt-6-astra`, medium reasoning, `original` image detail,
and a 24,000-token output budget as an initial quality-oriented configuration.
The [current model documentation](https://developers.openai.com/api/docs/models)
identifies Astra as the flagship; this selection is not an extraction benchmark
result. Account availability and real-image quality have not been tested here.
Other profiles retain their model settings, and CLI overrides remain available.

The app already uses Responses with strict Structured Outputs. Field descriptions
and compatible enums now appear in the schema, with all vocabularies enforced by
local validation. GeoMaterial is a string in the API schema because its official
`"Made" or human-engineered land` term contains quotes rejected in strict enum
literals. Its full vocabulary remains in the prompt and saved profile; invalid
terms are rejected before export. The unsupported
`schema_version` parameter was removed from the API format object; internal run
version metadata remains. See [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

`original` detail is now accepted in profile settings. It cannot recover detail
already removed by local resizing: the application still defaults to a 2,800-pixel
long side. Inspect `processed_api_image` in the dry run. For dense panels, a 5,000
pixel long side is a useful starting point to evaluate, not a guaranteed resolution.
Astra original-detail images must remain within the documented 30,000-patch limit;
the current rough token estimator is not model-specific billing guidance.
See [image input detail and sizing](https://developers.openai.com/api/docs/guides/images-vision).

From the repository root, prepare an inspectable request without API usage:

```powershell
geo-map-exp-extractor single --image "panel.png" --profile "profiles/stratigraphic_column_gems_dmu.yml" --max-image-side-px 5000 --dry-run
```

Remove `--dry-run` for extraction. The profile is also selectable in the existing
GUI. Segmented mode is explicitly disabled for this profile: independent crops
can restart keys and lose parent context. A future crop workflow needs a global
reconciliation pass. Batch images likewise produce independent staging tables;
concatenating them does not create a valid combined hierarchy.

## Validation implemented and next work

Implemented offline checks cover dynamic field order, exact controlled terms,
profile switches, malformed vocabularies, portable saved profiles, the API format
shape, and the segmented-mode gate. Saved profiles embed resolved vocabularies,
so a moved run can reconstruct the same prompt and schema.

The current validator enforces structure and allowed values. It does **not** yet
check hierarchy uniqueness/parent existence/sort order, style-to-row consistency,
material-confidence pairing, foreign keys, target lengths, or visual correctness.
Those are mandatory review/import checks, not guarantees of Structured Outputs.
The next implementation should add a deterministic DMU validation report and
reviewed ID/source assignment, followed by measured color sampling and a globally
reconciled overview-plus-crops workflow. Keep these outside the transcription
prompt so invented values cannot pass as observed source content.

Before adopting the new profile at scale, compare the old and new prompts on a
reviewed set of complete and cropped panels. Measure text omissions, character
fidelity, row coverage, parent assignments, exact material accuracy, and fit
confidence separately. Include unlabeled parents, named coal beds, mixed lithology,
special glyphs, headnotes, and nested brackets. No live extraction or accuracy
benchmark was performed as part of this change.
