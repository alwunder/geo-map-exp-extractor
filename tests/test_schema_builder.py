from pathlib import Path

from geo_map_exp_extractor.config import load_profile
from geo_map_exp_extractor.schema_builder import build_response_schema, build_text_format


def test_build_response_schema_uses_dynamic_fields() -> None:
    profile = load_profile(Path("profiles/stratigraphic_column.yml"))

    schema = build_response_schema(profile)
    row_schema = schema["properties"]["rows"]["items"]

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["fields", "rows", "notes", "warnings"]
    assert row_schema["required"] == profile.fields
    assert list(row_schema["properties"].keys()) == profile.fields
    assert row_schema["additionalProperties"] is False


def test_build_text_format_enables_strict_json_schema() -> None:
    profile = load_profile(Path("profiles/water_production.yml"))

    text_format = build_text_format(profile)

    assert text_format["type"] == "json_schema"
    assert text_format["strict"] is True
    assert text_format["name"] == "water_production_extraction"
    assert text_format["schema"]["properties"]["fields"]["items"]["enum"] == profile.fields
