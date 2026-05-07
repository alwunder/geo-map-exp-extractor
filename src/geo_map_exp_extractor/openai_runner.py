"""OpenAI Responses API integration."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from geo_map_exp_extractor.config import ExtractionProfile
from geo_map_exp_extractor.env_utils import load_env_from_candidates
from geo_map_exp_extractor.image_io import image_to_data_url
from geo_map_exp_extractor.schema_builder import build_text_format
from geo_map_exp_extractor.settings import (
    DEFAULT_IMAGE_DETAIL,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL as SETTINGS_DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
)

DEFAULT_MODEL = SETTINGS_DEFAULT_MODEL


@dataclass(frozen=True)
class UsageSummary:
    """Token usage metadata pulled from Responses API payloads."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class ExtractionResult:
    """Validated extraction data plus raw API response."""

    data: dict[str, Any]
    raw_response: dict[str, Any]
    usage: UsageSummary | None = None
    incomplete_max_output_tokens: bool = False
    token_limit_warning: str | None = None


class ExtractionValidationError(RuntimeError):
    """Raised when structured output cannot be parsed/validated."""

    def __init__(self, message: str, raw_response: dict[str, Any]) -> None:
        super().__init__(message)
        self.raw_response = raw_response


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


def _extract_usage(raw_response: dict[str, Any]) -> UsageSummary | None:
    """Extract usage fields from raw response payload."""

    usage = raw_response.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total_tokens = usage.get("total_tokens")
    cached_tokens = None
    reasoning_tokens = None
    details = usage.get("input_tokens_details")
    if isinstance(details, dict):
        cached_tokens = details.get("cached_tokens")
    output_details = usage.get("output_tokens_details")
    if isinstance(output_details, dict):
        reasoning_tokens = output_details.get("reasoning_tokens")
    return UsageSummary(
        input_tokens=input_tokens if isinstance(input_tokens, int) else None,
        output_tokens=output_tokens if isinstance(output_tokens, int) else None,
        total_tokens=total_tokens if isinstance(total_tokens, int) else None,
        cached_tokens=cached_tokens if isinstance(cached_tokens, int) else None,
        reasoning_tokens=reasoning_tokens if isinstance(reasoning_tokens, int) else None,
    )


def _supports_reasoning_config(model: str) -> bool:
    normalized = model.strip().lower()
    return (
        normalized.startswith("gpt-5")
        or normalized.startswith("o")
        or normalized.endswith("chat-latest")
        or normalized == "chat-latest"
    )


def _is_incomplete_for_max_output_tokens(raw_response: dict[str, Any]) -> bool:
    if raw_response.get("status") != "incomplete":
        return False
    incomplete_details = raw_response.get("incomplete_details")
    if not isinstance(incomplete_details, dict):
        return False
    reason = incomplete_details.get("reason")
    return reason in {"max_output_tokens", "max_tokens"}


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


def _is_transient_error(exc: Exception) -> bool:
    """Return True for retry-eligible network/rate/server failures."""

    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and status_code in {408, 409, 429, 500, 502, 503, 504}:
        return True
    transient_names = {
        "RateLimitError",
        "APITimeoutError",
        "APIConnectionError",
        "InternalServerError",
    }
    return exc.__class__.__name__ in transient_names


def run_extraction(
    *,
    image_path: str | Path,
    prompt: str,
    profile: ExtractionProfile,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    image_detail: str = DEFAULT_IMAGE_DETAIL,
    max_output_tokens: int | None = DEFAULT_MAX_OUTPUT_TOKENS,
    retries: int = DEFAULT_RETRY_ATTEMPTS,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    schema: dict[str, Any] | None = None,
) -> ExtractionResult:
    """Send an image and prompt to the OpenAI Responses API and validate the output."""

    resolved_api_key = _resolve_api_key(api_key)
    if not resolved_api_key:
        msg = (
            "OPENAI_API_KEY must be set to run extraction. "
            "Set it in your environment, or add it to a .env file."
        )
        raise RuntimeError(msg)

    client = _build_openai_client(resolved_api_key)
    attempts = max(1, retries)
    response: Any | None = None
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request_payload: dict[str, Any] = {
                "model": model,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {
                                "type": "input_image",
                                "image_url": image_to_data_url(image_path),
                                "detail": image_detail,
                            },
                        ],
                    }
                ],
                "text": {"format": schema or build_text_format(profile)},
            }
            if isinstance(max_output_tokens, int) and max_output_tokens > 0:
                request_payload["max_output_tokens"] = max_output_tokens
            if _supports_reasoning_config(model):
                request_payload["reasoning"] = {"effort": reasoning_effort}

            response = client.responses.create(**request_payload)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= attempts or not _is_transient_error(exc):
                break
            time.sleep(retry_backoff_seconds * (2 ** (attempt - 1)))

    if response is None:
        if last_error is None:
            raise RuntimeError("OpenAI request failed without a captured exception.")
        raise RuntimeError(f"OpenAI request failed after {attempts} attempt(s): {last_error}") from last_error

    raw = response_to_dict(response)
    incomplete_for_budget = _is_incomplete_for_max_output_tokens(raw)
    token_limit_warning: str | None = None
    if incomplete_for_budget:
        token_limit_warning = (
            "Response status is incomplete because max_output_tokens was reached. "
            "The model may have run out of token budget during reasoning or final output."
        )
    try:
        parsed = parse_response_json(response)
        data = validate_extraction_data(profile, parsed)
    except (ValueError, ValidationError) as exc:
        message = f"Structured output validation failed: {exc}"
        if token_limit_warning:
            message = f"{message}. {token_limit_warning}"
        raise ExtractionValidationError(message, raw) from exc
    return ExtractionResult(
        data=data,
        raw_response=raw,
        usage=_extract_usage(raw),
        incomplete_max_output_tokens=incomplete_for_budget,
        token_limit_warning=token_limit_warning,
    )
