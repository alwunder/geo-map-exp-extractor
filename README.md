# geo-map-exp-extractor

Template-driven visual table extraction from scanned geologic map explanation images.

The project is built around deliberate, auditable OpenAI API usage. It is not a bulk OCR utility and does not auto-learn by rewriting prompts.

## API key and billing

- Set `OPENAI_API_KEY` in your shell environment or in a repo-local `.env` file.
- Do not store keys in `.env.example`; that file is a template only.
- API billing is separate from ChatGPT subscriptions.
- The app never logs or writes your API key to run artifacts.

Example `.env`:

```env
OPENAI_API_KEY=your_real_key_here
```

## Installation

Use Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

## CLI usage

Single image (default safe workflow):

```bash
geo-map-exp-extractor single \
  --image input/water_properties.png \
  --profile profiles/water_production.yml \
  --out-dir outputs
```

Dry run (no API call):

```bash
geo-map-exp-extractor single \
  --image input/water_properties.png \
  --profile profiles/water_production.yml \
  --out-dir outputs \
  --dry-run
```

Batch mode (explicit confirmation required unless `--yes`):

```bash
geo-map-exp-extractor batch \
  --image-dir input/ \
  --pattern "*.png" \
  --profile profiles/water_production.yml \
  --out-dir outputs
```

## GUI usage

Launch:

```bash
geo-image-extract-gui
```

GUI defaults to one image at a time. Before API runs it shows a charge warning and confirms the call.

Key options:

- `Dry run (no API call)`
- `Force rerun` (ignore cache)
- `Detail` (`high` default, configurable)
- `Include profile notes`

Preview canvas tools:

- `Zoom -` / `Zoom +`: step zoom out/in.
- `Fit 100%`: show image at 1:1 scale.
- `Zoom Extents`: fit full image inside the current canvas viewport.
- `Zoom Width`: fit image to viewport width for top-to-bottom review.
- Left-click and drag on the image to pan.

Results table editing:

- `Row height`: increase or decrease visible row height while keeping panel size fixed.
- Double-click any table cell to open a multiline editor.
- `OK` applies the edit to the table.
- `Cancel` discards the edit.
- `Save corrected` writes table and notes edits to corrected outputs (no API call).

Help:

- `Help` opens this `README.md` inside the app.

You can edit extracted cells locally and save corrected outputs without triggering another model call.

## Request safety and efficiency

Before API execution, the app:

1. Loads and validates profile.
2. Builds final prompt and strict JSON schema.
3. Prepares image (conversion/resizing only when needed).
4. Computes request fingerprint using:
   - processed image hash
   - selected profile and field order
   - final prompt hash
   - model name
   - image detail setting
   - schema version

If the same fingerprint already exists in cache, the app reuses cached data unless rerun is forced.

## Image preparation policy

- Accepts common source formats.
- Converts unsupported formats locally before API submission.
- Converts TIFF/PDF pages to API-friendly images (first page/frame).
- Resizes only when necessary to avoid oversized payloads while preserving readability.
- Uses `high` detail by default for map/explanation panels.

## Run folders and audit trail

Each run creates a timestamped folder under `outputs/` with:

```text
source_image.<ext>
processed_api_image.<ext>
profile.yml
prompt.txt
schema.json
raw_response.json            # omitted for dry run
extracted.json               # omitted for dry run
extracted.csv                # omitted for dry run
corrected.json               # written after review save
corrected.csv                # written after review save
feedback.jsonl
manifest.json
notes.md
segments/                    # when segmented mode is enabled
```

`manifest.json` includes run metadata, hashes, model/detail settings, request fingerprint, fresh-vs-cache mode, usage tokens (when available), estimated cost (when pricing is configured), and output paths.

## Cost awareness

Model pricing is manually configurable in:

- `src/geo_map_exp_extractor/pricing.py`

Post-run cost estimation is based on actual usage fields returned by the API (`input_tokens`, `output_tokens`, `cached_tokens` when present). Pre-run image token numbers are rough estimates only.

## Review/correction workflow

- Manual edits do not call the API.
- `Save corrected` writes `corrected.csv`, `corrected.json`, and `feedback.jsonl`.
- `feedback.jsonl` records row index, field name, old value, new value, status, and optional comment.
- `Promote corrected` copies corrected outputs to `examples/gold/<profile_id>/` for future testing.

## Profile improvement loop

- Profiles stay in `profiles/*.yml`.
- Optional notes file support: `profiles/<profile>.notes.md`.
- Notes are included in prompts only when enabled (`--include-profile-notes` or GUI checkbox).
- Corrections are stored for human review; prompts/profiles are updated intentionally, not automatically.

## Segmentation policy

- Segmentation is not the default.
- If enabled programmatically, segments are created with overlap and each call is logged separately.
- Segmented mode can increase cost because it makes multiple API calls.

## Recommended workflow

1. Dry run.
2. Single extraction.
3. Review/edit.
4. Save corrections.
5. Promote good examples to `examples/gold/`.
6. Only then run confirmed batch processing.

## Testing

Run tests:

```bash
python -m pytest
```

Unit tests cover hashing/caching behavior, dry-run no-call behavior, manifest content, cost estimation, corrected output writing, and feedback JSONL writing with mocked extraction runners.
