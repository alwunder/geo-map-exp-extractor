from geo_map_exp_extractor.pricing import estimate_cost_usd


def test_estimate_cost_uses_cached_and_uncached_input_tokens() -> None:
    cost = estimate_cost_usd(
        model="gpt-5.5",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cached_tokens=500_000,
    )

    assert cost is not None
    assert cost > 0


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
