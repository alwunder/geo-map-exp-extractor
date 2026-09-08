from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from geo_map_exp_extractor import gui
from geo_map_exp_extractor.gui import ReviewWorkbench, filedialog, messagebox
from geo_map_exp_extractor.jobs import ProjectLoadError


def _workbench(*, unsaved: bool = True) -> ReviewWorkbench:
    workbench = object.__new__(ReviewWorkbench)
    workbench.result = SimpleNamespace(run_id="test-run")
    workbench.has_unsaved_changes = unsaved
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
    workbench.output_dir = SimpleNamespace(get=lambda: str(tmp_path))
    workbench._confirm_save_before = lambda _: True  # type: ignore[method-assign]
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
    assert workbench.row_comments == ["Confirmed by reviewer.", "Keep this comment.", "Confirmed by reviewer."]
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
