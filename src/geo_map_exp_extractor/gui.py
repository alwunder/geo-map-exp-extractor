"""Simple Tkinter review workbench for geo-map extraction results."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from PIL import Image, ImageTk

# Support running this file directly (e.g., IDE "Run file") in a src-layout project.
if __package__ is None or __package__ == "":
    src_root = Path(__file__).resolve().parents[1]
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))

from geo_map_exp_extractor.config import load_profile
from geo_map_exp_extractor.jobs import (
    ExtractionJobResult,
    build_feedback_record,
    run_extraction_job,
    write_corrected_outputs,
    write_feedback_jsonl,
)
from geo_map_exp_extractor.openai_runner import DEFAULT_MODEL


class ReviewWorkbench(tk.Tk):
    """Small, functional GUI for running extraction and correcting rows."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Geo Image Extract Review Workbench")
        self.geometry("1200x800")

        self.image_path = tk.StringVar()
        self.profile_path = tk.StringVar(value=str(self._default_profile_path()))
        self.output_dir = tk.StringVar(value=str(Path.cwd() / "review_runs"))
        self.model = tk.StringVar(value=DEFAULT_MODEL)
        self.status = tk.StringVar(value="Choose an image, profile, and output folder.")

        self.result: ExtractionJobResult | None = None
        self.rows: list[dict[str, Any]] = []
        self.original_rows: list[dict[str, Any]] = []
        self.feedback_records: list[dict[str, Any]] = []
        self.zoom = 1.0
        self.source_image: Image.Image | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None

        self._build_widgets()
        self._refresh_profiles()

    def _default_profile_path(self) -> Path:
        profiles = self._profiles_dir()
        choices = sorted(profiles.glob("*.yml")) + sorted(profiles.glob("*.yaml"))
        return choices[0] if choices else profiles

    def _profiles_dir(self) -> Path:
        repo_profiles = Path(__file__).resolve().parents[2] / "profiles"
        return repo_profiles if repo_profiles.exists() else Path.cwd() / "profiles"

    def _build_widgets(self) -> None:
        top = ttk.Frame(self, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        self._path_row(top, "Image", self.image_path, self._browse_image, row=0)
        self._profile_row(top, row=1)
        self._path_row(top, "Output", self.output_dir, self._browse_output, row=2)

        ttk.Label(top, text="Model").grid(row=3, column=0, sticky="w", padx=(0, 4), pady=2)
        ttk.Entry(top, textvariable=self.model, width=44).grid(row=3, column=1, sticky="we", pady=2)
        ttk.Button(top, text="Run extraction", command=self._run_extraction).grid(
            row=3, column=2, padx=4
        )
        ttk.Button(top, text="Save corrected", command=self._save_corrected).grid(
            row=3, column=3, padx=4
        )
        ttk.Button(top, text="Open output folder", command=self._open_output_folder).grid(
            row=3, column=4, padx=4
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
        ttk.Button(controls, text="Fit 100%", command=lambda: self._set_zoom(1.0)).pack(
            side=tk.LEFT
        )
        self.canvas = tk.Canvas(preview_frame, background="#222222")
        x_scroll = ttk.Scrollbar(preview_frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        y_scroll = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        x_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        paned.add(preview_frame, weight=1)

        right = ttk.Frame(paned)
        self.table = ttk.Treeview(right, show="headings")
        self.table.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.table.bind("<Double-1>", self._begin_cell_edit)
        table_y = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.table.yview)
        table_x = ttk.Scrollbar(right, orient=tk.HORIZONTAL, command=self.table.xview)
        self.table.configure(yscrollcommand=table_y.set, xscrollcommand=table_x.set)
        table_y.pack(side=tk.RIGHT, fill=tk.Y)
        table_x.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(right, text="Notes").pack(anchor="w", pady=(8, 0))
        self.notes = tk.Text(right, height=6, wrap=tk.WORD)
        self.notes.pack(fill=tk.X)
        paned.add(right, weight=1)

        status_frame = ttk.Frame(self, padding=(8, 4))
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(status_frame, textvariable=self.status).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _path_row(
        self, parent: ttk.Frame, label: str, variable: tk.StringVar, command: Any, row: int
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 4), pady=2)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="we", pady=2)
        ttk.Button(parent, text="Browse", command=command).grid(row=row, column=2, padx=4)

    def _profile_row(self, parent: ttk.Frame, row: int) -> None:
        ttk.Label(parent, text="Profile").grid(row=row, column=0, sticky="w", padx=(0, 4), pady=2)
        self.profile_combo = ttk.Combobox(parent, textvariable=self.profile_path)
        self.profile_combo.grid(row=row, column=1, sticky="we", pady=2)
        ttk.Button(parent, text="Browse", command=self._browse_profile).grid(
            row=row, column=2, padx=4
        )

    def _refresh_profiles(self) -> None:
        profiles = sorted(self._profiles_dir().glob("*.yml")) + sorted(
            self._profiles_dir().glob("*.yaml")
        )
        self.profile_combo["values"] = [str(path) for path in profiles]

    def _browse_image(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("Images", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp"), ("All files", "*.*")]
        )
        if path:
            self.image_path.set(path)
            self._load_preview(Path(path))

    def _browse_profile(self) -> None:
        path = filedialog.askopenfilename(
            initialdir=self._profiles_dir(),
            filetypes=[("YAML", "*.yml *.yaml"), ("All files", "*.*")],
        )
        if path:
            self.profile_path.set(path)
            self._configure_table(load_profile(path).fields, [])

    def _browse_output(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.output_dir.set(path)

    def _run_extraction(self) -> None:
        try:
            self.status.set("Running extraction...")
            self.update_idletasks()
            self.result = run_extraction_job(
                image_path=self.image_path.get(),
                profile_path=self.profile_path.get(),
                output_dir=self.output_dir.get(),
                model=self.model.get(),
            )
            self.rows = [dict(row) for row in self.result.rows]
            self.original_rows = [dict(row) for row in self.result.rows]
            self.feedback_records = []
            self._load_preview(Path(self.image_path.get()))
            self._configure_table(self.result.fields, self.rows)
            self.notes.delete("1.0", tk.END)
            self.notes.insert(
                "1.0", Path(self.result.output_paths["notes"]).read_text(encoding="utf-8")
            )
            self.status.set(f"Run complete: {self.result.run_dir}")
        except Exception as exc:  # noqa: BLE001 - GUI should report unexpected failures to the user.
            self.status.set(f"Extraction failed: {exc}")
            messagebox.showerror("Extraction failed", str(exc))

    def _load_preview(self, path: Path) -> None:
        self.source_image = Image.open(path)
        self._set_zoom(self.zoom)

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

    def _configure_table(self, fields: list[str], rows: list[dict[str, Any]]) -> None:
        self.table.delete(*self.table.get_children())
        self.table["columns"] = fields
        for field in fields:
            self.table.heading(field, text=field)
            self.table.column(field, width=max(120, min(300, len(field) * 12)), stretch=True)
        for index, row in enumerate(rows):
            self.table.insert(
                "", tk.END, iid=str(index), values=[row.get(field, "") for field in fields]
            )

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
        x, y, width, height = bbox
        original = self.rows[int(row_id)].get(field, "")
        editor = ttk.Entry(self.table)
        editor.insert(0, str(original))
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        committed = False

        def commit(_: tk.Event[Any] | None = None) -> None:
            nonlocal committed
            if committed:
                return
            committed = True
            corrected = editor.get()
            editor.destroy()
            row_index = int(row_id)
            if corrected == original:
                return
            self.rows[row_index][field] = corrected
            self.table.item(row_id, values=[self.rows[row_index].get(name, "") for name in fields])
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

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)

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
