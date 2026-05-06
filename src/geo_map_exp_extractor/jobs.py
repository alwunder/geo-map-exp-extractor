"""Review-workbench extraction job orchestration."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from geo_map_exp_extractor import __version__
from geo_map_exp_extractor.config import load_profile
from geo_map_exp_extractor.exporters import write_csv, write_json, write_prompt
from geo_map_exp_extractor.image_io import get_image_metadata
from geo_map_exp_extractor.openai_runner import DEFAULT_MODEL, ExtractionResult, run_extraction
from geo_map_exp_extractor.prompt_builder import build_prompt

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


def _default_prompt_template_path() -> Path:
    """Return the repo-local prompt template path used by the CLI."""

    return Path(__file__).resolve().parents[2] / "prompts" / "extraction_prompt.md"


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


def review_output_paths(run_dir: str | Path) -> dict[str, Path]:
    """Return the standard review-workbench output file paths for a run folder."""

    path = Path(run_dir)
    return {
        "source_image": path / "source_image",
        "profile": path / "profile.yml",
        "prompt": path / "prompt.txt",
        "raw_response": path / "raw_response.json",
        "extracted_csv": path / "extracted.csv",
        "extracted_json": path / "extracted.json",
        "corrected_csv": path / "corrected.csv",
        "corrected_json": path / "corrected.json",
        "manifest": path / "manifest.json",
        "notes": path / "notes.md",
        "feedback": path / "feedback.jsonl",
    }


def _copy_inputs(image_path: Path, profile_path: Path, paths: dict[str, Path]) -> dict[str, Path]:
    """Copy source inputs into the run folder and return their concrete paths."""

    source_image_path = paths["source_image"].with_suffix(image_path.suffix)
    shutil.copy2(image_path, source_image_path)
    shutil.copy2(profile_path, paths["profile"])
    paths["source_image"] = source_image_path
    return paths


def build_review_manifest(
    *,
    run_id: str,
    timestamp: str,
    image_path: str | Path,
    copied_image_path: str | Path,
    image_dimensions: tuple[int, int],
    profile_path: str | Path,
    copied_profile_path: str | Path,
    profile_id: str,
    profile_fields: Sequence[str],
    model: str,
    output_paths: dict[str, Path],
) -> dict[str, Any]:
    """Build manifest metadata for a timestamped GUI review run."""

    width, height = image_dimensions
    return {
        "run_id": run_id,
        "timestamp": timestamp,
        "image_path": str(Path(image_path)),
        "source_image_copy": str(Path(copied_image_path)),
        "image_dimensions": {"width": width, "height": height},
        "profile_path": str(Path(profile_path)),
        "profile_copy": str(Path(copied_profile_path)),
        "profile_id": profile_id,
        "profile_fields": list(profile_fields),
        "model": model,
        "output_paths": {key: str(path) for key, path in output_paths.items()},
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
) -> dict[str, Any]:
    """Build one lightweight correction-tracking JSON object."""

    return {
        "run_id": run_id,
        "row_index": row_index,
        "field_name": field_name,
        "original_value": "" if original_value is None else str(original_value),
        "corrected_value": "" if corrected_value is None else str(corrected_value),
        "status": status,
        "comment": comment,
    }


def run_extraction_job(
    *,
    image_path: str | Path,
    profile_path: str | Path,
    output_dir: str | Path,
    model: str = DEFAULT_MODEL,
    prompt_template_path: str | Path | None = None,
    extraction_runner: ExtractionRunner = run_extraction,
    timestamp: datetime | None = None,
) -> ExtractionJobResult:
    """Run extraction and create a timestamped review folder with all sidecar files."""

    image = Path(image_path)
    profile_file = Path(profile_path)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    created_at = timestamp or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    timestamp_text = created_at.astimezone(timezone.utc).isoformat()
    run_id = _make_run_id(created_at.astimezone(timezone.utc))
    run_dir = _unique_run_dir(destination, run_id)
    run_dir.mkdir(parents=True)

    paths = review_output_paths(run_dir)
    paths = _copy_inputs(image, profile_file, paths)

    profile = load_profile(profile_file)
    prompt_template = (
        Path(prompt_template_path) if prompt_template_path else _default_prompt_template_path()
    )
    prompt = build_prompt(profile, prompt_template)
    result = extraction_runner(image_path=image, prompt=prompt, profile=profile, model=model)
    rows = result.data["rows"]
    fields = profile.fields

    write_prompt(prompt, paths["prompt"])
    write_json(result.raw_response, paths["raw_response"])
    write_csv(rows, fields, paths["extracted_csv"])
    write_json(
        {
            "fields": fields,
            "rows": rows,
            "notes": result.data.get("notes", []),
            "warnings": result.data.get("warnings", []),
        },
        paths["extracted_json"],
    )
    write_corrected_outputs(
        rows=rows, fields=fields, csv_path=paths["corrected_csv"], json_path=paths["corrected_json"]
    )
    paths["notes"].write_text("# Review notes\n\n", encoding="utf-8")
    write_feedback_jsonl([], paths["feedback"])

    metadata = get_image_metadata(image)
    manifest = build_review_manifest(
        run_id=run_dir.name,
        timestamp=timestamp_text,
        image_path=image,
        copied_image_path=paths["source_image"],
        image_dimensions=(metadata.width, metadata.height),
        profile_path=profile_file,
        copied_profile_path=paths["profile"],
        profile_id=profile.id,
        profile_fields=fields,
        model=model,
        output_paths=paths,
    )
    write_json(manifest, paths["manifest"])

    return ExtractionJobResult(
        run_id=run_dir.name,
        run_dir=run_dir,
        fields=fields,
        rows=[{field: row.get(field, "") for field in fields} for row in rows],
        manifest=manifest,
        output_paths=paths,
    )
