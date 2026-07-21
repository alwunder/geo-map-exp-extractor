"""Application-level defaults and tunable constants."""

from __future__ import annotations

from pathlib import Path

SCHEMA_VERSION = "1.0"

DEFAULT_MODEL = "gpt-5.6-sol"
EXPERIMENTAL_MODEL = "chat-latest"
SUPPORTED_MODELS = ("gpt-5.4-mini", "gpt-5.4", "gpt-5.5", "gpt-5.5-pro", "gpt-5.6-sol", EXPERIMENTAL_MODEL)

DEFAULT_REASONING_EFFORT = "medium"
SUPPORTED_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")

DEFAULT_IMAGE_DETAIL = "high"
SUPPORTED_IMAGE_DETAILS = ("high", "low", "auto")
DEFAULT_MAX_OUTPUT_TOKENS = 12000
DEFAULT_MAX_IMAGE_SIDE_PX = 2800

DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.5

DEFAULT_OUTPUT_DIR_NAME = "outputs"

# Local cache for request fingerprints and reusable extraction payloads.
REQUEST_CACHE_DIR = Path(".cache") / "geo_map_exp_extractor" / "requests"
