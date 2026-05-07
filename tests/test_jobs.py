import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from geo_map_exp_extractor.jobs import (
    build_feedback_record,
    run_extraction_job,
    write_corrected_outputs,
    write_feedback_jsonl,
)
from geo_map_exp_extractor.openai_runner import (
    ExtractionResult,
    ExtractionValidationError,
    UsageSummary,
)


def _make_image(path: Path) -> None:
    Image.new("RGB", (1600, 1200), color="white").save(path)


def _fake_runner(**_: object) -> ExtractionResult:
    return ExtractionResult(
        data={
            "fields": [
                "MapUnit",
                "Properties",
                "Lithology",
                "List of Geologic Formations",
                "Description",
            ],
            "rows": [
                {
                    "MapUnit": "Qa",
                    "Properties": "Moderate permeability",
                    "Lithology": "sand and gravel",
                    "List of Geologic Formations": "Alluvium",
                    "Description": "Stream deposits.",
                }
            ],
            "notes": ["one note"],
            "warnings": [],
        },
        raw_response={
            "id": "resp_test",
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 200,
                "total_tokens": 1200,
                "input_tokens_details": {"cached_tokens": 100},
                "output_tokens_details": {"reasoning_tokens": 150},
            },
        },
        usage=UsageSummary(
            input_tokens=1000,
            output_tokens=200,
            total_tokens=1200,
            cached_tokens=100,
            reasoning_tokens=150,
        ),
    )


def test_run_extraction_job_creates_timestamped_run_folder(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("geo_map_exp_extractor.jobs._cache_root", lambda: tmp_path / ".cache")
    image_path = tmp_path / "source.png"
    _make_image(image_path)

    result = run_extraction_job(
        image_path=image_path,
        profile_path=Path("profiles/water_production.yml"),
        output_dir=tmp_path / "runs",
        model="test-model",
        extraction_runner=_fake_runner,
        timestamp=datetime(2026, 5, 6, 12, 30, tzinfo=timezone.utc),
    )

    assert result.run_id == "20260506T123000Z"
    assert result.run_dir.is_dir()
    expected_files = {
        "source_image.png",
        "processed_api_image.png",
        "profile.yml",
        "prompt.txt",
        "schema.json",
        "raw_response.json",
        "extracted.csv",
        "extracted.json",
        "manifest.json",
        "notes.md",
        "feedback.jsonl",
    }
    assert expected_files == {path.name for path in result.run_dir.iterdir()}


def test_run_extraction_job_writes_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("geo_map_exp_extractor.jobs._cache_root", lambda: tmp_path / ".cache")
    image_path = tmp_path / "source.png"
    _make_image(image_path)

    result = run_extraction_job(
        image_path=image_path,
        profile_path=Path("profiles/water_production.yml"),
        output_dir=tmp_path / "runs",
        model="gpt-5.5",
        extraction_runner=_fake_runner,
        timestamp=datetime(2026, 5, 6, 12, 30, tzinfo=timezone.utc),
    )

    manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "20260506T123000Z"
    assert manifest["timestamp"] == "2026-05-06T12:30:00+00:00"
    assert manifest["source_image_path"] == image_path.as_posix()
    assert manifest["original_image_dimensions"] == {"width": 1600, "height": 1200}
    assert manifest["profile_id"] == "water_production"
    assert manifest["profile_fields"] == result.fields
    assert manifest["model"] == "gpt-5.5"
    assert manifest["reasoning_effort"] == "medium"
    assert manifest["image_detail"] == "high"
    assert manifest["max_output_tokens"] == 12000
    assert manifest["api_call_mode"] == "fresh_api_call"
    assert manifest["input_tokens"] == 1000
    assert manifest["output_tokens"] == 200
    assert manifest["total_tokens"] == 1200
    assert manifest["cached_tokens"] == 100
    assert manifest["reasoning_tokens"] == 150
    assert isinstance(manifest["elapsed_seconds"], float)
    assert manifest["elapsed_seconds"] >= 0.0
    assert manifest["estimated_cost_usd"] is not None
    assert "package_version" in manifest


def test_run_extraction_job_dry_run_skips_api_call(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("geo_map_exp_extractor.jobs._cache_root", lambda: tmp_path / ".cache")
    image_path = tmp_path / "source.png"
    _make_image(image_path)

    calls = {"count": 0}

    def _never_call_runner(**_: object) -> ExtractionResult:
        calls["count"] += 1
        raise AssertionError("API runner should not be invoked during dry run")

    result = run_extraction_job(
        image_path=image_path,
        profile_path=Path("profiles/water_production.yml"),
        output_dir=tmp_path / "runs",
        model="test-model",
        extraction_runner=_never_call_runner,
        dry_run=True,
    )

    assert calls["count"] == 0
    assert result.dry_run is True
    assert result.rows == []
    assert not result.output_paths["raw_response"].exists()
    assert not result.output_paths["extracted_csv"].exists()
    assert result.rough_image_tokens > 0


def test_run_extraction_job_reuses_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("geo_map_exp_extractor.jobs._cache_root", lambda: tmp_path / ".cache")
    image_path = tmp_path / "source.png"
    _make_image(image_path)

    calls = {"count": 0}

    def _counting_runner(**_: object) -> ExtractionResult:
        calls["count"] += 1
        return _fake_runner()

    first = run_extraction_job(
        image_path=image_path,
        profile_path=Path("profiles/water_production.yml"),
        output_dir=tmp_path / "runs",
        model="cache-model",
        extraction_runner=_counting_runner,
    )
    second = run_extraction_job(
        image_path=image_path,
        profile_path=Path("profiles/water_production.yml"),
        output_dir=tmp_path / "runs",
        model="cache-model",
        extraction_runner=_counting_runner,
    )

    assert calls["count"] == 1
    assert first.cache_reused is False
    assert second.cache_reused is True
    assert second.rows == first.rows


def test_request_fingerprint_changes_with_detail_setting(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("geo_map_exp_extractor.jobs._cache_root", lambda: tmp_path / ".cache")
    image_path = tmp_path / "source.png"
    _make_image(image_path)

    high = run_extraction_job(
        image_path=image_path,
        profile_path=Path("profiles/water_production.yml"),
        output_dir=tmp_path / "runs",
        model="hash-model",
        extraction_runner=_fake_runner,
        dry_run=True,
        image_detail="high",
    )
    low = run_extraction_job(
        image_path=image_path,
        profile_path=Path("profiles/water_production.yml"),
        output_dir=tmp_path / "runs",
        model="hash-model",
        extraction_runner=_fake_runner,
        dry_run=True,
        image_detail="low",
    )

    assert high.request_fingerprint != low.request_fingerprint


def test_request_fingerprint_changes_with_max_output_token_limit_toggle(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("geo_map_exp_extractor.jobs._cache_root", lambda: tmp_path / ".cache")
    image_path = tmp_path / "source.png"
    _make_image(image_path)

    limited = run_extraction_job(
        image_path=image_path,
        profile_path=Path("profiles/water_production.yml"),
        output_dir=tmp_path / "runs",
        model="hash-model",
        extraction_runner=_fake_runner,
        dry_run=True,
        use_max_output_tokens_limit=True,
    )
    unlimited = run_extraction_job(
        image_path=image_path,
        profile_path=Path("profiles/water_production.yml"),
        output_dir=tmp_path / "runs",
        model="hash-model",
        extraction_runner=_fake_runner,
        dry_run=True,
        use_max_output_tokens_limit=False,
    )

    assert limited.request_fingerprint != unlimited.request_fingerprint


def test_validation_failures_preserve_raw_response(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("geo_map_exp_extractor.jobs._cache_root", lambda: tmp_path / ".cache")
    image_path = tmp_path / "source.png"
    _make_image(image_path)

    def _invalid_runner(**_: object) -> ExtractionResult:
        raise ExtractionValidationError("bad schema", {"id": "raw-failure", "output_text": "bad"})

    try:
        run_extraction_job(
            image_path=image_path,
            profile_path=Path("profiles/water_production.yml"),
            output_dir=tmp_path / "runs",
            model="test-model",
            extraction_runner=_invalid_runner,
        )
        raise AssertionError("expected RuntimeError for structured validation failure")
    except RuntimeError:
        run_folders = sorted((tmp_path / "runs").iterdir())
        latest = run_folders[-1]
        raw_path = latest / "raw_response.json"
        assert raw_path.exists()
        loaded = json.loads(raw_path.read_text(encoding="utf-8"))
        assert loaded["id"] == "raw-failure"


def test_write_corrected_outputs_writes_json_and_csv(tmp_path: Path) -> None:
    rows = [{"MapUnit": "Qa", "Description": "Corrected text", "extra": "ignored"}]
    fields = ["MapUnit", "Description"]

    write_corrected_outputs(
        rows=rows,
        fields=fields,
        csv_path=tmp_path / "corrected.csv",
        json_path=tmp_path / "corrected.json",
    )

    with (tmp_path / "corrected.csv").open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == fields
        assert list(reader) == [{"MapUnit": "Qa", "Description": "Corrected text"}]
    assert json.loads((tmp_path / "corrected.json").read_text(encoding="utf-8")) == {
        "fields": fields,
        "rows": [{"MapUnit": "Qa", "Description": "Corrected text"}],
    }


def test_write_feedback_jsonl_writes_one_object_per_line(tmp_path: Path) -> None:
    records = [
        build_feedback_record(
            run_id="run-1",
            row_index=0,
            field_name="Description",
            original_value="old",
            corrected_value="new",
            status="corrected",
            comment="fixed wrap",
        ),
        build_feedback_record(
            run_id="run-1",
            row_index=None,
            field_name="notes",
            original_value="",
            corrected_value="Review note",
            status="note",
            comment="User review note",
        ),
    ]

    write_feedback_jsonl(records, tmp_path / "feedback.jsonl")

    lines = (tmp_path / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == records


def test_build_feedback_record_includes_structured_review_fields() -> None:
    record = build_feedback_record(
        run_id="run-2",
        profile_id="water_production",
        image="108056_7-DMU.png",
        row_index=1,
        field_name="Description",
        original_value="Model value",
        corrected_value="Corrected value",
        status="accepted_with_minor_edit",
        comment="Model handled line wrapping correctly.",
        event_type="final_review",
        timestamp="2026-05-07T10:15:00+00:00",
    )

    assert record["run_id"] == "run-2"
    assert record["profile_id"] == "water_production"
    assert record["image"] == "108056_7-DMU.png"
    assert record["field"] == "Description"
    assert record["model_value"] == "Model value"
    assert record["corrected_value"] == "Corrected value"
    assert record["status"] == "accepted_with_minor_edit"
    assert record["comment"] == "Model handled line wrapping correctly."
    assert record["event_type"] == "final_review"
    assert record["timestamp"] == "2026-05-07T10:15:00+00:00"
    # Backward compatibility for earlier consumers.
    assert record["field_name"] == "Description"
    assert record["original_value"] == "Model value"
