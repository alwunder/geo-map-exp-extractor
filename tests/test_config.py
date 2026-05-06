from pathlib import Path

import pytest
from pydantic import ValidationError

from geo_map_exp_extractor.config import ExtractionProfile, load_profile


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
