# geo-map-exp-extractor

Template-driven visual table extraction from scanned geologic map explanation images.

This project is intended to extract structured tables from scanned geologic map panels using a vision-capable OpenAI model. It is **not** primarily an OCR project. OCR may be added later as an optional quality-control aid, but the core workflow should rely on visual interpretation of headings, symbol boxes, lithology labels, formation lists, wrapped paragraphs, footnotes, and row groupings.

## Initial goals

Build a Python package and CLI that can:

1. Accept an image path, extraction profile, and output path.
2. Read a YAML profile that defines the extraction task and output fields.
3. Build a prompt from a reusable prompt template.
4. Build a strict JSON Schema dynamically from the profile field list.
5. Send the prompt and image to the OpenAI Responses API.
6. Receive structured JSON containing fields, rows, notes, and warnings.
7. Export the rows to CSV using the exact field order from the profile.
8. Save sidecar files for reproducibility:
   - raw JSON response
   - final prompt text
   - manifest JSON
9. Support future segmented-image mode for tall scanned panels.

## Why profiles?

Each extraction type should be controlled by a profile rather than hard-coded in Python. A profile defines the task label, output columns, and special transcription rules.

Example profiles included:

- `profiles/engineering_properties.yml`
- `profiles/water_production.yml`
- `profiles/stratigraphic_column.yml`

## Intended CLI

```bash
geo-map-exp-extractor \
  --image input/water_properties.png \
  --profile profiles/water_production.yml \
  --out output/water_properties.csv
```

Optional future command:

```bash
geo-map-exp-extractor \
  --image input/page.png \
  --task-label "explanation of engineering properties" \
  --fields "MapUnit,Lithology,List of Geologic Formations,Description" \
  --out output/engineering_properties.csv
```

## Recommended output files

For an output CSV named `water_properties.csv`, also write:

```text
water_properties.csv
water_properties.raw.json
water_properties.prompt.txt
water_properties.manifest.json
```

The manifest should include:

- input image path
- input image dimensions
- profile path and profile id
- model name
- timestamp
- output file paths
- package version, if available

## Environment setup

Use Python 3.11 or newer.

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows PowerShell/CMD style may vary
pip install -e .[dev]
```

Create a `.env` file from `.env.example` and add your API key:

```text
OPENAI_API_KEY=your_api_key_here
```

Never commit `.env` or API keys.

## Planned package structure

```text
src/geo_map_exp_extractor/
  __init__.py
  cli.py
  config.py
  image_io.py
  prompt_builder.py
  schema_builder.py
  openai_runner.py
  exporters.py
  qc.py
```

## Design rules

- Preserve wording and punctuation as closely as possible.
- Normalize broken line wraps into readable paragraphs.
- Normalize obvious line-break hyphenation.
- Leave non-applicable fields blank.
- Do not invent missing values.
- Put introduction, explanation, footnote, and descriptive standalone text into separate rows when the profile requests it.
- Preserve geologic symbols and formation abbreviations.
- Keep the output schema strict and predictable.

## Testing expectations

Start with unit tests for:

- profile loading
- prompt building
- dynamic JSON Schema generation
- CSV export
- manifest writing

Integration tests against the OpenAI API should be optional and skipped unless an API key is present.
