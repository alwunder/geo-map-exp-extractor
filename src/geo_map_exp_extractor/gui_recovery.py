"""Tk autosave integration; widget reads stay on the UI thread, writes do not."""

from __future__ import annotations

import time
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tkinter import messagebox
from uuid import uuid4

from geo_map_exp_extractor.recovery import (
    AUTOSAVE_POLL_MS,
    AutosaveClock,
    RecoveryRecord,
    ReviewState,
    find_snapshots,
    make_snapshot,
    project_revision,
    recovery_directory,
    save_snapshot,
    snapshot_path,
)


class RecoveryMixin:
    """Recovery behavior shared by normal saves and project transitions."""

    def _init_recovery(self) -> None:
        self._recovery_root = recovery_directory()
        self._recovery_session = uuid4().hex
        self._recovery_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="autosave")
        self._recovery_future: Future[None] | None = None
        self._recovery_clock = AutosaveClock()
        self._recovery_path: Path | None = None
        self._recovery_source: Path | None = None
        self._recovery_clean_digest = ""
        self._recovery_revision = ""
        self._recovery_written_digest = ""
        self._recovery_timer: str | None = None
        self._recovery_cell_editor: tuple[int, str, tk.Text] | None = None
        self.recovery_status = tk.StringVar(value="Recovery autosave: ready")

    def _capture_review_state(self) -> ReviewState:
        fields = list(self.result.fields)
        rows = [
            {field: "" if row.get(field) is None else str(row[field]) for field in fields}
            for row in self.rows
        ]
        if self._use_tksheet and self.sheet is not None:
            for index, values in enumerate(self.sheet.get_sheet_data()):
                if index >= len(rows):
                    break
                for field, value in zip(fields, values):
                    rows[index][field] = "" if value is None else str(value)
            editor = self.sheet.get_text_editor_widget()
            if editor is not None and editor.winfo_viewable():
                selected = self.sheet.get_currently_selected()
                row, column = selected.row, selected.column
                value = self.sheet.get_text_editor_value()
                if 0 <= row < len(rows) and 0 <= column < len(fields) and value is not None:
                    rows[row][fields[column]] = value
        if self._recovery_cell_editor is not None:
            row, field, editor = self._recovery_cell_editor
            if editor.winfo_exists() and 0 <= row < len(rows):
                rows[row][field] = editor.get("1.0", "end-1c")
        targets = self._pending_row_metadata_target_indexes()
        return ReviewState(
            fields=fields,
            rows=rows,
            original_rows=[
                {field: "" if row.get(field) is None else str(row[field]) for field in fields}
                for row in self.original_rows
            ],
            row_statuses=list(self.row_statuses),
            row_comments=list(self.row_comments),
            feedback_records=list(self.feedback_records),
            notes_text=self.notes.get("1.0", "end-1c"),
            pending_targets=targets,
            pending_status=self._status_code_from_label(self.row_status_var.get())
            if targets
            else "needs_review",
            pending_comment=self.row_comment_var.get() if targets else "",
        )

    def _start_project_recovery(self) -> None:
        self._recovery_path = snapshot_path(
            self._recovery_root,
            self.result.run_dir,
            self._recovery_session,
        )
        self._recovery_source = None
        self._recovery_clean_digest = self._capture_review_state().digest()
        self._recovery_clock.reset(self._recovery_clean_digest)
        # An unreadable project must not disable future recovery attempts silently.
        try:
            self._recovery_revision = project_revision(self.result.output_paths)
        except OSError as exc:
            self._recovery_revision = ""
            self.recovery_status.set(f"Recovery warning: cannot fingerprint saved project: {exc}")
        else:
            self.recovery_status.set("Recovery autosave: waiting for edits")

    def _finish_recovery_write(self, *, wait: bool = False) -> None:
        future = self._recovery_future
        if future is None or (not wait and not future.done()):
            return
        self._recovery_future = None
        future.result()
        self._recovery_clock.saved(self._recovery_written_digest)
        self.recovery_status.set(f"Recovery copy saved at {time.strftime('%H:%M:%S')}")

    def _write_recovery(self, state: ReviewState) -> None:
        snapshot = make_snapshot(
            self.result.run_dir,
            self.result.run_id,
            self.result.request_fingerprint,
            self._recovery_revision,
            state,
        )
        self._recovery_written_digest = state.digest()
        self._recovery_clock.last_attempt = time.monotonic()
        self._recovery_future = self._recovery_executor.submit(
            save_snapshot,
            self._recovery_path,
            snapshot,
        )

    def _autosave_tick(self) -> None:
        self._recovery_timer = None
        try:
            self._finish_recovery_write()
            if self.result is not None and self._recovery_path is not None:
                state = self._capture_review_state()
                digest = state.digest()
                if self._recovery_future is None and self._recovery_clock.due(
                    digest, time.monotonic()
                ):
                    self._write_recovery(state)
        except Exception as exc:  # noqa: BLE001 - keep editing possible and retry later.
            self._recovery_clock.last_attempt = time.monotonic()
            self.recovery_status.set(f"Recovery autosave failed; retrying: {exc}")
        finally:
            self._recovery_timer = self.after(AUTOSAVE_POLL_MS, self._autosave_tick)

    def _checkpoint_before_save(self) -> None:
        """Retain a coherent recovery copy if a multi-file explicit save fails."""

        if self._recovery_path is not None:
            self._finish_recovery_write(wait=True)
            self._write_recovery(self._capture_review_state())
            self._finish_recovery_write(wait=True)

    def _end_project_recovery(self) -> bool:
        """Drain any writer before deleting copies after Save or explicit Discard."""

        try:
            try:
                self._finish_recovery_write(wait=True)
            except Exception:
                # A failed background write does not prevent intentional discard.
                pass
            for path in {self._recovery_path, self._recovery_source} - {None}:
                path.unlink(missing_ok=True)
        except OSError as exc:
            messagebox.showerror("Recovery cleanup failed", str(exc), parent=self)
            return False
        self._recovery_path = self._recovery_source = None
        self._recovery_cell_editor = None
        self._recovery_clock.reset("")
        self.recovery_status.set("Recovery autosave: ready")
        return True

    def _shutdown_recovery(self) -> None:
        if self._recovery_timer is not None:
            self.after_cancel(self._recovery_timer)
        self._recovery_executor.shutdown(wait=True)

    def _choose_recovery(
        self, project_dir: Path | None = None
    ) -> tuple[bool, RecoveryRecord | None]:
        try:
            records, errors = find_snapshots(self._recovery_root, project_dir)
        except OSError as exc:
            messagebox.showerror("Could not read recovery copies", str(exc), parent=self)
            return False, None
        if errors:
            messagebox.showwarning(
                "Unreadable recovery copies",
                "These files were preserved for inspection:\n\n" + "\n".join(errors),
                parent=self,
            )
        for record in records:
            if record.path in {self._recovery_path, self._recovery_source}:
                continue
            snapshot = record.snapshot
            decision = messagebox.askyesnocancel(
                "Recover unsaved work?",
                f"Project: {snapshot.project_dir}\n"
                f"Recovery copy: {snapshot.saved_at.astimezone():%Y-%m-%d %H:%M:%S}\n\n"
                "Yes: restore this copy for review.\nNo: discard this recovery copy.\n"
                "Cancel: keep it for later. Saved project files will not be overwritten.",
                parent=self,
            )
            if decision is None:
                return False, None
            if decision:
                return True, record
            try:
                record.path.unlink()
            except OSError as exc:
                messagebox.showerror("Could not discard recovery copy", str(exc), parent=self)
                return False, None
        return True, None

    def _recover_autosave(self) -> None:
        if self._block_while_running("recovering another project"):
            return
        proceed, record = self._choose_recovery()
        if proceed and record is not None:
            self._load_project(
                project_dir=Path(record.snapshot.project_dir), recovery_record=record
            )

    def _apply_recovery(self, record: RecoveryRecord) -> None:
        state = record.snapshot.state
        self.rows = [dict(row) for row in state.rows]
        self.original_rows = [dict(row) for row in state.original_rows]
        self.row_statuses = list(state.row_statuses)
        self.row_comments = list(state.row_comments)
        self.feedback_records = list(state.feedback_records)
        self.selected_row_index = (
            state.pending_targets[0] if state.pending_targets else (0 if self.rows else None)
        )
        self._configure_table(state.fields, self.rows)
        self._sync_row_metadata_controls()
        if state.pending_targets:
            self._row_metadata_target_indexes = list(state.pending_targets)
            self.row_status_var.set(self._status_label_from_code(state.pending_status))
            self.row_comment_var.set(state.pending_comment)
        self.notes.delete("1.0", tk.END)
        self.notes.insert("1.0", state.notes_text)
        self.notes.edit_modified(False)
        self._recovery_source = record.path
        self.has_unsaved_changes = True
        self._set_status_message("Recovered unsaved work. Review and Save to keep it.")
        self.recovery_status.set("Recovery restored; explicit Save still required")
