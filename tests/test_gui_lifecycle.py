from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from geo_map_exp_extractor.gui import ReviewWorkbench, messagebox


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
