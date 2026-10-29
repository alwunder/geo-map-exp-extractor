from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from PIL import Image

from geo_map_exp_extractor import gui
from geo_map_exp_extractor.gui import ReviewWorkbench, filedialog, messagebox
from geo_map_exp_extractor.jobs import ProjectLoadError


def _workbench(*, unsaved: bool = True) -> ReviewWorkbench:
    workbench = object.__new__(ReviewWorkbench)
    workbench.result = SimpleNamespace(run_id="test-run")
    workbench.has_unsaved_changes = unsaved
    workbench._run_thread = None
    workbench._use_tksheet = False
    workbench.sheet = None
    workbench.notes = Mock()
    workbench.notes.edit_modified.return_value = False
    workbench._set_status_message = Mock()
    workbench._end_project_recovery = Mock(return_value=True)
    workbench._start_project_recovery = Mock()
    workbench._choose_recovery = Mock(return_value=(True, None))
    workbench._resolve_pending_row_metadata_change = lambda: True  # type: ignore[method-assign]
    return workbench


@pytest.mark.parametrize(
    ("decision", "expected"),
    [(None, False), (False, True)],
)
def test_confirm_save_before_respects_cancel_and_discard(
    monkeypatch: pytest.MonkeyPatch,
    decision: bool | None,
    expected: bool,
) -> None:
    workbench = _workbench()
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *args, **kwargs: decision)

    assert workbench._confirm_save_before("clearing the project") is expected


def test_confirm_save_before_saves_then_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    workbench = _workbench()
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *args, **kwargs: True)

    def save() -> None:
        workbench.has_unsaved_changes = False

    workbench._save_corrected = save  # type: ignore[method-assign]

    assert workbench._confirm_save_before("exiting") is True


def test_confirm_save_before_skips_prompt_without_unsaved_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench = _workbench(unsaved=False)

    def unexpected_prompt(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("prompt should not be shown")

    monkeypatch.setattr(messagebox, "askyesnocancel", unexpected_prompt)

    assert workbench._confirm_save_before("exiting") is True


def test_load_project_shows_specific_project_load_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench = object.__new__(ReviewWorkbench)
    workbench.result = None
    workbench._run_thread = None
    workbench.output_dir = SimpleNamespace(get=lambda: str(tmp_path))
    workbench._confirm_save_before = lambda _: True  # type: ignore[method-assign]
    workbench._choose_recovery = Mock(return_value=(True, None))
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(filedialog, "askdirectory", lambda **_: str(tmp_path))
    monkeypatch.setattr(
        gui,
        "load_review_project",
        lambda _: (_ for _ in ()).throw(ProjectLoadError("manifest.json is malformed")),
    )
    monkeypatch.setattr(
        messagebox,
        "showerror",
        lambda title, detail, **_: errors.append((title, detail)),
    )

    workbench._load_project()

    assert errors == [("Could not load project", "manifest.json is malformed")]


def test_duplicate_selected_row_inserts_a_reviewable_copy_below_source() -> None:
    workbench = object.__new__(ReviewWorkbench)
    source_row = {"map_unit": "Qa", "description": "Alluvium"}
    workbench.result = SimpleNamespace(fields=["map_unit", "description"])
    workbench.rows = [dict(source_row)]
    workbench.original_rows = [dict(source_row)]
    workbench.row_statuses = ["accepted"]
    workbench.row_comments = ["Verified against map."]
    workbench.selected_row_index = 0
    configured: list[tuple[list[str], list[dict[str, str]]]] = []
    feedback: list[dict[str, Any]] = []
    workbench._configure_table = lambda fields, rows: configured.append((fields, rows))  # type: ignore[method-assign]
    workbench._resolve_pending_row_metadata_change = lambda: True  # type: ignore[method-assign]
    workbench._set_selected_row = lambda index: setattr(workbench, "selected_row_index", index)  # type: ignore[method-assign]
    workbench._record_feedback = lambda **record: feedback.append(record)  # type: ignore[method-assign]
    workbench._mark_unsaved = lambda: setattr(workbench, "has_unsaved_changes", True)  # type: ignore[method-assign]

    workbench._duplicate_selected_row()

    assert workbench.rows == [source_row, source_row]
    assert workbench.rows[0] is not workbench.rows[1]
    assert workbench.original_rows[1] == source_row
    assert workbench.row_statuses == ["accepted", "needs_review"]
    assert workbench.row_comments == ["Verified against map.", ""]
    assert workbench.selected_row_index == 1
    assert configured == [(["map_unit", "description"], workbench.rows)]
    assert feedback == [
        {
            "row_index": 1,
            "field": "__row__",
            "model_value": source_row,
            "corrected_value": source_row,
            "status": "needs_review",
            "comment": "Row duplicated by reviewer",
            "event_type": "row_duplicated",
        }
    ]
    assert workbench.has_unsaved_changes is True


def test_apply_row_metadata_updates_every_selected_treeview_row() -> None:
    workbench = object.__new__(ReviewWorkbench)
    workbench.result = SimpleNamespace()
    workbench.rows = [{"map_unit": "Qa"}, {"map_unit": "Qls"}, {"map_unit": "Qaf"}]
    workbench.row_statuses = ["accepted", "needs_review", "bad_extraction"]
    workbench.row_comments = ["", "Keep this comment.", ""]
    workbench.selected_row_index = 0
    workbench._use_tksheet = False
    workbench.sheet = None
    workbench.table = SimpleNamespace(selection=lambda: ("0", "2"))
    workbench.row_status_options = ("accepted", "needs_review", "bad_extraction")
    workbench.row_status_labels = {
        "accepted": "accepted",
        "needs_review": "needs review",
        "bad_extraction": "bad extraction",
    }
    workbench.row_status_var = SimpleNamespace(get=lambda: "accepted")
    workbench.row_comment_var = SimpleNamespace(get=lambda: "Confirmed by reviewer.")
    feedback: list[dict[str, Any]] = []
    workbench._record_feedback = lambda **record: feedback.append(record)  # type: ignore[method-assign]
    workbench._mark_unsaved = lambda: setattr(workbench, "has_unsaved_changes", True)  # type: ignore[method-assign]
    workbench._refresh_table_display = lambda: None  # type: ignore[method-assign]
    workbench._sync_status_dropdown_color = lambda: None  # type: ignore[method-assign]

    workbench._apply_selected_row_metadata()

    assert workbench.row_statuses == ["accepted", "needs_review", "accepted"]
    assert workbench.row_comments == [
        "Confirmed by reviewer.",
        "Keep this comment.",
        "Confirmed by reviewer.",
    ]
    assert [record["row_index"] for record in feedback] == [0, 2]
    assert all(record["event_type"] == "row_metadata" for record in feedback)
    assert workbench.has_unsaved_changes is True


def _row_metadata_workbench(*, auto_apply: bool) -> tuple[ReviewWorkbench, list[dict[str, Any]]]:
    workbench = object.__new__(ReviewWorkbench)
    workbench.result = SimpleNamespace()
    workbench.rows = [{"map_unit": "Qa"}, {"map_unit": "Qls"}]
    workbench.row_statuses = ["needs_review", "needs_review"]
    workbench.row_comments = ["", ""]
    workbench.row_status_options = ("accepted", "needs_review", "bad_extraction")
    workbench.row_status_labels = {
        "accepted": "accepted",
        "needs_review": "needs review",
        "bad_extraction": "bad extraction",
    }
    workbench.row_status_var = SimpleNamespace(get=lambda: "accepted")
    workbench.row_comment_var = SimpleNamespace(get=lambda: "Verified against map.")
    workbench.auto_apply_row_metadata = SimpleNamespace(get=lambda: auto_apply)
    workbench.selected_row_index = 0
    workbench._row_metadata_target_indexes = [0]
    workbench._use_tksheet = False
    feedback: list[dict[str, Any]] = []
    workbench._record_feedback = lambda **record: feedback.append(record)  # type: ignore[method-assign]
    workbench._mark_unsaved = lambda: setattr(workbench, "has_unsaved_changes", True)  # type: ignore[method-assign]
    workbench._refresh_table_display = lambda: None  # type: ignore[method-assign]
    workbench._sync_status_dropdown_color = lambda: None  # type: ignore[method-assign]
    return workbench, feedback


def test_switching_rows_prompts_to_apply_pending_row_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench, feedback = _row_metadata_workbench(auto_apply=False)
    workbench._sync_row_metadata_controls = lambda: None  # type: ignore[method-assign]
    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        messagebox,
        "askyesnocancel",
        lambda title, detail, **_: prompts.append((title, detail)) or True,
    )

    workbench._set_selected_row(1, sync_widget=False)

    assert prompts == [
        (
            "Apply row changes?",
            "The current row has unapplied status or comment changes. Apply them before switching rows?",
        )
    ]
    assert workbench.selected_row_index == 1
    assert workbench.row_statuses == ["accepted", "needs_review"]
    assert workbench.row_comments == ["Verified against map.", ""]
    assert [record["row_index"] for record in feedback] == [0]


def test_auto_apply_saves_pending_metadata_when_comment_loses_focus() -> None:
    workbench, feedback = _row_metadata_workbench(auto_apply=True)

    workbench._auto_apply_row_metadata_on_focus_out(None)  # type: ignore[arg-type]

    assert workbench.row_statuses == ["accepted", "needs_review"]
    assert workbench.row_comments == ["Verified against map.", ""]
    assert [record["row_index"] for record in feedback] == [0]


class _Value:
    def __init__(self, value: Any = "") -> None:
        self.value = value

    def get(self) -> Any:
        return self.value

    def set(self, value: Any) -> None:
        self.value = value


def _image_workbench(tmp_path: Path, *, unsaved: bool = True) -> ReviewWorkbench:
    """Use the real reset/preview methods with lightweight widget stand-ins."""
    workbench = _workbench(unsaved=unsaved)
    workbench.image_path = _Value(str(tmp_path / "old.png"))
    workbench.preview_source_path = (tmp_path / "old.png").resolve()
    workbench.source_image = Image.new("RGB", (2, 2), "red")
    workbench.preview_photo = object()
    workbench.zoom = 2.0
    workbench._is_panning = True
    workbench.canvas = Mock()
    workbench.rows = [{"Name": "edited"}]
    workbench.original_rows = [{"Name": "original"}]
    workbench.feedback_records = [{"event": "edit"}]
    workbench.row_statuses = ["accepted"]
    workbench.row_comments = ["comment"]
    workbench.table_fields = ["Name"]
    workbench.selected_row_index = 0
    workbench._row_metadata_target_indexes = [0]
    workbench.column_widths_by_field = {"Name": 200}
    workbench._manual_column_resize = True
    workbench.row_status_labels = {"needs_review": "needs review"}
    workbench.row_status_options = ("accepted", "needs_review", "bad_extraction")
    for name in (
        "profile_path",
        "model",
        "reasoning_effort",
        "service_tier",
        "image_detail",
        "max_output_tokens",
        "use_max_output_tokens_limit",
        "include_profile_notes",
        "dry_run",
        "force_rerun",
        "segmented_mode",
        "row_status_var",
        "row_comment_var",
    ):
        setattr(workbench, name, _Value("previous value"))
    workbench._active_profile_path = "old-profile.yml"
    workbench.output_dir = _Value(str(tmp_path / "outputs"))
    workbench.api_key_override = "test-session-key"
    workbench._configure_table = Mock(
        side_effect=lambda fields, _: setattr(workbench, "table_fields", fields)
    )
    workbench._sync_status_dropdown_color = Mock()
    workbench._sync_row_metadata_controls = Mock()
    workbench._zoom_extents = Mock()
    return workbench


def _choose_new_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "new.png"
    Image.new("RGB", (3, 3), "blue").save(path)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **_: str(path))
    return path


@pytest.mark.parametrize("decision", [True, False])
def test_loading_image_saves_or_discards_then_starts_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    decision: bool,
) -> None:
    workbench = _image_workbench(tmp_path)
    path = _choose_new_image(tmp_path, monkeypatch)
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *_, **__: decision)
    old_image_path = workbench.image_path.get()

    def save() -> bool:
        # Saving must still refer to the old image and its edited rows.
        assert workbench.image_path.get() == old_image_path
        assert workbench.rows == [{"Name": "edited"}]
        workbench._mark_saved()
        return True

    workbench._save_corrected = Mock(side_effect=save)
    workbench._browse_image()

    assert workbench._save_corrected.call_count == int(decision)
    assert workbench.result is None
    assert workbench.rows == workbench.original_rows == workbench.feedback_records == []
    assert workbench.row_statuses == workbench.row_comments == []
    assert workbench.table_fields == workbench._row_metadata_target_indexes == []
    assert workbench.selected_row_index is None
    assert workbench.profile_path.get() == workbench._active_profile_path == ""
    assert workbench.image_path.get() == workbench._display_path(path)
    assert workbench.preview_source_path == path.resolve()
    assert workbench.source_image.getpixel((0, 0)) == (0, 0, 255)
    assert workbench.has_unsaved_changes is False
    assert workbench.column_widths_by_field == {}
    assert workbench.zoom == 1.0
    assert workbench._is_panning is False
    assert workbench.segmented_mode.get() is False
    assert workbench.model.get() == gui.DEFAULT_MODEL
    assert workbench.output_dir.get() == str(tmp_path / "outputs")
    assert workbench.api_key_override == "test-session-key"
    workbench.notes.delete.assert_called_once_with("1.0", gui.tk.END)


@pytest.mark.parametrize("failure", ["cancel", "save_false", "save_error", "pending_metadata"])
def test_loading_image_keeps_project_when_transition_is_cancelled_or_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    workbench = _image_workbench(tmp_path)
    _choose_new_image(tmp_path, monkeypatch)
    old_result, old_preview = workbench.result, workbench.source_image
    monkeypatch.setattr(
        messagebox,
        "askyesnocancel",
        lambda *_, **__: None if failure == "cancel" else True,
    )
    monkeypatch.setattr(messagebox, "showerror", Mock())
    workbench._save_corrected = Mock(return_value=False)
    if failure == "save_error":
        workbench._save_corrected.side_effect = OSError("disk full")
    if failure == "pending_metadata":
        workbench._resolve_pending_row_metadata_change = lambda: False

    workbench._browse_image()

    assert workbench.result is old_result
    assert workbench.source_image is old_preview
    assert old_preview.getpixel((0, 0)) == (255, 0, 0)
    assert workbench.image_path.get() == str(tmp_path / "old.png")
    assert workbench.rows == [{"Name": "edited"}]
    assert workbench.has_unsaved_changes is True
    workbench.notes.delete.assert_not_called()


@pytest.mark.parametrize("selection", ["cancel", "same", "missing", "corrupt"])
def test_image_selection_does_not_discard_work_without_a_new_readable_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    workbench = _image_workbench(tmp_path)
    path = ""
    if selection == "same":
        path = workbench.image_path.get()
    elif selection in {"missing", "corrupt"}:
        path = str(tmp_path / "bad.png")
        if selection == "corrupt":
            Path(path).write_text("not an image", encoding="utf-8")
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **_: path)
    monkeypatch.setattr(messagebox, "showerror", Mock())
    workbench._confirm_save_before = Mock(side_effect=AssertionError("unexpected save prompt"))
    old_result = workbench.result
    workbench._browse_image()
    assert workbench.result is old_result
    assert workbench.rows == [{"Name": "edited"}]
    assert workbench.has_unsaved_changes is True
    assert workbench.image_path.get() == str(tmp_path / "old.png")
    assert messagebox.showerror.call_count == int(selection in {"missing", "corrupt"})


def test_loading_image_without_unsaved_changes_needs_no_save_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench = _image_workbench(tmp_path, unsaved=False)
    path = _choose_new_image(tmp_path, monkeypatch)
    monkeypatch.setattr(messagebox, "askyesnocancel", Mock(side_effect=AssertionError("prompt")))
    workbench._browse_image()
    assert workbench.preview_source_path == path.resolve()
    assert workbench.result is None


@pytest.mark.parametrize(
    "action",
    ["_browse_image", "_browse_profile", "_on_profile_selected", "_load_project", "_clear_project"],
)
def test_project_switches_are_blocked_while_extraction_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    workbench = _image_workbench(tmp_path)
    workbench._run_thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(messagebox, "showinfo", Mock())
    monkeypatch.setattr(filedialog, "askopenfilename", Mock(side_effect=AssertionError("dialog")))
    monkeypatch.setattr(filedialog, "askdirectory", Mock(side_effect=AssertionError("dialog")))
    old_result = workbench.result
    if action == "_on_profile_selected":
        workbench._on_profile_selected(None)
        assert workbench.profile_path.get() == workbench._active_profile_path
    else:
        getattr(workbench, action)()
    assert workbench.result is old_result
    messagebox.showinfo.assert_called_once()


def test_save_guard_detects_uncommitted_cell_edits(monkeypatch: pytest.MonkeyPatch) -> None:
    workbench = _workbench(unsaved=False)
    workbench._use_tksheet = True
    workbench.sheet = Mock()
    workbench.rows = [{"Name": "old"}]
    workbench.table_fields = ["Name"]
    workbench.sheet.get_sheet_data.return_value = [["edited"]]
    prompt = Mock(return_value=None)
    monkeypatch.setattr(messagebox, "askyesnocancel", prompt)
    assert workbench._confirm_save_before("exiting") is False
    workbench.sheet.close_text_editor.assert_called_once()
    assert workbench.rows == [{"Name": "edited"}]
    assert workbench.has_unsaved_changes is True
    prompt.assert_called_once()


def test_save_guard_detects_pending_notes_event(monkeypatch: pytest.MonkeyPatch) -> None:
    workbench = _workbench(unsaved=False)
    workbench.notes.edit_modified.return_value = True
    monkeypatch.setattr(messagebox, "askyesnocancel", Mock(return_value=None))
    assert workbench._confirm_save_before("clearing") is False
    assert workbench.has_unsaved_changes is True


@pytest.mark.parametrize("use_dropdown", [False, True])
def test_invalid_profile_preserves_current_selection_and_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_dropdown: bool,
) -> None:
    workbench = _image_workbench(tmp_path)
    workbench.profile_path.set(workbench._active_profile_path)
    path = tmp_path / "bad.yml"
    path.write_text("fields: [", encoding="utf-8")
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **_: str(path))
    monkeypatch.setattr(messagebox, "showerror", Mock())
    workbench._confirm_save_before = Mock(side_effect=AssertionError("prompt"))
    old_result = workbench.result
    if use_dropdown:
        workbench.profile_path.set(str(path))
        workbench._on_profile_selected(None)
    else:
        workbench._browse_profile()
    assert workbench.result is old_result
    assert workbench.profile_path.get() == workbench._active_profile_path == "old-profile.yml"
    messagebox.showerror.assert_called_once()


def test_loading_project_without_image_clears_previous_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench = _image_workbench(tmp_path, unsaved=False)
    workbench.result.run_dir = tmp_path
    loaded = SimpleNamespace(
        run_dir=tmp_path,
        manifest={"run_id": "new-project"},
        output_paths={},
        fields=["Unit"],
        rows=[{"Unit": "Qal"}],
        original_rows=[{"Unit": "Qal"}],
        feedback_records=[],
        notes_text="New project notes",
        source_image_path=None,
    )
    monkeypatch.setattr(filedialog, "askdirectory", lambda **_: str(tmp_path))
    monkeypatch.setattr(gui, "load_review_project", lambda _: loaded)
    workbench._load_project()
    assert workbench.result.run_id == "new-project"
    assert workbench.image_path.get() == ""
    assert (
        workbench.source_image is workbench.preview_photo is workbench.preview_source_path is None
    )
    assert workbench.profile_path.get() == workbench._active_profile_path == ""
    assert workbench.rows == [{"Unit": "Qal"}]


def test_unreadable_project_image_preserves_current_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbench = _image_workbench(tmp_path)
    workbench.result.run_dir = tmp_path
    old_result, old_preview = workbench.result, workbench.source_image
    monkeypatch.setattr(filedialog, "askdirectory", lambda **_: str(tmp_path))
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *_, **__: False)
    monkeypatch.setattr(messagebox, "showerror", Mock())
    monkeypatch.setattr(
        gui,
        "load_review_project",
        lambda _: SimpleNamespace(source_image_path=tmp_path / "missing.png"),
    )
    workbench._load_project()
    assert workbench.result is old_result
    assert workbench.source_image is old_preview
    assert workbench.has_unsaved_changes is True
    assert workbench.rows == [{"Name": "edited"}]
    messagebox.showerror.assert_called_once()
