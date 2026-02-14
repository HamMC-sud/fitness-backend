from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field

from models.enums import Gender, ActivityLevel, Goal, Preference, Equipment, Injury


class UserScheduleIn(BaseModel):
    days_per_week: int = Field(ge=1, le=7)
    session_minutes: int = Field(ge=15, le=60)


class ProfileUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    gender: Gender
    birth_date: date
    height_cm: int = Field(ge=50, le=250)
    weight_kg: float = Field(ge=20, le=300)
    activity_level: ActivityLevel

    goals: List[Goal] = Field(default_factory=list, max_length=3)
    preferences: List[Preference] = Field(default_factory=list)
    equipment: List[Equipment] = Field(default_factory=list)
    injuries: List[Injury] = Field(default_factory=list)

    schedule: UserScheduleIn
    photo_url: Optional[str] = None
