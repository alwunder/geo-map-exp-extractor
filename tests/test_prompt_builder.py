from pathlib import Path

from geo_map_exp_extractor.config import load_profile
from geo_map_exp_extractor.prompt_builder import build_prompt


def test_build_prompt_inserts_profile_values() -> None:
    profile = load_profile(Path("profiles/engineering_properties.yml"))

    prompt = build_prompt(profile, Path("prompts/extraction_prompt.md"))

    assert "Precisely transcribe the explanation of engineering properties" in prompt
    assert "- MapUnit" in prompt
    assert "- List of Geologic Formations" in prompt
    assert "If an introduction, explanation, footnote" in prompt
    assert "{task_label}" not in prompt
