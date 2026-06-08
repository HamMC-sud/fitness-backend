from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional, TypedDict
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_MEDIA_PREFIXES = {
    "/upload_exercises/": WORKSPACE_ROOT / "upload_exercises",
    "/statics/": WORKSPACE_ROOT / "statics",
}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}

_VIDEO_NAME_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>[rs])$", re.IGNORECASE)


class ExerciseVideoMeta(TypedDict):
    video_mode: Optional[str]
    repetitions: Optional[int]
    duration_seconds: Optional[float]


def parse_exercise_video_filename(file_name: str) -> ExerciseVideoMeta:
    stem = Path(str(file_name or "").strip()).stem
    match = _VIDEO_NAME_RE.match(stem)
    if not match:
        return {"video_mode": None, "repetitions": None, "duration_seconds": None}

    value = match.group("value")
    unit = match.group("unit").lower()

    if unit == "r":
        if "." in value:
            logger.warning("Invalid reps value in exercise video filename: %s", file_name)
            return {"video_mode": None, "repetitions": None, "duration_seconds": None}
        return {"video_mode": "reps", "repetitions": int(value), "duration_seconds": None}

    return {"video_mode": "time", "repetitions": None, "duration_seconds": float(value)}


def parse_exercise_video_from_url(video_url: Optional[str]) -> ExerciseVideoMeta:
    if not video_url:
        logger.info(
            "Exercise video metadata: video_url=%s local_path=%s file_exists=%s video_mode=%s repetitions=%s duration_seconds=%s reason=%s",
            None,
            None,
            False,
            None,
            None,
            None,
            "empty_url",
        )
        return {"video_mode": None, "repetitions": None, "duration_seconds": None}

    local_path = resolve_local_media_path(video_url)
    file_exists = bool(local_path and local_path.exists() and local_path.is_file())
    try:
        file_name = Path(urlparse(video_url).path).name
    except Exception:
        logger.warning("Failed to parse video URL: %s", video_url)
        logger.info(
            "Exercise video metadata: video_url=%s local_path=%s file_exists=%s video_mode=%s repetitions=%s duration_seconds=%s reason=%s",
            video_url,
            str(local_path) if local_path else None,
            file_exists,
            None,
            None,
            None,
            "url_parse_failed",
        )
        return {"video_mode": None, "repetitions": None, "duration_seconds": None}
    meta = parse_exercise_video_filename(file_name)
    logger.info(
        "Exercise video metadata: video_url=%s local_path=%s file_exists=%s video_mode=%s repetitions=%s duration_seconds=%s reason=%s",
        video_url,
        str(local_path) if local_path else None,
        file_exists,
        meta.get("video_mode"),
        meta.get("repetitions"),
        meta.get("duration_seconds"),
        "parsed" if meta.get("video_mode") else "filename_unrecognized",
    )
    return meta


def resolve_local_media_path(media_url: Optional[str]) -> Optional[Path]:
    if not media_url:
        return None

    try:
        parsed = urlparse(media_url)
        path_value = parsed.path or ""
    except Exception:
        logger.warning("Failed to resolve media URL path: %s", media_url)
        return None

    for prefix, base_dir in _LOCAL_MEDIA_PREFIXES.items():
        if not path_value.startswith(prefix):
            continue
        relative = path_value[len(prefix) :].strip("/")
        candidate = (base_dir / Path(relative)).resolve()
        try:
            candidate.relative_to(base_dir.resolve())
        except Exception:
            logger.warning("Rejected media URL outside static roots: %s", media_url)
            return None
        return candidate
    return None


def ensure_existing_media_url(media_url: Optional[str], *, kind: str = "file") -> Optional[str]:
    if not media_url:
        return None

    local_path = resolve_local_media_path(media_url)
    if local_path is None:
        return media_url
    if not local_path.exists() or not local_path.is_file():
        logger.warning("Skipping missing %s URL: %s -> %s", kind, media_url, local_path)
        return None
    if kind == "video" and local_path.suffix.lower() not in _VIDEO_EXTENSIONS:
        logger.warning("Skipping non-video URL for video field: %s", media_url)
        return None
    return media_url
