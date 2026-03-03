from __future__ import annotations

from typing import Dict, Optional

from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from .base import BaseDoc


class ContentAsset(BaseDoc):
    title: str
    author: Optional[str] = None
    asset_type: str = Field(default="video")
    status: str = Field(default="draft")

    duration_seconds: Optional[int] = Field(default=None, ge=0, le=24 * 3600)
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    video_url: Optional[str] = None
    audio_url: Optional[str] = None
    image_url: Optional[str] = None

    meta: Dict[str, object] = Field(default_factory=dict)

    class Settings:
        name = "content_assets"
        indexes = [
            IndexModel([("asset_type", ASCENDING), ("status", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
        ]
