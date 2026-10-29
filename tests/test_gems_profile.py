"""Offline checks for controlled extraction and portable vocabulary snapshots."""

from pathlib import Path

import pytest
import yaml
from PIL import Image
from pydantic import ValidationError

from geo_map_exp_extractor.config import ExtractionProfile, load_profile
from geo_map_exp_extractor.exporters import write_profile_snapshot
from geo_map_exp_extractor.jobs import run_extraction_job
from geo_map_exp_extractor.openai_runner import validate_extraction_data
from geo_map_exp_extractor.prompt_builder import build_prompt
from geo_map_exp_extractor.schema_builder import build_response_schema, build_text_format

PROFILE = Path("profiles/stratigraphic_column_gems_dmu.yml")
TEMPLATE = Path("prompts/extraction_prompt.md")


def test_vocabulary_is_loaded_relative_to_profile(monkeypatch, tmp_path: Path) -> None:
    source = PROFILE.resolve()
    monkeypatch.chdir(tmp_path)
    profile = load_profile(source)
    rule = profile.field_rules["GeoMaterial"]
    assert len(rule.terms) == 101
    assert "Sandstone and mudstone" in rule.terms
    assert "granite" not in rule.terms
    schema = build_response_schema(profile)
    columns = schema["properties"]["rows"]["items"]["properties"]
    assert list(columns) == profile.fields
    # Quoted canonical terms cannot be used in the API's strict string enum.
    assert columns["GeoMaterial"]["type"] == "string"
    assert "enum" not in columns["GeoMaterial"]
    assert '"Made" or human-engineered land' in rule.allowed_values
    assert columns["GeoMaterialConfidence"]["enum"] == ["", "High", "Medium", "Low"]
    assert columns["GeoMaterial"]["description"] == rule.description


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("GeoMaterial", "sandstone"),
        ("GeoMaterial", "Granite"),
        ("GeoMaterial", "Made or human-engineered land"),
        ("GeoMaterialConfidence", "Moderate"),
        ("ParagraphStyle", "DMUUnit99"),
    ],
)
def test_local_validation_rejects_non_vocabulary_values(field: str, value: str) -> None:
    profile = load_profile(PROFILE)
    row = dict.fromkeys(profile.fields, "")
    data = {"fields": profile.fields, "rows": [row], "notes": [], "warnings": []}
    assert validate_extraction_data(profile, data) == data
    row.update(GeoMaterial="Sandstone", GeoMaterialConfidence="High")
    assert validate_extraction_data(profile, data) == data
    row["GeoMaterial"] = '"Made" or human-engineered land'
    assert validate_extraction_data(profile, data) == data
    row[field] = value
    with pytest.raises(ValueError, match=f"row 1: {field}"):
        validate_extraction_data(profile, data)


def test_snapshot_can_rebuild_identical_prompt_and_schema(tmp_path: Path) -> None:
    profile = load_profile(PROFILE)
    snapshot = write_profile_snapshot(profile, tmp_path / "profile.yml")
    restored = load_profile(snapshot)
    assert restored.field_rules["GeoMaterial"].vocabulary is None
    assert build_prompt(restored, TEMPLATE) == build_prompt(profile, TEMPLATE)
    assert build_response_schema(restored) == build_response_schema(profile)


def test_prompt_includes_definitions_without_optional_notes() -> None:
    profile = load_profile(PROFILE)
    prompt = build_prompt(profile, TEMPLATE)
    assert profile.field_rules["GeoMaterial"].terms["Sandstone"] in prompt
    assert "GeoMaterialConfidence: Fit" in prompt
    assert "include_intro_footnotes: true" in prompt
    assert "Profile Notes:" not in prompt
    assert "{preserve_wording}" not in prompt


def test_prompt_respects_false_settings_and_legacy_templates(tmp_path: Path) -> None:
    profile = load_profile(PROFILE).model_copy(
        update={
            "include_intro_footnotes": False,
            "preserve_wording": False,
            "normalize_line_breaks": False,
            "normalize_hyphenated_line_breaks": False,
        }
    )
    prompt = build_prompt(profile, TEMPLATE)
    for name in (
        "include_intro_footnotes",
        "preserve_wording",
        "normalize_line_breaks",
        "normalize_hyphenated_line_breaks",
    ):
        assert f"{name}: false" in prompt
    legacy = tmp_path / "legacy.md"
    legacy.write_text("{task_label}\n{field_list}\n{special_instructions}", encoding="utf-8")
    assert "GeoMaterial: " in build_prompt(profile, legacy)


def test_field_rules_reject_unknown_fields() -> None:
    data = load_profile(PROFILE).model_dump()
    data["field_rules"]["GeoMaterials"] = {"description": "typo"}
    with pytest.raises(ValidationError, match="unknown fields"):
        ExtractionProfile.model_validate(data)


@pytest.mark.parametrize(
    "csv_text",
    [
        "bad,Definition\nRock,a\n",
        "GeoMaterial,Definition\nRock,a\nRock,b\n",
        "GeoMaterial,Definition\n",
        "GeoMaterial,Definition\n,blank term\n",
    ],
)
def test_malformed_vocabularies_fail_before_request(tmp_path: Path, csv_text: str) -> None:
    data = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    data["field_rules"]["GeoMaterial"]["vocabulary"]["path"] = "terms.csv"
    (tmp_path / "terms.csv").write_text(csv_text, encoding="utf-8")
    source = tmp_path / "profile.yml"
    source.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="vocabulary"):
        load_profile(source)


def test_gems_job_is_portable_and_rejects_segmented_mode(tmp_path: Path) -> None:
    image = tmp_path / "panel.png"
    Image.new("RGB", (100, 100), "white").save(image)
    result = run_extraction_job(
        image_path=image,
        profile_path=PROFILE,
        output_dir=tmp_path / "runs",
        dry_run=True,
    )
    saved_profile = load_profile(result.run_dir / "profile.yml")
    assert len(saved_profile.field_rules["GeoMaterial"].terms) == 101
    assert build_response_schema(saved_profile) == build_response_schema(load_profile(PROFILE))
    with pytest.raises(ValueError, match="whole-panel"):
        run_extraction_job(
            image_path=image,
            profile_path=PROFILE,
            output_dir=tmp_path / "segments",
            dry_run=True,
            segmented_mode=True,
        )
    assert not (tmp_path / "segments").exists()


def test_api_format_has_only_supported_parameters() -> None:
    assert set(build_text_format(load_profile(PROFILE))) == {
        "type",
        "name",
        "description",
        "strict",
        "schema",
    }
