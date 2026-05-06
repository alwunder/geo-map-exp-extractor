"""Image helpers for model input and manifest metadata."""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass(frozen=True)
class ImageMetadata:
    """Basic image metadata stored in the manifest."""

    path: Path
    width: int
    height: int
    mime_type: str


def get_image_metadata(path: str | Path) -> ImageMetadata:
    """Read image dimensions and MIME type."""

    image_path = Path(path)
    with Image.open(image_path) as image:
        width, height = image.size
    mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    return ImageMetadata(path=image_path, width=width, height=height, mime_type=mime_type)


def image_to_data_url(path: str | Path) -> str:
    """Encode an image file as a data URL suitable for Responses API image input."""

    metadata = get_image_metadata(path)
    encoded = base64.b64encode(metadata.path.read_bytes()).decode("ascii")
    return f"data:{metadata.mime_type};base64,{encoded}"
