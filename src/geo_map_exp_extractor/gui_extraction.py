"""GUI actions for cancelled, interrupted and token-limited extraction runs."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from geo_map_exp_extractor.extraction_session import SESSION_FILE
from geo_map_exp_extractor.settings import DEFAULT_MAX_OUTPUT_TOKENS


class ExtractionContinuationMixin:
    """Keep continuation spending opt-in and preserve the previous review project."""

    def _cancel_extraction(self) -> None:
        self._cancel_event.set()
        self._progress_elapsed.set("Cancellation requested; saving available results...")

    def _offer_continuation(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Extraction incomplete — progress saved")
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        result = self.result
        text = (
            f"{len(result.rows)} complete rows saved.\n"
            f"Status: {result.manifest.get('extraction_status')}\n"
            f"{result.warnings[-1] if result.warnings else ''}\n"
            f"Total reported tokens: {result.usage.get('total_tokens') or 'unavailable'}\n"
            f"Estimated cost so far (USD): {result.estimated_cost_usd if result.estimated_cost_usd is not None else 'unavailable'}\n\n"
            "The table is incomplete. Continue requires approval for another token allowance. "
            "You can also review the partial table or keep it for later.\n\n"
            f"Saved project: {result.run_dir}"
        )
        ttk.Label(frame, text=text, wraplength=540, justify=tk.LEFT).pack(fill=tk.X)
        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, pady=(16, 0))

        def continue_now() -> None:
            dialog.destroy()
            self._continue_extraction()

        ttk.Button(buttons, text="Continue…", command=continue_now).pack(side=tk.LEFT, padx=3)
        ttk.Button(buttons, text="Review partial results", command=dialog.destroy).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(buttons, text="Stop and keep progress", command=dialog.destroy).pack(
            side=tk.LEFT, padx=3
        )
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        self._center_window(dialog)

    def _continue_extraction(self) -> None:
        if self._block_while_running("continuing extraction"):
            return
        if (
            self.result is None
            or not self.result.manifest.get("continuation_available")
            or not (self.result.run_dir / SESSION_FILE).is_file()
        ):
            messagebox.showinfo(
                "Continue extraction", "Load an incomplete extraction project first.", parent=self
            )
            return
        previous = self.result.manifest.get("max_output_tokens") or DEFAULT_MAX_OUTPUT_TOKENS
        allowance = simpledialog.askinteger(
            "Approve continuation",
            f"Saved rows: {len(self.rows)}\n"
            f"Reported tokens used so far: {self.result.usage.get('total_tokens') or 'unavailable'}\n\n"
            "Continue uses the saved image, profile and row boundary. New API calls incur "
            "additional charges, including input context. Existing charges remain.\n\n"
            "A saved active response is retrieved first without creating a replacement. "
            "If no response ID was received before a connection failure, the original "
            "request may still have run.\n\n"
            "Maximum output tokens per new call (reasoning plus table output):",
            initialvalue=previous,
            minvalue=1,
            parent=self,
        )
        if allowance is None:
            return
        # Saving first commits open editors and pending review metadata. The new
        # run copies those files, leaving this project and its trail intact.
        if not self._save_corrected():
            return
        review_state = self._capture_review_state().model_dump(mode="json")
        self._cancel_event = threading.Event()
        self._continuing_extraction = True
        self._worker_result = None
        self._worker_error = None
        self._start_progress_dialog(False)
        self.run_button.configure(state=tk.DISABLED)
        self._run_thread = threading.Thread(
            target=self._run_extraction_worker,
            kwargs={
                "continue_run_dir": self.result.run_dir,
                "max_output_tokens": allowance,
                "api_key": self.api_key_override,
                "cancel_event": self._cancel_event,
                "review_state": review_state,
            },
            daemon=True,
        )
        self._run_thread.start()
        self.after(150, self._poll_run_thread)
