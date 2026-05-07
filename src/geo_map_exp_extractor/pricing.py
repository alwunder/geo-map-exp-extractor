"""Manual pricing configuration and usage-cost helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    """Per-1M-token pricing used for local cost estimates."""

    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None


# Keep this map user-editable. Values here are examples and may change over time.
# Update these entries to match the current OpenAI pricing for your account/model snapshot.
# UPDATED 2026-05-06
MODEL_PRICING_USD_PER_1M: dict[str, ModelPricing] = {
    "chat-latest": ModelPricing(
        input_per_million=5.00,
        output_per_million=30.00,
        cached_input_per_million=0.50,
    ),
}


def estimate_cost_usd(
    *,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_tokens: int | None = None,
) -> float | None:
    """Estimate run cost from usage tokens and local pricing config."""

    pricing = MODEL_PRICING_USD_PER_1M.get(model)
    if pricing is None or input_tokens is None or output_tokens is None:
        return None

    cached = max(0, cached_tokens or 0)
    uncached_input = max(0, input_tokens - cached)
    input_cost = uncached_input * pricing.input_per_million / 1_000_000
    output_cost = output_tokens * pricing.output_per_million / 1_000_000
    cached_cost = 0.0
    if pricing.cached_input_per_million is not None and cached > 0:
        cached_cost = cached * pricing.cached_input_per_million / 1_000_000
    return round(input_cost + cached_cost + output_cost, 8)
