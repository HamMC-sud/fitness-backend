from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class MeasurementSaveIn(BaseModel):
    day: date
    weight_kg: float = Field(ge=20, le=400)


class MeasurementItemOut(BaseModel):
    day: date
    weight_kg: Optional[float] = None


class DayActivityOut(BaseModel):
    day: date
    minutes: int = 0
    kkal: float = 0.0
    steps: int = 0


class CompletedAchievementOut(BaseModel):
    name: str
    points: int
    date: Optional[datetime] = None


class DayExercisesOut(BaseModel):
    date: date
    workout_name: str
    workout_type: str
    points: int


class MeasurementSummaryOut(BaseModel):
    measurements: List[MeasurementItemOut] = Field(default_factory=list)
    totals: DayActivityOut
    by_days: List[DayActivityOut] = Field(default_factory=list)
    completed_achievements: List[CompletedAchievementOut] = Field(default_factory=list)
    exercises_by_day: List[DayExercisesOut] = Field(default_factory=list)
