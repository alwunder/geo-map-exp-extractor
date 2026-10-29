import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from geo_map_exp_extractor.config import load_profile
from geo_map_exp_extractor.extraction_session import (
    SESSION_FILE,
    continue_extraction_job,
    read_session,
)
from geo_map_exp_extractor.jobs import (
    load_review_project,
    run_extraction_job,
    write_corrected_outputs,
)
from geo_map_exp_extractor.openai_runner import ExtractionResult, run_extraction
from geo_map_exp_extractor.partial_output import recover_complete_rows


@pytest.fixture
def profile():
    return load_profile(Path("profiles/stratigraphic_column.yml"))


def row(profile, label):
    return {field: label if i == 0 else "" for i, field in enumerate(profile.fields)}


def data(profile, rows):
    return {"fields": profile.fields, "rows": rows, "notes": [], "warnings": []}


@pytest.mark.parametrize("tail", [', {"MapUnit": "unfinished', ', {"MapUnit": "bad\\', ",", ""])
def test_recovers_only_whole_valid_rows(profile, tail):
    retained = row(profile, 'a "quoted" unit, with } braces')
    text = '{"fields": ' + json.dumps(profile.fields) + ', "rows": [' + json.dumps(retained) + tail
    assert recover_complete_rows(profile, text)["rows"] == [retained]


def test_invalid_row_stops_prefix_without_skipping(profile):
    valid = row(profile, "first")
    text = json.dumps(data(profile, [valid, {"wrong": "schema"}, row(profile, "third")]))
    assert recover_complete_rows(profile, text)["rows"] == [valid]
    assert recover_complete_rows(profile, text.replace(profile.fields[0], "wrong", 1))["rows"] == []


def response(profile, rows=(), *, status="completed", text=None, response_id="resp_one"):
    text = json.dumps(data(profile, list(rows))) if text is None else text
    raw = {
        "id": response_id,
        "status": status,
        "incomplete_details": None,
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
    }
    if status == "incomplete":
        raw["incomplete_details"] = {"reason": "max_output_tokens"}
    return SimpleNamespace(output_text=text, model_dump=lambda **_: raw)


def client_setup(monkeypatch, responses):
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        "geo_map_exp_extractor.openai_runner._build_openai_client", lambda _: client
    )
    monkeypatch.setattr("geo_map_exp_extractor.openai_runner.image_to_data_url", lambda _: "image")
    monkeypatch.setattr("geo_map_exp_extractor.openai_runner.time.sleep", lambda _: None)
    return client


def test_background_persists_id_then_cancels_with_partial_rows(monkeypatch, profile):
    stop = Event()
    first = response(profile, status="in_progress", text="")
    text = json.dumps(data(profile, [row(profile, "one")]))[:-4]
    cancelled = response(profile, status="cancelled", text=text)
    calls = SimpleNamespace(create=Mock(return_value=first), cancel=Mock(return_value=cancelled))
    client_setup(monkeypatch, calls)
    snapshots = []

    def checkpoint(raw):
        snapshots.append(raw)
        stop.set()

    result = run_extraction(
        image_path="unused",
        prompt="p",
        profile=profile,
        api_key="test",
        background=True,
        cancel_event=stop,
        on_response=checkpoint,
    )
    assert snapshots[0]["id"] == "resp_one"
    calls.cancel.assert_called_once_with("resp_one")
    assert calls.create.call_args.kwargs["background"] is True
    assert result.interruption_reason == "cancelled"
    assert result.data["rows"] == [row(profile, "one")]
    assert result.usage.total_tokens == 30


def test_retrieve_existing_response_never_creates_again(monkeypatch, profile):
    calls = SimpleNamespace(create=Mock(), retrieve=Mock(return_value=response(profile)))
    client_setup(monkeypatch, calls)
    run_extraction(
        image_path="unused",
        prompt="p",
        profile=profile,
        api_key="test",
        background=True,
        response_id="existing",
    )
    calls.create.assert_not_called()
    calls.retrieve.assert_called_once_with("existing")


def test_cancel_before_submission_makes_no_api_call(monkeypatch, profile):
    calls = SimpleNamespace(create=Mock())
    client_setup(monkeypatch, calls)
    stop = Event()
    stop.set()
    result = run_extraction(
        image_path="unused",
        prompt="p",
        profile=profile,
        api_key="test",
        background=True,
        cancel_event=stop,
    )
    calls.create.assert_not_called()
    assert result.interruption_reason == "cancelled"


def test_ambiguous_background_create_is_not_retried(monkeypatch, profile):
    error = RuntimeError("connection lost")
    error.status_code = 500
    calls = SimpleNamespace(create=Mock(side_effect=error))
    client_setup(monkeypatch, calls)
    with pytest.raises(RuntimeError, match="after 1 attempt"):
        run_extraction(
            image_path="unused", prompt="p", profile=profile, api_key="test", background=True
        )
    calls.create.assert_called_once()


@pytest.mark.parametrize("boundary,expected", [(2, 1), (1, 0)])
def test_truncated_continuation_requires_confirmed_boundary(
    monkeypatch, profile, boundary, expected
):
    text = json.dumps({"resume_after": boundary, **data(profile, [row(profile, "next")])})[:-4]
    calls = SimpleNamespace(
        create=Mock(return_value=response(profile, status="incomplete", text=text))
    )
    client_setup(monkeypatch, calls)
    result = run_extraction(
        image_path="unused",
        prompt="p",
        profile=profile,
        api_key="test",
        background=True,
        resume_after=2,
    )
    assert len(result.data["rows"]) == expected
    assert result.incomplete_max_output_tokens


def result_for(profile, labels, *, reason=None):
    raw = response(profile, [row(profile, label) for label in labels]).model_dump()
    return ExtractionResult(
        data=data(profile, [row(profile, label) for label in labels]),
        raw_response=raw,
        interruption_reason=reason,
        incomplete_max_output_tokens=reason == "max_output_tokens",
    )


def initial_run(tmp_path, profile, runner, **kwargs):
    image = tmp_path / "map.png"
    Image.new("RGB", (80, 100), "white").save(image)
    return run_extraction_job(
        image_path=image,
        profile_path="profiles/stratigraphic_column.yml",
        output_dir=tmp_path / "runs",
        recoverable=True,
        use_cache=False,
        extraction_runner=runner,
        **kwargs,
    )


def test_partial_project_reopens_and_continuation_preserves_review_and_usage(tmp_path, profile):
    initial = initial_run(
        tmp_path, profile, lambda **_: result_for(profile, ["one"], reason="max_output_tokens")
    )
    assert load_review_project(initial.run_dir).rows == [row(profile, "one")]
    assert initial.manifest["continuation_available"]
    assert initial.usage["total_tokens"] == 30
    reviewed = [row(profile, "edited")]
    write_corrected_outputs(
        rows=reviewed,
        fields=profile.fields,
        csv_path=initial.output_paths["corrected_csv"],
        json_path=initial.output_paths["corrected_json"],
    )
    initial.output_paths["notes"].write_text("My review note", encoding="utf-8")
    prior = (initial.run_dir / SESSION_FILE).read_bytes()

    def next_call(**kwargs):
        assert kwargs["resume_after"] == 1
        assert '"one"' in kwargs["prompt"]
        assert '"edited"' not in kwargs["prompt"]
        assert kwargs["max_output_tokens"] == 32000
        return result_for(profile, ["two"])

    continued = continue_extraction_job(
        run_dir=initial.run_dir, max_output_tokens=32000, extraction_runner=next_call
    )
    assert continued.run_dir != initial.run_dir
    assert (initial.run_dir / SESSION_FILE).read_bytes() == prior
    loaded = load_review_project(continued.run_dir)
    assert loaded.rows == reviewed + [row(profile, "two")]
    assert loaded.original_rows == [row(profile, "one"), row(profile, "two")]
    assert loaded.notes_text == "My review note"
    assert continued.manifest["extraction_status"] == "complete"
    assert not continued.manifest["continuation_available"]
    assert continued.usage["total_tokens"] == 60


def test_network_disconnect_keeps_response_id_for_retrieval(tmp_path, profile):
    def disconnected(**kwargs):
        kwargs["on_response"]({"id": "resp_keep", "status": "in_progress"})
        raise ConnectionError("offline")

    initial = initial_run(tmp_path, profile, disconnected)
    assert read_session(initial.run_dir).pending["id"] == "resp_keep"

    def retrieve(**kwargs):
        assert kwargs["response_id"] == "resp_keep"
        assert kwargs["resume_after"] is None
        return result_for(profile, ["one"])

    continued = continue_extraction_job(
        run_dir=initial.run_dir, max_output_tokens=100, extraction_runner=retrieve
    )
    assert continued.manifest["extraction_status"] == "complete"
    assert len(continued.rows) == 1


def test_repeated_boundary_is_not_silently_appended(tmp_path, profile):
    initial = initial_run(
        tmp_path, profile, lambda **_: result_for(profile, ["one"], reason="max_output_tokens")
    )
    continued = continue_extraction_job(
        run_dir=initial.run_dir,
        max_output_tokens=100,
        extraction_runner=lambda **_: result_for(profile, ["one", "two"]),
    )
    assert continued.manifest["extraction_status"] == "boundary_review"
    assert len(continued.rows) == 1
    assert len(read_session(continued.run_dir).attempts) == 2


def test_second_token_limit_stops_without_automatic_extra_request(tmp_path, profile):
    initial = initial_run(
        tmp_path, profile, lambda **_: result_for(profile, ["one"], reason="max_output_tokens")
    )
    runner = Mock(return_value=result_for(profile, ["two"], reason="max_output_tokens"))
    continued = continue_extraction_job(
        run_dir=initial.run_dir, max_output_tokens=100, extraction_runner=runner
    )
    runner.assert_called_once()
    assert len(continued.rows) == 2
    assert continued.manifest["continuation_available"]


def test_crash_checkpoint_repairs_missing_exports(tmp_path, profile):
    initial = initial_run(
        tmp_path, profile, lambda **_: result_for(profile, ["one"], reason="max_output_tokens")
    )
    initial.output_paths["manifest"].unlink()
    initial.output_paths["extracted_json"].write_text("broken", encoding="utf-8")
    loaded = load_review_project(initial.run_dir)
    assert loaded.rows == [row(profile, "one")]


def test_changed_input_blocks_continuation_before_billing(tmp_path, profile):
    initial = initial_run(
        tmp_path, profile, lambda **_: result_for(profile, [], reason="max_output_tokens")
    )
    initial.output_paths["prompt"].write_text("modified prompt", encoding="utf-8")
    runner = Mock()
    with pytest.raises(ValueError, match="input changed"):
        continue_extraction_job(
            run_dir=initial.run_dir, max_output_tokens=100, extraction_runner=runner
        )
    runner.assert_not_called()


def test_reasoning_only_limit_preserves_usage_without_fabricating_rows(monkeypatch, profile):
    calls = SimpleNamespace(
        create=Mock(return_value=response(profile, status="incomplete", text=""))
    )
    client_setup(monkeypatch, calls)
    result = run_extraction(
        image_path="unused", prompt="p", profile=profile, api_key="test", background=True
    )
    assert result.data["rows"] == []
    assert result.usage.total_tokens == 30
    assert result.interruption_reason == "max_output_tokens"


def test_partial_controlled_vocabulary_failure_keeps_only_valid_prefix():
    profile = load_profile(Path("profiles/stratigraphic_column_gems_dmu.yml"))
    valid = row(profile, "one")
    invalid = {**row(profile, "two"), "GeoMaterial": "invented material"}
    assert recover_complete_rows(profile, json.dumps(data(profile, [valid, invalid])))["rows"] == [
        valid
    ]


def test_complete_cached_response_reused_but_partial_never_cached(tmp_path, profile, monkeypatch):
    monkeypatch.setattr("geo_map_exp_extractor.jobs._cache_root", lambda: tmp_path / "cache")
    image = tmp_path / "image.png"
    Image.new("RGB", (80, 80), "white").save(image)
    kwargs = dict(
        image_path=image,
        profile_path="profiles/stratigraphic_column.yml",
        output_dir=tmp_path / "runs",
        recoverable=True,
    )
    partial = Mock(return_value=result_for(profile, ["one"], reason="max_output_tokens"))
    run_extraction_job(**kwargs, extraction_runner=partial)
    assert not (tmp_path / "cache").exists()
    complete = Mock(return_value=result_for(profile, ["one", "two"]))
    run_extraction_job(**kwargs, extraction_runner=complete)
    never = Mock(side_effect=AssertionError("cache should avoid a new request"))
    cached = run_extraction_job(**kwargs, extraction_runner=never)
    never.assert_not_called()
    assert cached.cache_reused
    assert cached.manifest["api_call_mode"] == "cache_reuse"
    assert cached.usage["total_tokens"] is None
    rerun = Mock(return_value=result_for(profile, ["new"]))
    run_extraction_job(**kwargs, extraction_runner=rerun, force_rerun=True)
    rerun.assert_called_once()
    assert run_extraction_job(**kwargs, extraction_runner=never).rows == [row(profile, "new")]


def test_segment_limit_stops_before_next_segment_and_resume_finishes_remaining(
    tmp_path, monkeypatch
):
    profile = load_profile(Path("profiles/water_production.yml"))
    image = tmp_path / "image.png"
    Image.new("RGB", (100, 900), "white").save(image)
    runner = Mock(
        side_effect=[
            result_for(profile, ["first"]),
            result_for(profile, ["second-a"], reason="max_output_tokens"),
        ]
    )
    initial = run_extraction_job(
        image_path=image,
        profile_path="profiles/water_production.yml",
        output_dir=tmp_path / "runs",
        recoverable=True,
        use_cache=False,
        segmented_mode=True,
        segment_height_px=300,
        segment_overlap_px=0,
        extraction_runner=runner,
    )
    assert runner.call_count == 2
    assert read_session(initial.run_dir).segment_index == 1
    boundaries = []

    def finish(**kwargs):
        boundaries.append(kwargs["resume_after"])
        return result_for(profile, ["second-b" if len(boundaries) == 1 else "third"])

    continued = continue_extraction_job(
        run_dir=initial.run_dir, max_output_tokens=200, extraction_runner=finish
    )
    assert boundaries == [1, None]
    assert len(continued.rows) == 4
    assert continued.manifest["extraction_status"] == "complete"


def test_reviewer_row_deletion_retains_original_mapping_in_continuation(tmp_path, profile):
    initial = initial_run(
        tmp_path,
        profile,
        lambda **_: result_for(profile, ["one", "two"], reason="max_output_tokens"),
    )
    review = {"rows": [row(profile, "edited two")], "original_rows": [row(profile, "two")]}
    continued = continue_extraction_job(
        run_dir=initial.run_dir,
        max_output_tokens=100,
        review_state=review,
        extraction_runner=lambda **_: result_for(profile, ["three"]),
    )
    loaded = load_review_project(continued.run_dir)
    assert loaded.rows == [row(profile, "edited two"), row(profile, "three")]
    assert loaded.original_rows == [row(profile, "two"), row(profile, "three")]


def test_manual_edits_survive_checkpoint_export_repair(tmp_path, profile):
    initial = initial_run(
        tmp_path, profile, lambda **_: result_for(profile, ["one"], reason="max_output_tokens")
    )
    continued = continue_extraction_job(
        run_dir=initial.run_dir,
        max_output_tokens=100,
        extraction_runner=lambda **_: result_for(profile, ["two"]),
    )
    write_corrected_outputs(
        rows=[row(profile, "manual")],
        fields=profile.fields,
        csv_path=continued.output_paths["corrected_csv"],
        json_path=continued.output_paths["corrected_json"],
    )
    assert load_review_project(continued.run_dir).rows == [row(profile, "manual")]


def test_missing_image_does_not_prevent_review_but_blocks_continuation(tmp_path, profile):
    initial = initial_run(
        tmp_path, profile, lambda **_: result_for(profile, ["one"], reason="max_output_tokens")
    )
    initial.output_paths["processed_image"].unlink()
    assert load_review_project(initial.run_dir).rows == [row(profile, "one")]
    with pytest.raises(OSError):
        continue_extraction_job(run_dir=initial.run_dir, max_output_tokens=100)


def test_corrupt_corrected_file_still_allows_autosave_recovery_load(tmp_path, profile):
    initial = initial_run(
        tmp_path, profile, lambda **_: result_for(profile, ["one"], reason="max_output_tokens")
    )
    continued = continue_extraction_job(
        run_dir=initial.run_dir,
        max_output_tokens=100,
        extraction_runner=lambda **_: result_for(profile, ["two"]),
    )
    continued.output_paths["corrected_json"].write_text("corrupted", encoding="utf-8")
    loaded = load_review_project(continued.run_dir, prefer_extracted=True)
    assert len(loaded.rows) == 2
    assert continued.output_paths["corrected_json"].read_text("utf-8") == "corrupted"
