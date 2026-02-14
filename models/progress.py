from __future__ import annotations

from datetime import date, datetime
from typing import Dict, Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import Field
from pymongo import IndexModel, ASCENDING, DESCENDING

from .base import BaseDoc
from .enums import MediaType, Feedback, PhotoSlot


class ActivityEvent(BaseDoc):
    user_id: PydanticObjectId
    type: MediaType
    ref_id: Optional[PydanticObjectId] = None
    points: int = Field(ge=0, le=100)

    occurred_at: datetime
    meta: Dict[str, object] = Field(default_factory=dict)

    class Settings:
        name = "activity_events"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("occurred_at", DESCENDING)]),
            IndexModel([("type", ASCENDING), ("occurred_at", DESCENDING)]),
        ]


class WeeklyFocusWeek(BaseDoc):
    user_id: PydanticObjectId
    week_start: str  # YYYY-MM-DD
    points_total: int = 0
    goal_points: int = 50
    is_completed: bool = False

    class Settings:
        name = "weekly_focus_weeks"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("week_start", ASCENDING)], unique=True),
        ]


class AchievementDef(BaseDoc):
    code: str
    title: Dict[str, str]
    type: str
    target: int = Field(ge=1)
    meta: Dict[str, object] = Field(default_factory=dict)

    class Settings:
        name = "achievement_defs"
        indexes = [IndexModel([("code", ASCENDING)], unique=True)]


class UserAchievement(BaseDoc):
    user_id: PydanticObjectId
    achievement_code: str
    progress: int = 0
    unlocked_at: Optional[datetime] = None

    class Settings:
        name = "user_achievements"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("achievement_code", ASCENDING)], unique=True),
        ]


class UserExerciseStats(BaseDoc):
    user_id: PydanticObjectId
    exercise_id: PydanticObjectId

    difficulty_multiplier: float = 1.0
    suggested_reps: Optional[int] = None
    suggested_rest_seconds: Optional[int] = None

    easy_streak: int = 0
    last_feedback: Optional[Feedback] = None
    last_done_at: Optional[datetime] = None

    class Settings:
        name = "user_exercise_stats"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("exercise_id", ASCENDING)], unique=True),
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


class BeforeAfterPhoto(BaseDoc):
    user_id: PydanticObjectId
    date: date
    slot: PhotoSlot = PhotoSlot.other

    photo_url: str
    thumb_url: Optional[str] = None
    visibility: str = "private"

    class Settings:
        name = "before_after_photos"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("date", DESCENDING)]),
        ]
