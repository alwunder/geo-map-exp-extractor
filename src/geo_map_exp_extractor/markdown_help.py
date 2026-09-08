"""Small Markdown renderer used by the in-application help window.

The application deliberately renders a useful subset of Markdown instead of
embedding a web browser.  That keeps Help available in the Windows release
without a network connection while preserving the README's headings, links,
lists, code samples, tables, and local images.
"""

from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from typing import Any, Callable

from PIL import Image, ImageTk


_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((?:<([^>]+)>|([^)]+))\)")
_MARKDOWN_INLINE_RE = re.compile(
    r"(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|(?<!\*)\*[^*]+\*(?!\*))"
)
_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_MARKDOWN_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
_MARKDOWN_NUMBER_RE = re.compile(r"^\s*(\d+)\.\s+(.+)$")


def parse_markdown_image(line: str) -> tuple[str, str] | None:
    """Return alt text and target for one standalone Markdown image line."""

    match = _MARKDOWN_IMAGE_RE.fullmatch(line.strip())
    if match is None:
        return None
    return match.group(1).strip(), (match.group(2) or match.group(3)).strip()


def parse_markdown_link(token: str) -> tuple[str, str] | None:
    """Return the label and target from one inline Markdown link token."""

    match = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", token)
    if match is None:
        return None
    target = match.group(2).strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    return match.group(1).strip(), target


def resolve_markdown_image_path(target: str, asset_directory: Path | None) -> Path | None:
    """Resolve a local Markdown image target relative to its document."""

    if asset_directory is None or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):
        return None
    path = Path(target)
    if not path.is_absolute():
        path = asset_directory / path
    return path.resolve()


def configure_markdown_tags(widget: tk.Text) -> None:
    """Configure readable, native-looking styles for rendered Markdown."""

    default_font = tkfont.nametofont("TkDefaultFont").copy()
    default_font.configure(size=10)
    fixed_font = tkfont.nametofont("TkFixedFont").copy()
    fixed_font.configure(size=9)
    heading_fonts: dict[int, tkfont.Font] = {}
    for level, size in ((1, 18), (2, 15), (3, 13)):
        heading_font = default_font.copy()
        heading_font.configure(size=size, weight="bold")
        heading_fonts[level] = heading_font
        widget.tag_configure(
            f"heading{level}",
            font=heading_font,
            foreground="#17365D",
            spacing1=14 if level == 1 else 10,
            spacing3=5,
        )

    # Keep font objects referenced for the life of the widget.
    widget._markdown_fonts = (default_font, fixed_font, *heading_fonts.values())  # type: ignore[attr-defined]
    widget.tag_configure("strong", font=(default_font.actual("family"), 10, "bold"))
    widget.tag_configure("emphasis", font=(default_font.actual("family"), 10, "italic"))
    widget.tag_configure("inline_code", font=fixed_font, background="#F1F3F5")
    widget.tag_configure("code_block", font=fixed_font, background="#F1F3F5", lmargin1=16, lmargin2=16)
    widget.tag_configure("link", foreground="#0563C1", underline=True)
    widget.tag_configure("list_marker", foreground="#1F4E79", lmargin1=12, lmargin2=30)
    widget.tag_configure("table_title", font=(default_font.actual("family"), 10, "bold"), foreground="#17365D")
    widget.tag_configure("table_label", font=(default_font.actual("family"), 10, "bold"))
    widget.tag_configure("markdown_image", justify="center", spacing1=6, spacing3=6)
    widget.tag_configure("image_error", foreground="#7F6000", lmargin1=18, lmargin2=18)


def _tags(base_tag: str | None, inline_tag: str | None = None) -> tuple[str, ...]:
    return tuple(tag for tag in (base_tag, inline_tag) if tag)


def _insert_markdown_link(widget: tk.Text, label: str, target: str, base_tag: str | None) -> None:
    serial = getattr(widget, "_markdown_link_serial", 0) + 1
    widget._markdown_link_serial = serial  # type: ignore[attr-defined]
    tag_name = f"markdown_link_{serial}"
    widget.insert("end", label, (*_tags(base_tag, "link"), tag_name))
    handler = getattr(widget, "_markdown_link_handler", None)
    if handler is not None:
        def activate_link(_event: Any, link_target: str = target) -> str:
            handler(link_target)
            return "break"

        widget.tag_bind(tag_name, "<Button-1>", activate_link)


def _insert_markdown_inline(widget: tk.Text, text: str, base_tag: str | None = None) -> None:
    position = 0
    for match in _MARKDOWN_INLINE_RE.finditer(text):
        widget.insert("end", text[position:match.start()], _tags(base_tag))
        token = match.group(0)
        if token.startswith("**"):
            widget.insert("end", token[2:-2], _tags(base_tag, "strong"))
        elif token.startswith("`"):
            widget.insert("end", token[1:-1], _tags(base_tag, "inline_code"))
        elif token.startswith("*"):
            widget.insert("end", token[1:-1], _tags(base_tag, "emphasis"))
        else:
            link = parse_markdown_link(token)
            if link is None:
                widget.insert("end", token, _tags(base_tag))
            else:
                _insert_markdown_link(widget, *link, base_tag)
        position = match.end()
    widget.insert("end", text[position:], _tags(base_tag))


def _split_markdown_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_markdown_table_separator(line: str) -> bool:
    cells = _split_markdown_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _insert_markdown_table(widget: tk.Text, table_lines: list[str]) -> None:
    headers = _split_markdown_table_row(table_lines[0])
    for row_line in table_lines[2:]:
        if not row_line.strip():
            continue
        row = _split_markdown_table_row(row_line)
        if not row:
            continue
        _insert_markdown_inline(widget, row[0], "table_title")
        widget.insert("end", "\n")
        for index, value in enumerate(row[1:], start=1):
            header = headers[index] if index < len(headers) else f"Column {index + 1}"
            widget.insert("end", f"    {header}: ", "table_label")
            _insert_markdown_inline(widget, value)
            widget.insert("end", "\n")
        widget.insert("end", "\n")


def _insert_markdown_image(
    widget: tk.Text,
    alt_text: str,
    target: str,
    asset_directory: Path | None,
) -> None:
    image_path = resolve_markdown_image_path(target, asset_directory)
    try:
        if image_path is None or not image_path.is_file():
            raise FileNotFoundError(target)
        with Image.open(image_path) as source_image:
            image = source_image.copy()
        image.thumbnail((780, 1000))
        photo = ImageTk.PhotoImage(image, master=widget)
    except (FileNotFoundError, OSError, tk.TclError):
        description = alt_text or Path(target).name or "unnamed image"
        widget.insert("end", f"[Image unavailable: {description} ({target})]\n\n", "image_error")
        return

    references = getattr(widget, "_markdown_image_references", None)
    if references is None:
        references = []
        widget._markdown_image_references = references  # type: ignore[attr-defined]
    references.append(photo)
    start = widget.index("end-1c")
    widget.image_create("end", image=photo, padx=8, pady=6)
    widget.insert("end", "\n\n")
    widget.tag_add("markdown_image", start, "end-1c")


def render_markdown(
    widget: tk.Text,
    markdown_text: str,
    asset_directory: Path | None = None,
    link_handler: Callable[[str], None] | None = None,
) -> None:
    """Render a useful subset of Markdown into a configured Tk Text widget."""

    widget._markdown_link_handler = link_handler  # type: ignore[attr-defined]
    lines = markdown_text.expandtabs(4).splitlines()
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            _insert_markdown_inline(widget, " ".join(part.strip() for part in paragraph))
            widget.insert("end", "\n\n")
            paragraph.clear()

    index = 0
    in_code_block = False
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            in_code_block = not in_code_block
            if not in_code_block:
                widget.insert("end", "\n")
            index += 1
            continue
        if in_code_block:
            widget.insert("end", line + "\n", "code_block")
            index += 1
            continue

        markdown_image = parse_markdown_image(stripped)
        if markdown_image is not None:
            flush_paragraph()
            _insert_markdown_image(widget, *markdown_image, asset_directory)
            index += 1
            continue

        heading = _MARKDOWN_HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            _insert_markdown_inline(widget, heading.group(2), f"heading{min(len(heading.group(1)), 3)}")
            widget.insert("end", "\n")
            index += 1
            continue

        if "|" in line and index + 1 < len(lines) and _is_markdown_table_separator(lines[index + 1]):
            flush_paragraph()
            end = index + 2
            while end < len(lines) and "|" in lines[end] and lines[end].strip():
                end += 1
            _insert_markdown_table(widget, lines[index:end])
            index = end
            continue

        bullet = _MARKDOWN_BULLET_RE.match(line)
        numbered = _MARKDOWN_NUMBER_RE.match(line)
        if bullet or numbered:
            flush_paragraph()
            marker = "\u2022" if bullet else f"{numbered.group(1)}."
            content = bullet.group(1) if bullet else numbered.group(2)
            widget.insert("end", f"  {marker} ", "list_marker")
            _insert_markdown_inline(widget, content)
            widget.insert("end", "\n")
            index += 1
            continue

        if not stripped:
            flush_paragraph()
            index += 1
            continue
        paragraph.append(stripped)
        index += 1

    flush_paragraph()
