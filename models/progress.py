from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import Field
from pymongo import IndexModel, ASCENDING, DESCENDING

from .base import BaseDoc
class UserAchievement(BaseDoc):
    user_id: PydanticObjectId
    achievement_code: str
    category: str = Field(default="general", min_length=1, max_length=32)
    name: str = Field(default="achievement", min_length=1, max_length=120)
    logic: Optional[str] = Field(default=None, max_length=1000)
    progress: float = Field(default=0, ge=0)
    max_progress: float = Field(default=100, ge=1)
    points: int = Field(default=0, ge=0)
    unlocked_at: Optional[datetime] = None

    class Settings:
        name = "user_achievements"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("achievement_code", ASCENDING)], unique=True),
        ]


class BodyMeasurement(BaseDoc):
    user_id: PydanticObjectId
    date: date

    weight_kg: Optional[float] = None
    chest_cm: Optional[float] = None
    waist_cm: Optional[float] = None
    hips_cm: Optional[float] = None
    arms_cm: Optional[float] = None

    class Settings:
        name = "body_measurements"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("date", DESCENDING)]),
        ]


