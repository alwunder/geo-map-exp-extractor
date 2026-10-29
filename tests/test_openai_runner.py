import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from geo_map_exp_extractor.config import load_profile
from geo_map_exp_extractor.openai_runner import (
    _extract_usage,
    _is_incomplete_for_max_output_tokens,
    _validate_request_options,
    run_extraction,
    validate_extraction_data,
)
from geo_map_exp_extractor.prompt_builder import build_prompt
from geo_map_exp_extractor.schema_builder import build_text_format


def test_validate_extraction_data_accepts_dynamic_fields_with_spaces() -> None:
    profile = load_profile(Path("profiles/engineering_properties.yml"))
    data = {
        "fields": profile.fields,
        "rows": [
            {
                "MapUnit": "Qa",
                "Lithology": "sand and gravel",
                "List of Geologic Formations": "Alluvium",
                "Description": "Generally suitable for foundations.",
            }
        ],
        "notes": [],
        "warnings": [],
    }

    assert validate_extraction_data(profile, data) == data


def test_extract_usage_includes_reasoning_tokens() -> None:
    usage = _extract_usage(
        {
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
                "total_tokens": 30,
                "input_tokens_details": {"cached_tokens": 2},
                "output_tokens_details": {"reasoning_tokens": 7},
            }
        }
    )

    assert usage is not None
    assert usage.input_tokens == 10
    assert usage.output_tokens == 20
    assert usage.total_tokens == 30
    assert usage.cached_tokens == 2
    assert usage.reasoning_tokens == 7


def test_detects_max_output_tokens_incomplete_response() -> None:
    raw = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
    assert _is_incomplete_for_max_output_tokens(raw) is True


def test_gpt_6_astra_rejects_unsupported_none_reasoning_effort() -> None:
    with pytest.raises(ValueError, match="low, medium"):
        _validate_request_options("gpt-6-astra", "none", "default")


def test_known_model_rejects_unsupported_service_tier() -> None:
    with pytest.raises(ValueError, match="service tier"):
        _validate_request_options("gpt-5.6-sol", "medium", "ultrafast")


@pytest.mark.parametrize("provide_schema", [False, True])
def test_gems_request_preserves_quoted_term_without_invalid_enum(
    monkeypatch,
    provide_schema: bool,
) -> None:
    profile = load_profile(Path("profiles/stratigraphic_column_gems_dmu.yml"))
    term = '"Made" or human-engineered land'
    row = dict.fromkeys(profile.fields, "")
    row.update(GeoMaterial=term, GeoMaterialConfidence="High")
    data = {"fields": profile.fields, "rows": [row], "notes": [], "warnings": []}
    response = SimpleNamespace(output_text=json.dumps(data), model_dump=lambda **_: {})

    def create(**payload):
        schema = payload["text"]["format"]["schema"]
        columns = schema["properties"]["rows"]["items"]["properties"]
        assert columns["GeoMaterial"]["type"] == "string"
        assert "enum" not in columns["GeoMaterial"]
        assert columns["GeoMaterialConfidence"]["enum"] == ["", "High", "Medium", "Low"]
        assert term in payload["input"][0]["content"][0]["text"]
        return response

    client = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=create)))
    monkeypatch.setattr(
        "geo_map_exp_extractor.openai_runner._build_openai_client", lambda _: client
    )
    monkeypatch.setattr("geo_map_exp_extractor.openai_runner.image_to_data_url", lambda _: "image")
    result = run_extraction(
        image_path="unused.png",
        profile=profile,
        api_key="test-key",
        prompt=build_prompt(profile, Path("prompts/extraction_prompt.md")),
        schema=build_text_format(profile) if provide_schema else None,
    )
    assert result.data["rows"][0]["GeoMaterial"] == term
    client.responses.create.assert_called_once()


@pytest.mark.parametrize(("status", "expected_attempts"), [(400, 1), (429, 3)])
def test_request_error_reports_actual_attempt_count(monkeypatch, status, expected_attempts) -> None:
    error = RuntimeError("request rejected")
    error.status_code = status
    create = Mock(side_effect=error)
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(
        "geo_map_exp_extractor.openai_runner._build_openai_client", lambda _: client
    )
    monkeypatch.setattr("geo_map_exp_extractor.openai_runner.image_to_data_url", lambda _: "image")
    with pytest.raises(RuntimeError, match=f"after {expected_attempts} attempt"):
        run_extraction(
            image_path="unused.png",
            prompt="test",
            api_key="test-key",
            profile=load_profile(Path("profiles/stratigraphic_column.yml")),
            retries=3,
            retry_backoff_seconds=0,
        )
    assert create.call_count == expected_attempts
