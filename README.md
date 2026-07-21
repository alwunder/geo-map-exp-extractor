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

The GUI is a single-image extraction and review workbench. It shows a charge warning and asks for confirmation before a live API call. Reviewing, editing, saving, loading, and promoting results are local operations and do not call the API.

### Input and project controls

- `Image`: the scanned explanation image to extract. Use `Browse...` to select it; the selected image appears in the preview pane.
- `Profile`: the YAML profile that defines the extraction task, output columns and column order, text-preservation rules, and special instructions. Selecting a profile also loads its saved model and processing defaults.
- `Output`: the parent directory in which the app creates a timestamped, auditable run folder.
- `Use profile notes`: appends the optional `profiles/<profile>.notes.md` file to the extraction prompt. Enable this only when those reviewed notes are relevant to the selected profile.
- `Set API key...`: sets an API key override for the current GUI session. `Use .env key` returns to the key loaded from `OPENAI_API_KEY` or the repo-local `.env` file. The key is not written into run artifacts.
- `Open output folder`: opens the active run folder, or the selected output directory before a run exists.
- `Help`: opens this README inside the application.

### API call options

- `Model`: chooses the OpenAI API model used for extraction. `gpt-5.6-sol` is the current default. The full list is `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`, `gpt-5.5-pro`, `gpt-5.6-sol`, and experimental `chat-latest`.
- `Reasoning effort`: controls how much reasoning work the model may perform. `none` or `low` can reduce latency and token use; `medium` is the default; `high` and `xhigh` are intended for difficult scans or layouts and may take longer and cost more. Support can vary by model.
- `Image detail`: controls how the API processes the image. `high` is the recommended default for small geologic text and complex panels; `low` can be faster and cheaper but may lose fine detail; `auto` lets the API choose.
- `Dry run (no API call)`: prepares the image, prompt, schema, hashes, and run artifacts without sending a request or incurring an extraction charge. Use it to verify configuration before a live run.
- `Force re-run`: bypasses an existing matching cache entry and makes a fresh API request. Leave it off to reuse cached output when the image and request settings have not changed.
- `Segmented mode (higher cost)`: divides a tall image into overlapping vertical sections and extracts each section separately. It can improve readability on unusually long or dense panels, but it may make multiple API calls, cost more, and produce rows that need duplicate review near overlaps.
- `Apply maximum output token limit`: sends the adjacent value as `max_output_tokens`, which covers both reasoning and final output tokens. The default is `12000`. Increase it if a large table is truncated; disable the checkbox to omit the cap. Larger limits permit, but do not guarantee, greater token usage.
- `Run extraction`: validates the selections, requests confirmation for a live call, then runs extraction. An identical request may be served from cache unless `Force re-run` is enabled.

Model, reasoning effort, image detail, token-limit settings, profile content, and the processed image all affect the request. Changing them can produce a different cache fingerprint and result.

### Image preview

- `Zoom -` / `Zoom +`: decrease or increase the current magnification in steps.
- `100%`: display one image pixel per screen pixel.
- `Zoom Width`: fit the image to the preview pane's width, useful for reading a tall panel from top to bottom.
- `Zoom Extents`: fit the entire image inside the current preview pane.
- Left-click and drag the image to pan. The scrollbars provide horizontal and vertical navigation when the image is larger than the pane.

The divider between the image and results panes can be dragged to give either side more room.

### Results table and review controls

The table columns come directly from the selected profile and remain in profile order.

- Edit a cell directly in the table. In the fallback table view, double-clicking a cell opens a multiline editor; `OK` applies the edit and `Cancel` discards it.
- `Add row`: inserts a blank row for content the model missed.
- `Delete row`: removes the selected row from the corrected result.
- `Move up` / `Move down`: changes the selected row's position in the output.
- `Auto-fit rows`: adjusts displayed row heights to make wrapped cell content easier to read.
- `Reset widths`: restores automatically calculated column widths after manual resizing.
- `Status`: records the review decision for the selected row. Choose the appropriate review state before applying it.
- `Comment`: stores an optional reviewer note for the selected row.
- `Apply`: saves the selected row's status and comment in the current in-memory project. These review details are written to `feedback.jsonl` when the project is saved.
- `Notes`: stores run-level review notes that are written to `notes.md` and included in the feedback log when saved.

Cell changes and row operations are tracked as review feedback. They do not modify the original `extracted.json` or `extracted.csv` files.

### Saving, reopening, and promoting reviewed work

- `Save project`: writes the current reviewed table to `corrected.json` and `corrected.csv`, saves `notes.md`, and writes review events and final review records to `feedback.jsonl`. This is a local operation with no API call.
- `Load project`: opens an existing run folder containing `manifest.json` and loads `corrected.json` when available, otherwise `extracted.json`. It also restores the source image, notes, and available row review metadata.
- `Promote corrected`: copies previously saved corrected JSON and CSV files into `examples/gold/<profile_id>/` for future regression testing and profile improvement. Save the project before promoting it.

The status bar at the bottom reports the current operation, completion state, cache use, save state, and errors.

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
It records `model`, `reasoning_effort`, `image_detail`, and `max_output_tokens` for every run.

## Model selection guidance

| Use case                                     | Model                     | Reasoning effort | Image detail |
|----------------------------------------------|---------------------------|------------------|--------------|
| Cheap quick test                             | `gpt-5.4-mini`            | low or medium    | high         |
| General extraction                           | `gpt-5.4` or `gpt-5.5`    | medium           | high         |
| Default production extraction                | `gpt-5.6-sol`             | medium           | high         |
| Difficult panel / poor scan / complex layout | `gpt-5.6-sol` or `gpt-5.5` | high             | high         |
| Very difficult audit/review pass             | `gpt-5.5-pro`             | high or xhigh    | high         |

`gpt-5.6-sol` is available in both the GUI model selector and the CLI (`--model gpt-5.6-sol`) and is the current application default. Profiles may specify a different default model, and the GUI applies that setting when a profile is selected.

`chat-latest` is available only as an experimental option; production and repeatable audit runs should prefer fixed model names. Model availability, reasoning support, and pricing depend on the API account and current OpenAI service configuration.

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
