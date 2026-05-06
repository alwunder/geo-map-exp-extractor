from pathlib import Path

from geo_map_exp_extractor.config import load_profile
from geo_map_exp_extractor.openai_runner import validate_extraction_data


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
