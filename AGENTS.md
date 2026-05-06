# AGENTS.md

Guidance for Codex and other coding agents working in this repository.

## Project purpose

This repo builds a Python tool for template-driven visual table extraction from scanned geologic map explanation images. The tool should use model vision and structured outputs, not conventional OCR as the primary extraction method.

## User context

The primary use case is extracting structured tables from geologic map explanation panels, including engineering properties, water-production properties, stratigraphic columns, and related scanned cartographic/geologic text blocks.

The tool should be useful for GIS/geologic publication workflows where preserving descriptive text is important.

## Coding standards

- Use Python 3.11+.
- Use `pathlib` for paths.
- Keep modules small and focused.
- Prefer dataclasses or Pydantic models for configuration/profile objects.
- Use type hints throughout.
- Avoid global mutable state.
- Keep OpenAI API logic isolated in `openai_runner.py`.
- Keep CLI concerns isolated in `cli.py`.
- Keep prompt construction isolated in `prompt_builder.py`.
- Keep schema construction isolated in `schema_builder.py`.
- Keep file export logic isolated in `exporters.py`.

## OpenAI/API rules

- Read `OPENAI_API_KEY` from the environment.
- Do not hard-code model names deeply in code; expose a CLI option and provide a reasonable default.
- Do not commit API keys or `.env` files.
- Use structured JSON output so downstream code receives predictable rows.
- Treat the model output as data that still needs validation.

## Extraction behavior

The tool should:

- Preserve wording, punctuation, geologic symbols, and formation abbreviations as closely as possible.
- Join broken line wraps into readable paragraphs.
- Normalize obvious line-break hyphenation.
- Leave fields blank when they do not apply.
- Avoid inventing rows or values.
- Include introduction, explanation, footnote, or other standalone descriptive text as separate rows when the selected profile requests it.
- Preserve output field order exactly as defined in the profile.

## Profiles

Profiles live in `profiles/` and should be YAML files. A profile defines:

- `id`
- `name`
- `task_label`
- `fields`
- `include_intro_footnotes`
- `preserve_wording`
- `normalize_line_breaks`
- `normalize_hyphenated_line_breaks`
- `special_instructions`

Do not hard-code field names in the extraction code. Field names should come from the profile.

## Testing

Before marking work complete, run:

```bash
python -m pytest
```

Also run formatting/linting if those tools are configured.

Tests should not require a live OpenAI API call by default. Mock API responses or isolate live tests behind an environment-variable gate.

## Good first implementation order

1. Create package structure under `src/geo_map_exp_extractor/`.
2. Implement profile loading.
3. Implement prompt builder.
4. Implement dynamic JSON Schema builder.
5. Implement CSV and sidecar file exporters.
6. Implement OpenAI runner.
7. Implement CLI.
8. Add tests.
9. Add optional segmented-image mode after the basic workflow works.

## Avoid

- Do not build a GUI first.
- Do not make OCR the main path.
- Do not skip validation of model output.
- Do not silently reorder fields.
- Do not bury user-editable task instructions inside Python code.
