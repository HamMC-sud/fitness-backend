from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import BaseModel, EmailStr, Field, field_validator
from pymongo import IndexModel, ASCENDING

from .base import BaseDoc
from .enums import (
    Region, Language, Gender, ActivityLevel, Goal, Preference, Equipment, Injury
)


class UserSchedule(BaseModel):
    days_per_week: int = Field(ge=1, le=7)
    session_minutes: int = Field(ge=15, le=60)  # enforce 15/30/45/60 in API if needed


class UserProfile(BaseModel):
    name: str
    photo_url: Optional[str] = None

    gender: Gender
    birth_date: date

    height_cm: int = Field(ge=50, le=250)
    weight_kg: float = Field(ge=20, le=300)

    activity_level: ActivityLevel

    goals: List[Goal] = Field(default_factory=list, max_length=3)
    preferences: List[Preference] = Field(default_factory=list)
    equipment: List[Equipment] = Field(default_factory=list)
    injuries: List[Injury] = Field(default_factory=list)

    schedule: UserSchedule

    @field_validator("goals")
    @classmethod
    def validate_goals(cls, v: List[Goal]):
        if Goal.lose_weight in v and Goal.build_muscle in v:
            raise ValueError("goals conflict: lose_weight cannot be combined with build_muscle")
        if len(v) > 3:
            raise ValueError("max 3 goals allowed")
        return v


class UserFlags(BaseModel):
    onboarding_completed: bool = False
    is_premium: bool = False


class UserStats(BaseModel):
    streak_days: int = 0
    last_activity_at: Optional[datetime] = None


class User(BaseDoc):
    email: Optional[EmailStr] = None
    email_verified: bool = False
    password_hash: Optional[str] = None  # bcrypt hash or None for oauth-only
    region: Region = Region.INTL
    country: str = "US"
    language: Language = Language.en
    timezone: str = "UTC"
    profile: Optional[UserProfile] = None
    flags: UserFlags = Field(default_factory=UserFlags)
    stats: UserStats = Field(default_factory=UserStats)

    class Settings:
        name = "users"
        indexes = [
            IndexModel([("email", ASCENDING)], unique=True, sparse=True),
        ]
