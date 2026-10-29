"""Durable extraction checkpoints and explicitly requested continuation jobs."""

from __future__ import annotations

import shutil
import hashlib
import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from geo_map_exp_extractor.config import load_profile
from geo_map_exp_extractor.exporters import write_csv, write_json_atomic
from geo_map_exp_extractor.openai_runner import (
    ExtractionResult,
    ExtractionValidationError,
    _extract_usage,
    run_extraction,
    validate_extraction_data,
)
from geo_map_exp_extractor.pricing import estimate_cost_usd
from geo_map_exp_extractor.prompt_builder import build_continuation_prompt
from geo_map_exp_extractor.schema_builder import build_text_format

SESSION_FILE = "extraction_session.json"
INCOMPLETE_WARNING = "INCOMPLETE extraction: saved rows are only a partial table."


class ExtractionSession(BaseModel):
    """The checkpoint is authoritative; CSV/JSON/manifest are derived exports."""

    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    manifest: dict[str, Any]
    segments: list[str]
    segment_fingerprints: list[str] = Field(default_factory=list)
    use_cache: bool = False
    allow_cache_lookup: bool = True
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    segment_index: int = 0
    rows: list[dict[str, str]] = Field(default_factory=list)
    segment_rows: list[dict[str, str]] = Field(default_factory=list)
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    pending: dict[str, Any] | None = None
    pending_resume_after: int | None = None
    status: str = "ready"
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    review_rows: list[dict[str, str]] | None = None
    review_originals: list[dict[str, str]] | None = None
    review_base_count: int = 0


def _local_path(run_dir: Path, name: str) -> Path:
    path = (run_dir / name).resolve()
    if not path.is_relative_to(run_dir.resolve()):
        raise ValueError("Extraction checkpoint references a file outside its project")
    return path


def read_session(run_dir: Path, *, verify_inputs: bool = False) -> ExtractionSession:
    state = ExtractionSession.model_validate_json((run_dir / SESSION_FILE).read_text("utf-8"))
    if not 0 <= state.segment_index <= len(state.segments):
        raise ValueError("Invalid extraction checkpoint segment index")
    for name in state.segments:
        _local_path(run_dir, name)
    for name, digest in state.artifact_hashes.items():
        path = _local_path(run_dir, name)
        if verify_inputs and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(
                f"Saved extraction input changed: {name}. Restore it before continuing."
            )
    return state


def _checkpoint(run_dir: Path, state: ExtractionSession) -> None:
    write_json_atomic(state.model_dump(mode="json"), run_dir / SESSION_FILE)


def _usage(state: ExtractionSession) -> dict[str, int | None]:
    summaries = [
        _extract_usage(attempt["raw_response"])
        for attempt in state.attempts
        if not attempt.get("cache_reused")
    ]
    return {
        key: (sum(values) if values else None)
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cached_tokens",
            "reasoning_tokens",
        )
        for values in [
            [
                getattr(item, key)
                for item in summaries
                if item is not None and getattr(item, key) is not None
            ]
        ]
    }


def _export(run_dir: Path, state: ExtractionSession) -> None:
    """Regenerate outputs from an atomic checkpoint, including after an app crash."""
    from geo_map_exp_extractor.jobs import review_output_paths

    paths = review_output_paths(run_dir)
    manifest = dict(state.manifest)
    manifest["run_id"] = run_dir.name
    usage = _usage(state)
    manifest.update(usage)
    manifest["estimated_cost_usd"] = estimate_cost_usd(
        model=manifest["model"],
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cached_tokens=usage["cached_tokens"],
        service_tier=manifest["service_tier"],
    )
    warnings = list(state.warnings)
    if state.status != "complete":
        warnings.insert(0, INCOMPLETE_WARNING)
    manifest.update(
        extraction_status=state.status,
        warnings=warnings,
        continuation_available=state.status != "complete",
    )
    if manifest["api_call_mode"] != "continuation" and state.attempts:
        modes = {bool(attempt.get("cache_reused")) for attempt in state.attempts}
        manifest["api_call_mode"] = (
            "cache_reuse" if modes == {True} else "fresh_api_call" if modes == {False} else "mixed"
        )
    fields = manifest["profile_fields"]
    original_rows = state.rows
    if state.review_originals is not None:
        original_rows = state.review_originals + state.rows[state.review_base_count :]
    data = {
        "fields": fields,
        "rows": original_rows,
        "notes": state.notes,
        "warnings": warnings,
        "extraction_status": state.status,
    }
    write_json_atomic(data, paths["extracted_json"])
    write_csv(original_rows, fields, paths["extracted_csv"])
    if state.review_rows is not None:
        # A marker makes interrupted export repair idempotent. Manual review
        # saves omit the marker and are never overwritten by checkpoint repair.
        try:
            existing = (
                json.loads(paths["corrected_json"].read_text("utf-8"))
                if paths["corrected_json"].exists()
                else None
            )
            if existing is not None and not isinstance(existing, dict):
                existing = {}
        except (OSError, ValueError):
            # Preserve damaged review files for inspection/autosave recovery.
            existing = {}
        count = existing.get("session_row_count") if existing is not None else None
        if existing is None or (isinstance(count, int) and count < len(state.rows)):
            reviewed = (
                existing["rows"] + state.rows[count:]
                if existing is not None
                else state.review_rows + state.rows[state.review_base_count :]
            )
            write_json_atomic(
                {**data, "rows": reviewed, "session_row_count": len(state.rows)},
                paths["corrected_json"],
            )
            write_csv(reviewed, fields, paths["corrected_csv"])
    write_json_atomic({"attempts": state.attempts, "pending": state.pending}, paths["raw_response"])
    write_json_atomic(manifest, paths["manifest"])


def repair_session_exports(run_dir: Path) -> None:
    if (run_dir / SESSION_FILE).is_file():
        _export(run_dir, read_session(run_dir))


def _result(run_dir: Path, state: ExtractionSession) -> Any:
    from geo_map_exp_extractor.jobs import ExtractionJobResult, load_review_project

    loaded = load_review_project(run_dir)
    manifest = loaded.manifest
    return ExtractionJobResult(
        run_id=run_dir.name,
        run_dir=run_dir,
        fields=loaded.fields,
        rows=loaded.original_rows,
        manifest=manifest,
        output_paths=loaded.output_paths,
        dry_run=False,
        cache_reused=any(a.get("cache_reused") for a in state.attempts),
        request_fingerprint=manifest["request_fingerprint"],
        rough_image_tokens=manifest["rough_image_tokens_estimate"],
        usage=_usage(state),
        elapsed_seconds=manifest["elapsed_seconds"],
        estimated_cost_usd=manifest["estimated_cost_usd"],
        warnings=manifest["warnings"],
    )


def run_session(
    run_dir: Path,
    state: ExtractionSession,
    *,
    api_key: str | None = None,
    cancel_event: Event | None = None,
    extraction_runner: Callable[..., ExtractionResult] = run_extraction,
) -> Any:
    """Run until complete or interrupted; never automatically buy a continuation."""
    profile = load_profile(run_dir / "profile.yml")
    prompt = (run_dir / "prompt.txt").read_text("utf-8")
    start = time.monotonic()
    if not state.artifact_hashes:
        state.artifact_hashes = {
            name: hashlib.sha256(_local_path(run_dir, name).read_bytes()).hexdigest()
            for name in ["profile.yml", "prompt.txt", "schema.json", *state.segments]
        }
    _checkpoint(run_dir, state)
    _export(run_dir, state)
    try:
        while state.segment_index < len(state.segments):
            if cancel_event is not None and cancel_event.is_set() and not state.pending:
                state.status = "cancelled"
                break
            prefix = state.segment_rows
            resuming = bool(prefix) or bool(state.attempts and state.status != "ready")
            boundary = len(prefix) if resuming else None
            pending_id = (state.pending or {}).get("id")
            if pending_id:
                boundary = state.pending_resume_after
            request_prompt = (
                build_continuation_prompt(prompt, prefix) if boundary is not None else prompt
            )
            state.pending_resume_after = boundary
            state.status = "running"
            _checkpoint(run_dir, state)

            def on_response(raw: dict[str, Any]) -> None:
                if raw != state.pending:
                    state.pending = raw
                    _checkpoint(run_dir, state)

            try:
                from geo_map_exp_extractor.jobs import _load_cache_payload, _save_cache_payload

                fingerprint = (
                    state.segment_fingerprints[state.segment_index]
                    if state.segment_fingerprints
                    else None
                )
                cached = (
                    _load_cache_payload(fingerprint)
                    if state.use_cache
                    and state.allow_cache_lookup
                    and fingerprint
                    and boundary is None
                    and not pending_id
                    else None
                )
                if cached is not None:
                    extraction = ExtractionResult(
                        data=validate_extraction_data(profile, cached["data"]),
                        raw_response=cached["raw_response"],
                    )
                else:
                    extraction = extraction_runner(
                        image_path=_local_path(run_dir, state.segments[state.segment_index]),
                        prompt=request_prompt,
                        profile=profile,
                        api_key=api_key,
                        model=state.manifest["model"],
                        reasoning_effort=state.manifest["reasoning_effort"],
                        service_tier=state.manifest["service_tier"],
                        image_detail=state.manifest["image_detail"],
                        max_output_tokens=state.manifest["max_output_tokens"],
                        schema=build_text_format(profile, resume_after=boundary),
                        background=True,
                        cancel_event=cancel_event,
                        on_response=on_response,
                        response_id=pending_id,
                        resume_after=boundary,
                    )
                if state.use_cache and fingerprint and cached is None and boundary is None:
                    _save_cache_payload(fingerprint, extraction=extraction)
            except ExtractionValidationError as exc:
                state.attempts.append(
                    {
                        "raw_response": exc.raw_response,
                        "segment_index": state.segment_index,
                        "resume_after": boundary,
                        "error": str(exc),
                    }
                )
                state.pending = None
                state.status = "validation_error"
                state.warnings.append(str(exc))
                break

            data = validate_extraction_data(profile, extraction.data)
            new_rows = data["rows"]
            state.attempts.append(
                {
                    "raw_response": extraction.raw_response,
                    "segment_index": state.segment_index,
                    "resume_after": boundary,
                    "prompt": request_prompt,
                    "cache_reused": cached is not None,
                }
            )
            state.pending = None
            state.status = extraction.interruption_reason or (
                "max_output_tokens" if extraction.incomplete_max_output_tokens else "complete"
            )
            if boundary is not None and prefix and new_rows:
                # A repeated boundary may be a legitimate repeated source row, so
                # flag it for review instead of silently deduplicating or accepting.
                if any(
                    prefix[-n:] == new_rows[:n]
                    for n in range(1, min(len(prefix), len(new_rows)) + 1)
                ):
                    new_rows = []
                    state.status = "boundary_review"
                    state.warnings.append(
                        "Continuation repeated saved boundary rows; raw output retained."
                    )
            if boundary is not None and not new_rows and data["warnings"]:
                state.status = "boundary_review"
            state.rows.extend(new_rows)
            state.segment_rows.extend(new_rows)
            state.warnings.extend(data["warnings"])
            state.notes.extend(data["notes"])
            if state.status != "complete":
                break
            state.segment_index += 1
            state.segment_rows = []
            state.status = "ready" if state.segment_index < len(state.segments) else "complete"
            _checkpoint(run_dir, state)
            _export(run_dir, state)
    except Exception as exc:  # Keep a known response ID for retrieval, never re-create it blindly.
        state.status = "interrupted"
        state.warnings.append(f"Extraction interrupted: {exc}")
    state.manifest["elapsed_seconds"] += time.monotonic() - start
    _checkpoint(run_dir, state)
    _export(run_dir, state)
    return _result(run_dir, state)


def continue_extraction_job(
    *,
    run_dir: str | Path,
    max_output_tokens: int,
    api_key: str | None = None,
    cancel_event: Event | None = None,
    review_state: dict[str, Any] | None = None,
    extraction_runner: Callable[..., ExtractionResult] = run_extraction,
) -> Any:
    """Fork a saved project, preserving its review trail and original checkpoint."""
    source = Path(run_dir).resolve()
    state = read_session(source, verify_inputs=True)
    if state.status == "complete":
        raise ValueError("This extraction is already complete")
    if max_output_tokens <= 0:
        raise ValueError("Continuation requires a positive maximum output token allowance")
    from geo_map_exp_extractor.jobs import _make_run_id, _unique_run_dir, load_review_project

    loaded = load_review_project(source)
    destination = _unique_run_dir(source.parent, _make_run_id(datetime.now(timezone.utc)))
    shutil.copytree(source, destination)
    state.manifest.update(
        parent_run_id=source.name,
        max_output_tokens=max_output_tokens,
        api_call_mode="continuation",
        run_id=destination.name,
    )
    state.use_cache = False
    state.warnings.append(
        "Continued extraction: review the join for omissions, repeated source rows and hierarchy consistency."
    )
    state.review_rows = review_state["rows"] if review_state else loaded.rows
    state.review_originals = review_state["original_rows"] if review_state else loaded.original_rows
    state.review_base_count = len(state.rows)
    # These are derived files in the new copy, never the user's source project.
    for name in ("corrected.json", "corrected.csv"):
        (destination / name).unlink(missing_ok=True)
    return run_session(
        destination,
        state,
        api_key=api_key,
        cancel_event=cancel_event,
        extraction_runner=extraction_runner,
    )
