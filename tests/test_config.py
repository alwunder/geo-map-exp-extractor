from pathlib import Path

import pytest
from pydantic import ValidationError

from geo_map_exp_extractor.config import ExtractionProfile, load_profile
from geo_map_exp_extractor.settings import (
    DEFAULT_IMAGE_DETAIL,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
)


def test_load_profile_preserves_field_order() -> None:
    profile = load_profile(Path("profiles/water_production.yml"))

    assert profile.id == "water_production"
    assert profile.fields == [
        "MapUnit",
        "Properties",
        "Lithology",
        "List of Geologic Formations",
        "Description",
    ]
    assert profile.include_intro_footnotes is True
    assert profile.model == DEFAULT_MODEL
    assert profile.reasoning_effort == DEFAULT_REASONING_EFFORT
    assert profile.image_detail == DEFAULT_IMAGE_DETAIL
    assert profile.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS


def test_profile_rejects_duplicate_fields() -> None:
    with pytest.raises(ValidationError):
        ExtractionProfile(
            id="duplicate_fields",
            name="Duplicate fields",
            task_label="duplicate task",
            fields=["MapUnit", "MapUnit"],
            include_intro_footnotes=True,
            preserve_wording=True,
            normalize_line_breaks=True,
            normalize_hyphenated_line_breaks=True,
            special_instructions=[],
        )


def test_profile_defaults_are_applied_when_model_config_is_missing() -> None:
    profile = ExtractionProfile(
        id="defaults_profile",
        name="Defaults profile",
        task_label="defaults task",
        fields=["MapUnit"],
        include_intro_footnotes=True,
        preserve_wording=True,
        normalize_line_breaks=True,
        normalize_hyphenated_line_breaks=True,
        special_instructions=[],
    )

    assert profile.model == DEFAULT_MODEL
    assert profile.reasoning_effort == DEFAULT_REASONING_EFFORT
    assert profile.image_detail == DEFAULT_IMAGE_DETAIL
    assert profile.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS


def test_profile_rejects_invalid_reasoning_effort() -> None:
    with pytest.raises(ValidationError):
        ExtractionProfile(
            id="invalid_reasoning",
            name="Invalid reasoning",
            task_label="invalid reasoning task",
            fields=["MapUnit"],
            include_intro_footnotes=True,
            preserve_wording=True,
            normalize_line_breaks=True,
            normalize_hyphenated_line_breaks=True,
            special_instructions=[],
            reasoning_effort="ultra",
        )
