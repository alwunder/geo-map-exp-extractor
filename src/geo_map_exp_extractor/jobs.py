"""Review-workbench extraction job orchestration."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any

from geo_map_exp_extractor import __version__
from geo_map_exp_extractor.config import load_profile
from geo_map_exp_extractor.exporters import write_csv, write_json, write_prompt
from geo_map_exp_extractor.image_io import (
    PreparedImage,
    create_image_segments,
    prepare_image_for_api,
)
from geo_map_exp_extractor.openai_runner import (
    ExtractionResult,
    ExtractionValidationError,
    UsageSummary,
    run_extraction,
)
from geo_map_exp_extractor.pricing import estimate_cost_usd
from geo_map_exp_extractor.prompt_builder import build_prompt, read_profile_notes
from geo_map_exp_extractor.schema_builder import build_response_schema, build_text_format
from geo_map_exp_extractor.settings import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_IMAGE_DETAIL,
    DEFAULT_MAX_IMAGE_SIDE_PX,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    REQUEST_CACHE_DIR,
    SCHEMA_VERSION,
)

ExtractionRunner = Callable[..., ExtractionResult]


@dataclass(frozen=True)
class ExtractionJobResult:
    """Paths and data produced by one review-workbench extraction run."""

    run_id: str
    run_dir: Path
    fields: list[str]
    rows: list[dict[str, Any]]
    manifest: dict[str, Any]
    output_paths: dict[str, Path]
    dry_run: bool
    cache_reused: bool
    request_fingerprint: str
    rough_image_tokens: int
    usage: dict[str, int | None]
    elapsed_seconds: float
    estimated_cost_usd: float | None
    warnings: list[str]


@dataclass(frozen=True)
class LoadedReviewProject:
    """Validated project data resolved beneath the selected run folder."""

    run_dir: Path
    manifest: dict[str, Any]
    output_paths: dict[str, Path]
    fields: list[str]
    rows: list[dict[str, Any]]
    original_rows: list[dict[str, Any]]
    feedback_records: list[dict[str, Any]]
    notes_text: str
    source_image_path: Path | None
    processed_image_path: Path | None


class ProjectLoadError(ValueError):
    """Raised when a selected review project cannot be loaded safely."""


def _default_prompt_template_path() -> Path:
    """Return the repo-local prompt template path used by the CLI."""

    return Path(__file__).resolve().parents[2] / "prompts" / "extraction_prompt.md"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _cache_root() -> Path:
    return _repo_root() / REQUEST_CACHE_DIR


def _make_run_id(timestamp: datetime) -> str:
    """Create a filesystem-friendly UTC run identifier."""

    return timestamp.strftime("%Y%m%dT%H%M%SZ")


def _unique_run_dir(output_dir: Path, run_id: str) -> Path:
    """Return a run directory path, suffixing on rare timestamp collisions."""

    candidate = output_dir / run_id
    counter = 2
    while candidate.exists():
        candidate = output_dir / f"{run_id}-{counter}"
        counter += 1
    return candidate


def _portable_path(path: str | Path, *, relative_to: str | Path | None = None) -> str:
    candidate = Path(path)
    if relative_to is not None:
        try:
            candidate = candidate.resolve().relative_to(Path(relative_to).resolve())
        except ValueError:
            pass
    return candidate.as_posix()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(data: dict[str, Any]) -> str:
    return _sha256_text(json.dumps(data, ensure_ascii=False, sort_keys=True))


def _request_fingerprint(
    *,
    processed_image_hash: str,
    profile_id: str,
    profile_fields: Sequence[str],
    prompt_hash: str,
    model: str,
    reasoning_effort: str,
    image_detail: str,
    max_output_tokens: int | None,
    schema_version: str,
) -> str:
    """Build stable fingerprint for cache lookup and run de-duplication."""

    payload = {
        "processed_image_hash": processed_image_hash,
        "profile_id": profile_id,
        "profile_fields": list(profile_fields),
        "prompt_hash": prompt_hash,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "image_detail": image_detail,
        "max_output_tokens": max_output_tokens,
        "schema_version": schema_version,
    }
    return _sha256_json(payload)


def _cache_file_path(request_hash: str) -> Path:
    return _cache_root() / f"{request_hash}.json"


def _save_cache_payload(
    request_hash: str,
    *,
    extraction: ExtractionResult,
) -> None:
    payload = {
        "raw_response": extraction.raw_response,
        "data": extraction.data,
        "usage": _usage_to_dict(extraction),
        "incomplete_max_output_tokens": extraction.incomplete_max_output_tokens,
        "token_limit_warning": extraction.token_limit_warning,
    }
    cache_file = _cache_file_path(request_hash)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_cache_payload(request_hash: str) -> dict[str, Any] | None:
    cache_file = _cache_file_path(request_hash)
    if not cache_file.exists():
        return None
    loaded = json.loads(cache_file.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return None
    return loaded


def review_output_paths(run_dir: str | Path) -> dict[str, Path]:
    """Return the standard review-workbench output file paths for a run folder."""

    path = Path(run_dir)
    return {
        "source_image": path / "source_image",
        "processed_image": path / "processed_api_image",
        "profile": path / "profile.yml",
        "prompt": path / "prompt.txt",
        "schema": path / "schema.json",
        "raw_response": path / "raw_response.json",
        "extracted_csv": path / "extracted.csv",
        "extracted_json": path / "extracted.json",
        "corrected_csv": path / "corrected.csv",
        "corrected_json": path / "corrected.json",
        "manifest": path / "manifest.json",
        "notes": path / "notes.md",
        "feedback": path / "feedback.jsonl",
    }


def _manifest_pure_path(value: str) -> PurePath:
    """Interpret manifest paths from either Windows or POSIX systems."""

    windows_path = PureWindowsPath(value)
    if windows_path.is_absolute():
        return windows_path
    return PurePosixPath(value.replace("\\", "/"))


def _safe_project_path(run_dir: Path, relative_path: PurePath, *, label: str) -> Path:
    candidate = run_dir.joinpath(*relative_path.parts).resolve()
    try:
        candidate.relative_to(run_dir)
    except ValueError as exc:
        msg = f"Manifest path for {label!r} escapes the selected project folder: {relative_path}"
        raise ProjectLoadError(msg) from exc
    return candidate


def _legacy_manifest_root(manifest_outputs: dict[str, Any]) -> PurePath | None:
    value = manifest_outputs.get("manifest")
    if not isinstance(value, str) or not value.strip():
        return None
    path = _manifest_pure_path(value)
    return path.parent if path.is_absolute() else None


def _resolve_manifest_output_paths(
    run_dir: Path,
    manifest_outputs: dict[str, Any],
) -> dict[str, Path]:
    """Resolve output paths locally without ever following legacy external paths."""

    defaults = review_output_paths(run_dir)
    resolved = dict(defaults)
    legacy_root = _legacy_manifest_root(manifest_outputs)

    for key, default_path in defaults.items():
        if key == "manifest":
            value = manifest_outputs.get(key)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    msg = "manifest.json output_paths.manifest must be a non-empty path string."
                    raise ProjectLoadError(msg)
                manifest_path = _manifest_pure_path(value)
                if not manifest_path.is_absolute():
                    _safe_project_path(
                        run_dir,
                        manifest_path,
                        label="output_paths.manifest",
                    )
            resolved[key] = default_path.resolve()
            continue
        value = manifest_outputs.get(key)
        if value is None:
            resolved[key] = default_path.resolve()
            continue
        if not isinstance(value, str) or not value.strip():
            msg = f"manifest.json output_paths.{key} must be a non-empty path string."
            raise ProjectLoadError(msg)

        manifest_path = _manifest_pure_path(value)
        if not manifest_path.is_absolute():
            resolved[key] = _safe_project_path(run_dir, manifest_path, label=f"output_paths.{key}")
            continue

        local_candidates: list[Path] = []
        if legacy_root is not None and type(manifest_path) is type(legacy_root):
            try:
                legacy_relative = manifest_path.relative_to(legacy_root)
            except ValueError:
                pass
            else:
                local_candidates.append(
                    _safe_project_path(run_dir, legacy_relative, label=f"output_paths.{key}")
                )
        local_candidates.append(
            _safe_project_path(
                run_dir,
                PurePosixPath(manifest_path.name),
                label=f"output_paths.{key}",
            )
        )
        local_candidates.append(default_path.resolve())
        resolved[key] = next((path for path in local_candidates if path.exists()), local_candidates[0])

    return resolved


def _resolve_manifest_image_path(
    *,
    run_dir: Path,
    manifest: dict[str, Any],
    output_paths: dict[str, Path],
    output_key: str,
    manifest_key: str,
    filename_stem: str,
    legacy_root: PurePath | None,
) -> Path | None:
    """Prefer a run-local image copy and safely rebase legacy image paths."""

    recorded_value = manifest.get(manifest_key)
    recorded_candidate: Path | None = None
    if recorded_value is not None:
        if not isinstance(recorded_value, str) or not recorded_value.strip():
            msg = f"manifest.json field {manifest_key!r} must be a non-empty path string."
            raise ProjectLoadError(msg)
        recorded_path = _manifest_pure_path(recorded_value)
        if not recorded_path.is_absolute():
            recorded_candidate = _safe_project_path(run_dir, recorded_path, label=manifest_key)
        else:
            candidates: list[Path] = []
            if legacy_root is not None and type(recorded_path) is type(legacy_root):
                try:
                    relative_path = recorded_path.relative_to(legacy_root)
                except ValueError:
                    pass
                else:
                    candidates.append(_safe_project_path(run_dir, relative_path, label=manifest_key))
            candidates.append(
                _safe_project_path(
                    run_dir,
                    PurePosixPath(recorded_path.name),
                    label=manifest_key,
                )
            )
            recorded_candidate = next((path for path in candidates if path.exists()), candidates[0])

    candidates = [output_paths[output_key]]
    candidates.extend(sorted(run_dir.glob(f"{filename_stem}.*")))
    if recorded_candidate is not None:
        candidates.append(recorded_candidate)
    for candidate in candidates:
        safe_candidate = _safe_project_path(
            run_dir,
            PurePosixPath(candidate.relative_to(run_dir).as_posix()),
            label=manifest_key,
        )
        if safe_candidate.is_file():
            output_paths[output_key] = safe_candidate
            return safe_candidate
    return None


def _read_project_json(path: Path, *, description: str) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{description} contains malformed JSON: {exc.msg} (line {exc.lineno})."
        raise ProjectLoadError(msg) from exc
    except OSError as exc:
        msg = f"Could not read {description} at {path}: {exc}"
        raise ProjectLoadError(msg) from exc
    if not isinstance(loaded, dict):
        raise ProjectLoadError(f"{description} must contain a JSON object.")
    return loaded


def load_review_project(run_dir: str | Path) -> LoadedReviewProject:
    """Load a review project with the selected run folder as its authority boundary."""

    selected_dir = Path(run_dir).resolve()
    if not selected_dir.is_dir():
        raise ProjectLoadError(f"Selected project folder does not exist: {selected_dir}")

    manifest_path = selected_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ProjectLoadError("Selected project folder does not contain manifest.json.")
    manifest = _read_project_json(manifest_path, description="manifest.json")

    manifest_outputs = manifest.get("output_paths", {})
    if not isinstance(manifest_outputs, dict):
        raise ProjectLoadError("manifest.json field 'output_paths' must be a JSON object.")
    output_paths = _resolve_manifest_output_paths(selected_dir, manifest_outputs)
    legacy_root = _legacy_manifest_root(manifest_outputs)

    source_image_path = _resolve_manifest_image_path(
        run_dir=selected_dir,
        manifest=manifest,
        output_paths=output_paths,
        output_key="source_image",
        manifest_key="source_image_path",
        filename_stem="source_image",
        legacy_root=legacy_root,
    )
    processed_image_path = _resolve_manifest_image_path(
        run_dir=selected_dir,
        manifest=manifest,
        output_paths=output_paths,
        output_key="processed_image",
        manifest_key="processed_image_path",
        filename_stem="processed_api_image",
        legacy_root=legacy_root,
    )

    corrected_json = output_paths["corrected_json"]
    extracted_json = output_paths["extracted_json"]
    data_path = corrected_json if corrected_json.is_file() else extracted_json
    if not data_path.is_file():
        raise ProjectLoadError(
            "Selected project folder does not contain required corrected.json or extracted.json data."
        )
    loaded_data = _read_project_json(data_path, description=data_path.name)
    fields_value = loaded_data.get("fields")
    rows_value = loaded_data.get("rows")
    if not isinstance(fields_value, list) or not all(
        isinstance(field, str) for field in fields_value
    ):
        raise ProjectLoadError(f"{data_path.name} field 'fields' must be a list of strings.")
    if not isinstance(rows_value, list) or not all(isinstance(row, dict) for row in rows_value):
        raise ProjectLoadError(f"{data_path.name} field 'rows' must be a list of objects.")

    fields = list(fields_value)
    rows = [{field: row.get(field, "") for field in fields} for row in rows_value]
    original_rows: list[dict[str, Any]] = []
    if extracted_json.is_file():
        extracted_data = _read_project_json(extracted_json, description=extracted_json.name)
        extracted_rows = extracted_data.get("rows")
        if not isinstance(extracted_rows, list) or not all(
            isinstance(row, dict) for row in extracted_rows
        ):
            raise ProjectLoadError("extracted.json field 'rows' must be a list of objects.")
        original_rows = [
            {field: row.get(field, "") for field in fields} for row in extracted_rows
        ]
    if not original_rows:
        original_rows = [dict(row) for row in rows]
    if len(original_rows) < len(rows):
        original_rows.extend(
            [{field: "" for field in fields} for _ in range(len(rows) - len(original_rows))]
        )
    original_rows = original_rows[: len(rows)]

    feedback_records: list[dict[str, Any]] = []
    feedback_path = output_paths["feedback"]
    if feedback_path.is_file():
        try:
            feedback_lines = feedback_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ProjectLoadError(f"Could not read feedback file {feedback_path}: {exc}") from exc
        for line in feedback_lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                feedback_records.append(item)

    notes_text = ""
    notes_path = output_paths["notes"]
    if notes_path.is_file():
        try:
            notes_text = notes_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProjectLoadError(f"Could not read notes file {notes_path}: {exc}") from exc

    return LoadedReviewProject(
        run_dir=selected_dir,
        manifest=manifest,
        output_paths=output_paths,
        fields=fields,
        rows=rows,
        original_rows=original_rows,
        feedback_records=feedback_records,
        notes_text=notes_text,
        source_image_path=source_image_path,
        processed_image_path=processed_image_path,
    )


def _copy_inputs(image_path: Path, profile_path: Path, paths: dict[str, Path]) -> dict[str, Path]:
    """Copy source inputs into the run folder and return their concrete paths."""

    source_image_path = paths["source_image"].with_suffix(image_path.suffix)
    shutil.copy2(image_path, source_image_path)
    shutil.copy2(profile_path, paths["profile"])
    paths["source_image"] = source_image_path
    return paths


def _usage_to_dict(extraction: ExtractionResult | None) -> dict[str, int | None]:
    if extraction is None or extraction.usage is None:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cached_tokens": None,
            "reasoning_tokens": None,
        }
    return {
        "input_tokens": extraction.usage.input_tokens,
        "output_tokens": extraction.usage.output_tokens,
        "total_tokens": extraction.usage.total_tokens,
        "cached_tokens": extraction.usage.cached_tokens,
        "reasoning_tokens": extraction.usage.reasoning_tokens,
    }


def _usage_from_dict(payload: Any) -> UsageSummary | None:
    if not isinstance(payload, dict):
        return None
    return UsageSummary(
        input_tokens=payload.get("input_tokens") if isinstance(payload.get("input_tokens"), int) else None,
        output_tokens=payload.get("output_tokens") if isinstance(payload.get("output_tokens"), int) else None,
        total_tokens=payload.get("total_tokens") if isinstance(payload.get("total_tokens"), int) else None,
        cached_tokens=payload.get("cached_tokens") if isinstance(payload.get("cached_tokens"), int) else None,
        reasoning_tokens=payload.get("reasoning_tokens")
        if isinstance(payload.get("reasoning_tokens"), int)
        else None,
    )


def build_review_manifest(
    *,
    run_id: str,
    timestamp: str,
    prepared_image: PreparedImage,
    profile_id: str,
    profile_fields: Sequence[str],
    prompt_hash: str,
    schema_hash: str,
    model: str,
    reasoning_effort: str,
    image_detail: str,
    max_output_tokens: int | None,
    schema_version: str,
    request_hash: str,
    api_call_mode: str,
    usage: dict[str, int | None],
    elapsed_seconds: float,
    estimated_cost_usd: float | None,
    segmented_mode: bool,
    segment_settings: dict[str, int] | None,
    segment_calls: list[dict[str, Any]],
    warnings: list[str],
    output_paths: dict[str, Path],
) -> dict[str, Any]:
    """Build manifest metadata for one extraction run."""

    run_dir = output_paths["manifest"].parent
    return {
        "run_id": run_id,
        "timestamp": timestamp,
        "source_image_path": _portable_path(output_paths["source_image"], relative_to=run_dir),
        "source_image_hash": prepared_image.source_hash,
        "original_image_dimensions": {
            "width": prepared_image.source_width,
            "height": prepared_image.source_height,
        },
        "processed_image_path": _portable_path(
            output_paths["processed_image"], relative_to=run_dir
        ),
        "processed_image_hash": prepared_image.processed_hash,
        "processed_image_dimensions": {
            "width": prepared_image.processed_width,
            "height": prepared_image.processed_height,
        },
        "profile_id": profile_id,
        "profile_fields": list(profile_fields),
        "prompt_hash": prompt_hash,
        "schema_hash": schema_hash,
        "schema_version": schema_version,
        "request_fingerprint": request_hash,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "image_detail": image_detail,
        "max_output_tokens": max_output_tokens,
        "api_call_mode": api_call_mode,
        "segmented_mode": segmented_mode,
        "segment_settings": segment_settings,
        "segment_calls": segment_calls,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cached_tokens": usage.get("cached_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "elapsed_seconds": elapsed_seconds,
        "estimated_cost_usd": estimated_cost_usd,
        "warnings": warnings,
        "rough_image_tokens_estimate": prepared_image.rough_image_tokens,
        "image_preparation": {
            "was_converted": prepared_image.was_converted,
            "was_resized": prepared_image.was_resized,
        },
        "output_paths": {
            key: _portable_path(path, relative_to=run_dir) for key, path in output_paths.items()
        },
        "package_version": __version__,
    }


def write_corrected_outputs(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    csv_path: str | Path,
    json_path: str | Path,
) -> None:
    """Write reviewer-corrected rows as CSV and structured JSON."""

    normalized_rows = [{field: row.get(field, "") for field in fields} for row in rows]
    write_csv(normalized_rows, fields, csv_path)
    write_json({"fields": fields, "rows": normalized_rows}, json_path)


def write_feedback_jsonl(records: Sequence[dict[str, Any]], output_path: str | Path) -> Path:
    """Write correction-tracking records as JSON Lines."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def build_feedback_record(
    *,
    run_id: str,
    row_index: int | None,
    field_name: str,
    original_value: Any,
    corrected_value: Any,
    status: str = "corrected",
    comment: str = "",
    profile_id: str = "",
    image: str = "",
    event_type: str = "cell_feedback",
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build one lightweight correction-tracking JSON object."""

    captured_at = timestamp or datetime.now(timezone.utc).isoformat()
    model_value = "" if original_value is None else str(original_value)
    final_value = "" if corrected_value is None else str(corrected_value)
    return {
        "timestamp": captured_at,
        "event_type": event_type,
        "run_id": run_id,
        "profile_id": profile_id,
        "image": image,
        "row_index": row_index,
        "field": field_name,
        "model_value": model_value,
        "corrected_value": final_value,
        # Backward-compatible aliases for older parsers/tests.
        "field_name": field_name,
        "original_value": model_value,
        "status": status,
        "comment": comment,
    }


def promote_corrected_to_gold(
    *,
    run_id: str,
    profile_id: str,
    corrected_json_path: str | Path,
    corrected_csv_path: str | Path,
    destination_root: str | Path | None = None,
) -> Path:
    """Copy corrected outputs into examples/gold/<profile_id>/ for curated tests."""

    root = Path(destination_root) if destination_root else _repo_root() / "examples" / "gold"
    profile_folder = root / profile_id
    profile_folder.mkdir(parents=True, exist_ok=True)
    target_prefix = profile_folder / run_id
    json_target = target_prefix.with_suffix(".corrected.json")
    csv_target = target_prefix.with_suffix(".corrected.csv")
    shutil.copy2(corrected_json_path, json_target)
    shutil.copy2(corrected_csv_path, csv_target)
    return profile_folder


def run_extraction_job(
    *,
    image_path: str | Path,
    profile_path: str | Path,
    output_dir: str | Path,
    api_key: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    image_detail: str | None = None,
    max_output_tokens: int | None = None,
    use_max_output_tokens_limit: bool = True,
    prompt_template_path: str | Path | None = None,
    include_profile_notes: bool = False,
    use_cache: bool = True,
    force_rerun: bool = False,
    dry_run: bool = False,
    max_image_side_px: int = DEFAULT_MAX_IMAGE_SIDE_PX,
    segmented_mode: bool = False,
    segment_height_px: int = 1800,
    segment_overlap_px: int = 200,
    extraction_runner: ExtractionRunner = run_extraction,
    timestamp: datetime | None = None,
) -> ExtractionJobResult:
    """Run extraction and create a timestamped review folder with all sidecar files."""

    image = Path(image_path)
    if image.is_dir():
        msg = "Folder paths are not allowed for single-image extraction."
        raise ValueError(msg)
    profile_file = Path(profile_path)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    created_at = timestamp or datetime.now(timezone.utc)
    started_at = datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    timestamp_text = created_at.astimezone(timezone.utc).isoformat()
    run_id = _make_run_id(created_at.astimezone(timezone.utc))
    run_dir = _unique_run_dir(destination, run_id)
    run_dir.mkdir(parents=True)

    paths = review_output_paths(run_dir)
    paths = _copy_inputs(image, profile_file, paths)

    profile = load_profile(profile_file)
    selected_model = model if model is not None else profile.model
    selected_reasoning_effort = (
        reasoning_effort if reasoning_effort is not None else profile.reasoning_effort
    )
    selected_image_detail = image_detail if image_detail is not None else profile.image_detail
    selected_max_output_tokens = None
    if use_max_output_tokens_limit:
        selected_max_output_tokens = (
            max_output_tokens if max_output_tokens is not None else profile.max_output_tokens
        )
    if not selected_model:
        selected_model = DEFAULT_MODEL
    if not selected_reasoning_effort:
        selected_reasoning_effort = DEFAULT_REASONING_EFFORT
    if not selected_image_detail:
        selected_image_detail = DEFAULT_IMAGE_DETAIL
    if use_max_output_tokens_limit and selected_max_output_tokens is None:
        selected_max_output_tokens = DEFAULT_MAX_OUTPUT_TOKENS

    prompt_template = (
        Path(prompt_template_path) if prompt_template_path else _default_prompt_template_path()
    )
    profile_notes = read_profile_notes(profile_file) if include_profile_notes else None
    prompt = build_prompt(
        profile,
        prompt_template,
        include_profile_notes=include_profile_notes,
        profile_notes=profile_notes,
    )
    schema = build_response_schema(profile)
    text_format = build_text_format(profile)

    write_prompt(prompt, paths["prompt"])
    write_json(schema, paths["schema"])
    paths["notes"].write_text("# Review notes\n\n", encoding="utf-8")
    write_feedback_jsonl([], paths["feedback"])

    prepared = prepare_image_for_api(
        image_path=image,
        output_dir=run_dir,
        detail=selected_image_detail,
        max_side_px=max_image_side_px,
    )
    paths["processed_image"] = prepared.processed_path

    prompt_hash = _sha256_text(prompt)
    schema_hash = _sha256_json(schema)
    request_hash = _request_fingerprint(
        processed_image_hash=prepared.processed_hash,
        profile_id=profile.id,
        profile_fields=profile.fields,
        prompt_hash=prompt_hash,
        model=selected_model,
        reasoning_effort=selected_reasoning_effort,
        image_detail=selected_image_detail,
        max_output_tokens=selected_max_output_tokens,
        schema_version=(
            f"{SCHEMA_VERSION}|seg:{int(segmented_mode)}|h:{segment_height_px}|o:{segment_overlap_px}"
        ),
    )

    rows: list[dict[str, Any]] = []
    extraction: ExtractionResult | None = None
    cache_reused = False
    api_call_mode = "dry_run" if dry_run else "fresh_api_call"
    segment_calls: list[dict[str, Any]] = []
    run_warnings: list[str] = []

    if not dry_run:
        segment_items: list[tuple[int, Path, str, int]] = [
            (1, prepared.processed_path, prepared.processed_hash, prepared.rough_image_tokens)
        ]
        if segmented_mode:
            segment_dir = run_dir / "segments"
            segments = create_image_segments(
                image_path=prepared.processed_path,
                output_dir=segment_dir,
                segment_height_px=segment_height_px,
                overlap_px=segment_overlap_px,
            )
            segment_items = [
                (
                    segment.index,
                    segment.path,
                    segment.sha256,
                    segment.width * segment.height,
                )
                for segment in segments
            ]

        all_rows: list[dict[str, Any]] = []
        segment_raw_payloads: list[dict[str, Any]] = []
        usage_totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
        }
        cache_modes: set[str] = set()

        for index, segment_path, segment_hash, _ in segment_items:
            segment_request_hash = _request_fingerprint(
                processed_image_hash=segment_hash,
                profile_id=profile.id,
                profile_fields=profile.fields,
                prompt_hash=prompt_hash,
                model=selected_model,
                reasoning_effort=selected_reasoning_effort,
                image_detail=selected_image_detail,
                max_output_tokens=selected_max_output_tokens,
                schema_version=(
                    f"{SCHEMA_VERSION}|seg:{int(segmented_mode)}|h:{segment_height_px}|o:{segment_overlap_px}"
                ),
            )
            cached_payload = (
                _load_cache_payload(segment_request_hash) if use_cache and not force_rerun else None
            )
            if cached_payload is not None:
                cache_reused = True
                cache_modes.add("cache_reuse")
                raw_response = cached_payload.get("raw_response", {})
                data = cached_payload.get("data", {})
                segment_extraction = ExtractionResult(
                    data=data if isinstance(data, dict) else {},
                    raw_response=raw_response if isinstance(raw_response, dict) else {},
                    usage=_usage_from_dict(cached_payload.get("usage")),
                    incomplete_max_output_tokens=bool(cached_payload.get("incomplete_max_output_tokens")),
                    token_limit_warning=(
                        cached_payload.get("token_limit_warning")
                        if isinstance(cached_payload.get("token_limit_warning"), str)
                        else None
                    ),
                )
            else:
                cache_modes.add("fresh_api_call")
                try:
                    segment_extraction = extraction_runner(
                        image_path=segment_path,
                        prompt=prompt,
                        profile=profile,
                        model=selected_model,
                        api_key=api_key,
                        reasoning_effort=selected_reasoning_effort,
                        image_detail=selected_image_detail,
                        max_output_tokens=selected_max_output_tokens,
                        schema=text_format,
                    )
                except ExtractionValidationError as exc:
                    write_json(exc.raw_response, paths["raw_response"])
                    raise RuntimeError(f"Structured output validation failed: {exc}") from exc
                _save_cache_payload(segment_request_hash, extraction=segment_extraction)

            segment_rows = segment_extraction.data.get("rows", [])
            if isinstance(segment_rows, list):
                all_rows.extend(segment_rows)
            segment_raw_payloads.append(segment_extraction.raw_response)
            segment_usage = _usage_to_dict(segment_extraction)
            for key in usage_totals:
                value = segment_usage.get(key)
                if isinstance(value, int):
                    usage_totals[key] += value
            segment_calls.append(
                {
                    "segment_index": index,
                    "segment_path": _portable_path(segment_path, relative_to=run_dir),
                    "request_fingerprint": segment_request_hash,
                    "api_call_mode": "cache_reuse" if cached_payload is not None else "fresh_api_call",
                    "usage": segment_usage,
                    "token_limit_warning": segment_extraction.token_limit_warning,
                }
            )
            if segment_extraction.token_limit_warning:
                run_warnings.append(
                    f"Segment {index}: {segment_extraction.token_limit_warning}"
                )

        rows = all_rows
        extraction = ExtractionResult(
            data={
                "fields": profile.fields,
                "rows": rows,
                "notes": [],
                "warnings": run_warnings,
            },
            raw_response=(
                segment_raw_payloads[0]
                if len(segment_raw_payloads) == 1
                else {"segmented": True, "segments": segment_raw_payloads}
            ),
            usage=UsageSummary(
                input_tokens=usage_totals["input_tokens"] or None,
                output_tokens=usage_totals["output_tokens"] or None,
                total_tokens=usage_totals["total_tokens"] or None,
                cached_tokens=usage_totals["cached_tokens"] or None,
                reasoning_tokens=usage_totals.get("reasoning_tokens") or None,
            ),
        )

        if cache_modes == {"cache_reuse"}:
            api_call_mode = "cache_reuse"
        elif cache_modes == {"fresh_api_call"}:
            api_call_mode = "fresh_api_call"
        else:
            api_call_mode = "mixed"

        fields = profile.fields
        write_json(extraction.raw_response, paths["raw_response"])
        write_csv(rows, fields, paths["extracted_csv"])
        write_json(
            {
                "fields": fields,
                "rows": rows,
                "notes": extraction.data.get("notes", []),
                "warnings": extraction.data.get("warnings", []),
            },
            paths["extracted_json"],
        )

    usage = _usage_to_dict(extraction)
    elapsed_seconds = max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds())
    estimated_cost = estimate_cost_usd(
        model=selected_model,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cached_tokens=usage.get("cached_tokens"),
    )
    manifest = build_review_manifest(
        run_id=run_dir.name,
        timestamp=timestamp_text,
        prepared_image=prepared,
        profile_id=profile.id,
        profile_fields=profile.fields,
        prompt_hash=prompt_hash,
        schema_hash=schema_hash,
        model=selected_model,
        reasoning_effort=selected_reasoning_effort,
        image_detail=selected_image_detail,
        max_output_tokens=selected_max_output_tokens,
        schema_version=SCHEMA_VERSION,
        request_hash=request_hash,
        api_call_mode=api_call_mode,
        usage=usage,
        elapsed_seconds=elapsed_seconds,
        estimated_cost_usd=estimated_cost,
        segmented_mode=segmented_mode,
        segment_settings=(
            {"segment_height_px": segment_height_px, "segment_overlap_px": segment_overlap_px}
            if segmented_mode
            else None
        ),
        segment_calls=segment_calls,
        warnings=run_warnings,
        output_paths=paths,
    )
    write_json(manifest, paths["manifest"])

    return ExtractionJobResult(
        run_id=run_dir.name,
        run_dir=run_dir,
        fields=profile.fields,
        rows=[{field: row.get(field, "") for field in profile.fields} for row in rows],
        manifest=manifest,
        output_paths=paths,
        dry_run=dry_run,
        cache_reused=cache_reused,
        request_fingerprint=request_hash,
        rough_image_tokens=prepared.rough_image_tokens,
        usage=usage,
        elapsed_seconds=elapsed_seconds,
        estimated_cost_usd=estimated_cost,
        warnings=run_warnings,
    )
