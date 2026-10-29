"""Prompt construction for extraction requests."""

from __future__ import annotations

import json

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
        include_intro_footnotes=str(profile.include_intro_footnotes).lower(),
        preserve_wording=str(profile.preserve_wording).lower(),
        normalize_line_breaks=str(profile.normalize_line_breaks).lower(),
        normalize_hyphenated_line_breaks=str(profile.normalize_hyphenated_line_breaks).lower(),
    )
    if profile.field_rules:
        guidance = ["Field definitions and controlled vocabularies:"]
        for field in profile.fields:
            rule = profile.field_rules.get(field)
            if rule is None:
                continue
            guidance.append(f"\n{field}: {rule.description}")
            if rule.allowed_values is not None:
                guidance.append(f"Allowed values (exact spelling): {rule.allowed_values!r}")
            for term, definition in rule.terms.items():
                guidance.append(f"- {term}: {definition}")
        prompt = f"{prompt}\n\n" + "\n".join(guidance) + "\n"
    if include_profile_notes and profile_notes:
        prompt = f"{prompt}\n\nProfile Notes:\n{profile_notes.strip()}\n"
    return prompt


def build_continuation_prompt(prompt: str, rows: list[dict[str, str]]) -> str:
    """Carry a saved source-order prefix into a separately validated response."""
    template = Path(__file__).resolve().parents[2] / "prompts" / "continuation_prompt.md"
    continuation = template.read_text(encoding="utf-8").format(
        row_count=len(rows),
        saved_rows=json.dumps(rows, ensure_ascii=False),
    )
    return f"{prompt}\n\n{continuation}"
