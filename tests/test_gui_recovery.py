from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from geo_map_exp_extractor import gui_recovery
from geo_map_exp_extractor.gui import ReviewWorkbench
from geo_map_exp_extractor.jobs import load_review_project
from geo_map_exp_extractor.recovery import (
    RecoveryRecord,
    find_snapshots,
    make_snapshot,
    project_revision,
    save_snapshot,
)


class Value:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


@pytest.fixture
def workbench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        gui_recovery.tk, "StringVar", lambda **kwargs: Value(kwargs.get("value", ""))
    )
    monkeypatch.setattr(gui_recovery, "recovery_directory", lambda: tmp_path / "recovery")
    app = object.__new__(ReviewWorkbench)
    app._init_recovery()
    app.result = SimpleNamespace(
        fields=["Name"],
        run_dir=tmp_path / "project",
        run_id="run",
        request_fingerprint="request",
        output_paths={},
    )
    app.rows = [{"Name": "original"}]
    app.original_rows = [{"Name": "original"}]
    app.row_statuses = ["needs_review"]
    app.row_comments = [""]
    app.feedback_records = []
    app._use_tksheet = False
    app.sheet = None
    app.notes = Mock()
    app.notes.get.return_value = "notes"
    app.row_status_var = Value("needs review")
    app.row_comment_var = Value()
    app.row_status_labels = {
        "needs_review": "needs review",
        "accepted": "accepted",
        "bad_extraction": "bad extraction",
    }
    app.row_status_options = ("needs_review", "accepted", "bad_extraction")
    app._pending_row_metadata_target_indexes = Mock(return_value=[])
    app._set_status_message = Mock()
    app._configure_table = Mock()
    app._sync_row_metadata_controls = Mock()
    app.after = Mock(return_value="timer")
    app.after_cancel = Mock()
    app.has_unsaved_changes = False
    app._run_thread = None
    app._start_project_recovery()
    yield app
    app._shutdown_recovery()


def test_timer_saves_changed_state_without_touching_explicit_outputs(
    workbench, monkeypatch
) -> None:
    app = workbench
    app.rows[0]["Name"] = "edited"
    app.has_unsaved_changes = True
    now = [0.0]
    monkeypatch.setattr(gui_recovery.time, "monotonic", lambda: now[0])
    app._autosave_tick()
    assert app._recovery_future is None
    now[0] = 30
    app._autosave_tick()
    app._finish_recovery_write(wait=True)
    records, errors = find_snapshots(app._recovery_root)
    assert not errors
    assert records[0].snapshot.state.rows == [{"Name": "edited"}]
    assert app.has_unsaved_changes is True
    assert not app.result.run_dir.exists()
    now[0] = 1000
    app._autosave_tick()
    assert app._recovery_future is None


def test_capture_includes_open_cell_and_pending_metadata_without_committing(workbench) -> None:
    app = workbench
    app._use_tksheet = True
    app.sheet = Mock()
    app.sheet.get_sheet_data.return_value = [["original"]]
    app.sheet.get_text_editor_widget.return_value.winfo_viewable.return_value = True
    app.sheet.get_currently_selected.return_value = SimpleNamespace(row=0, column=0)
    app.sheet.get_text_editor_value.return_value = "unfinished cell text"
    app._pending_row_metadata_target_indexes.return_value = [0]
    app.row_comment_var.set("unfinished comment")
    state = app._capture_review_state()
    assert state.rows == [{"Name": "unfinished cell text"}]
    assert state.pending_targets == [0]
    assert state.pending_comment == "unfinished comment"
    assert app.rows == [{"Name": "original"}]
    app.sheet.close_text_editor.assert_not_called()


def test_capture_includes_fallback_dialog_editor(workbench) -> None:
    editor = Mock()
    editor.winfo_exists.return_value = True
    editor.get.return_value = "draft"
    workbench._recovery_cell_editor = (0, "Name", editor)
    assert workbench._capture_review_state().rows == [{"Name": "draft"}]
    assert workbench.rows == [{"Name": "original"}]


def test_capture_preserves_zero(workbench) -> None:
    workbench.rows = [{"Name": 0}]
    assert workbench._capture_review_state().rows == [{"Name": "0"}]


def test_failed_background_write_is_visible_and_retried(workbench, monkeypatch) -> None:
    app = workbench
    app.rows[0]["Name"] = "edited"
    now = [0.0]
    monkeypatch.setattr(gui_recovery.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(gui_recovery, "save_snapshot", Mock(side_effect=OSError("disk full")))
    app._autosave_tick()
    now[0] = 30
    app._autosave_tick()
    future = app._recovery_future
    with pytest.raises(OSError):
        future.result()
    app._autosave_tick()
    assert "disk full" in app.recovery_status.get()
    assert app._recovery_timer == "timer"
    now[0] = 35
    app._autosave_tick()
    assert app._recovery_future is None
    monkeypatch.setattr(gui_recovery, "save_snapshot", save_snapshot)
    now[0] = 60
    app._autosave_tick()
    app._finish_recovery_write(wait=True)
    assert app._recovery_path.exists()


def test_discard_waits_for_writer_and_cannot_resurrect_snapshot(workbench, monkeypatch) -> None:
    app = workbench
    entered = threading.Event()
    release = threading.Event()

    def slow_write(path, snapshot):
        entered.set()
        assert release.wait(2)
        save_snapshot(path, snapshot)

    monkeypatch.setattr(gui_recovery, "save_snapshot", slow_write)
    path = app._recovery_path
    app._write_recovery(app._capture_review_state())
    assert entered.wait(2)
    release.set()
    assert app._end_project_recovery()
    assert not path.exists()
    assert app._recovery_path is None
    assert app._recovery_future is None


def test_restore_preserves_rows_original_alignment_and_pending_comment(workbench) -> None:
    app = workbench
    state = app._capture_review_state()
    state.rows = [{"Name": "second"}, {"Name": "first"}]
    state.original_rows = [{"Name": "original second"}, {"Name": "original first"}]
    state.row_statuses = ["accepted", "bad_extraction"]
    state.row_comments = ["a", "b"]
    state.notes_text = "recovered notes"
    state.pending_targets = [1]
    state.pending_comment = "pending"
    snapshot = make_snapshot(app.result.run_dir, "run", "request", "base", state)
    path = app._recovery_root / "old.json"
    save_snapshot(path, snapshot)
    app._apply_recovery(RecoveryRecord(path, snapshot))
    assert app.rows == state.rows
    assert app.original_rows == state.original_rows
    assert app.row_statuses == state.row_statuses
    assert app.row_comments == state.row_comments
    assert app.selected_row_index == 1
    assert app.row_comment_var.get() == "pending"
    assert app.has_unsaved_changes
    assert path.exists()  # Cancel/another crash before Save must still be recoverable.
    app.notes.insert.assert_called_once_with("1.0", "recovered notes")
    assert app._end_project_recovery()
    assert not path.exists()


@pytest.mark.parametrize("decision", [True, False, None])
def test_recovery_choices_restore_discard_or_defer(workbench, monkeypatch, decision) -> None:
    app = workbench
    snapshot = make_snapshot(
        app.result.run_dir, "run", "request", "base", app._capture_review_state()
    )
    path = app._recovery_root / "previous-session.json"
    save_snapshot(path, snapshot)
    monkeypatch.setattr(gui_recovery.messagebox, "askyesnocancel", lambda *_, **__: decision)
    proceed, record = app._choose_recovery()
    assert proceed is (decision is not None)
    assert (record is not None) is (decision is True)
    assert path.exists() is (decision is not False)


def test_failed_explicit_save_keeps_dirty_state_and_recovery_copy(workbench, monkeypatch) -> None:
    app = workbench
    app._checkpoint_before_save()
    path = app._recovery_path
    app._save_corrected_impl = Mock(side_effect=OSError("project disk full"))
    monkeypatch.setattr(gui_recovery.messagebox, "showerror", Mock())
    assert app._save_corrected() is False
    assert app.has_unsaved_changes
    assert path.exists()


@pytest.mark.parametrize("recovery_fails", [False, True])
def test_explicit_save_cleans_recovery_and_can_survive_autosave_failure(
    workbench,
    monkeypatch,
    recovery_fails,
) -> None:
    app = workbench
    app.result.run_dir.mkdir()
    app.result.manifest = {"profile_id": "test"}
    app.result.output_paths = {
        "notes": app.result.run_dir / "notes.md",
        "corrected_csv": app.result.run_dir / "corrected.csv",
        "corrected_json": app.result.run_dir / "corrected.json",
        "feedback": app.result.run_dir / "feedback.jsonl",
    }
    app.image_path = Value("source.png")
    app._resolve_pending_row_metadata_change = Mock(return_value=True)
    app._checkpoint_before_save()
    path = app._recovery_path
    app.has_unsaved_changes = True
    if recovery_fails:
        app._checkpoint_before_save = Mock(side_effect=OSError("local recovery disk full"))
    assert app._save_corrected()
    assert not path.exists()
    assert not app.has_unsaved_changes
    assert app.result.output_paths["corrected_json"].is_file()
    assert app.result.output_paths["feedback"].is_file()


def test_startup_recovery_opens_the_selected_project(workbench, monkeypatch) -> None:
    app = workbench
    snapshot = make_snapshot(
        app.result.run_dir, "run", "request", "base", app._capture_review_state()
    )
    record = RecoveryRecord(app._recovery_root / "previous.json", snapshot)
    app._choose_recovery = Mock(return_value=(True, record))
    app._load_project = Mock()
    app._recover_autosave()
    app._load_project.assert_called_once_with(
        project_dir=app.result.run_dir,
        recovery_record=record,
    )


def test_snapshot_cleanup_failure_keeps_current_session(workbench, monkeypatch) -> None:
    app = workbench
    app._checkpoint_before_save()
    path = app._recovery_path
    monkeypatch.setattr(Path, "unlink", Mock(side_effect=PermissionError("locked")))
    monkeypatch.setattr(gui_recovery.messagebox, "showerror", Mock())
    assert app._end_project_recovery() is False
    assert app._recovery_path == path
    assert path.exists()


@pytest.mark.parametrize("scenario", ["restore", "wrong_run", "changed_project"])
def test_load_project_recovery_checks_identity_and_keeps_saved_files(
    workbench,
    monkeypatch,
    scenario,
) -> None:
    app = workbench
    project = app.result.run_dir
    project.mkdir()
    (project / "manifest.json").write_text(
        '{"run_id":"run", "request_fingerprint":"request"}',
        encoding="utf-8",
    )
    original_json = '{"fields":["Name"], "rows":[{"Name":"original"}]}'
    (project / "extracted.json").write_text(original_json, encoding="utf-8")
    loaded = load_review_project(project)
    state = app._capture_review_state()
    state.rows[0]["Name"] = "unsaved recovery edit"
    snapshot = make_snapshot(
        project,
        "different-run" if scenario == "wrong_run" else "run",
        "request",
        "old-revision" if scenario == "changed_project" else project_revision(loaded.output_paths),
        state,
    )
    path = app._recovery_root / "previous-session.json"
    save_snapshot(path, snapshot)
    app.output_dir = Value(str(project.parent))
    app.image_path = Value("old.png")
    app.profile_path = Value("old.yml")
    app.notes.edit_modified.return_value = False
    app.source_image = None
    app.canvas = Mock()
    old_result = app.result
    monkeypatch.setattr(gui_recovery.messagebox, "showerror", Mock())
    monkeypatch.setattr(gui_recovery.messagebox, "askyesno", Mock(return_value=False))

    app._load_project(project_dir=project, recovery_record=RecoveryRecord(path, snapshot))

    assert path.exists()
    assert (project / "extracted.json").read_text(encoding="utf-8") == original_json
    assert not (project / "corrected.json").exists()
    if scenario == "restore":
        assert app.rows == [{"Name": "unsaved recovery edit"}]
        assert app.original_rows == [{"Name": "original"}]
        assert app.has_unsaved_changes
        assert app._recovery_source == path
    else:
        assert app.result is old_result
        assert app.rows == [{"Name": "original"}]
