from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from geo_map_exp_extractor import exporters
from geo_map_exp_extractor.jobs import ProjectLoadError, load_review_project
from geo_map_exp_extractor.recovery import (
    AutosaveClock,
    ReviewState,
    find_snapshots,
    make_snapshot,
    project_revision,
    save_snapshot,
    snapshot_path,
)


def review_state() -> ReviewState:
    return ReviewState(
        fields=["Unit", "Description"],
        rows=[{"Unit": "Qal", "Description": "Edited Δ description"}],
        original_rows=[{"Unit": "Qal", "Description": "Original"}],
        row_statuses=["accepted"],
        row_comments=["Reviewed"],
        feedback_records=[{"event_type": "cell_edit", "row_index": 0}],
        notes_text="Review notes\nSecond paragraph",
        pending_targets=[0],
        pending_status="needs_review",
        pending_comment="Still typing",
    )


def test_idle_autosave_and_unchanged_content() -> None:
    clock = AutosaveClock()
    clock.reset("original")
    assert not clock.due("original", 1000)
    assert not clock.due("edited", 1005)
    assert not clock.due("edited", 1034)
    assert clock.due("edited", 1035)
    clock.saved("edited")
    assert not clock.due("edited", 2000)


def test_continuous_edits_cannot_postpone_autosave_forever() -> None:
    clock = AutosaveClock()
    clock.reset("original")
    for second in range(0, 120, 5):
        assert not clock.due(f"edit {second}", second)
    assert clock.due("edit 120", 120)


def test_failed_write_retries_without_tight_loop() -> None:
    clock = AutosaveClock()
    clock.reset("old")
    assert not clock.due("new", 0)
    assert clock.due("new", 30)
    clock.last_attempt = 30
    assert not clock.due("new", 59)
    assert clock.due("new", 60)
    assert clock.saved_digest == "old"


def test_edits_during_background_write_are_saved_next() -> None:
    clock = AutosaveClock()
    clock.reset("old")
    assert not clock.due("first", 0)
    assert clock.due("first", 30)
    clock.last_attempt = 30
    clock.saved("first")
    assert not clock.due("second", 35)
    assert clock.due("second", 65)


def test_revert_to_saved_content_requires_no_write() -> None:
    clock = AutosaveClock()
    clock.reset("saved")
    assert not clock.due("edit", 0)
    assert not clock.due("saved", 35)
    assert clock.dirty_since is None


def test_round_trip_keeps_all_review_content_and_separate_sessions(tmp_path: Path) -> None:
    root = tmp_path / "recovery"
    project = tmp_path / "project"
    state = review_state()
    snapshot = make_snapshot(project, "run-1", "request-1", "base-revision", state)
    path = snapshot_path(root, project, "session-a")
    save_snapshot(path, snapshot)
    other = snapshot_path(root, project, "session-b")
    assert other != path
    save_snapshot(other, snapshot)
    records, errors = find_snapshots(root, project)
    assert len(records) == 2
    assert not errors
    assert records[0].snapshot.state == state
    assert records[0].snapshot.version == 1
    assert not find_snapshots(root, tmp_path / "other-project")[0]


@pytest.mark.parametrize("fail_at", ["replace", "fsync"])
def test_failed_atomic_write_preserves_previous_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_at: str,
) -> None:
    path = tmp_path / "snapshot.json"
    exporters.write_json_atomic({"revision": "previous"}, path)
    before = path.read_bytes()

    def fail(*args: object) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(exporters.os, fail_at, fail)
    with pytest.raises(OSError, match="disk failure"):
        exporters.write_json_atomic({"revision": "new"}, path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_truncated_snapshot_does_not_block_valid_recovery(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text('{"version":', encoding="utf-8")
    valid = make_snapshot(tmp_path / "project", "run", "request", "base", review_state())
    save_snapshot(tmp_path / "valid.json", valid)
    records, errors = find_snapshots(tmp_path)
    assert len(records) == 1
    assert len(errors) == 1
    assert "broken.json" in errors[0]
    assert (tmp_path / "broken.json").exists()


@pytest.mark.parametrize("damage", ["metadata", "fields", "target", "version", "relative_path"])
def test_malformed_recovery_is_not_offered(tmp_path: Path, damage: str) -> None:
    data = make_snapshot(tmp_path / "project", "run", "req", "base", review_state()).model_dump(
        mode="json"
    )
    if damage == "metadata":
        data["state"]["row_comments"] = []
    elif damage == "fields":
        data["state"]["rows"][0]["Unexpected"] = "value"
    elif damage == "target":
        data["state"]["pending_targets"] = [99]
    elif damage == "version":
        data["version"] = 99
    else:
        data["project_dir"] = "relative/path"
    (tmp_path / "bad.json").write_text(json.dumps(data), encoding="utf-8")
    records, errors = find_snapshots(tmp_path)
    assert not records
    assert errors


def test_snapshot_schema_does_not_accept_credentials() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        ReviewState.model_validate({**review_state().model_dump(), "api_key": "not-allowed"})


def test_revision_detects_saved_changes_and_missing_files(tmp_path: Path) -> None:
    paths = {"manifest": tmp_path / "manifest.json", "notes": tmp_path / "notes.md"}
    paths["manifest"].write_text("{}", encoding="utf-8")
    original = project_revision(paths)
    paths["notes"].write_text("changed", encoding="utf-8")
    assert project_revision(paths) != original
    paths["notes"].unlink()
    assert project_revision(paths) == original


def test_recovery_can_bypass_interrupted_multi_file_save(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text('{"run_id": "run"}', encoding="utf-8")
    (tmp_path / "extracted.json").write_text(
        '{"fields": ["Unit"], "rows": [{"Unit": "Qal"}]}',
        encoding="utf-8",
    )
    (tmp_path / "corrected.json").write_text('{"fields":', encoding="utf-8")
    (tmp_path / "notes.md").write_bytes(b"\xff")
    (tmp_path / "feedback.jsonl").write_bytes(b"\xff")
    with pytest.raises(ProjectLoadError):
        load_review_project(tmp_path)
    loaded = load_review_project(tmp_path, prefer_extracted=True)
    assert loaded.fields == ["Unit"]
    assert loaded.original_rows == [{"Unit": "Qal"}]
    assert loaded.feedback_records == []
    assert loaded.notes_text == ""
    assert (tmp_path / "corrected.json").read_text(encoding="utf-8") == '{"fields":'
