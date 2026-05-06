"""CSV and sidecar file exporters."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from geo_map_exp_extractor import __version__
from geo_map_exp_extractor.config import ExtractionProfile
from geo_map_exp_extractor.image_io import ImageMetadata


def _portable_path(path: str | Path) -> str:
    """Return a stable, forward-slash path string for JSON metadata."""

    return Path(path).as_posix()


def write_csv(rows: list[dict[str, Any]], fields: list[str], output_path: str | Path) -> Path:
    """Write extracted rows to CSV preserving the profile field order exactly."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    return path


def sidecar_paths(output_path: str | Path) -> dict[str, Path]:
    """Return sidecar paths for a CSV output path."""

    csv_path = Path(output_path)
    base = csv_path.with_suffix("")
    return {
        "csv": csv_path,
        "raw_json": base.with_suffix(".raw.json"),
        "prompt": base.with_suffix(".prompt.txt"),
        "manifest": base.with_suffix(".manifest.json"),
    }


def write_json(data: Any, output_path: str | Path) -> Path:
    """Write JSON data with stable formatting."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_prompt(prompt: str, output_path: str | Path) -> Path:
    """Write final prompt text."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prompt, encoding="utf-8")
    return path


def build_manifest(
    *,
    image_metadata: ImageMetadata,
    profile: ExtractionProfile,
    profile_path: str | Path,
    model: str,
    output_paths: dict[str, Path],
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build reproducibility metadata for an extraction run."""

    created_at = timestamp or datetime.now(timezone.utc).isoformat()
    return {
        "input_image_path": _portable_path(image_metadata.path),
        "input_image_dimensions": {
            "width": image_metadata.width,
            "height": image_metadata.height,
        },
        "profile_path": _portable_path(profile_path),
        "profile_id": profile.id,
        "model": model,
        "timestamp": created_at,
        "output_file_paths": {key: _portable_path(path) for key, path in output_paths.items()},
        "package_version": __version__,
    }


def write_manifest(manifest: dict[str, Any], output_path: str | Path) -> Path:
    """Write a manifest JSON file."""

    return write_json(manifest, output_path)
