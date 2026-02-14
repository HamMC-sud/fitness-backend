from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import Field
from pymongo import IndexModel, ASCENDING, DESCENDING

from .base import BaseDoc


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MeditationRun(BaseDoc):
    user_id: PydanticObjectId
    meditation_id: PydanticObjectId
    type: str
    completed_at: datetime = Field(default_factory=utcnow)
    seconds_done: int = Field(default=0, ge=0)
    points: int = Field(default=0, ge=0)

    class Settings:
        name = "meditation_runs"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("completed_at", DESCENDING)]),
            IndexModel([("completed_at", ASCENDING)]),
        ]
