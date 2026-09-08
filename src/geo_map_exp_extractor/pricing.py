"""OpenAI model catalog, pricing configuration, and cost-estimation helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelPricing:
    """Per-1M-token Standard, short-context prices for a model."""

    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None


@dataclass(frozen=True)
class ModelOptions:
    """All extraction-relevant options supported by one OpenAI model."""

    id: str
    name: str
    pricing: ModelPricing
    reasoning_efforts: tuple[str, ...]
    default_reasoning_effort: str
    service_tiers: tuple[str, ...]


DEFAULT_SERVICE_TIER = "default"
SERVICE_TIER_PRICE_MULTIPLIERS: dict[str, float] = {
    "default": 1.0,
    "flex": 0.5,
    "fast": 2.0,
}

# Standard direct-API, short-context prices per 1M tokens, verified against
# https://developers.openai.com/api/docs/pricing on 2026-09-08. The updater can
# replace these built-in values with a user-local, reviewable JSON catalog.
_BUILTIN_MODEL_CATALOG = (
    ModelOptions(
        id="gpt-6-astra",
        name="GPT-6 Astra",
        pricing=ModelPricing(10.00, 50.00, 1.00),
        reasoning_efforts=("low", "medium", "high", "xhigh", "max"),
        default_reasoning_effort="medium",
        service_tiers=("default", "flex", "fast"),
    ),
    ModelOptions(
        id="gpt-5.6-sol",
        name="GPT-5.6 Sol",
        pricing=ModelPricing(4.00, 20.00, 0.40),
        reasoning_efforts=("none", "low", "medium", "high", "xhigh", "max"),
        default_reasoning_effort="medium",
        service_tiers=("default", "flex", "fast"),
    ),
    ModelOptions(
        id="gpt-5.6-terra",
        name="GPT-5.6 Terra",
        pricing=ModelPricing(2.00, 12.00, 0.20),
        reasoning_efforts=("none", "low", "medium", "high", "xhigh", "max"),
        default_reasoning_effort="medium",
        service_tiers=("default", "flex", "fast"),
    ),
    ModelOptions(
        id="gpt-5.6-luna",
        name="GPT-5.6 Luna",
        pricing=ModelPricing(0.20, 1.20, 0.02),
        reasoning_efforts=("none", "low", "medium", "high", "xhigh", "max"),
        default_reasoning_effort="medium",
        service_tiers=("default", "flex", "fast"),
    ),
)


def default_catalog_path() -> Path:
    """Return the user-editable catalog path for the active application folder."""

    return Path.cwd() / "openai_model_catalog.json"


def _model_options_from_dict(payload: Any) -> ModelOptions:
    if not isinstance(payload, dict):
        raise ValueError("each model catalog entry must be a JSON object")
    pricing_payload = payload.get("pricing")
    if not isinstance(pricing_payload, dict):
        raise ValueError("each model catalog entry needs a pricing object")
    model_id = payload.get("id")
    name = payload.get("name")
    efforts = payload.get("reasoning_efforts")
    default_effort = payload.get("default_reasoning_effort")
    tiers = payload.get("service_tiers")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("each model catalog entry needs a non-empty id")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"model {model_id!r} needs a non-empty name")
    if not isinstance(efforts, list) or not all(isinstance(item, str) for item in efforts):
        raise ValueError(f"model {model_id!r} needs a reasoning_efforts list")
    if default_effort not in efforts:
        raise ValueError(f"model {model_id!r} has an invalid default_reasoning_effort")
    if not isinstance(tiers, list) or not all(item in SERVICE_TIER_PRICE_MULTIPLIERS for item in tiers):
        raise ValueError(f"model {model_id!r} has unsupported service_tiers")
    try:
        pricing = ModelPricing(
            input_per_million=float(pricing_payload["input_per_million"]),
            output_per_million=float(pricing_payload["output_per_million"]),
            cached_input_per_million=(
                float(pricing_payload["cached_input_per_million"])
                if pricing_payload.get("cached_input_per_million") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"model {model_id!r} has invalid pricing") from exc
    if pricing.input_per_million < 0 or pricing.output_per_million < 0:
        raise ValueError(f"model {model_id!r} has negative pricing")
    return ModelOptions(
        id=model_id.strip(),
        name=name.strip(),
        pricing=pricing,
        reasoning_efforts=tuple(efforts),
        default_reasoning_effort=default_effort,
        service_tiers=tuple(tiers),
    )


def load_model_catalog(path: str | Path | None = None) -> tuple[ModelOptions, ...]:
    """Load a validated user catalog, falling back to the vetted built-in catalog."""

    catalog_path = Path(path) if path is not None else default_catalog_path()
    if not catalog_path.is_file():
        return _BUILTIN_MODEL_CATALOG
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list) or not models:
            raise ValueError("catalog needs a non-empty models list")
        catalog = tuple(_model_options_from_dict(model) for model in models)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid model catalog {catalog_path}: {exc}") from exc
    if len({model.id for model in catalog}) != len(catalog):
        raise ValueError(f"invalid model catalog {catalog_path}: duplicate model id")
    return catalog


def catalog_as_jsonable(catalog: tuple[ModelOptions, ...] | None = None) -> dict[str, Any]:
    """Return a catalog in the updater's JSON-file format."""

    models = catalog if catalog is not None else MODEL_CATALOG
    return {"schema_version": 1, "models": [asdict(model) for model in models]}


MODEL_CATALOG = load_model_catalog()
MODEL_OPTIONS_BY_ID = {model.id: model for model in MODEL_CATALOG}
MODEL_PRICING_USD_PER_1M = {model.id: model.pricing for model in MODEL_CATALOG}
SUPPORTED_MODELS = tuple(model.id for model in MODEL_CATALOG)
DEFAULT_MODEL = next(
    (model.id for model in MODEL_CATALOG if model.id == "gpt-5.6-sol"), MODEL_CATALOG[0].id
)
SUPPORTED_REASONING_EFFORTS = tuple(
    dict.fromkeys(effort for model in MODEL_CATALOG for effort in model.reasoning_efforts)
)
SUPPORTED_SERVICE_TIERS = tuple(SERVICE_TIER_PRICE_MULTIPLIERS)


def get_model_options(model: str) -> ModelOptions | None:
    """Return catalog options for a model ID, if the model is configured locally."""

    return MODEL_OPTIONS_BY_ID.get(model.strip())


def estimate_cost_usd(
    *,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_tokens: int | None = None,
    service_tier: str = DEFAULT_SERVICE_TIER,
) -> float | None:
    """Estimate token cost using the selected model's documented service tier."""

    pricing = MODEL_PRICING_USD_PER_1M.get(model)
    multiplier = SERVICE_TIER_PRICE_MULTIPLIERS.get(service_tier)
    if pricing is None or multiplier is None or input_tokens is None or output_tokens is None:
        return None

    cached = max(0, cached_tokens or 0)
    uncached_input = max(0, input_tokens - cached)
    input_cost = uncached_input * pricing.input_per_million / 1_000_000
    output_cost = output_tokens * pricing.output_per_million / 1_000_000
    cached_cost = 0.0
    if pricing.cached_input_per_million is not None and cached > 0:
        cached_cost = cached * pricing.cached_input_per_million / 1_000_000
    return round((input_cost + cached_cost + output_cost) * multiplier, 8)
