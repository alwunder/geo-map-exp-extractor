from pathlib import Path

from geo_map_exp_extractor.config import load_profile
from geo_map_exp_extractor.openai_runner import (
    _extract_usage,
    _is_incomplete_for_max_output_tokens,
    validate_extraction_data,
)


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
