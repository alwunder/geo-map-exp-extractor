"""Environment-loading helpers with no external runtime dependency."""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv_file(path: Path) -> None:
    """Load KEY=VALUE pairs from one .env file into the current process env."""

    if not path.exists() or not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        env_key = key.strip()
        if env_key.startswith("export "):
            env_key = env_key.removeprefix("export ").strip()
        if env_key.lower() == "openai_api_key":
            env_key = "OPENAI_API_KEY"
        env_value = value.strip().strip('"').strip("'")
        if env_key and env_key not in os.environ:
            os.environ[env_key] = env_value


def load_env_from_candidates(candidates: list[str | Path]) -> None:
    """Load environment variables from first-party .env paths if present."""

    for candidate in candidates:
        _load_dotenv_file(Path(candidate))


def user_env_path() -> Path:
    """Return the preferred per-user .env location for installed applications."""

    if local_app_data := os.environ.get("LOCALAPPDATA"):
        return Path(local_app_data) / "GeoMapExpExtractor" / ".env"
    return Path.home() / ".config" / "geo-map-exp-extractor" / ".env"


def default_env_candidates() -> list[Path]:
    """Return .env paths in priority order, without overwriting shell variables."""

    package_env = Path(__file__).with_name(".env")
    repository_env = Path(__file__).resolve().parents[2] / ".env"
    candidates = [user_env_path(), Path.cwd() / ".env", package_env, repository_env]
    return list(dict.fromkeys(candidates))
