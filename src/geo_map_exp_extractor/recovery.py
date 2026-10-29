"""Local crash-recovery snapshots and an independently testable autosave policy."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geo_map_exp_extractor.exporters import write_json_atomic

AUTOSAVE_POLL_MS = 5000
AUTOSAVE_IDLE_SECONDS = 30
AUTOSAVE_MAX_SECONDS = 120
AUTOSAVE_RETRY_SECONDS = 30


class ReviewState(BaseModel):
    """Only review content; never credentials, model settings, or image bytes."""

    model_config = ConfigDict(extra="forbid", strict=True)

    fields: list[str]
    rows: list[dict[str, str]]
    original_rows: list[dict[str, str]]
    row_statuses: list[Literal["accepted", "needs_review", "bad_extraction"]]
    row_comments: list[str]
    feedback_records: list[dict[str, Any]]
    notes_text: str
    pending_targets: list[int] = Field(default_factory=list)
    pending_status: Literal["accepted", "needs_review", "bad_extraction"] = "needs_review"
    pending_comment: str = ""

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("Recovery fields must be unique")
        if not (
            len(self.rows)
            == len(self.original_rows)
            == len(self.row_statuses)
            == len(self.row_comments)
        ):
            raise ValueError("Recovery row metadata does not match row count")
        if any(set(row) != set(self.fields) for row in self.rows + self.original_rows):
            raise ValueError("Recovery rows do not match fields")
        if any(index < 0 or index >= len(self.rows) for index in self.pending_targets):
            raise ValueError("Recovery review targets are out of range")
        return self

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()


class RecoverySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    project_dir: str
    run_id: str
    request_fingerprint: str
    saved_at: datetime
    base_revision: str
    state: ReviewState


@dataclass(frozen=True)
class RecoveryRecord:
    path: Path
    snapshot: RecoverySnapshot


@dataclass
class AutosaveClock:
    """Idle debounce with a maximum interval so typing cannot postpone forever."""

    saved_digest: str = ""
    observed_digest: str = ""
    last_change: float = 0.0
    dirty_since: float | None = None
    last_attempt: float | None = None

    def reset(self, digest: str) -> None:
        self.saved_digest = self.observed_digest = digest
        self.dirty_since = self.last_attempt = None

    def due(self, digest: str, now: float) -> bool:
        if digest != self.observed_digest:
            self.observed_digest = digest
            self.last_change = now
        if digest == self.saved_digest:
            self.dirty_since = None
            return False
        if self.dirty_since is None:
            self.dirty_since = now
        if self.last_attempt is not None and now - self.last_attempt < AUTOSAVE_RETRY_SECONDS:
            return False
        return (
            now - self.last_change >= AUTOSAVE_IDLE_SECONDS
            or now - self.dirty_since >= AUTOSAVE_MAX_SECONDS
        )

    def saved(self, digest: str) -> None:
        self.saved_digest = digest
        self.dirty_since = None


def recovery_directory() -> Path:
    """Keep recovery local even when project files live on a network/synced drive."""

    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "GeoMapExpExtractor" / "recovery"


def project_revision(paths: dict[str, Path]) -> str:
    """Detect a project changed on disk since the recovery copy was started."""

    digest = hashlib.sha256()
    for key in ("manifest", "corrected_json", "extracted_json", "notes", "feedback"):
        path = paths.get(key)
        digest.update(key.encode())
        digest.update(path.read_bytes() if path is not None and path.exists() else b"<missing>")
    return digest.hexdigest()


def snapshot_path(root: Path, project_dir: Path, session_id: str) -> Path:
    key = hashlib.sha256(str(project_dir.resolve()).encode("utf-8")).hexdigest()
    return root / f"{key}-{session_id}.json"


def save_snapshot(path: Path, snapshot: RecoverySnapshot) -> None:
    write_json_atomic(snapshot.model_dump(mode="json"), path)


def find_snapshots(
    root: Path, project_dir: Path | None = None
) -> tuple[list[RecoveryRecord], list[str]]:
    records: list[RecoveryRecord] = []
    errors: list[str] = []
    if not root.exists():
        return records, errors
    for path in root.glob("*.json"):
        try:
            snapshot = RecoverySnapshot.model_validate_json(path.read_text(encoding="utf-8"))
            if not Path(snapshot.project_dir).is_absolute():
                raise ValueError("Recovery project path must be absolute")
            if snapshot.saved_at.tzinfo is None:
                raise ValueError("Recovery timestamp requires a timezone")
            if project_dir is None or Path(snapshot.project_dir).resolve() == project_dir.resolve():
                records.append(RecoveryRecord(path, snapshot))
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
    records.sort(key=lambda record: record.snapshot.saved_at, reverse=True)
    return records, errors


def make_snapshot(
    project_dir: Path,
    run_id: str,
    request_fingerprint: str,
    base_revision: str,
    state: ReviewState,
) -> RecoverySnapshot:
    return RecoverySnapshot(
        project_dir=str(project_dir.resolve()),
        run_id=run_id,
        request_fingerprint=request_fingerprint,
        base_revision=base_revision,
        saved_at=datetime.now(timezone.utc),
        state=state,
    )
