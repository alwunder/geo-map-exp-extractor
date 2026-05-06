"""Configuration and profile loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExtractionProfile(BaseModel):
    """User-editable extraction profile loaded from YAML."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    task_label: str
    fields: list[str] = Field(min_length=1)
    include_intro_footnotes: bool
    preserve_wording: bool
    normalize_line_breaks: bool
    normalize_hyphenated_line_breaks: bool
    special_instructions: list[str] = Field(default_factory=list)

    @field_validator("id", "name", "task_label")
    @classmethod
    def require_non_empty_string(cls, value: str) -> str:
        if not value.strip():
            msg = "value must not be empty"
            raise ValueError(msg)
        return value

    @field_validator("fields")
    @classmethod
    def require_unique_non_empty_fields(cls, fields: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for field in fields:
            if not field.strip():
                msg = "profile fields must not be empty"
                raise ValueError(msg)
            if field in seen:
                msg = f"duplicate profile field: {field}"
                raise ValueError(msg)
            cleaned.append(field)
            seen.add(field)
        return cleaned

    @field_validator("special_instructions", mode="before")
    @classmethod
    def normalize_special_instructions(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


def load_profile(path: str | Path) -> ExtractionProfile:
    """Load and validate an extraction profile YAML file."""

    profile_path = Path(path)
    with profile_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        msg = f"profile must be a YAML mapping: {profile_path}"
        raise ValueError(msg)
    return ExtractionProfile.model_validate(data)
