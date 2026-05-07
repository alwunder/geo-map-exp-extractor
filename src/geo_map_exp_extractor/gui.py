"""Simple Tkinter review workbench for geo-map extraction results."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import textwrap
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from tkinter import font as tkfont
from typing import Any

from PIL import Image, ImageTk
from arcgis.apps.storymap import collection

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
    run_extraction_job,
    write_corrected_outputs,
    write_feedback_jsonl,
)
from geo_map_exp_extractor.openai_runner import DEFAULT_MODEL
from geo_map_exp_extractor.settings import DEFAULT_IMAGE_DETAIL


class ReviewWorkbench(tk.Tk):
    """Small, functional GUI for running extraction and correcting rows."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Geo Image Extract Review Workbench")
        self.geometry("1200x800")
        self._load_environment()

        self.image_path = tk.StringVar()
        self.profile_path = tk.StringVar(value=self._display_path(self._default_profile_path()))
        self.output_dir = tk.StringVar(value=self._display_path(self._repo_root() / "outputs"))
        self.api_key_override: str | None = None
        self.model = tk.StringVar(value=DEFAULT_MODEL)
        self.image_detail = tk.StringVar(value=DEFAULT_IMAGE_DETAIL)
        self.include_profile_notes = tk.BooleanVar(value=False)
        self.dry_run = tk.BooleanVar(value=False)
        self.force_rerun = tk.BooleanVar(value=False)
        self.segmented_mode = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value=self._api_key_status_message())

        self.result: ExtractionJobResult | None = None
        self.rows: list[dict[str, Any]] = []
        self.original_rows: list[dict[str, Any]] = []
        self.feedback_records: list[dict[str, Any]] = []
        self.zoom = 1.0
        self.source_image: Image.Image | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.preview_source_path: Path | None = None
        self.row_height = tk.IntVar(value=72)
        self.table_style_name = "Results.Treeview"
        self._is_panning = False

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

    def _prompt_api_key_override(self) -> None:
        """Prompt for an optional session-only API key."""

        value = simpledialog.askstring(
            "Set API Key",
            "Enter an OpenAI API key for this GUI session.",
            show="*",
            parent=self,
        )
        if value is None:
            return
        self.api_key_override = value.strip() or None
        self.status.set(self._api_key_status_message())

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

        ttk.Label(top, text="Model:").grid(row=3, column=0, sticky="w", padx=(0, 4), pady=2)
        ttk.Entry(top, textvariable=self.model).grid(row=3, column=1, columnspan=2, sticky="we", pady=2)
        ttk.Label(top, text="Detail:").grid(row=4, column=1, sticky="e", padx=(0, 2), pady=2)
        ttk.Combobox(
            top,
            textvariable=self.image_detail,
            values=("high", "auto", "low"),
            width=8,
            state="readonly",
        ).grid(row=4, column=2, sticky="w", padx=2, pady=2)
        ttk.Button(top, text="Set API key...", command=self._prompt_api_key_override).grid(
            row=3, column=9, padx=0, ipadx=4
        )
        """ttk.Button(top, text="Use .env key", command=self._clear_api_key_override).grid(
            row=3, column=5, padx=4, ipadx=4
        )"""
        ttk.Checkbutton(top, text="Include profile notes", variable=self.include_profile_notes).grid(
            row=5, column=1, columnspan=2, sticky="w", pady=2
        )
        ttk.Label(top, text="Model options:").grid(row=3, column=3, sticky="e", padx=(20, 0), pady=2)
        ttk.Checkbutton(top, text="Dry run (no API call)", variable=self.dry_run).grid(
            row=3, column=4, sticky="w", pady=2, padx=4
        )
        ttk.Checkbutton(top, text="Force rerun", variable=self.force_rerun).grid(
            row=3, column=5, sticky="w", pady=2, padx=4
        )
        ttk.Checkbutton(top, text="Segmented mode (higher cost)", variable=self.segmented_mode).grid(
            row=3, column=6, sticky="w", pady=2, padx=(4,50)
        )
        ttk.Button(top, text="Run extraction", command=self._run_extraction).grid(
            row=5, column=4, sticky="we", padx=4, ipadx=4
        )
        ttk.Button(top, text="Open output folder", command=self._open_output_folder).grid(
            row=5, column=6, columnspan=2, sticky="e", padx=4
        )
        ttk.Button(top, text="Save corrected", command=self._save_corrected).grid(
            row=5, column=8, padx=4
        )
        ttk.Button(top, text="Promote corrected", command=self._promote_corrected).grid(
            row=5, column=9, padx=4
        )
        ttk.Button(top, text="Help", command=self._show_help).grid(row=0, column=9, padx=4)
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
        ttk.Label(table_controls, text="Row height").pack(side=tk.LEFT)
        row_height_spinbox = ttk.Spinbox(
            table_controls,
            from_=24,
            to=220,
            increment=4,
            textvariable=self.row_height,
            width=5,
            command=self._on_row_height_change,
        )
        row_height_spinbox.pack(side=tk.LEFT, padx=(4, 0))
        row_height_spinbox.bind("<Return>", self._on_row_height_change)
        row_height_spinbox.bind("<FocusOut>", self._on_row_height_change)

        style = ttk.Style(self)
        style.configure(self.table_style_name, rowheight=self.row_height.get())
        table_frame = ttk.Frame(right)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.grid_propagate(False)
        self.table = ttk.Treeview(table_frame, show="headings", style=self.table_style_name)
        self.table.bind("<Double-1>", self._begin_cell_edit)
        self.table.bind("<Configure>", self._refresh_table_display)
        self.table.bind("<ButtonRelease-1>", self._refresh_table_display)
        self.table.bind("<MouseWheel>", self._on_table_mousewheel)
        self.table.bind("<Shift-MouseWheel>", self._on_table_shift_mousewheel)
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
        self.profile_combo = ttk.Combobox(parent, textvariable=self.profile_path)
        self.profile_combo.grid(row=row, column=1, columnspan=7, sticky="we", pady=2)
        ttk.Button(parent, text="Browse...", command=self._browse_profile).grid(
            row=row, column=8, padx=4
        )

    def _refresh_profiles(self) -> None:
        profiles = sorted(self._profiles_dir().glob("*.yml")) + sorted(
            self._profiles_dir().glob("*.yaml")
        )
        self.profile_combo["values"] = [self._display_path(path) for path in profiles]

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
            self._configure_table(load_profile(path).fields, [])

    def _browse_output(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.output_dir.set(self._display_path(path))

    def _run_extraction(self) -> None:
        active_key, _ = self._resolve_api_key_source()
        if not self.dry_run.get() and active_key is None:
            self.status.set(self._api_key_status_message())
            messagebox.showerror(
                "Missing API key",
                "No API key loaded. Add OPENAI_API_KEY to .env or use 'Set API key...'.",
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
            )
            if not proceed:
                self.status.set("Folder run cancelled. Choose a single image file.")
                return
        if not self.dry_run.get():
            proceed = messagebox.askyesno(
                "Confirm API call",
                "This operation will send 1 image and one prompt to the OpenAI API. "
                "This may incur API charges.\n\nContinue?",
            )
            if not proceed:
                self.status.set("Extraction cancelled before API call.")
                return
            if self.segmented_mode.get():
                seg_proceed = messagebox.askyesno(
                    "Segmented mode enabled",
                    "Segmented mode can issue multiple API calls and increase cost.\n\nContinue?",
                )
                if not seg_proceed:
                    self.status.set("Segmented extraction cancelled.")
                    return
        try:
            self.status.set(
                "Running dry run..." if self.dry_run.get() else "Running extraction via OpenAI API..."
            )
            self.update_idletasks()
            self.result = run_extraction_job(
                image_path=self.image_path.get(),
                profile_path=self.profile_path.get(),
                output_dir=self.output_dir.get(),
                api_key=self.api_key_override,
                model=self.model.get(),
                image_detail=self.image_detail.get(),
                include_profile_notes=self.include_profile_notes.get(),
                dry_run=self.dry_run.get(),
                force_rerun=self.force_rerun.get(),
                segmented_mode=self.segmented_mode.get(),
            )
            self.rows = [dict(row) for row in self.result.rows]
            self.original_rows = [dict(row) for row in self.result.rows]
            self.feedback_records = []
            image_path = Path(self.image_path.get())
            if self.preview_source_path is None or image_path.resolve() != self.preview_source_path:
                self._load_preview(image_path)
            self._configure_table(self.result.fields, self.rows)
            self.notes.delete("1.0", tk.END)
            self.notes.insert(
                "1.0", Path(self.result.output_paths["notes"]).read_text(encoding="utf-8")
            )
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
                summary_bits.append(
                    "est cost USD: "
                    f"{self.result.estimated_cost_usd if self.result.estimated_cost_usd is not None else 'n/a'}"
                )
            self.status.set(" | ".join(summary_bits))
        except Exception as exc:  # noqa: BLE001 - GUI should report unexpected failures to the user.
            self.status.set(f"Extraction failed: {exc}")
            messagebox.showerror("Extraction failed", str(exc))

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
        self.table.delete(*self.table.get_children())
        self.table["columns"] = fields
        for field in fields:
            self.table.heading(field, text=field)
            self.table.column(field, width=max(120, min(300, len(field) * 12)), stretch=True)
        for index, row in enumerate(rows):
            self.table.insert(
                "", tk.END, iid=str(index), values=[self._wrapped_cell_value(index, field) for field in fields]
            )
        self._refresh_table_display()

    def _on_row_height_change(self, _: tk.Event[Any] | None = None) -> None:
        try:
            value = int(self.row_height.get())
        except (tk.TclError, ValueError):
            return
        value = max(24, min(value, 220))
        self.row_height.set(value)
        ttk.Style(self).configure(self.table_style_name, rowheight=value)
        self._refresh_table_display()

    def _on_table_mousewheel(self, event: tk.Event[Any]) -> str:
        delta = -1 if event.delta > 0 else 1
        self.table.yview_scroll(delta, "units")
        return "break"

    def _on_table_shift_mousewheel(self, event: tk.Event[Any]) -> str:
        delta = -1 if event.delta > 0 else 1
        self.table.xview_scroll(delta, "units")
        return "break"

    def _refresh_table_display(self, _: tk.Event[Any] | None = None) -> None:
        if self.result is None:
            return
        fields = self.result.fields
        for row_id in self.table.get_children():
            row_index = int(row_id)
            self.table.item(
                row_id,
                values=[self._wrapped_cell_value(row_index, field) for field in fields],
            )

    def _wrapped_cell_value(self, row_index: int, field: str) -> str:
        raw_value = str(self.rows[row_index].get(field, ""))
        if not raw_value:
            return ""
        try:
            column_width = int(self.table.column(field, option="width"))
        except tk.TclError:
            return raw_value
        if column_width <= 20:
            return raw_value
        available_px = max(40, column_width - 12)
        font = tkfont.nametofont("TkDefaultFont")
        wrapped_sections: list[str] = []
        for section in raw_value.splitlines() or [raw_value]:
            words = section.split()
            if not words:
                wrapped_sections.append("")
                continue
            lines: list[str] = []
            current: list[str] = []
            for word in words:
                trial = " ".join(current + [word])
                if font.measure(trial) <= available_px:
                    current.append(word)
                    continue
                if current:
                    lines.append(" ".join(current))
                    current = [word]
                    continue
                # Single long token: hard-wrap by character count fallback.
                avg_char_px = max(1, font.measure("n"))
                width_chars = max(4, available_px // avg_char_px)
                lines.extend(textwrap.wrap(word, width=width_chars))
                current = []
            if current:
                lines.append(" ".join(current))
            wrapped_sections.append("\n".join(lines))
        return "\n".join(wrapped_sections)

    def _begin_cell_edit(self, event: tk.Event[Any]) -> None:
        if self.result is None:
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
        original = self.rows[int(row_id)].get(field, "")
        dialog = tk.Toplevel(self)
        dialog.title(f"Edit row {int(row_id) + 1} | {field}")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("700x260")

        editor = tk.Text(dialog, wrap=tk.WORD)
        editor.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))
        editor.insert("1.0", str(original))
        editor.focus_set()

        controls = ttk.Frame(dialog)
        controls.pack(fill=tk.X, padx=8, pady=(0, 8))

        def commit() -> None:
            corrected = editor.get("1.0", tk.END).rstrip("\n")
            row_index = int(row_id)
            if corrected != original:
                self.rows[row_index][field] = corrected
                self.table.item(
                    row_id,
                    values=[self._wrapped_cell_value(row_index, name) for name in fields],
                )
                self.feedback_records.append(
                    build_feedback_record(
                        run_id=self.result.run_id,
                        row_index=row_index,
                        field_name=field,
                        original_value=original,
                        corrected_value=corrected,
                    )
                )
                self.status.set(f"Edited row {row_index + 1}, field {field}.")
            dialog.destroy()

        ttk.Button(controls, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(controls, text="OK", command=commit).pack(side=tk.RIGHT)
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        dialog.bind("<Control-Return>", lambda _: commit())

    def _show_help(self) -> None:
        help_path = self._repo_root() / "README.md"
        if not help_path.exists():
            messagebox.showerror("Help unavailable", f"Could not find {help_path}.")
            return
        help_window = tk.Toplevel(self)
        help_window.title("Help and Workflow")
        help_window.geometry("900x700")
        help_window.transient(self)
        body = scrolledtext.ScrolledText(help_window, wrap=tk.WORD)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        body.insert("1.0", help_path.read_text(encoding="utf-8"))
        body.configure(state="disabled")

    def _save_corrected(self) -> None:
        if self.result is None:
            messagebox.showinfo("No run", "Run extraction before saving corrections.")
            return
        notes_text = self.notes.get("1.0", tk.END).rstrip() + "\n"
        Path(self.result.output_paths["notes"]).write_text(notes_text, encoding="utf-8")
        note_body = notes_text.strip()
        records = list(self.feedback_records)
        if note_body:
            records.append(
                build_feedback_record(
                    run_id=self.result.run_id,
                    row_index=None,
                    field_name="notes",
                    original_value="",
                    corrected_value=note_body,
                    status="note",
                    comment="User review note",
                )
            )
        write_corrected_outputs(
            rows=self.rows,
            fields=self.result.fields,
            csv_path=self.result.output_paths["corrected_csv"],
            json_path=self.result.output_paths["corrected_json"],
        )
        write_feedback_jsonl(records, self.result.output_paths["feedback"])
        self.status.set(f"Saved corrected outputs in {self.result.run_dir}")

    def _promote_corrected(self) -> None:
        if self.result is None:
            messagebox.showinfo("No run", "Run extraction before promoting corrected outputs.")
            return
        corrected_csv = self.result.output_paths["corrected_csv"]
        corrected_json = self.result.output_paths["corrected_json"]
        if not corrected_csv.exists() or not corrected_json.exists():
            messagebox.showinfo(
                "No corrected outputs",
                "Save corrected outputs first, then promote them.",
            )
            return
        target = promote_corrected_to_gold(
            run_id=self.result.run_id,
            profile_id=self.result.manifest["profile_id"],
            corrected_json_path=corrected_json,
            corrected_csv_path=corrected_csv,
        )
        self.status.set(f"Promoted corrected outputs to {target}")

    def _open_output_folder(self) -> None:
        path = self.result.run_dir if self.result else Path(self.output_dir.get())
        try:
            if platform.system() == "Windows":
                os.startfile(path)  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
            self.status.set(f"Opened {path}")
        except Exception as exc:  # noqa: BLE001 - GUI should surface platform opener failures.
            self.status.set(f"Could not open folder: {exc}")
            messagebox.showerror("Open folder failed", str(exc))


def main() -> None:
    """Launch the Tkinter review workbench."""

    app = ReviewWorkbench()
    app.mainloop()


if __name__ == "__main__":
    main()
