"""Image helpers for model input, preprocessing, and manifest metadata."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
from math import ceil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from geo_map_exp_extractor.settings import DEFAULT_MAX_IMAGE_SIDE_PX

SUPPORTED_API_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


@dataclass(frozen=True)
class ImageMetadata:
    """Basic image metadata stored in the manifest."""

    path: Path
    width: int
    height: int
    mime_type: str


@dataclass(frozen=True)
class PreparedImage:
    """Prepared image info used for API requests and audit logging."""

    source_path: Path
    source_hash: str
    source_width: int
    source_height: int
    source_mime_type: str
    processed_path: Path
    processed_hash: str
    processed_width: int
    processed_height: int
    processed_mime_type: str
    was_converted: bool
    was_resized: bool
    rough_image_tokens: int


@dataclass(frozen=True)
class ImageSegment:
    """One saved image segment for explicit segmented extraction mode."""

    index: int
    path: Path
    sha256: str
    width: int
    height: int


def file_sha256(path: str | Path) -> str:
    """Return SHA-256 hex digest for a file path."""

    payload = Path(path).read_bytes()
    return hashlib.sha256(payload).hexdigest()


def get_image_metadata(path: str | Path) -> ImageMetadata:
    """Read image dimensions and MIME type."""

    image_path = Path(path)
    with Image.open(image_path) as image:
        width, height = image.size
    mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    return ImageMetadata(path=image_path, width=width, height=height, mime_type=mime_type)


def estimate_rough_image_tokens(width: int, height: int, detail: str) -> int:
    """Estimate vision-image token footprint for previews (rough only)."""

    # Rough tile-based heuristic, not a billing-accurate count.
    tiles = ceil(max(1, width) / 512) * ceil(max(1, height) / 512)
    per_tile = {"low": 85, "auto": 140, "high": 255}.get(detail, 140)
    return max(1, tiles * per_tile)


def _flatten_for_image_save(image: Image.Image) -> Image.Image:
    """Convert palette/alpha images into RGB before saving."""

    if image.mode in {"RGB", "L"}:
        return image.convert("RGB")
    if image.mode in {"RGBA", "LA"}:
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.split()[-1])
        return background
    return image.convert("RGB")


def prepare_image_for_api(
    *,
    image_path: str | Path,
    output_dir: str | Path,
    detail: str,
    max_side_px: int = DEFAULT_MAX_IMAGE_SIDE_PX,
) -> PreparedImage:
    """Prepare one source image for API submission and return audit metadata."""

    source_path = Path(image_path)
    source_meta = get_image_metadata(source_path)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    source_hash = file_sha256(source_path)

    suffix = source_path.suffix.lower()
    should_convert = suffix not in SUPPORTED_API_IMAGE_SUFFIXES
    processed_suffix = suffix if suffix in SUPPORTED_API_IMAGE_SUFFIXES else ".png"
    processed_path = output_root / f"processed_api_image{processed_suffix}"
    was_resized = False

    with Image.open(source_path) as source_image:
        if getattr(source_image, "n_frames", 1) > 1:
            source_image.seek(0)
        image = source_image.copy()

    width, height = image.size
    if max(width, height) > max_side_px:
        scale = max_side_px / max(width, height)
        resized_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        image = image.resize(resized_size, Image.Resampling.LANCZOS)
        was_resized = True

    if should_convert or was_resized:
        processed_image = _flatten_for_image_save(image)
        format_name = "JPEG" if processed_suffix in {".jpg", ".jpeg"} else "PNG"
        processed_image.save(processed_path, format=format_name)
        was_converted = True
    else:
        processed_path.write_bytes(source_path.read_bytes())
        was_converted = False

    processed_meta = get_image_metadata(processed_path)
    processed_hash = file_sha256(processed_path)
    rough_tokens = estimate_rough_image_tokens(processed_meta.width, processed_meta.height, detail)

    return PreparedImage(
        source_path=source_path,
        source_hash=source_hash,
        source_width=source_meta.width,
        source_height=source_meta.height,
        source_mime_type=source_meta.mime_type,
        processed_path=processed_path,
        processed_hash=processed_hash,
        processed_width=processed_meta.width,
        processed_height=processed_meta.height,
        processed_mime_type=processed_meta.mime_type,
        was_converted=was_converted,
        was_resized=was_resized,
        rough_image_tokens=rough_tokens,
    )


def create_image_segments(
    *,
    image_path: str | Path,
    output_dir: str | Path,
    segment_height_px: int,
    overlap_px: int,
) -> list[ImageSegment]:
    """Split an image into vertical overlapping segments and save them."""

    if segment_height_px <= 0:
        msg = "segment_height_px must be greater than zero"
        raise ValueError(msg)
    if overlap_px < 0:
        msg = "overlap_px must be zero or greater"
        raise ValueError(msg)

    source = Path(image_path)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)

    segments: list[ImageSegment] = []
    with Image.open(source) as image:
        width, height = image.size
        if height <= segment_height_px:
            saved = root / "segment_001.png"
            _flatten_for_image_save(image).save(saved, format="PNG")
            segments.append(
                ImageSegment(
                    index=1,
                    path=saved,
                    sha256=file_sha256(saved),
                    width=width,
                    height=height,
                )
            )
            return segments

        stride = max(1, segment_height_px - overlap_px)
        start = 0
        index = 1
        while start < height:
            end = min(height, start + segment_height_px)
            crop = image.crop((0, start, width, end))
            saved = root / f"segment_{index:03d}.png"
            _flatten_for_image_save(crop).save(saved, format="PNG")
            segments.append(
                ImageSegment(
                    index=index,
                    path=saved,
                    sha256=file_sha256(saved),
                    width=crop.width,
                    height=crop.height,
                )
            )
            if end >= height:
                break
            start += stride
            index += 1
    return segments


def image_to_data_url(path: str | Path) -> str:
    """Encode an image file as a data URL suitable for Responses API image input."""

    metadata = get_image_metadata(path)
    encoded = base64.b64encode(metadata.path.read_bytes()).decode("ascii")
    return f"data:{metadata.mime_type};base64,{encoded}"
