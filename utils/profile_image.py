from __future__ import annotations

import base64
import binascii
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024

STATICS_DIR = Path("statics")
STATICS_DIR.mkdir(parents=True, exist_ok=True)


def _detect_image_ext(image_bytes: bytes) -> Optional[str]:
    if image_bytes.startswith(b"\xFF\xD8\xFF"):
        return ".jpg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return ".webp"
    return None


def _parse_data_uri_base64(value: str) -> tuple[Optional[str], str]:
    value = (value or "").strip()
    if value.startswith("data:"):
        try:
            header, b64_data = value.split(",", 1)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid image data URI format")
        if ";base64" not in header:
            raise HTTPException(status_code=400, detail="Image must be base64 encoded")
        mime_part = header.split(";")[0]
        mime_type = mime_part.replace("data:", "").lower().strip()
        return mime_type, b64_data.strip()
    return None, value


def _public_base_url() -> str:
    base = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base:
        return base
    # fallback to current deployment host used in project
    return "http://26.214.57.127:8000"


def save_base64_profile_image(base64_value: str) -> str:
    mime_type, b64_data = _parse_data_uri_base64(base64_value)

    try:
        image_bytes = base64.b64decode(b64_data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid base64 image")

    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image data")

    if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Image too large. Max size is {MAX_IMAGE_SIZE_BYTES // (1024 * 1024)} MB",
        )

    ext_from_bytes = _detect_image_ext(image_bytes)

    ext_from_mime = None
    if mime_type:
        mime_to_ext = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        ext_from_mime = mime_to_ext.get(mime_type)

    ext = ext_from_bytes or ext_from_mime
    if not ext or ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported image format. Use jpg, png, or webp")

    if ext_from_mime and ext_from_bytes and ext_from_mime != ext_from_bytes:
        raise HTTPException(status_code=400, detail="Image MIME type does not match file content")

    folder_name = uuid.uuid4().hex
    folder_path = STATICS_DIR / folder_name
    folder_path.mkdir(parents=True, exist_ok=True)

    filename = f"image{ext}"
    file_path = folder_path / filename
    with open(file_path, "wb") as f:
        f.write(image_bytes)

    return f"{_public_base_url()}/statics/{folder_name}/{filename}"


def normalize_profile_photo_value(photo_value: Optional[str]) -> Optional[str]:
    if not photo_value:
        return photo_value
    raw = photo_value.strip()
    # Already URL/path: keep as is.
    if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("/statics/"):
        return raw
    # Otherwise treat as base64/image data and store into statics.
    return save_base64_profile_image(raw)

