from __future__ import annotations

from datetime import datetime , timezone
from typing import List, Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import BaseModel, Field, model_validator

from models.enums import ExerciseMode, Feedback


# ---------- Workouts (custom templates) ----------

class WorkoutStepIn(BaseModel):
    order: int = Field(ge=1)
    exercise_id: PydanticObjectId

    mode: ExerciseMode
    reps: Optional[int] = Field(default=None, ge=1, le=500)
    duration_seconds: Optional[int] = Field(default=None, ge=5, le=3600)
    rest_seconds_after: int = Field(default=45, ge=0, le=600)

    @model_validator(mode="after")
    def validate_mode(self):
        if self.mode == ExerciseMode.reps:
            if self.reps is None:
                raise ValueError("reps is required when mode=reps")
            if self.duration_seconds is not None:
                raise ValueError("duration_seconds must be null when mode=reps")
        if self.mode == ExerciseMode.time:
            if self.duration_seconds is None:
                raise ValueError("duration_seconds is required when mode=time")
            if self.reps is not None:
                raise ValueError("reps must be null when mode=time")
        return self


class WorkoutCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    steps: List[WorkoutStepIn] = Field(default_factory=list)


class WorkoutUpdateIn(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=80)
    steps: Optional[List[WorkoutStepIn]] = None


# ---------- Runs (history) ----------

class WorkoutRunExerciseResultIn(BaseModel):
    exercise_id: PydanticObjectId
    mode: ExerciseMode
    reps_done: Optional[int] = None
    seconds_done: Optional[int] = None
    feedback: Optional[Feedback] = None


class WorkoutStartOut(BaseModel):
    run_id: PydanticObjectId
    started_at: datetime


class WorkoutCompleteIn(BaseModel):
    total_seconds: Optional[int] = Field(default=None, ge=0)
    calories_estimated: Optional[float] = Field(default=None, ge=0)

    rating_stars: Optional[int] = Field(default=None, ge=1, le=5)
    difficulty_feedback: Optional[Feedback] = None

    exercise_results: List[WorkoutRunExerciseResultIn] = Field(default_factory=list)


class HistoryStatsOut(BaseModel):
    total_completed: int
    total_seconds: int
    total_calories_estimated: float
    streak_days: int
    last_activity_at: Optional[datetime] = None
