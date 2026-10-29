import threading
from types import SimpleNamespace
from unittest.mock import Mock

from geo_map_exp_extractor import gui_extraction
from geo_map_exp_extractor.gui import ReviewWorkbench


def workbench(tmp_path):
    app = object.__new__(ReviewWorkbench)
    app._block_while_running = Mock(return_value=False)
    app.result = SimpleNamespace(
        run_dir=tmp_path, manifest={"continuation_available": True}, usage={"total_tokens": 30}
    )
    (tmp_path / "extraction_session.json").write_text("{}", encoding="utf-8")
    app.rows = []
    app._save_corrected = Mock(return_value=True)
    app._capture_review_state = Mock(return_value=SimpleNamespace(model_dump=lambda **_: {}))
    app.api_key_override = None
    app._start_progress_dialog = Mock()
    app.run_button = Mock()
    app.after = Mock()
    app._progress_elapsed = Mock()
    app._cancel_event = threading.Event()
    return app


def test_declined_allowance_never_starts_worker(tmp_path, monkeypatch):
    app = workbench(tmp_path)
    monkeypatch.setattr(gui_extraction.simpledialog, "askinteger", lambda *a, **k: None)
    start = Mock()
    monkeypatch.setattr(gui_extraction.threading, "Thread", start)
    app._continue_extraction()
    app._save_corrected.assert_not_called()
    start.assert_not_called()


def test_failed_save_blocks_continuation_request(tmp_path, monkeypatch):
    app = workbench(tmp_path)
    app._save_corrected.return_value = False
    monkeypatch.setattr(gui_extraction.simpledialog, "askinteger", lambda *a, **k: 100)
    start = Mock()
    monkeypatch.setattr(gui_extraction.threading, "Thread", start)
    app._continue_extraction()
    start.assert_not_called()


def test_approved_continuation_passes_budget_review_and_cancel_event(tmp_path, monkeypatch):
    app = workbench(tmp_path)
    monkeypatch.setattr(gui_extraction.simpledialog, "askinteger", lambda *a, **k: 24000)
    thread = Mock()
    start = Mock(return_value=thread)
    monkeypatch.setattr(gui_extraction.threading, "Thread", start)
    app._continue_extraction()
    kwargs = start.call_args.kwargs["kwargs"]
    assert kwargs["continue_run_dir"] == tmp_path
    assert kwargs["max_output_tokens"] == 24000
    assert kwargs["cancel_event"] is app._cancel_event
    assert kwargs["review_state"] == {}
    thread.start.assert_called_once()
    app._save_corrected.assert_called_once()


def test_cancel_signals_worker_without_destroying_progress(tmp_path):
    app = workbench(tmp_path)
    app.destroy = Mock()
    app._cancel_extraction()
    assert app._cancel_event.is_set()
    app.destroy.assert_not_called()


def test_status_remains_incomplete_after_manual_save(tmp_path):
    app = workbench(tmp_path)
    app.status = Mock()
    app.has_unsaved_changes = False
    app._set_status_message("Project saved")
    assert "INCOMPLETE TABLE" in app.status.set.call_args.args[0]
