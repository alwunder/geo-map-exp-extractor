"""Configuration and profile loading."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from geo_map_exp_extractor.settings import (
    DEFAULT_IMAGE_DETAIL,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_SERVICE_TIER,
    SUPPORTED_IMAGE_DETAILS,
    SUPPORTED_REASONING_EFFORTS,
    SUPPORTED_SERVICE_TIERS,
)


class VocabularyReference(BaseModel):
    """A pinned CSV vocabulary, relative to the profile file."""

    model_config = ConfigDict(extra="forbid")

    path: str
    value_column: str
    definition_column: str


class FieldRule(BaseModel):
    """Optional field guidance and closed vocabulary for any profile."""

    model_config = ConfigDict(extra="forbid")

    description: str = ""
    enum: list[str] | None = Field(default=None, min_length=1)
    vocabulary: VocabularyReference | None = None
    terms: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_vocabulary(self) -> FieldRule:
        if self.enum is not None and self.vocabulary is not None:
            raise ValueError("use enum or vocabulary, not both")
        if self.enum is not None and len(set(self.enum)) != len(self.enum):
            raise ValueError("enum values must be unique")
        return self

    @property
    def allowed_values(self) -> list[str] | None:
        if self.vocabulary is not None:
            if not self.terms:
                raise ValueError("vocabulary is unresolved; use load_profile to load CSV terms")
            return ["", *self.terms]
        return self.enum


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
    field_rules: dict[str, FieldRule] = Field(default_factory=dict)
    allow_segmented_mode: bool = True
    model: str = DEFAULT_MODEL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    service_tier: str = DEFAULT_SERVICE_TIER
    image_detail: str = DEFAULT_IMAGE_DETAIL
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS

    @model_validator(mode="after")
    def check_field_rules(self) -> ExtractionProfile:
        unknown = self.field_rules.keys() - set(self.fields)
        if unknown:
            raise ValueError(f"field_rules contains unknown fields: {sorted(unknown)}")
        return self

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

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "model must not be empty"
            raise ValueError(msg)
        return normalized

    @field_validator("reasoning_effort")
    @classmethod
    def validate_reasoning_effort(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_REASONING_EFFORTS:
            supported = ", ".join(SUPPORTED_REASONING_EFFORTS)
            msg = f"unsupported reasoning_effort {value!r}; expected one of: {supported}"
            raise ValueError(msg)
        return normalized

    @field_validator("service_tier")
    @classmethod
    def validate_service_tier(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_SERVICE_TIERS:
            supported = ", ".join(SUPPORTED_SERVICE_TIERS)
            msg = f"unsupported service_tier {value!r}; expected one of: {supported}"
            raise ValueError(msg)
        return normalized

    @field_validator("image_detail")
    @classmethod
    def validate_image_detail(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_IMAGE_DETAILS:
            supported = ", ".join(SUPPORTED_IMAGE_DETAILS)
            msg = f"unsupported image_detail {value!r}; expected one of: {supported}"
            raise ValueError(msg)
        return normalized

    @field_validator("max_output_tokens")
    @classmethod
    def validate_max_output_tokens(cls, value: int) -> int:
        if value <= 0:
            msg = "max_output_tokens must be greater than zero"
            raise ValueError(msg)
        return value


def load_profile(path: str | Path) -> ExtractionProfile:
    """Load and validate an extraction profile YAML file."""

    profile_path = Path(path)
    with profile_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        msg = f"profile must be a YAML mapping: {profile_path}"
        raise ValueError(msg)
    profile = ExtractionProfile.model_validate(data)
    for rule in profile.field_rules.values():
        if rule.vocabulary is None:
            continue
        reference = rule.vocabulary
        vocabulary_path = profile_path.parent / reference.path
        terms: dict[str, str] = {}
        with vocabulary_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {reference.value_column, reference.definition_column}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError(f"missing vocabulary columns in {vocabulary_path}: {required}")
            for row in reader:
                value = row[reference.value_column]
                definition = row[reference.definition_column]
                if not value or not value.strip() or value in terms or not definition:
                    raise ValueError(f"empty or duplicate vocabulary entry in {vocabulary_path}")
                terms[value] = definition
        if not terms:
            raise ValueError(f"empty vocabulary: {vocabulary_path}")
        rule.terms = terms
    return profile
