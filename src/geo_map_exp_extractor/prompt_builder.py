"""Prompt construction for extraction requests."""

from __future__ import annotations

from pathlib import Path

from geo_map_exp_extractor.config import ExtractionProfile


def default_profile_notes_path(profile_path: str | Path) -> Path:
    """Return conventional profile notes filename next to YAML profile."""

    resolved = Path(profile_path)
    return resolved.with_name(f"{resolved.stem}.notes.md")


def read_profile_notes(profile_path: str | Path) -> str | None:
    """Load optional profile notes file when present."""

    notes_path = default_profile_notes_path(profile_path)
    if not notes_path.exists():
        return None
    text = notes_path.read_text(encoding="utf-8").strip()
    return text or None


def build_prompt(
    profile: ExtractionProfile,
    template_path: str | Path,
    *,
    include_profile_notes: bool = False,
    profile_notes: str | None = None,
) -> str:
    """Build the final extraction prompt from a Markdown template and profile."""

    template = Path(template_path).read_text(encoding="utf-8")
    field_list = "\n".join(f"- {field}" for field in profile.fields)
    special_instructions = "\n".join(
        f"- {instruction}" for instruction in profile.special_instructions
    )
    if not special_instructions:
        special_instructions = "- No additional profile-specific instructions."

    prompt = template.format(
        task_label=profile.task_label,
        field_list=field_list,
        special_instructions=special_instructions,
    )
    if include_profile_notes and profile_notes:
        prompt = f"{prompt}\n\nProfile Notes:\n{profile_notes.strip()}\n"
    return prompt
