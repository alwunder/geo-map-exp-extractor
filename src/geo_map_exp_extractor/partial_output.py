"""Conservative recovery of complete row objects from interrupted JSON."""

from __future__ import annotations

import json
from typing import Any

from geo_map_exp_extractor.config import ExtractionProfile


def response_text(raw: dict[str, Any]) -> str:
    """Read only assistant output text, never reasoning or refusal content."""
    return "".join(
        part.get("text", "")
        for item in raw.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    )


def recover_complete_rows(profile: ExtractionProfile, text: str) -> dict[str, Any]:
    """Keep a validated contiguous prefix; never repair strings or skip a bad row."""
    from geo_map_exp_extractor.openai_runner import validate_extraction_data

    data: dict[str, Any] = {"fields": profile.fields, "rows": [], "notes": [], "warnings": []}

    def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result

    decoder = json.JSONDecoder(object_pairs_hook=unique_keys)
    pos = 0

    def whitespace() -> None:
        nonlocal pos
        while pos < len(text) and text[pos].isspace():
            pos += 1

    try:
        whitespace()
        if text[pos] != "{":
            return data
        pos += 1
        fields_seen = False
        seen: set[str] = set()
        while True:
            whitespace()
            key, pos = decoder.raw_decode(text, pos)
            if not isinstance(key, str) or key in seen:
                return data
            seen.add(key)
            whitespace()
            if text[pos] != ":":
                return data
            pos += 1
            whitespace()
            if key == "rows":
                if not fields_seen or text[pos] != "[":
                    return data
                pos += 1
                while True:
                    whitespace()
                    if text[pos] == "]":
                        return data
                    row, pos = decoder.raw_decode(text, pos)
                    validate_extraction_data(profile, {**data, "rows": [row]})
                    # Require a structural delimiter after the complete object.
                    whitespace()
                    if pos < len(text) and text[pos] not in ",]":
                        return data
                    data["rows"].append(row)
                    if pos == len(text) or text[pos] == "]":
                        return data
                    pos += 1
            value, pos = decoder.raw_decode(text, pos)
            if key == "fields":
                if value != profile.fields:
                    return data
                fields_seen = True
            whitespace()
            if text[pos] != ",":
                return data
            pos += 1
    except (ValueError, IndexError, TypeError):
        return data
