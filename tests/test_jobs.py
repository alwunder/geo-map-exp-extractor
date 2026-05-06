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
from geo_map_exp_extractor.openai_runner import ExtractionResult


def _make_image(path: Path) -> None:
    Image.new("RGB", (16, 8), color="white").save(path)


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
        raw_response={"id": "resp_test", "output_text": "{}"},
    )


def test_run_extraction_job_creates_timestamped_run_folder(tmp_path: Path) -> None:
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
        "profile.yml",
        "prompt.txt",
        "raw_response.json",
        "extracted.csv",
        "extracted.json",
        "corrected.csv",
        "corrected.json",
        "manifest.json",
        "notes.md",
        "feedback.jsonl",
    }
    assert expected_files == {path.name for path in result.run_dir.iterdir()}


def test_run_extraction_job_writes_manifest(tmp_path: Path) -> None:
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

    manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "20260506T123000Z"
    assert manifest["timestamp"] == "2026-05-06T12:30:00+00:00"
    assert manifest["image_path"] == str(image_path)
    assert manifest["image_dimensions"] == {"width": 16, "height": 8}
    assert manifest["profile_id"] == "water_production"
    assert manifest["profile_fields"] == result.fields
    assert manifest["model"] == "test-model"
    assert manifest["output_paths"]["corrected_csv"].endswith("corrected.csv")
    assert "package_version" in manifest


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
