from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional, TypedDict
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_VIDEO_NAME_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>[rs])$", re.IGNORECASE)


class ExerciseVideoMeta(TypedDict):
    video_mode: Optional[str]
    repetitions: Optional[int]
    duration_seconds: Optional[float]


def parse_exercise_video_filename(file_name: str) -> ExerciseVideoMeta:
    stem = Path(str(file_name or "").strip()).stem
    match = _VIDEO_NAME_RE.match(stem)
    if not match:
        logger.warning("Unrecognized exercise video filename format: %s", file_name)
        return {"video_mode": None, "repetitions": None, "duration_seconds": None}

    value = match.group("value")
    unit = match.group("unit").lower()

    if unit == "r":
        if "." in value:
            logger.warning("Invalid reps value in exercise video filename: %s", file_name)
            return {"video_mode": None, "repetitions": None, "duration_seconds": None}
        return {"video_mode": "reps", "repetitions": int(value), "duration_seconds": None}

    return {"video_mode": "seconds", "repetitions": None, "duration_seconds": float(value)}


def parse_exercise_video_from_url(video_url: Optional[str]) -> ExerciseVideoMeta:
    if not video_url:
        return {"video_mode": None, "repetitions": None, "duration_seconds": None}
    try:
        file_name = Path(urlparse(video_url).path).name
    except Exception:
        logger.warning("Failed to parse video URL: %s", video_url)
        return {"video_mode": None, "repetitions": None, "duration_seconds": None}
    return parse_exercise_video_filename(file_name)
