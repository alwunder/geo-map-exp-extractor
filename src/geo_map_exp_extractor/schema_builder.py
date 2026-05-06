"""Dynamic JSON Schema construction for structured model output."""

from __future__ import annotations

from typing import Any

from geo_map_exp_extractor.config import ExtractionProfile
from geo_map_exp_extractor.settings import SCHEMA_VERSION

JsonSchema = dict[str, Any]


def build_response_schema(profile: ExtractionProfile) -> JsonSchema:
    """Build a strict JSON Schema using the profile's field order and names."""

    row_properties: dict[str, JsonSchema] = {
        field: {"type": "string", "description": f"Value for the {field} column."}
        for field in profile.fields
    }

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["fields", "rows", "notes", "warnings"],
        "properties": {
            "fields": {
                "type": "array",
                "description": "Output field names in the exact order defined by the profile.",
                "items": {"type": "string", "enum": profile.fields},
            },
            "rows": {
                "type": "array",
                "description": "Extracted table rows. Each row includes every profile field.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": profile.fields,
                    "properties": row_properties,
                },
            },
            "notes": {
                "type": "array",
                "description": "Non-warning notes about extraction decisions or visual ambiguity.",
                "items": {"type": "string"},
            },
            "warnings": {
                "type": "array",
                "description": "Warnings for low confidence, illegible text, cut-off image areas, or ambiguity.",
                "items": {"type": "string"},
            },
        },
    }


def build_text_format(profile: ExtractionProfile) -> JsonSchema:
    """Build the Responses API text.format payload for Structured Outputs."""

    return {
        "type": "json_schema",
        "name": f"{profile.id}_extraction",
        "description": f"Structured extraction output for {profile.name}.",
        "strict": True,
        "schema_version": SCHEMA_VERSION,
        "schema": build_response_schema(profile),
    }
