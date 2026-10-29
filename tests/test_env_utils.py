from pathlib import Path

from geo_map_exp_extractor.env_utils import default_env_candidates, user_env_path


def test_user_env_path_uses_local_app_data(monkeypatch, tmp_path: Path) -> None:
    local_app_data = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    assert user_env_path() == local_app_data / "GeoMapExpExtractor" / ".env"


def test_default_env_candidates_include_user_and_package_locations(monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Example\AppData\Local")

    candidates = default_env_candidates()

    assert candidates[0] == user_env_path()
    assert Path("src/geo_map_exp_extractor/.env").resolve() in candidates
