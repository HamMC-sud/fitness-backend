from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import BaseModel, Field, model_validator
from pymongo import IndexModel, ASCENDING, DESCENDING

from .base import BaseDoc
from .enums import ExerciseMode, Feedback

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class UserWorkoutStep(BaseModel):
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
        else:
            if self.duration_seconds is None:
                raise ValueError("duration_seconds is required when mode=time")
            if self.reps is not None:
                raise ValueError("reps must be null when mode=time")
        return self


class UserWorkout(BaseDoc):
    user_id: PydanticObjectId
    title: str = Field(min_length=1, max_length=80)
    steps: List[UserWorkoutStep] = Field(default_factory=list)

    class Settings:
        name = "user_workouts"
        indexes = [
            IndexModel([("user_id", ASCENDING)]),
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
        ]


class WorkoutRunExerciseResult(BaseModel):
    exercise_id: PydanticObjectId
    mode: ExerciseMode
    reps_done: Optional[int] = None
    seconds_done: Optional[int] = None
    feedback: Optional[Feedback] = None


class WorkoutRun(BaseDoc):
    user_id: PydanticObjectId

    source: str  # later make Enum: template|program|custom|ai
    workout_ref_id: Optional[PydanticObjectId] = None
    program_id: Optional[PydanticObjectId] = None
    ai_plan_id: Optional[PydanticObjectId] = None

    started_at: datetime
    completed_at: Optional[datetime] = None

    total_seconds: Optional[int] = Field(default=None, ge=0)
    calories_estimated: Optional[float] = Field(default=None, ge=0)

    rating_stars: Optional[int] = Field(default=None, ge=1, le=5)
    difficulty_feedback: Optional[Feedback] = None

    exercise_results: List[WorkoutRunExerciseResult] = Field(default_factory=list)

    class Settings:
        name = "workout_runs"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("completed_at", DESCENDING)]),
            IndexModel([("user_id", ASCENDING), ("started_at", DESCENDING)]),
        ]


class ExerciseFeedbackEvent(BaseDoc):
    user_id: PydanticObjectId
    exercise_id: PydanticObjectId
    workout_run_id: PydanticObjectId
    feedback: Feedback

    class Settings:
        name = "exercise_feedback_events"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("exercise_id", ASCENDING), ("created_at", DESCENDING)]),
        ]
