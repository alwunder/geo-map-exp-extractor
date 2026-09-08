"""Fetch the current featured OpenAI model catalog and write a local override.

Run from the application folder:

    python tools/update_pricing.py

The resulting ``openai_model_catalog.json`` is deliberately data, not Python
code. Review its printed summary before accepting an update, then restart the
application for the new choices and prices to appear.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPOSITORY_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from geo_map_exp_extractor.pricing import (  # noqa: E402
    ModelOptions,
    ModelPricing,
    catalog_as_jsonable,
    default_catalog_path,
)


DOCS_ROOT = "https://developers.openai.com"
MODELS_URL = f"{DOCS_ROOT}/api/docs/models.md"
PRICING_URL = f"{DOCS_ROOT}/api/docs/pricing.md"
SERVICE_TIER_URL = f"{DOCS_ROOT}/api/reference/resources/responses/methods/create.md"
SERVICE_TIERS = ("default", "flex", "fast")


def _fetch_markdown(url: str) -> str:
    request = Request(url, headers={"User-Agent": "geo-map-exp-extractor pricing updater"})
    with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed official documentation URL.
        return response.read().decode("utf-8")


def _featured_model_pages(models_markdown: str) -> list[tuple[str, str]]:
    featured_section = models_markdown.split("## Featured models", maxsplit=1)
    if len(featured_section) != 2:
        raise ValueError("OpenAI Models documentation does not contain a Featured models section")
    section = featured_section[1].split("## Browse our full catalog", maxsplit=1)[0]
    matches = re.findall(r"^- \[([^]]+)]\((/api/docs/models/[^)]+\.md)\):", section, re.MULTILINE)
    if not matches:
        raise ValueError("could not find featured model links in OpenAI Models documentation")
    return matches


def _standard_prices(pricing_markdown: str) -> dict[str, ModelPricing]:
    table_start = pricing_markdown.find("### Standard pricing data")
    if table_start < 0:
        raise ValueError("OpenAI Pricing documentation does not contain Standard pricing data")
    section = pricing_markdown[table_start:].split("### Batch pricing data", maxsplit=1)[0]
    prices: dict[str, ModelPricing] = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not cells[0].startswith("gpt-"):
            continue
        model_id = re.sub(r" \(<[^)]*\)$", "", cells[0])
        try:
            prices[model_id] = ModelPricing(
                input_per_million=float(cells[1].removeprefix("$")),
                cached_input_per_million=(
                    None if cells[2] == "-" else float(cells[2].removeprefix("$"))
                ),
                output_per_million=float(cells[4].removeprefix("$")),
            )
        except ValueError as exc:
            raise ValueError(f"could not parse Standard pricing for {model_id}") from exc
    return prices


def _reasoning_efforts(model_markdown: str, model_id: str) -> tuple[str, ...]:
    if "structured_outputs" not in model_markdown or "image_input" not in model_markdown:
        raise ValueError(f"{model_id} does not document Structured Outputs support")
    match = re.search(r"`?reasoning\.effort`? supports:?\s*([^\.\n]+)", model_markdown, re.IGNORECASE)
    if match is None:
        raise ValueError(f"could not determine reasoning efforts for {model_id}")
    efforts = tuple(
        effort
        for effort in ("none", "minimal", "low", "medium", "high", "xhigh", "max")
        if re.search(rf"\b{effort}\b", match.group(1), re.IGNORECASE)
    )
    if not efforts:
        raise ValueError(f"could not parse reasoning efforts for {model_id}")
    return efforts


def _supported_service_tiers(reference_markdown: str) -> tuple[str, ...]:
    """Keep only documented tiers with a deterministic local price estimate."""

    supported = tuple(
        tier for tier in SERVICE_TIERS if re.search(rf"[\"'`]({tier})[\"'`]", reference_markdown)
    )
    if supported != SERVICE_TIERS:
        raise ValueError("could not confirm default, flex, and fast service tiers in the Responses API")
    return supported


def fetch_catalog() -> tuple[ModelOptions, ...]:
    """Build an extraction-safe model catalog from current official OpenAI docs."""

    featured_models = _featured_model_pages(_fetch_markdown(MODELS_URL))
    prices = _standard_prices(_fetch_markdown(PRICING_URL))
    service_tiers = _supported_service_tiers(_fetch_markdown(SERVICE_TIER_URL))
    catalog: list[ModelOptions] = []
    for name, path in featured_models:
        model_id = Path(path).stem
        pricing = prices.get(model_id)
        if pricing is None:
            raise ValueError(f"OpenAI Pricing documentation has no Standard price for {model_id}")
        efforts = _reasoning_efforts(_fetch_markdown(f"{DOCS_ROOT}{path}"), model_id)
        default_effort = "medium" if "medium" in efforts else efforts[0]
        catalog.append(
            ModelOptions(
                id=model_id,
                name=name,
                pricing=pricing,
                reasoning_efforts=efforts,
                default_reasoning_effort=default_effort,
                service_tiers=service_tiers,
            )
        )
    return tuple(catalog)


def _print_summary(catalog: tuple[ModelOptions, ...]) -> None:
    print("\nFetched OpenAI Standard short-context pricing (per 1M tokens):")
    for model in catalog:
        pricing = model.pricing
        cached_price = "-" if pricing.cached_input_per_million is None else f"${pricing.cached_input_per_million:g}"
        print(
            f"  {model.id}: input ${pricing.input_per_million:g}, "
            f"cached {cached_price}, output ${pricing.output_per_million:g}; "
            f"efforts: {', '.join(model.reasoning_efforts)}"
        )
    print("  Service tiers: default, flex, fast")


def main() -> int:
    parser = argparse.ArgumentParser(description="Update the local OpenAI model and pricing catalog.")
    parser.add_argument(
        "--output",
        type=Path,
        default=default_catalog_path(),
        help="Catalog JSON to write (default: openai_model_catalog.json in the current folder).",
    )
    parser.add_argument("--check", action="store_true", help="Fetch and display updates without writing.")
    parser.add_argument("--yes", action="store_true", help="Write without an interactive confirmation.")
    args = parser.parse_args()
    try:
        catalog = fetch_catalog()
    except (OSError, ValueError) as exc:
        print(f"Could not update the model catalog: {exc}", file=sys.stderr)
        return 1

    _print_summary(catalog)
    if args.check:
        return 0
    if not args.yes and input(f"\nWrite this catalog to {args.output}? [y/N] ").strip().lower() not in {"y", "yes"}:
        print("No files changed.")
        return 0

    payload: dict[str, Any] = catalog_as_jsonable(catalog)
    payload["source"] = {
        "models": MODELS_URL,
        "pricing": PRICING_URL,
        "responses_api": SERVICE_TIER_URL,
    }
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {args.output}. Restart the application to use the new catalog.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
