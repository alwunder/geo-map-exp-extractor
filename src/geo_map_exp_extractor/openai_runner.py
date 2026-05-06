"""OpenAI Responses API integration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from geo_map_exp_extractor.config import ExtractionProfile
from geo_map_exp_extractor.env_utils import load_env_from_candidates
from geo_map_exp_extractor.image_io import image_to_data_url
from geo_map_exp_extractor.schema_builder import build_text_format

DEFAULT_MODEL = "gpt-4o-mini"


@dataclass(frozen=True)
class ExtractionResult:
    """Validated extraction data plus raw API response."""

    data: dict[str, Any]
    raw_response: dict[str, Any]


def build_output_model(profile: ExtractionProfile) -> type[BaseModel]:
    """Build a Pydantic validator that preserves dynamic profile fields."""

    row_model = create_model(  # type: ignore[call-overload]
        f"{profile.id.title().replace('_', '')}Row",
        __config__=ConfigDict(extra="forbid"),
        **{field: (str, Field(...)) for field in profile.fields},
    )
    output_model = create_model(  # type: ignore[call-overload]
        f"{profile.id.title().replace('_', '')}Extraction",
        __config__=ConfigDict(extra="forbid"),
        fields=(list[str], ...),
        rows=(list[row_model], ...),
        notes=(list[str], ...),
        warnings=(list[str], ...),
    )
    return output_model


def validate_extraction_data(profile: ExtractionProfile, data: dict[str, Any]) -> dict[str, Any]:
    """Validate model output and ensure the profile field order is preserved."""

    model = build_output_model(profile)
    parsed = model.model_validate(data)
    validated = parsed.model_dump()
    if validated["fields"] != profile.fields:
        msg = "model output fields do not exactly match the profile field order"
        raise ValueError(msg)
    return validated


def parse_response_json(response: Any) -> dict[str, Any]:
    """Extract structured JSON from an OpenAI SDK response object."""

    output_text = getattr(response, "output_text", None)
    if output_text:
        parsed = json.loads(output_text)
        if isinstance(parsed, dict):
            return parsed
    parsed_attr = getattr(response, "output_parsed", None)
    if isinstance(parsed_attr, BaseModel):
        return parsed_attr.model_dump()
    if isinstance(parsed_attr, dict):
        return parsed_attr
    msg = "OpenAI response did not contain parseable JSON output"
    raise ValueError(msg)


def response_to_dict(response: Any) -> dict[str, Any]:
    """Convert an OpenAI SDK response object to a JSON-serializable dict."""

    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json")
    if isinstance(response, dict):
        return response
    return json.loads(json.dumps(response, default=str))


def _build_openai_client(api_key: str) -> Any:
    """Construct an OpenAI client with a clear runtime error if SDK is unavailable."""

    try:
        from openai import OpenAI  # Local import keeps non-API code paths importable.
    except ImportError as exc:  # pragma: no cover - depends on environment package set.
        msg = "OpenAI SDK is required to run extraction, but it is not available in this environment."
        raise RuntimeError(msg) from exc
    return OpenAI(api_key=api_key)


def _resolve_api_key(explicit_api_key: str | None) -> str | None:
    """Resolve API key from explicit input or environment, loading .env when needed."""

    if explicit_api_key:
        return explicit_api_key
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    repo_env = Path(__file__).resolve().parents[2] / ".env"
    load_env_from_candidates([Path.cwd() / ".env", repo_env])
    return os.environ.get("OPENAI_API_KEY")


def run_extraction(
    *,
    image_path: str | Path,
    prompt: str,
    profile: ExtractionProfile,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> ExtractionResult:
    """Send an image and prompt to the OpenAI Responses API and validate the output."""

    resolved_api_key = _resolve_api_key(api_key)
    if not resolved_api_key:
        msg = (
            "OPENAI_API_KEY must be set to run extraction. "
            "Set it in your environment, add it to a .env file, "
            "or provide it with the GUI 'Set API key...' action."
        )
        raise RuntimeError(msg)

    client = _build_openai_client(resolved_api_key)
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_to_data_url(image_path)},
                ],
            }
        ],
        text={"format": build_text_format(profile)},
    )
    data = validate_extraction_data(profile, parse_response_json(response))
    return ExtractionResult(data=data, raw_response=response_to_dict(response))
