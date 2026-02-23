from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field

from models.enums import (
    Gender,
    ActivityLevel,
    Goal,
    Preference,
    Equipment,
    Injury,
    Language,
    UnitSystem,
)


class UserScheduleIn(BaseModel):
    days_per_week: Optional[int] = Field(default=None, ge=1, le=7)
    session_minutes: Optional[int] = Field(default=None, ge=15, le=60)


class ProfileUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    gender: Optional[Gender] = None
    birth_date: Optional[date] = None
    height_cm: Optional[int] = Field(default=None, ge=50, le=250)
    weight_kg: Optional[float] = Field(default=None, ge=20, le=300)
    target_weight_kg: Optional[float] = Field(default=None, ge=20, le=300)
    activity_level: Optional[ActivityLevel] = None

    goals: Optional[List[Goal]] = Field(default=None, max_length=3)
    preferences: Optional[List[Preference]] = None
    equipment: Optional[List[Equipment]] = None
    injuries: Optional[List[Injury]] = None

    schedule: Optional[UserScheduleIn] = None
    photo_url: Optional[str] = None


class ProfileSettingsUpdateIn(BaseModel):
    unit_system: Optional[UnitSystem] = None
    training_rest_seconds: Optional[int] = Field(default=None, ge=10, le=600)
    language: Optional[Language] = None


class ProfileSettingsOut(BaseModel):
    unit_system: UnitSystem
    training_rest_seconds: int
    language: Language
