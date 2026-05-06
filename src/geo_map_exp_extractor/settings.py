"""Application-level defaults and tunable constants."""

from __future__ import annotations

from pathlib import Path

SCHEMA_VERSION = "1.0"

DEFAULT_IMAGE_DETAIL = "high"
SUPPORTED_IMAGE_DETAILS = ("high", "low", "auto")
DEFAULT_MAX_IMAGE_SIDE_PX = 2800

DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.5

DEFAULT_OUTPUT_DIR_NAME = "outputs"

# Local cache for request fingerprints and reusable extraction payloads.
REQUEST_CACHE_DIR = Path(".cache") / "geo_map_exp_extractor" / "requests"
