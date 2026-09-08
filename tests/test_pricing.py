import json
from pathlib import Path

import pytest

from geo_map_exp_extractor.pricing import (
    MODEL_PRICING_USD_PER_1M,
    catalog_as_jsonable,
    load_model_catalog,
    estimate_cost_usd,
)
from geo_map_exp_extractor.settings import SUPPORTED_MODELS


def test_estimate_cost_uses_cached_and_uncached_input_tokens() -> None:
    cost = estimate_cost_usd(
        model="gpt-5.6-terra",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cached_tokens=500_000,
    )

    assert cost == 13.1


def test_flex_service_tier_halves_the_standard_cost() -> None:
    cost = estimate_cost_usd(
        model="gpt-5.6-terra",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cached_tokens=500_000,
        service_tier="flex",
    )

    assert cost == 6.55


@pytest.mark.parametrize(
    ("model", "input_price", "cached_input_price", "output_price"),
    [
        ("gpt-6-astra", 10.00, 1.00, 50.00),
        ("gpt-5.6-sol", 4.00, 0.40, 20.00),
        ("gpt-5.6-terra", 2.00, 0.20, 12.00),
        ("gpt-5.6-luna", 0.20, 0.02, 1.20),
    ],
)
def test_current_model_pricing_matches_the_supported_model_menu(
    model: str,
    input_price: float,
    cached_input_price: float,
    output_price: float,
) -> None:
    pricing = MODEL_PRICING_USD_PER_1M[model]

    assert pricing.input_per_million == input_price
    assert pricing.cached_input_per_million == cached_input_price
    assert pricing.output_per_million == output_price


def test_pricing_entries_and_supported_model_menu_match() -> None:
    assert tuple(MODEL_PRICING_USD_PER_1M) == SUPPORTED_MODELS


def test_catalog_loader_accepts_a_user_updated_json_catalog(tmp_path: Path) -> None:
    payload = catalog_as_jsonable()
    payload["models"][0]["pricing"]["input_per_million"] = 11.0
    catalog_path = tmp_path / "openai_model_catalog.json"
    catalog_path.write_text(json.dumps(payload), encoding="utf-8")

    catalog = load_model_catalog(catalog_path)

    assert catalog[0].pricing.input_per_million == 11.0
    assert catalog[0].service_tiers == ("default", "flex", "fast")


def test_estimate_cost_returns_none_for_unknown_model() -> None:
    assert (
        estimate_cost_usd(
            model="unknown-model",
            input_tokens=100,
            output_tokens=100,
            cached_tokens=0,
        )
        is None
    )
