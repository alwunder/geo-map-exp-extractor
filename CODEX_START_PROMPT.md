Build the initial version of this repository.

Use the README.md and AGENTS.md as the primary project instructions.

Implement a Python package named `geo_map_exp_extractor` under `src/` with a Typer CLI named `geo-map-exp-extractor`.

The first working version should:

1. Load a YAML profile.
2. Build a prompt from `prompts/extraction_prompt.md`.
3. Dynamically build a strict JSON Schema from the profile field list.
4. Send one image and the prompt to the OpenAI Responses API.
5. Return structured JSON with fields, rows, notes, and warnings.
6. Export rows to CSV.
7. Save sidecar files:
   - raw JSON response
   - final prompt text
   - manifest JSON
8. Include unit tests for profile loading, prompt building, schema generation, CSV export, and manifest writing.

Keep the implementation modular. Do not add a GUI yet. Do not make OCR the primary extraction path.
