"""Template-driven visual extraction for geologic map explanation panels."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("geo-map-exp-extractor")
except PackageNotFoundError:  # pragma: no cover - package is not installed in editable mode yet
    __version__ = "0.0.0"

__all__ = ["__version__"]
