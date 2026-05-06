"""Prompt construction for extraction requests."""

from __future__ import annotations

from pathlib import Path

from geo_map_exp_extractor.config import ExtractionProfile


def build_prompt(profile: ExtractionProfile, template_path: str | Path) -> str:
    """Build the final extraction prompt from a Markdown template and profile."""

    template = Path(template_path).read_text(encoding="utf-8")
    field_list = "\n".join(f"- {field}" for field in profile.fields)
    special_instructions = "\n".join(
        f"- {instruction}" for instruction in profile.special_instructions
    )
    if not special_instructions:
        special_instructions = "- No additional profile-specific instructions."

    return template.format(
        task_label=profile.task_label,
        field_list=field_list,
        special_instructions=special_instructions,
    )
