from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from geo_map_exp_extractor.jobs import (
    ExtractionJobResult,
    ProjectLoadError,
    load_review_project,
    run_extraction_job,
    write_corrected_outputs,
)
from geo_map_exp_extractor.openai_runner import ExtractionResult


def _fake_runner(**_: object) -> ExtractionResult:
    return ExtractionResult(
        data={
            "fields": ["MapUnit", "Description"],
            "rows": [{"MapUnit": "Qa", "Description": "Extracted local row"}],
            "notes": [],
            "warnings": [],
        },
        raw_response={"id": "portable-test"},
    )


@pytest.fixture
def project_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ExtractionJobResult:
    monkeypatch.setattr("geo_map_exp_extractor.jobs._cache_root", lambda: tmp_path / ".cache")
    source = tmp_path / "input" / "map.png"
    source.parent.mkdir()
    Image.new("RGB", (80, 60), color="white").save(source)
    return run_extraction_job(
        image_path=source,
        profile_path=Path("profiles/water_production.yml"),
        output_dir=tmp_path / "original_parent",
        model="test-model",
        extraction_runner=_fake_runner,
        timestamp=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )


def _read_manifest(run_dir: Path) -> dict[str, object]:
    return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(run_dir: Path, manifest: dict[str, object]) -> None:
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_legacy_absolute_manifest(run_dir: Path) -> None:
    manifest = _read_manifest(run_dir)
    output_paths = manifest["output_paths"]
    assert isinstance(output_paths, dict)
    manifest["output_paths"] = {
        key: (run_dir / str(value)).resolve().as_posix() for key, value in output_paths.items()
    }
    manifest["source_image_path"] = (run_dir / str(manifest["source_image_path"])).resolve().as_posix()
    manifest["processed_image_path"] = (
        run_dir / str(manifest["processed_image_path"])
    ).resolve().as_posix()
    _write_manifest(run_dir, manifest)


def _write_corrected(run_dir: Path, description: str) -> None:
    write_corrected_outputs(
        rows=[{"MapUnit": "Qa", "Description": description}],
        fields=["MapUnit", "Description"],
        csv_path=run_dir / "corrected.csv",
        json_path=run_dir / "corrected.json",
    )


def test_new_manifest_is_relative_and_project_loads_in_place(
    project_result: ExtractionJobResult,
) -> None:
    manifest = _read_manifest(project_result.run_dir)
    output_paths = manifest["output_paths"]
    assert isinstance(output_paths, dict)
    assert output_paths["extracted_json"] == "extracted.json"
    assert output_paths["notes"] == "notes.md"
    assert output_paths["feedback"] == "feedback.jsonl"
    assert manifest["source_image_path"] == "source_image.png"
    assert manifest["processed_image_path"] == "processed_api_image.png"
    assert all(not Path(str(value)).is_absolute() for value in output_paths.values())

    loaded = load_review_project(project_result.run_dir)

    assert loaded.run_dir == project_result.run_dir.resolve()
    assert loaded.rows[0]["Description"] == "Extracted local row"
    assert loaded.source_image_path == (project_result.run_dir / "source_image.png").resolve()


def test_copied_project_prefers_local_corrected_notes_feedback_and_image(
    project_result: ExtractionJobResult,
    tmp_path: Path,
) -> None:
    _write_corrected(project_result.run_dir, "Corrected before copy")
    (project_result.run_dir / "notes.md").write_text("original note\n", encoding="utf-8")
    (project_result.run_dir / "feedback.jsonl").write_text(
        json.dumps({"row_index": 0, "status": "accepted", "comment": "original"}) + "\n",
        encoding="utf-8",
    )
    copied_dir = tmp_path / "copied_parent" / project_result.run_dir.name
    shutil.copytree(project_result.run_dir, copied_dir)
    _write_corrected(copied_dir, "Corrected in copied project")
    (copied_dir / "notes.md").write_text("copied note\n", encoding="utf-8")
    (copied_dir / "feedback.jsonl").write_text(
        json.dumps({"row_index": 0, "status": "rejected", "comment": "copied"}) + "\n",
        encoding="utf-8",
    )

    loaded = load_review_project(copied_dir)

    assert loaded.rows[0]["Description"] == "Corrected in copied project"
    assert loaded.original_rows[0]["Description"] == "Extracted local row"
    assert loaded.notes_text == "copied note\n"
    assert loaded.feedback_records[0]["comment"] == "copied"
    assert loaded.source_image_path == (copied_dir / "source_image.png").resolve()
    assert all(path.is_relative_to(copied_dir.resolve()) for path in loaded.output_paths.values())


def test_legacy_absolute_paths_rebase_to_copy_even_when_original_exists(
    project_result: ExtractionJobResult,
    tmp_path: Path,
) -> None:
    _write_corrected(project_result.run_dir, "Original-folder value")
    (project_result.run_dir / "notes.md").write_text("original-folder note\n", encoding="utf-8")
    _make_legacy_absolute_manifest(project_result.run_dir)
    copied_dir = tmp_path / "legacy_copy" / project_result.run_dir.name
    shutil.copytree(project_result.run_dir, copied_dir)
    _write_corrected(copied_dir, "Copied-folder value")
    (copied_dir / "notes.md").write_text("copied-folder note\n", encoding="utf-8")

    loaded = load_review_project(copied_dir)

    assert project_result.run_dir.exists()
    assert loaded.rows[0]["Description"] == "Copied-folder value"
    assert loaded.notes_text == "copied-folder note\n"
    assert loaded.output_paths["corrected_json"] == (copied_dir / "corrected.json").resolve()
    assert loaded.source_image_path == (copied_dir / "source_image.png").resolve()


def test_legacy_absolute_paths_rebase_after_original_is_removed(
    project_result: ExtractionJobResult,
    tmp_path: Path,
) -> None:
    _make_legacy_absolute_manifest(project_result.run_dir)
    copied_dir = tmp_path / "orphaned_copy" / project_result.run_dir.name
    shutil.copytree(project_result.run_dir, copied_dir)
    shutil.rmtree(project_result.run_dir)

    loaded = load_review_project(copied_dir)

    assert loaded.rows[0]["Description"] == "Extracted local row"
    assert loaded.source_image_path == (copied_dir / "source_image.png").resolve()
    assert loaded.output_paths["notes"] == (copied_dir / "notes.md").resolve()


def test_relative_manifest_path_cannot_escape_selected_project(
    project_result: ExtractionJobResult,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps({"fields": ["MapUnit"], "rows": [{"MapUnit": "outside"}]}),
        encoding="utf-8",
    )
    manifest = _read_manifest(project_result.run_dir)
    output_paths = manifest["output_paths"]
    assert isinstance(output_paths, dict)
    output_paths["extracted_json"] = "../../outside.json"
    _write_manifest(project_result.run_dir, manifest)

    with pytest.raises(ProjectLoadError, match="escapes the selected project folder"):
        load_review_project(project_result.run_dir)


def test_missing_or_unresolvable_required_data_has_clear_error(
    project_result: ExtractionJobResult,
) -> None:
    manifest = _read_manifest(project_result.run_dir)
    output_paths = manifest["output_paths"]
    assert isinstance(output_paths, dict)
    output_paths["extracted_json"] = "missing-data.json"
    _write_manifest(project_result.run_dir, manifest)

    with pytest.raises(ProjectLoadError, match="required corrected.json or extracted.json data"):
        load_review_project(project_result.run_dir)


def test_missing_and_malformed_manifest_have_clear_errors(tmp_path: Path) -> None:
    missing_manifest = tmp_path / "missing_manifest"
    missing_manifest.mkdir()
    with pytest.raises(ProjectLoadError, match="does not contain manifest.json"):
        load_review_project(missing_manifest)

    malformed_manifest = tmp_path / "malformed_manifest"
    malformed_manifest.mkdir()
    (malformed_manifest / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ProjectLoadError, match="manifest.json contains malformed JSON"):
        load_review_project(malformed_manifest)
