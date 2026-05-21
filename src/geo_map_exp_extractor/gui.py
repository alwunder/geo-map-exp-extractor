"""Simple Tkinter review workbench for geo-map extraction results."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
import json
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any

from PIL import Image, ImageTk
try:
    from tksheet import Sheet
except ImportError:  # pragma: no cover - optional GUI dependency
    Sheet = None

# Support running this file directly (e.g., IDE "Run file") in a src-layout project.
if __package__ is None or __package__ == "":
    src_root = Path(__file__).resolve().parents[1]
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))

from geo_map_exp_extractor.config import load_profile
from geo_map_exp_extractor.env_utils import load_env_from_candidates
from geo_map_exp_extractor.jobs import (
    ExtractionJobResult,
    build_feedback_record,
    promote_corrected_to_gold,
    review_output_paths,
    run_extraction_job,
    write_corrected_outputs,
    write_feedback_jsonl,
)
from geo_map_exp_extractor.settings import (
    DEFAULT_IMAGE_DETAIL,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    SUPPORTED_IMAGE_DETAILS,
    SUPPORTED_MODELS,
    SUPPORTED_REASONING_EFFORTS,
)


class ReviewWorkbench(tk.Tk):
    """Small, functional GUI for running extraction and correcting rows."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Geologic Map Explanation Extraction Review Workbench")
        self.geometry("1440x810")
        self._load_environment()

        self.image_path = tk.StringVar()
        self.profile_path = tk.StringVar(value="")
        self.output_dir = tk.StringVar(value=self._display_path(self._repo_root() / "outputs"))
        self.api_key_override: str | None = None
        self.model = tk.StringVar(value=DEFAULT_MODEL)
        self.reasoning_effort = tk.StringVar(value=DEFAULT_REASONING_EFFORT)
        self.image_detail = tk.StringVar(value=DEFAULT_IMAGE_DETAIL)
        self.max_output_tokens = tk.IntVar(value=DEFAULT_MAX_OUTPUT_TOKENS)
        self.use_max_output_tokens_limit = tk.BooleanVar(value=True)
        self.include_profile_notes = tk.BooleanVar(value=False)
        self.dry_run = tk.BooleanVar(value=False)
        self.force_rerun = tk.BooleanVar(value=False)
        self.segmented_mode = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value=self._api_key_status_message())

        self.result: ExtractionJobResult | None = None
        self.rows: list[dict[str, Any]] = []
        self.original_rows: list[dict[str, Any]] = []
        self.feedback_records: list[dict[str, Any]] = []
        self.row_statuses: list[str] = []
        self.row_comments: list[str] = []
        self.row_status_options = ("accepted", "needs_review", "bad_extraction")
        self.row_status_labels = {
            "accepted": "accepted",
            "needs_review": "needs review",
            "bad_extraction": "bad extraction",
        }
        self.row_status_var = tk.StringVar(value=self.row_status_labels["needs_review"])
        self.row_comment_var = tk.StringVar(value="")
        self.selected_row_index: int | None = None
        self.table_fields: list[str] = []
        self._use_tksheet = Sheet is not None
        self.sheet: Any = None
        self.table: ttk.Treeview | None = None
        self.table_frame: ttk.Frame | None = None
        self.column_widths_by_field: dict[str, int] = {}
        self._manual_column_resize = False
        self.zoom = 1.0
        self.source_image: Image.Image | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.preview_source_path: Path | None = None
        self.table_style_name = "Results.Treeview"
        self._default_treeview_row_height = 72
        self.has_unsaved_changes = False
        self._is_panning = False
        self._run_thread: threading.Thread | None = None
        self._worker_result: ExtractionJobResult | None = None
        self._worker_error: Exception | None = None
        self._progress_dialog: tk.Toplevel | None = None
        self._progress_elapsed = tk.StringVar(value="Elapsed: 00:00")
        self._progress_started_at: datetime | None = None

        self._build_widgets()
        self._refresh_profiles()

    def _default_profile_path(self) -> Path:
        profiles = self._profiles_dir()
        choices = sorted(profiles.glob("*.yml")) + sorted(profiles.glob("*.yaml"))
        return choices[0] if choices else profiles

    def _display_path(self, path: str | Path) -> str:
        """Format paths consistently for UI display across operating systems."""

        return Path(path).as_posix()

    def _profiles_dir(self) -> Path:
        repo_profiles = self._repo_root() / "profiles"
        return repo_profiles if repo_profiles.exists() else Path.cwd() / "profiles"

    def _repo_root(self) -> Path:
        """Return repository root for stable default paths."""

        return Path(__file__).resolve().parents[2]

    def _load_environment(self) -> None:
        """Load .env values so OPENAI_API_KEY works in GUI launches."""

        load_env_from_candidates([Path.cwd() / ".env", self._repo_root() / ".env"])

    def _is_valid_api_key_value(self, value: str | None) -> bool:
        """Basic sanity check for API-key-like values."""

        if not value:
            return False
        normalized = value.strip()
        if not normalized:
            return False
        placeholders = {"your_real_key_here", "your_api_key_here", "openai_api_key"}
        return normalized.lower() not in placeholders

    def _resolve_api_key_source(self) -> tuple[str | None, str]:
        """Return active API key and its source label."""

        if self._is_valid_api_key_value(self.api_key_override):
            return self.api_key_override, "session override"
        env_key = os.environ.get("OPENAI_API_KEY")
        if self._is_valid_api_key_value(env_key):
            return env_key, ".env / environment"
        return None, "not found"

    def _api_key_status_message(self) -> str:
        """Build an API-key status message for the footer status bar."""

        _, source = self._resolve_api_key_source()
        if source == "not found":
            return "No API key loaded. Add OPENAI_API_KEY to .env or use 'Set API key...'."
        return f"API key loaded from {source}. Choose an image, profile, and output folder."

    def _format_elapsed(self, elapsed_seconds: float) -> str:
        total_seconds = max(0, int(round(elapsed_seconds)))
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes:02d}:{seconds:02d}"

    def _set_status_message(self, message: str) -> None:
        suffix = " | unsaved changes" if self.result is not None and self.has_unsaved_changes else ""
        self.status.set(f"{message}{suffix}")

    def _mark_unsaved(self) -> None:
        if self.result is None:
            return
        self.has_unsaved_changes = True
        self._set_status_message(f"Project loaded: {self.result.run_id}")

    def _mark_saved(self) -> None:
        self.has_unsaved_changes = False
        if self.result is not None:
            self._set_status_message(f"Project saved: {self.result.run_id}")

    def _status_color(self, status: str) -> str:
        if status == "accepted":
            return "#E8F6E8"
        if status == "bad_extraction":
            return "#FCE8E8"
        return "#FFF7DA"

    def _sync_status_dropdown_color(self) -> None:
        status_code = self._status_code_from_label(self.row_status_var.get())
        color = self._status_color(status_code)
        style = ttk.Style(self)
        style.configure(self.row_status_style_name, fieldbackground=color, background=color)
        style.map(
            self.row_status_style_name,
            fieldbackground=[("readonly", color)],
            selectbackground=[("readonly", color)],
            background=[("readonly", color)],
        )

    def _on_notes_modified(self, _: tk.Event[Any]) -> None:
        if not self.notes.edit_modified():
            return
        self.notes.edit_modified(False)
        self._mark_unsaved()

    def _center_window(self, window: tk.Toplevel) -> None:
        """Center a dialog over the main app window, with screen bounds clamping."""

        window.update_idletasks()
        width = max(window.winfo_width(), window.winfo_reqwidth())
        height = max(window.winfo_height(), window.winfo_reqheight())
        if width <= 1:
            width = 480
        if height <= 1:
            height = 220

        self.update_idletasks()
        parent_w = max(self.winfo_width(), self.winfo_reqwidth())
        parent_h = max(self.winfo_height(), self.winfo_reqheight())
        parent_x = self.winfo_rootx()
        parent_y = self.winfo_rooty()

        x = parent_x + (parent_w - width) // 2
        y = parent_y + (parent_h - height) // 2

        screen_w = window.winfo_screenwidth()
        screen_h = window.winfo_screenheight()
        x = max(0, min(x, screen_w - width))
        y = max(0, min(y, screen_h - height))
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _prompt_api_key_override(self) -> None:
        """Open API key dialog with masked entry and .env reset support."""

        self._load_environment()
        env_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        initial_value = self.api_key_override if self.api_key_override else env_key
        key_var = tk.StringVar(value=initial_value)
        info_var = tk.StringVar(value="Key is hidden. Save to apply for this session.")
        env_loaded_via_button = {"value": False}

        dialog = tk.Toplevel(self)
        dialog.title("Set API Key")
        dialog.geometry("560x180")
        dialog.minsize(520, 170)
        dialog.transient(self)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        frame = ttk.Frame(dialog, padding=10)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        ttk.Label(frame, text="OpenAI API key (masked):").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(frame, textvariable=key_var, show="*")
        entry.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        entry.focus_set()
        ttk.Label(frame, textvariable=info_var).grid(row=2, column=0, sticky="w")

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, sticky="e", pady=(10, 0))

        def use_env_key() -> None:
            self._load_environment()
            current_env_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
            if not self._is_valid_api_key_value(current_env_key):
                messagebox.showerror(
                    "No valid .env key",
                    "Could not load a valid OPENAI_API_KEY from .env/environment.",
                    parent=dialog,
                )
                return
            key_var.set(current_env_key)
            env_loaded_via_button["value"] = True
            info_var.set("Loaded key from .env. Click Save to reset to environment key.")

        def save_key() -> None:
            value = key_var.get().strip()
            if env_loaded_via_button["value"] and value == (os.environ.get("OPENAI_API_KEY") or "").strip():
                self.api_key_override = None
            else:
                self.api_key_override = value or None
            self.status.set(self._api_key_status_message())
            dialog.destroy()

        ttk.Button(buttons, text="Use .env key", command=use_env_key).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="Save", command=save_key).pack(side=tk.LEFT)

        dialog.bind("<Escape>", lambda _: dialog.destroy())
        dialog.bind("<Return>", lambda _: save_key())
        self._center_window(dialog)

    def _clear_api_key_override(self) -> None:
        """Clear session override and fall back to .env/environment key."""

        self.api_key_override = None
        self.status.set(self._api_key_status_message())

    def _build_widgets(self) -> None:
        top = ttk.Frame(self, padding=8)
        top.pack(side=tk.TOP, anchor=tk.NW)

        self._path_row(top, "Image:", self.image_path, self._browse_image, row=0)
        self._profile_row(top, row=1)
        self._path_row(top, "Output:", self.output_dir, self._browse_output, row=2)
        
        ttk.Button(top, text="Help", command=self._show_help).grid(
            row=0, column=9, sticky="we", ipadx=4
        )
        ttk.Checkbutton(top, text="Use profile notes", variable=self.include_profile_notes).grid(
            row=1, column=9, sticky="w", pady=2
        )
        ttk.Button(top, text="Open output folder", command=self._open_output_folder).grid(
            row=2, column=9, sticky="we", ipadx=4
        )
        ttk.Button(top, text="Set API key...", command=self._prompt_api_key_override).grid(
            row=4, column=3, sticky="e", padx=2, ipadx=4
        )

        ttk.Label(top, text="Model:").grid(row=3, column=0, sticky="w", padx=(0, 4), pady=2)
        ttk.Combobox(
            top,
            textvariable=self.model,
            values=SUPPORTED_MODELS,
            state="readonly",
        ).grid(row=3, column=1, columnspan=2, sticky="we", padx=2, pady=2)
        ttk.Label(top, text="Reasoning effort:").grid(row=4, column=0, sticky="w", padx=(0, 4), pady=2)
        ttk.Combobox(
            top,
            textvariable=self.reasoning_effort,
            values=SUPPORTED_REASONING_EFFORTS,
            width=8,
            state="readonly",
        ).grid(row=4, column=1, sticky="w", padx=2, pady=2)
        ttk.Label(top, text="Image detail:").grid(row=5, column=0, sticky="w", padx=(0, 4), pady=2)
        ttk.Combobox(
            top,
            textvariable=self.image_detail,
            values=SUPPORTED_IMAGE_DETAILS,
            width=8,
            state="readonly",
        ).grid(row=5, column=1, sticky="w", padx=2, pady=2)

        ttk.Label(top, text="API call options:").grid(row=3, column=3, sticky="e", padx=(20, 0), pady=2)
        ttk.Checkbutton(top, text="Dry run (no API call)", variable=self.dry_run).grid(
            row=3, column=4, sticky="w", pady=2, padx=4
        )
        ttk.Checkbutton(top, text="Force re-run", variable=self.force_rerun).grid(
            row=3, column=5, sticky="w", pady=2, padx=4
        )
        ttk.Checkbutton(top, text="Segmented mode (higher cost)", variable=self.segmented_mode).grid(
            row=3, column=6, sticky="w", pady=2, padx=(4,50)
        )
        ttk.Checkbutton(top, text="Apply maximum output token limit:", variable=self.use_max_output_tokens_limit).grid(
            row=4, column=4, columnspan=2, sticky="e", pady=2
        )
        ttk.Entry(top, textvariable=self.max_output_tokens, width=10).grid(
            row=4, column=6, sticky="w", padx=0, pady=2
        )

        self.run_button = ttk.Button(top, text="Run extraction", command=self._run_extraction)
        self.run_button.grid(row=5, column=6, sticky="we", padx=(4,0), ipadx=20)

        ttk.Button(top, text="Load project", command=self._load_project).grid(
            row=5, column=10, padx=2, ipadx=4
        )
        ttk.Button(top, text="Save project", command=self._save_corrected).grid(
            row=5, column=11, padx=2, ipadx=4
        )
        ttk.Button(top, text="Promote corrected", command=self._promote_corrected).grid(
            row=5, column=12, padx=(50,0), ipadx=4
        )
        top.columnconfigure(1, weight=1)

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        preview_frame = ttk.Frame(paned)
        controls = ttk.Frame(preview_frame)
        controls.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(controls, text="Zoom -", command=lambda: self._set_zoom(self.zoom * 0.8)).pack(
            side=tk.LEFT
        )
        ttk.Button(controls, text="Zoom +", command=lambda: self._set_zoom(self.zoom * 1.25)).pack(
            side=tk.LEFT
        )
        ttk.Button(controls, text="100%", command=lambda: self._set_zoom(1.0)).pack(
            side=tk.LEFT
        )
        ttk.Button(controls, text="Zoom Width", command=self._zoom_width).pack(side=tk.LEFT)
        ttk.Button(controls, text="Zoom Extents", command=self._zoom_extents).pack(side=tk.LEFT)
        canvas_frame = ttk.Frame(preview_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(canvas_frame, background="#222222", cursor="")
        x_scroll = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        y_scroll = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        self.canvas.bind("<ButtonPress-1>", self._start_pan)
        self.canvas.bind("<B1-Motion>", self._pan_image)
        self.canvas.bind("<ButtonRelease-1>", self._end_pan)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        paned.add(preview_frame, weight=1)

        right = ttk.Frame(paned)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        table_controls = ttk.Frame(right)
        table_controls.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(table_controls, text="Add row", command=self._add_row).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(table_controls, text="Delete row", command=self._delete_selected_row).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Button(table_controls, text="Move up", command=lambda: self._move_selected_row(-1)).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Button(table_controls, text="Move down", command=lambda: self._move_selected_row(1)).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Button(table_controls, text="Auto-fit rows", command=self._auto_fit_rows).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(table_controls, text="Reset widths", command=self._reset_column_widths).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Label(table_controls, text="Status").pack(side=tk.LEFT, padx=(12, 2))
        self.row_status_style_name = "RowStatus.TCombobox"
        ttk.Style(self).configure(self.row_status_style_name, fieldbackground="#FFF7DA")
        self.row_status_combo = ttk.Combobox(
            table_controls,
            textvariable=self.row_status_var,
            values=[self.row_status_labels[code] for code in self.row_status_options],
            width=16,
            state="readonly",
            style=self.row_status_style_name,
        )
        self.row_status_combo.pack(side=tk.LEFT)
        self.row_status_combo.bind("<<ComboboxSelected>>", self._apply_selected_row_metadata)
        ttk.Label(table_controls, text="Comment").pack(side=tk.LEFT, padx=(10, 2))
        self.row_comment_entry = ttk.Entry(table_controls, textvariable=self.row_comment_var, width=26)
        self.row_comment_entry.pack(side=tk.LEFT)
        self.row_comment_entry.bind("<Return>", self._apply_selected_row_metadata)
        ttk.Button(table_controls, text="Apply", command=self._apply_selected_row_metadata).pack(
            side=tk.LEFT, padx=(4, 0)
        )

        style = ttk.Style(self)
        style.configure(self.table_style_name, rowheight=self._default_treeview_row_height)
        table_frame = ttk.Frame(right)
        self.table_frame = table_frame
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.grid_propagate(False)
        table_frame.bind("<Configure>", self._on_table_frame_resize)
        if self._use_tksheet and Sheet is not None:
            self.sheet = Sheet(
                table_frame,
                data=[],
                headers=[],
                theme="light blue",
                table_wrap="w",
                header_wrap="w",
                index_wrap="w",
                cell_auto_resize_enabled=False,
                auto_resize_columns=None,
            )
            self.sheet.enable_bindings("all")
            self.sheet.extra_bindings("end_edit_cell", self._on_sheet_end_edit_cell)
            self.sheet.extra_bindings("end_paste", self._on_sheet_end_paste)
            self.sheet.extra_bindings("cell_select", self._on_sheet_cell_select)
            self.sheet.extra_bindings("column_width_resize", self._on_sheet_column_resize)
            self.sheet.bind("<ButtonRelease-1>", self._on_sheet_select_event)
            self.sheet.bind("<KeyRelease>", self._on_sheet_select_event)
            self.sheet.pack(fill=tk.BOTH, expand=True)
        else:
            self.table = ttk.Treeview(table_frame, show="headings", style=self.table_style_name)
            self.table.bind("<Double-1>", self._begin_cell_edit)
            self.table.bind("<Configure>", self._refresh_table_display)
            self.table.bind("<ButtonRelease-1>", self._refresh_table_display)
            self.table.bind("<MouseWheel>", self._on_table_mousewheel)
            self.table.bind("<Shift-MouseWheel>", self._on_table_shift_mousewheel)
            self.table.bind("<<TreeviewSelect>>", self._on_treeview_select)
            table_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.table.yview)
            table_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.table.xview)
            self.table.configure(yscrollcommand=table_y.set, xscrollcommand=table_x.set)
            self.table.grid(row=0, column=0, sticky="nsew")
            table_y.grid(row=0, column=1, sticky="ns")
            table_x.grid(row=1, column=0, sticky="ew")
            table_frame.rowconfigure(0, weight=1)
            table_frame.columnconfigure(0, weight=1)

        ttk.Label(right, text="Notes").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.notes = tk.Text(right, height=6, wrap=tk.WORD)
        self.notes.grid(row=3, column=0, sticky="ew")
        self.notes.bind("<<Modified>>", self._on_notes_modified)
        paned.add(right, weight=1)

        status_frame = ttk.Frame(self, padding=(8, 4))
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(status_frame, textvariable=self.status).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _path_row(
        self, parent: ttk.Frame, label: str, variable: tk.StringVar, command: Any, row: int
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 4), pady=2)
        ttk.Entry(parent, textvariable=variable, width=120).grid(row=row, column=1, columnspan=7, sticky="we", pady=2)
        ttk.Button(parent, text="Browse...", command=command).grid(row=row, column=8, padx=4)

    def _profile_row(self, parent: ttk.Frame, row: int) -> None:
        ttk.Label(parent, text="Profile:").grid(row=row, column=0, sticky="w", padx=(0, 4), pady=2)
        self.profile_combo = ttk.Combobox(parent, textvariable=self.profile_path, state="readonly")
        self.profile_combo.grid(row=row, column=1, columnspan=7, sticky="we", pady=2)
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)
        ttk.Button(parent, text="Browse...", command=self._browse_profile).grid(
            row=row, column=8, padx=4
        )

    def _refresh_profiles(self) -> None:
        profiles = sorted(self._profiles_dir().glob("*.yml")) + sorted(
            self._profiles_dir().glob("*.yaml")
        )
        self.profile_combo["values"] = [self._display_path(path) for path in profiles]
        current = Path(self.profile_path.get()) if self.profile_path.get().strip() else None
        if current is not None and current.is_file():
            self._apply_profile_settings(load_profile(current))
        else:
            self.profile_path.set("")

    def _on_profile_selected(self, _: tk.Event[Any]) -> None:
        selected = self.profile_path.get().strip()
        if not selected:
            return
        profile_file = Path(selected)
        if not profile_file.is_file():
            messagebox.showerror("Invalid profile", f"Profile not found: {profile_file}", parent=self)
            self.profile_path.set("")
            return
        profile = load_profile(profile_file)
        self._apply_profile_settings(profile)
        self.rows = []
        self.original_rows = []
        self.row_statuses = []
        self.row_comments = []
        self.selected_row_index = None
        self.column_widths_by_field = {}
        self._manual_column_resize = False
        self._configure_table(profile.fields, [])
        self._sync_row_metadata_controls()

    def _apply_profile_settings(self, profile: Any) -> None:
        self.model.set(profile.model)
        self.reasoning_effort.set(profile.reasoning_effort)
        self.image_detail.set(profile.image_detail)
        self.max_output_tokens.set(profile.max_output_tokens)

    def _browse_image(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("Images", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp"), ("All files", "*.*")]
        )
        if path:
            self.image_path.set(self._display_path(path))
            self._load_preview(Path(path))

    def _browse_profile(self) -> None:
        path = filedialog.askopenfilename(
            initialdir=self._profiles_dir(),
            filetypes=[("YAML", "*.yml *.yaml"), ("All files", "*.*")],
        )
        if path:
            self.profile_path.set(self._display_path(path))
            profile = load_profile(path)
            self._apply_profile_settings(profile)
            self.rows = []
            self.original_rows = []
            self.row_statuses = []
            self.row_comments = []
            self.selected_row_index = None
            self.column_widths_by_field = {}
            self._manual_column_resize = False
            self._configure_table(profile.fields, [])
            self._sync_row_metadata_controls()

    def _browse_output(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.output_dir.set(self._display_path(path))

    def _run_extraction(self) -> None:
        if self._run_thread is not None and self._run_thread.is_alive():
            return
        selected_profile = self.profile_path.get().strip()
        if not selected_profile:
            messagebox.showerror("Missing profile", "Select a profile before running extraction.", parent=self)
            return
        profile_file = Path(selected_profile)
        if not profile_file.is_file():
            messagebox.showerror("Invalid profile", f"Profile not found: {profile_file}", parent=self)
            return
        active_key, _ = self._resolve_api_key_source()
        if not self.dry_run.get() and active_key is None:
            self.status.set(self._api_key_status_message())
            messagebox.showerror(
                "Missing API key",
                "No API key loaded. Add OPENAI_API_KEY to .env or use 'Set API key...'.",
                parent=self,
            )
            return
        image_candidate = Path(self.image_path.get())
        if image_candidate.is_dir():
            proceed = messagebox.askyesno(
                "Folder selected",
                (
                    "The selected path is a folder. GUI default mode is one image at a time.\n\n"
                    "Continue anyway? (The run will fail unless you choose a single file.)"
                ),
                parent=self,
            )
            if not proceed:
                self.status.set("Folder run cancelled. Choose a single image file.")
                return
        if not self.dry_run.get():
            proceed = messagebox.askyesno(
                "Confirm API call",
                "This operation will send 1 image and one prompt to the OpenAI API. "
                "This may incur API charges.\n\nContinue?",
                parent=self,
            )
            if not proceed:
                self.status.set("Extraction cancelled before API call.")
                return
            if self.segmented_mode.get():
                seg_proceed = messagebox.askyesno(
                    "Segmented mode enabled",
                    "Segmented mode can issue multiple API calls and increase cost.\n\nContinue?",
                    parent=self,
                )
                if not seg_proceed:
                    self.status.set("Segmented extraction cancelled.")
                    return
        try:
            max_output_tokens = self.max_output_tokens.get()
        except (tk.TclError, ValueError):
            messagebox.showerror(
                "Invalid max output tokens",
                "Max output tokens must be an integer.",
                parent=self,
            )
            return

        job_kwargs = {
            "image_path": self.image_path.get(),
            "profile_path": self.profile_path.get(),
            "output_dir": self.output_dir.get(),
            "api_key": self.api_key_override,
            "model": self.model.get(),
            "reasoning_effort": self.reasoning_effort.get(),
            "image_detail": self.image_detail.get(),
            "max_output_tokens": max_output_tokens,
            "use_max_output_tokens_limit": self.use_max_output_tokens_limit.get(),
            "include_profile_notes": self.include_profile_notes.get(),
            "dry_run": self.dry_run.get(),
            "force_rerun": self.force_rerun.get(),
            "segmented_mode": self.segmented_mode.get(),
        }

        self._worker_result = None
        self._worker_error = None
        self._start_progress_dialog(self.dry_run.get())
        self.run_button.configure(state=tk.DISABLED)
        self._set_status_message(
            "Running dry run..." if self.dry_run.get() else "Running extraction via OpenAI API..."
        )
        self._run_thread = threading.Thread(
            target=self._run_extraction_worker,
            kwargs=job_kwargs,
            daemon=True,
        )
        self._run_thread.start()
        self.after(150, self._poll_run_thread)

    def _run_extraction_worker(self, **job_kwargs: Any) -> None:
        try:
            self._worker_result = run_extraction_job(**job_kwargs)
        except Exception as exc:  # noqa: BLE001 - hand back to UI thread for display.
            self._worker_error = exc

    def _poll_run_thread(self) -> None:
        thread = self._run_thread
        if thread is not None and thread.is_alive():
            self._update_progress_elapsed()
            self.after(200, self._poll_run_thread)
            return

        self._close_progress_dialog()
        self.run_button.configure(state=tk.NORMAL)
        self._run_thread = None

        if self._worker_error is not None:
            exc = self._worker_error
            self._worker_error = None
            self._set_status_message(f"Extraction failed: {exc}")
            messagebox.showerror("Extraction failed", str(exc), parent=self)
            return
        if self._worker_result is None:
            self._set_status_message("Extraction failed: unknown worker error")
            messagebox.showerror(
                "Extraction failed",
                "No extraction result was returned.",
                parent=self,
            )
            return
        self._apply_extraction_result(self._worker_result)

    def _apply_extraction_result(self, result: ExtractionJobResult) -> None:
        self.result = result
        self.rows = [dict(row) for row in self.result.rows]
        self.original_rows = [dict(row) for row in self.result.rows]
        self.feedback_records = []
        self.row_statuses = ["needs_review" for _ in self.rows]
        self.row_comments = ["" for _ in self.rows]
        self.selected_row_index = 0 if self.rows else None
        self.column_widths_by_field = {}
        self._manual_column_resize = False
        self.has_unsaved_changes = False
        image_path = Path(self.image_path.get())
        if self.preview_source_path is None or image_path.resolve() != self.preview_source_path:
            self._load_preview(image_path)
        self._configure_table(self.result.fields, self.rows)
        self._sync_row_metadata_controls()
        self.notes.delete("1.0", tk.END)
        self.notes.insert(
            "1.0", Path(self.result.output_paths["notes"]).read_text(encoding="utf-8")
        )
        self.notes.edit_modified(False)
        self.has_unsaved_changes = False
        usage = self.result.usage
        summary_bits = [
            f"run: {self.result.run_id}",
            "dry-run" if self.result.dry_run else ("cache reuse" if self.result.cache_reused else "fresh API call"),
            f"rough image tokens: {self.result.rough_image_tokens}",
        ]
        if not self.result.dry_run:
            summary_bits.append(
                "usage input/output/total: "
                f"{usage.get('input_tokens')}/{usage.get('output_tokens')}/{usage.get('total_tokens')}"
            )
            summary_bits.append(f"reasoning tokens: {usage.get('reasoning_tokens')}")
            summary_bits.append(f"elapsed: {self._format_elapsed(self.result.elapsed_seconds)}")
            summary_bits.append(
                "est cost USD: "
                f"{self.result.estimated_cost_usd if self.result.estimated_cost_usd is not None else 'n/a'}"
            )
            if self.result.warnings:
                summary_bits.append("warnings present")
        self._set_status_message(" | ".join(summary_bits))
        if self.result.warnings:
            messagebox.showwarning("Extraction warning", "\n\n".join(self.result.warnings), parent=self)

    def _start_progress_dialog(self, is_dry_run: bool) -> None:
        self._close_progress_dialog()
        dialog = tk.Toplevel(self)
        dialog.title("Running extraction")
        dialog.geometry("460x160")
        dialog.minsize(420, 150)
        dialog.transient(self)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frame,
            text="Preparing dry run..." if is_dry_run else "Calling OpenAI API and validating output...",
            anchor="w",
        ).pack(fill=tk.X)
        progress = ttk.Progressbar(frame, mode="indeterminate")
        progress.pack(fill=tk.X, pady=(10, 8))
        progress.start(14)
        ttk.Label(frame, textvariable=self._progress_elapsed, anchor="w").pack(fill=tk.X)

        self._progress_dialog = dialog
        self._progress_started_at = datetime.now()
        self._progress_elapsed.set("Elapsed: 00:00")
        self._center_window(dialog)

    def _update_progress_elapsed(self) -> None:
        if self._progress_dialog is None or self._progress_started_at is None:
            return
        seconds = int((datetime.now() - self._progress_started_at).total_seconds())
        minutes = seconds // 60
        rem = seconds % 60
        self._progress_elapsed.set(f"Elapsed: {minutes:02d}:{rem:02d}")

    def _close_progress_dialog(self) -> None:
        if self._progress_dialog is None:
            return
        try:
            self._progress_dialog.grab_release()
        except tk.TclError:
            pass
        self._progress_dialog.destroy()
        self._progress_dialog = None
        self._progress_started_at = None

    def _load_preview(self, path: Path) -> None:
        self.source_image = Image.open(path)
        self.preview_source_path = path.resolve()
        self.canvas.configure(cursor="hand2")
        self._zoom_extents()

    def _set_zoom(self, zoom: float) -> None:
        self.zoom = max(0.1, min(zoom, 8.0))
        if self.source_image is None:
            return
        width = max(1, int(self.source_image.width * self.zoom))
        height = max(1, int(self.source_image.height * self.zoom))
        display = self.source_image.resize((width, height))
        self.preview_photo = ImageTk.PhotoImage(display)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.preview_photo)
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def _zoom_extents(self) -> None:
        if self.source_image is None:
            return
        self.update_idletasks()
        canvas_width = max(1, self.canvas.winfo_width())
        canvas_height = max(1, self.canvas.winfo_height())
        scale_x = canvas_width / self.source_image.width
        scale_y = canvas_height / self.source_image.height
        self._set_zoom(min(scale_x, scale_y))
        self.canvas.xview_moveto(0.0)
        self.canvas.yview_moveto(0.0)

    def _zoom_width(self) -> None:
        if self.source_image is None:
            return
        self.update_idletasks()
        canvas_width = max(1, self.canvas.winfo_width())
        scale_x = canvas_width / self.source_image.width
        self._set_zoom(scale_x)
        self.canvas.xview_moveto(0.0)
        self.canvas.yview_moveto(0.0)

    def _start_pan(self, event: tk.Event[Any]) -> None:
        if self.source_image is None:
            return
        self._is_panning = True
        self.canvas.configure(cursor="hand2")
        self.canvas.scan_mark(event.x, event.y)

    def _pan_image(self, event: tk.Event[Any]) -> None:
        if self.source_image is None or not self._is_panning:
            return
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _end_pan(self, _: tk.Event[Any]) -> None:
        self._is_panning = False
        if self.source_image is not None:
            self.canvas.configure(cursor="hand2")

    def _configure_table(self, fields: list[str], rows: list[dict[str, Any]]) -> None:
        self.table_fields = list(fields)
        self._ensure_row_metadata_length()
        if self._use_tksheet and self.sheet is not None:
            self._snapshot_sheet_column_widths()
            data = [[str(row.get(field, "")) for field in fields] for row in rows]
            self.sheet.headers(newheaders=fields, reset_col_positions=True, redraw=False)
            self.sheet.set_sheet_data(
                data,
                reset_col_positions=True,
                reset_row_positions=True,
                redraw=False,
                verify=False,
                reset_highlights=False,
                keep_formatting=True,
            )
            self._apply_sheet_column_widths(fields)
            self._fit_rows_for_all(redraw=False)
            self._apply_sheet_status_highlights()
            if self.selected_row_index is not None and rows:
                selected = max(0, min(self.selected_row_index, len(rows) - 1))
                self.selected_row_index = selected
                self._select_sheet_row_preserve_column(selected)
            self.sheet.refresh()
            return

        if self.table is None:
            return
        self.table.delete(*self.table.get_children())
        self.table["columns"] = fields
        default_widths = self._compute_default_column_widths(fields)
        for field in fields:
            self.table.heading(field, text=field)
            width = self.column_widths_by_field.get(field, default_widths.get(field, 120))
            self.table.column(field, width=width, stretch=False, minwidth=80)
        for index, row in enumerate(rows):
            self.table.insert(
                "",
                tk.END,
                iid=str(index),
                values=[str(row.get(field, "")) for field in fields],
                tags=(self._row_tag(index),),
            )
        self._refresh_table_display()

    def _on_table_frame_resize(self, _: tk.Event[Any]) -> None:
        if self.result is None:
            return
        if self._use_tksheet and self.sheet is not None and not self._manual_column_resize:
            self._apply_sheet_column_widths(self.table_fields or self.result.fields)
            self.sheet.refresh()
        elif self.table is not None:
            self._apply_treeview_column_widths(self.table_fields or self.result.fields)

    def _on_table_mousewheel(self, event: tk.Event[Any]) -> str:
        if self.table is None:
            return "break"
        delta = -1 if event.delta > 0 else 1
        self.table.yview_scroll(delta, "units")
        return "break"

    def _on_table_shift_mousewheel(self, event: tk.Event[Any]) -> str:
        if self.table is None:
            return "break"
        delta = -1 if event.delta > 0 else 1
        self.table.xview_scroll(delta, "units")
        return "break"

    def _compute_default_column_widths(self, fields: list[str]) -> dict[str, int]:
        available = 900
        self.update_idletasks()
        if self.table_frame is not None:
            available = max(360, self.table_frame.winfo_width() - 20)
        if self._use_tksheet and self.sheet is not None:
            # Exclude the row-index strip (row numbers) so percentage sizing targets
            # only data columns.
            index_width = self._sheet_row_index_width()
            available = max(220, available - index_width)
        if not fields:
            return {}

        def _normalized(name: str) -> str:
            return "".join(ch for ch in name.lower() if ch.isalnum())

        mapunit_field = next(
            (field for field in fields if _normalized(field).startswith("mapunit")),
            None,
        )
        description_field = next(
            (field for field in fields if "description" in field.lower()),
            None,
        )

        percentages: dict[str, float] = {}
        reserved = 0.0
        if mapunit_field is not None:
            percentages[mapunit_field] = 0.05
            reserved += 0.05
        if description_field is not None and description_field != mapunit_field:
            percentages[description_field] = 0.50
            reserved += 0.50

        remaining_fields = [field for field in fields if field not in percentages]
        if remaining_fields:
            remainder = max(0.0, 1.0 - reserved)
            each = remainder / len(remaining_fields)
            for field in remaining_fields:
                percentages[field] = each
        elif reserved > 0:
            # Only special columns exist; normalize to 100%.
            for field in list(percentages):
                percentages[field] = percentages[field] / reserved
        else:
            equal = 1.0 / len(fields)
            for field in fields:
                percentages[field] = equal

        widths = {field: max(60, int(round(available * percentages[field]))) for field in fields}
        total = sum(widths.values())
        if total != available and fields:
            adjust_field = description_field or fields[-1]
            widths[adjust_field] = max(60, widths[adjust_field] + (available - total))
        return widths

    def _sheet_row_index_width(self) -> int:
        if self.sheet is None:
            return 0
        # Prefer live rendered width when available; fall back to configured default.
        current_width = getattr(getattr(self.sheet, "RI", None), "current_width", None)
        if isinstance(current_width, (int, float)):
            return max(0, int(round(current_width)))
        configured_width = getattr(getattr(self.sheet, "ops", None), "default_row_index_width", None)
        if isinstance(configured_width, (int, float)):
            return max(0, int(round(configured_width)))
        return 30

    def _apply_sheet_column_widths(self, fields: list[str]) -> None:
        if self.sheet is None:
            return
        default_widths = self._compute_default_column_widths(fields)
        widths = [self.column_widths_by_field.get(field, default_widths[field]) for field in fields]
        self.sheet.set_column_widths(widths, reset=False)

    def _apply_treeview_column_widths(self, fields: list[str]) -> None:
        if self.table is None:
            return
        default_widths = self._compute_default_column_widths(fields)
        for field in fields:
            width = self.column_widths_by_field.get(field, default_widths[field])
            self.table.column(field, width=width, stretch=False, minwidth=80)

    def _snapshot_sheet_column_widths(self) -> None:
        if self.sheet is None or not self.table_fields:
            return
        widths = self.sheet.get_column_widths()
        if len(widths) != len(self.table_fields):
            return
        for field, width in zip(self.table_fields, widths):
            self.column_widths_by_field[field] = int(width)

    def _fit_rows_for_all(self, *, redraw: bool) -> None:
        if self.sheet is None:
            return
        # Fit row heights to wrapped content under current column widths.
        self.sheet.row_height("all", height="text", only_set_if_too_small=False, redraw=redraw)

    def _auto_fit_rows(self) -> None:
        if self.sheet is None:
            return
        self._fit_rows_for_all(redraw=True)

    def _reset_column_widths(self) -> None:
        if self.result is None:
            return
        self.column_widths_by_field = {}
        self._manual_column_resize = False
        if self._use_tksheet and self.sheet is not None:
            self._apply_sheet_column_widths(self.table_fields or self.result.fields)
            self._fit_rows_for_all(redraw=False)
            self.sheet.refresh()
        elif self.table is not None:
            self._apply_treeview_column_widths(self.table_fields or self.result.fields)

    def _refresh_table_display(self, _: tk.Event[Any] | None = None) -> None:
        if self.result is None or self._use_tksheet:
            return
        if self.table is None:
            return
        fields = self.table_fields or self.result.fields
        for row_id in self.table.get_children():
            row_index = int(row_id)
            self.table.item(
                row_id,
                values=[str(self.rows[row_index].get(field, "")) for field in fields],
                tags=(self._row_tag(row_index),),
            )
        self.table.tag_configure("status_accepted", background="#E8F6E8")
        self.table.tag_configure("status_needs_review", background="#FFF7DA")
        self.table.tag_configure("status_bad_extraction", background="#FCE8E8")

    def _row_tag(self, row_index: int) -> str:
        if row_index < 0 or row_index >= len(self.row_statuses):
            return "status_needs_review"
        return f"status_{self.row_statuses[row_index]}"

    def _ensure_row_metadata_length(self) -> None:
        while len(self.row_statuses) < len(self.rows):
            self.row_statuses.append("needs_review")
        while len(self.row_comments) < len(self.rows):
            self.row_comments.append("")
        if len(self.row_statuses) > len(self.rows):
            self.row_statuses = self.row_statuses[: len(self.rows)]
        if len(self.row_comments) > len(self.rows):
            self.row_comments = self.row_comments[: len(self.rows)]

    def _status_code_from_label(self, label: str) -> str:
        normalized = label.strip().lower()
        for code, code_label in self.row_status_labels.items():
            if normalized == code_label:
                return code
        if normalized in self.row_status_options:
            return normalized
        return "needs_review"

    def _status_label_from_code(self, code: str) -> str:
        return self.row_status_labels.get(code, self.row_status_labels["needs_review"])

    def _selected_row_from_sheet(self) -> int | None:
        if self.sheet is None:
            return None
        try:
            selected = self.sheet.get_currently_selected()
        except Exception:
            selected = ()
        if selected:
            row_value = getattr(selected, "row", None)
            if row_value is None and isinstance(selected, tuple) and len(selected) > 0:
                row_value = selected[0]
            if isinstance(row_value, int) and 0 <= row_value < len(self.rows):
                return row_value
        try:
            row_indexes = self.sheet.get_selected_rows(return_tuple=True)
        except Exception:
            row_indexes = ()
        if row_indexes:
            row_value = row_indexes[0]
            if isinstance(row_value, int) and 0 <= row_value < len(self.rows):
                return row_value
        return None

    def _select_sheet_row_preserve_column(self, row_index: int) -> None:
        if self.sheet is None:
            return
        try:
            selected = self.sheet.get_currently_selected()
        except Exception:
            selected = ()
        column = 0
        selected_col = getattr(selected, "column", None)
        if isinstance(selected_col, int) and selected_col >= 0:
            column = selected_col
        elif isinstance(selected, tuple) and len(selected) > 1 and isinstance(selected[1], int):
            column = selected[1]
        self.sheet.set_currently_selected(row_index, column)

    def _set_selected_row(self, row_index: int | None, *, sync_widget: bool = True) -> None:
        self.selected_row_index = row_index
        if row_index is None:
            self._sync_row_metadata_controls()
            return
        if sync_widget and self._use_tksheet and self.sheet is not None:
            try:
                self._select_sheet_row_preserve_column(row_index)
            except Exception:
                pass
        elif sync_widget and self.table is not None:
            iid = str(row_index)
            if self.table.exists(iid):
                self.table.selection_set(iid)
                self.table.focus(iid)
        self._sync_row_metadata_controls()

    def _sync_row_metadata_controls(self) -> None:
        self._ensure_row_metadata_length()
        row_index = self.selected_row_index
        if row_index is None or row_index < 0 or row_index >= len(self.rows):
            self.row_status_var.set(self.row_status_labels["needs_review"])
            self.row_comment_var.set("")
            self._sync_status_dropdown_color()
            return
        self.row_status_var.set(self._status_label_from_code(self.row_statuses[row_index]))
        self.row_comment_var.set(self.row_comments[row_index])
        self._sync_status_dropdown_color()

    def _active_profile_id(self) -> str:
        if self.result is None:
            return ""
        profile_id = self.result.manifest.get("profile_id", "")
        return str(profile_id) if profile_id is not None else ""

    def _active_image_name(self) -> str:
        if not self.image_path.get():
            return ""
        return Path(self.image_path.get()).name

    def _record_feedback(
        self,
        *,
        row_index: int | None,
        field: str,
        model_value: Any,
        corrected_value: Any,
        status: str,
        comment: str,
        event_type: str,
    ) -> None:
        if self.result is None:
            return
        self.feedback_records.append(
            build_feedback_record(
                run_id=self.result.run_id,
                profile_id=self._active_profile_id(),
                image=self._active_image_name(),
                row_index=row_index,
                field_name=field,
                original_value=model_value,
                corrected_value=corrected_value,
                status=status,
                comment=comment,
                event_type=event_type,
            )
        )

    def _on_treeview_select(self, _: tk.Event[Any]) -> None:
        if self.table is None:
            return
        selection = self.table.selection()
        if not selection:
            self._set_selected_row(None)
            return
        self._set_selected_row(int(selection[0]))

    def _on_sheet_end_edit_cell(self, event: Any) -> None:
        if self.result is None or self.sheet is None:
            return
        row = event.get("row") if isinstance(event, dict) else None
        column = event.get("column") if isinstance(event, dict) else None
        if not isinstance(row, int) or not isinstance(column, int):
            return
        if row < 0 or row >= len(self.rows) or column < 0 or column >= len(self.table_fields):
            return
        field = self.table_fields[column]
        corrected = event.get("value", event.get("text", ""))
        corrected_text = "" if corrected is None else str(corrected)
        previous = str(self.rows[row].get(field, ""))
        if corrected_text == previous:
            return
        self.rows[row][field] = corrected_text
        try:
            self.sheet.row_height(row, height="text", only_set_if_too_small=False, redraw=False)
        except Exception:
            pass
        self.sheet.refresh()
        model_value = self.original_rows[row].get(field, "")
        status = "accepted_with_minor_edit" if corrected_text != str(model_value) else "accepted"
        self._record_feedback(
            row_index=row,
            field=field,
            model_value=model_value,
            corrected_value=corrected_text,
            status=status,
            comment=self.row_comments[row] if row < len(self.row_comments) else "",
            event_type="cell_edit",
        )
        self._set_selected_row(row, sync_widget=False)
        self._mark_unsaved()

    def _on_sheet_end_paste(self, _: Any) -> None:
        if self.sheet is None or self.result is None:
            return
        changed = False
        changed_rows: set[int] = set()
        data = self.sheet.get_sheet_data()
        for row_index, row_values in enumerate(data):
            if row_index >= len(self.rows):
                break
            for column_index, field in enumerate(self.table_fields):
                if column_index >= len(row_values):
                    continue
                new_value = "" if row_values[column_index] is None else str(row_values[column_index])
                if new_value != str(self.rows[row_index].get(field, "")):
                    self.rows[row_index][field] = new_value
                    changed = True
                    changed_rows.add(row_index)
        if changed:
            for row_index in changed_rows:
                try:
                    self.sheet.row_height(row_index, height="text", only_set_if_too_small=False, redraw=False)
                except Exception:
                    pass
            self.sheet.refresh()
            self._mark_unsaved()

    def _on_sheet_cell_select(self, event: Any) -> None:
        row = event.get("row") if isinstance(event, dict) else None
        if isinstance(row, int):
            self._set_selected_row(row, sync_widget=False)

    def _on_sheet_column_resize(self, event: Any) -> None:
        _ = event
        self._manual_column_resize = True
        self._snapshot_sheet_column_widths()
        self._fit_rows_for_all(redraw=False)
        if self.sheet is not None:
            self.sheet.refresh()

    def _on_sheet_select_event(self, _: tk.Event[Any]) -> None:
        row_index = self._selected_row_from_sheet()
        self._set_selected_row(row_index, sync_widget=False)

    def _begin_cell_edit(self, event: tk.Event[Any]) -> None:
        if self.result is None or self.table is None:
            return
        row_id = self.table.identify_row(event.y)
        column_id = self.table.identify_column(event.x)
        if not row_id or not column_id:
            return
        column_index = int(column_id.lstrip("#")) - 1
        fields = self.result.fields
        if column_index < 0 or column_index >= len(fields):
            return
        field = fields[column_index]
        bbox = self.table.bbox(row_id, column_id)
        if not bbox:
            return
        self._set_selected_row(int(row_id))
        original = self.rows[int(row_id)].get(field, "")
        dialog = tk.Toplevel(self)
        dialog.title(f"Edit row {int(row_id) + 1} | {field}")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("760x360")
        dialog.minsize(560, 260)
        dialog.rowconfigure(0, weight=1)
        dialog.columnconfigure(0, weight=1)

        editor = tk.Text(dialog, wrap=tk.WORD)
        editor.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        editor.insert("1.0", str(original))
        editor.focus_set()

        controls = ttk.Frame(dialog)
        controls.grid(row=1, column=0, sticky="e", padx=8, pady=(0, 8))

        def commit() -> None:
            corrected = editor.get("1.0", tk.END).rstrip("\n")
            row_index = int(row_id)
            if corrected != original:
                self.rows[row_index][field] = corrected
                self.table.item(
                    row_id,
                    values=[str(self.rows[row_index].get(name, "")) for name in fields],
                )
                model_value = self.original_rows[row_index].get(field, "")
                status = "accepted_with_minor_edit" if corrected != str(model_value) else "accepted"
                self._record_feedback(
                    row_index=row_index,
                    field=field,
                    model_value=model_value,
                    corrected_value=corrected,
                    status=status,
                    comment=self.row_comments[row_index] if row_index < len(self.row_comments) else "",
                    event_type="cell_edit",
                )
                self._set_status_message(f"Edited row {row_index + 1}, field {field}.")
                self._mark_unsaved()
            dialog.destroy()

        ttk.Button(controls, text="OK", command=commit).pack(side=tk.RIGHT)
        ttk.Button(controls, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=(4, 0))
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        dialog.bind("<Control-Return>", lambda _: commit())
        self._center_window(dialog)

    def _apply_sheet_status_highlights(self) -> None:
        if self.sheet is None:
            return
        for row_index, status in enumerate(self.row_statuses):
            if status == "accepted":
                fg = "#0E4D20"
            elif status == "bad_extraction":
                fg = "#6F0F0F"
            else:
                fg = "#6A4D00"
            bg = self._status_color(status)
            try:
                self.sheet.highlight_rows(rows=[row_index], bg=bg, fg=fg, redraw=False)
            except Exception:
                return
        try:
            self.sheet.refresh()
        except Exception:
            pass

    def _add_row(self) -> None:
        if self.result is None:
            messagebox.showinfo("No run", "Run extraction before editing rows.", parent=self)
            return
        insert_at = len(self.rows)
        if self.selected_row_index is not None:
            insert_at = self.selected_row_index + 1
        blank_row = {field: "" for field in self.result.fields}
        self.rows.insert(insert_at, blank_row)
        self.original_rows.insert(insert_at, dict(blank_row))
        self.row_statuses.insert(insert_at, "needs_review")
        self.row_comments.insert(insert_at, "")
        self._configure_table(self.result.fields, self.rows)
        self._set_selected_row(insert_at)
        self._record_feedback(
            row_index=insert_at,
            field="__row__",
            model_value="",
            corrected_value="added",
            status="needs_review",
            comment="Row added by reviewer",
            event_type="row_added",
        )
        self._mark_unsaved()

    def _delete_selected_row(self) -> None:
        if self.result is None:
            messagebox.showinfo("No run", "Run extraction before editing rows.", parent=self)
            return
        row_index = self.selected_row_index
        if row_index is None or row_index < 0 or row_index >= len(self.rows):
            messagebox.showinfo("No row selected", "Select a row to delete.", parent=self)
            return
        removed_row = self.rows.pop(row_index)
        self.original_rows.pop(row_index)
        removed_status = self.row_statuses.pop(row_index)
        removed_comment = self.row_comments.pop(row_index)
        self._configure_table(self.result.fields, self.rows)
        next_selection = row_index if row_index < len(self.rows) else len(self.rows) - 1
        self._set_selected_row(next_selection if next_selection >= 0 else None)
        self._record_feedback(
            row_index=row_index,
            field="__row__",
            model_value=removed_row,
            corrected_value="deleted",
            status=removed_status,
            comment=removed_comment,
            event_type="row_deleted",
        )
        self._mark_unsaved()

    def _move_selected_row(self, step: int) -> None:
        if self.result is None:
            messagebox.showinfo("No run", "Run extraction before editing rows.", parent=self)
            return
        row_index = self.selected_row_index
        if row_index is None or row_index < 0 or row_index >= len(self.rows):
            messagebox.showinfo("No row selected", "Select a row to reorder.", parent=self)
            return
        target = row_index + step
        if target < 0 or target >= len(self.rows):
            return
        self.rows[row_index], self.rows[target] = self.rows[target], self.rows[row_index]
        self.original_rows[row_index], self.original_rows[target] = (
            self.original_rows[target],
            self.original_rows[row_index],
        )
        self.row_statuses[row_index], self.row_statuses[target] = (
            self.row_statuses[target],
            self.row_statuses[row_index],
        )
        self.row_comments[row_index], self.row_comments[target] = (
            self.row_comments[target],
            self.row_comments[row_index],
        )
        self._configure_table(self.result.fields, self.rows)
        self._set_selected_row(target)
        self._record_feedback(
            row_index=target,
            field="__row__",
            model_value=row_index,
            corrected_value=target,
            status=self.row_statuses[target],
            comment="Row reordered by reviewer",
            event_type="row_reordered",
        )
        self._mark_unsaved()

    def _apply_selected_row_metadata(self, _: tk.Event[Any] | None = None) -> None:
        row_index = self.selected_row_index
        if row_index is None or row_index < 0 or row_index >= len(self.rows):
            return
        new_status = self._status_code_from_label(self.row_status_var.get())
        new_comment = self.row_comment_var.get().strip()
        previous_status = self.row_statuses[row_index]
        previous_comment = self.row_comments[row_index]
        self.row_statuses[row_index] = new_status
        self.row_comments[row_index] = new_comment
        if previous_status != new_status or previous_comment != new_comment:
            self._record_feedback(
                row_index=row_index,
                field="__row__",
                model_value=previous_status,
                corrected_value=new_status,
                status=new_status,
                comment=new_comment,
                event_type="row_metadata",
            )
            self._mark_unsaved()
        if self._use_tksheet:
            self._apply_sheet_status_highlights()
        else:
            self._refresh_table_display()
        self._sync_status_dropdown_color()

    def _show_help(self) -> None:
        help_path = self._repo_root() / "README.md"
        if not help_path.exists():
            messagebox.showerror("Help unavailable", f"Could not find {help_path}.", parent=self)
            return
        help_window = tk.Toplevel(self)
        help_window.title("Help and Workflow")
        help_window.geometry("900x700")
        help_window.transient(self)
        body = scrolledtext.ScrolledText(help_window, wrap=tk.WORD)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        body.insert("1.0", help_path.read_text(encoding="utf-8"))
        body.configure(state="disabled")
        self._center_window(help_window)

    def _load_project(self) -> None:
        initial_dir = self.result.run_dir if self.result is not None else Path(self.output_dir.get())
        selected = filedialog.askdirectory(initialdir=str(initial_dir))
        if not selected:
            return
        run_dir = Path(selected)
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.exists():
            messagebox.showerror(
                "Invalid project folder",
                "Selected folder does not contain manifest.json.",
                parent=self,
            )
            return

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_outputs = manifest.get("output_paths", {})
        output_paths = review_output_paths(run_dir)
        if isinstance(manifest_outputs, dict):
            for key, value in manifest_outputs.items():
                if isinstance(value, str):
                    output_paths[key] = Path(value)

        extracted_json = output_paths.get("extracted_json", run_dir / "extracted.json")
        corrected_json = output_paths.get("corrected_json", run_dir / "corrected.json")
        data_path = corrected_json if corrected_json.exists() else extracted_json
        if not data_path.exists():
            messagebox.showerror(
                "Invalid project folder",
                "Could not find extracted.json or corrected.json in the selected folder.",
                parent=self,
            )
            return

        loaded_data = json.loads(data_path.read_text(encoding="utf-8"))
        if not isinstance(loaded_data, dict):
            messagebox.showerror("Invalid project data", "Project JSON is malformed.", parent=self)
            return
        fields = loaded_data.get("fields", [])
        rows = loaded_data.get("rows", [])
        if not isinstance(fields, list) or not isinstance(rows, list):
            messagebox.showerror("Invalid project data", "Project fields/rows are malformed.", parent=self)
            return

        original_rows: list[dict[str, Any]] = []
        if extracted_json.exists():
            extracted_data = json.loads(extracted_json.read_text(encoding="utf-8"))
            if isinstance(extracted_data, dict) and isinstance(extracted_data.get("rows"), list):
                original_rows = [
                    {field: row.get(field, "") for field in fields}
                    for row in extracted_data.get("rows", [])
                    if isinstance(row, dict)
                ]
        if not original_rows:
            original_rows = [{field: row.get(field, "") for field in fields} for row in rows if isinstance(row, dict)]

        normalized_rows = [
            {field: row.get(field, "") for field in fields}
            for row in rows
            if isinstance(row, dict)
        ]
        if len(original_rows) < len(normalized_rows):
            original_rows.extend(
                [{field: "" for field in fields} for _ in range(len(normalized_rows) - len(original_rows))]
            )
        original_rows = original_rows[: len(normalized_rows)]

        feedback_path = output_paths.get("feedback", run_dir / "feedback.jsonl")
        loaded_feedback: list[dict[str, Any]] = []
        if feedback_path.exists():
            for line in feedback_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    loaded_feedback.append(item)

        statuses = ["needs_review"] * len(normalized_rows)
        comments = [""] * len(normalized_rows)
        for record in loaded_feedback:
            row_index = record.get("row_index")
            if not isinstance(row_index, int) or row_index < 0 or row_index >= len(statuses):
                continue
            status = str(record.get("status", "needs_review"))
            if status == "accepted_with_minor_edit":
                status = "accepted"
            if status in self.row_status_options:
                statuses[row_index] = status
            comment = str(record.get("comment", "")).strip()
            if comment:
                comments[row_index] = comment

        usage = {
            "input_tokens": manifest.get("input_tokens") if isinstance(manifest.get("input_tokens"), int) else None,
            "output_tokens": manifest.get("output_tokens") if isinstance(manifest.get("output_tokens"), int) else None,
            "total_tokens": manifest.get("total_tokens") if isinstance(manifest.get("total_tokens"), int) else None,
            "cached_tokens": manifest.get("cached_tokens") if isinstance(manifest.get("cached_tokens"), int) else None,
            "reasoning_tokens": manifest.get("reasoning_tokens")
            if isinstance(manifest.get("reasoning_tokens"), int)
            else None,
        }

        result = ExtractionJobResult(
            run_id=str(manifest.get("run_id", run_dir.name)),
            run_dir=run_dir,
            fields=[str(field) for field in fields],
            rows=original_rows,
            manifest=manifest,
            output_paths=output_paths,
            dry_run=manifest.get("api_call_mode") == "dry_run",
            cache_reused=manifest.get("api_call_mode") == "cache_reuse",
            request_fingerprint=str(manifest.get("request_fingerprint", "")),
            rough_image_tokens=int(manifest.get("rough_image_tokens_estimate", 0) or 0),
            usage=usage,
            elapsed_seconds=float(manifest.get("elapsed_seconds", 0.0) or 0.0),
            estimated_cost_usd=float(manifest.get("estimated_cost_usd"))
            if isinstance(manifest.get("estimated_cost_usd"), (int, float))
            else None,
            warnings=[str(item) for item in manifest.get("warnings", []) if isinstance(item, str)],
        )

        self.result = result
        self.rows = normalized_rows
        self.original_rows = original_rows
        self.feedback_records = loaded_feedback
        self.row_statuses = statuses
        self.row_comments = comments
        self.selected_row_index = 0 if self.rows else None
        self.column_widths_by_field = {}
        self._manual_column_resize = False
        self.has_unsaved_changes = False

        profile_id = str(manifest.get("profile_id", "")).strip()
        if profile_id:
            self.profile_path.set(
                self._display_path(self._profiles_dir() / f"{profile_id}.yml")
            )

        source_image_path = Path(str(manifest.get("source_image_path", "")))
        if source_image_path.exists():
            self.image_path.set(self._display_path(source_image_path))
            self._load_preview(source_image_path)
        elif output_paths.get("source_image") and output_paths["source_image"].exists():
            self.image_path.set(self._display_path(output_paths["source_image"]))
            self._load_preview(output_paths["source_image"])

        self._configure_table(result.fields, self.rows)
        self._sync_row_metadata_controls()
        notes_path = output_paths.get("notes", run_dir / "notes.md")
        self.notes.delete("1.0", tk.END)
        if notes_path.exists():
            self.notes.insert("1.0", notes_path.read_text(encoding="utf-8"))
        self.notes.edit_modified(False)
        self.has_unsaved_changes = False
        self._set_status_message(f"Project loaded: {result.run_id}")

    def _save_corrected(self) -> None:
        if self.result is None:
            messagebox.showinfo("No run", "Run extraction before saving corrections.", parent=self)
            return
        if self._use_tksheet and self.sheet is not None:
            try:
                self.sheet.close_text_editor()
            except Exception:
                pass
            sheet_data = self.sheet.get_sheet_data()
            for row_index, row_values in enumerate(sheet_data):
                if row_index >= len(self.rows):
                    break
                for column_index, field in enumerate(self.result.fields):
                    if column_index >= len(row_values):
                        continue
                    self.rows[row_index][field] = "" if row_values[column_index] is None else str(
                        row_values[column_index]
                    )
        notes_text = self.notes.get("1.0", tk.END).rstrip() + "\n"
        Path(self.result.output_paths["notes"]).write_text(notes_text, encoding="utf-8")
        note_body = notes_text.strip()
        records = list(self.feedback_records)
        self._ensure_row_metadata_length()
        for row_index, row in enumerate(self.rows):
            row_status = self.row_statuses[row_index]
            row_comment = self.row_comments[row_index]
            for field in self.result.fields:
                model_value = self.original_rows[row_index].get(field, "")
                corrected_value = row.get(field, "")
                derived_status = row_status
                if row_status == "accepted" and str(corrected_value) != str(model_value):
                    derived_status = "accepted_with_minor_edit"
                records.append(
                    build_feedback_record(
                        run_id=self.result.run_id,
                        profile_id=self._active_profile_id(),
                        image=self._active_image_name(),
                        row_index=row_index,
                        field_name=field,
                        original_value=model_value,
                        corrected_value=corrected_value,
                        status=derived_status,
                        comment=row_comment,
                        event_type="final_review",
                    )
                )
        if note_body:
            records.append(
                build_feedback_record(
                    run_id=self.result.run_id,
                    profile_id=self._active_profile_id(),
                    image=self._active_image_name(),
                    row_index=None,
                    field_name="notes",
                    original_value="",
                    corrected_value=note_body,
                    status="note",
                    comment="User review note",
                    event_type="note",
                )
            )
        write_corrected_outputs(
            rows=self.rows,
            fields=self.result.fields,
            csv_path=self.result.output_paths["corrected_csv"],
            json_path=self.result.output_paths["corrected_json"],
        )
        write_feedback_jsonl(records, self.result.output_paths["feedback"])
        self._mark_saved()
        self._set_status_message(f"Saved project in {self.result.run_dir}")

    def _promote_corrected(self) -> None:
        if self.result is None:
            messagebox.showinfo("No run", "Run extraction before promoting corrected outputs.", parent=self)
            return
        corrected_csv = self.result.output_paths["corrected_csv"]
        corrected_json = self.result.output_paths["corrected_json"]
        if not corrected_csv.exists() or not corrected_json.exists():
            messagebox.showinfo(
                "No corrected outputs",
                "Save corrected outputs first, then promote them.",
                parent=self,
            )
            return
        target = promote_corrected_to_gold(
            run_id=self.result.run_id,
            profile_id=self.result.manifest["profile_id"],
            corrected_json_path=corrected_json,
            corrected_csv_path=corrected_csv,
        )
        self._set_status_message(f"Promoted corrected outputs to {target}")

    def _open_output_folder(self) -> None:
        path = self.result.run_dir if self.result else Path(self.output_dir.get())
        try:
            if platform.system() == "Windows":
                os.startfile(path)  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
            self._set_status_message(f"Opened {path}")
        except Exception as exc:  # noqa: BLE001 - GUI should surface platform opener failures.
            self._set_status_message(f"Could not open folder: {exc}")
            messagebox.showerror("Open folder failed", str(exc), parent=self)


def main() -> None:
    """Launch the Tkinter review workbench."""

    app = ReviewWorkbench()
    app.mainloop()


if __name__ == "__main__":
    main()
