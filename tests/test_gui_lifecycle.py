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
