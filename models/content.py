from __future__ import annotations

from typing import Dict, List, Optional, Any

from beanie.odm.fields import PydanticObjectId
from bson import ObjectId
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import IndexModel, ASCENDING, TEXT

from .base import BaseDoc
from .enums import ExerciseMode, Difficulty, WorkoutType, Equipment, Injury


class I18nList(BaseModel):
    ru: List[str] = Field(default_factory=list)
    en: List[str] = Field(default_factory=list)

    @field_validator("ru", "en", mode="before")
    @classmethod
    def coerce_str_to_list(cls, v: Any) -> List[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            return [str(x) for x in v if x is not None]
        return [str(v)]


class ExerciseMedia(BaseModel):
    video_url: str
    thumbnail_url: str
    duration_seconds: int = Field(ge=1, le=3600)
    mode: ExerciseMode = ExerciseMode.reps


class ExerciseDefaults(BaseModel):
    reps: Optional[int] = Field(default=None, ge=1, le=500)
    duration_seconds: Optional[int] = Field(default=None, ge=5, le=3600)


class Exercise(BaseDoc):
    code: str
    name: I18nList
    description: I18nList
    media: ExerciseMedia

    mode: ExerciseMode
    defaults: ExerciseDefaults

    beginner_tip: Optional[I18nList] = None

    muscle_groups: List[str] = Field(default_factory=list)
    movement_type: Optional[str] = None
    workout_type: List[WorkoutType] = Field(default_factory=list)

    equipment: List[Equipment] = Field(default_factory=list)
    contraindications: List[Injury] = Field(default_factory=list)

    difficulty: Difficulty
    calories_per_minute: Optional[float] = Field(default=None, ge=0)

    instructions: Dict[str, List[str]] = Field(default_factory=dict)
    status: str = "active"

    class Settings:
        name = "exercises"
        indexes = [
            IndexModel([("code", ASCENDING)], unique=True),
            IndexModel([("difficulty", ASCENDING)]),
            IndexModel([("movement_type", ASCENDING)]),
            IndexModel([("muscle_groups", ASCENDING)]),
            IndexModel([("equipment", ASCENDING)]),
            IndexModel([("contraindications", ASCENDING)]),
            IndexModel([("name.ru", TEXT), ("name.en", TEXT)]),
        ]


class WorkoutStep(BaseModel):
    order: int = Field(ge=1)
    exercise_id: PydanticObjectId

    mode: ExerciseMode
    reps: Optional[int] = Field(default=None, ge=1, le=500)
    duration_seconds: Optional[int] = Field(default=None, ge=5, le=3600)
    rest_seconds_after: int = Field(default=45, ge=0, le=600)

    @field_validator("exercise_id", mode="before")
    @classmethod
    def parse_exercise_id(cls, v: Any):
        if v is None:
            return v
        if isinstance(v, (PydanticObjectId, ObjectId)):
            return v
        if isinstance(v, str):
            return ObjectId(v)
        return ObjectId(str(v))

    @model_validator(mode="after")
    def validate_step(self):
        if self.mode == ExerciseMode.reps:
            if self.reps is None:
                raise ValueError("reps is required when mode='reps'")
            if self.duration_seconds is not None:
                raise ValueError("duration_seconds must be null when mode='reps'")
        elif self.mode == ExerciseMode.time:
            if self.duration_seconds is None:
                raise ValueError("duration_seconds is required when mode='time'")
            if self.reps is not None:
                raise ValueError("reps must be null when mode='time'")
        return self


class WorkoutTemplate(BaseDoc):
    title: I18nList
    description: Optional[I18nList] = None

    type: WorkoutType
    level: Difficulty
    estimated_minutes: int = Field(ge=5, le=180)

    steps: List[WorkoutStep] = Field(default_factory=list)
    equipment_required: List[Equipment] = Field(default_factory=list)

    status: str = "active"

    class Settings:
        name = "workout_templates"
        indexes = [
            IndexModel([("type", ASCENDING)]),
            IndexModel([("level", ASCENDING)]),
        ]


class ProgramScheduleItem(BaseModel):
    day_index: int = Field(ge=1, le=7)
    workout_template_id: PydanticObjectId

    @field_validator("workout_template_id", mode="before")
    @classmethod
    def parse_template_id(cls, v: Any):
        if v is None:
            return v
        if isinstance(v, (PydanticObjectId, ObjectId)):
            return v
        if isinstance(v, str):
            return ObjectId(v)
        return ObjectId(str(v))


class WorkoutProgram(BaseDoc):
    slug: str
    title: I18nList
    description: Optional[I18nList] = None

    weeks: int = Field(ge=1, le=52)
    workouts_per_week: int = Field(ge=1, le=7)
    session_minutes: int = Field(ge=15, le=120)

    level: Difficulty
    goals: List[str] = Field(default_factory=list)
    location: str = "home"
    equipment_required: List[Equipment] = Field(default_factory=list)

    preview: Dict[str, Optional[str]] = Field(default_factory=dict)
    schedule: List[ProgramScheduleItem] = Field(default_factory=list)

    status: str = "active"

    class Settings:
        name = "workout_programs"
        indexes = [
            IndexModel([("slug", ASCENDING)], unique=True),
            IndexModel([("level", ASCENDING)]),
            IndexModel([("location", ASCENDING)]),
        ]


class MeditationItem(BaseDoc):
    type: str
    title: I18nList
    description: Optional[I18nList] = None

    duration_minutes: int = Field(ge=1, le=180)
    media: Dict[str, Optional[str]] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)

    status: str = "active"

    class Settings:
        name = "meditation_items"
        indexes = [
            IndexModel([("type", ASCENDING)]),
            IndexModel([("tags", ASCENDING)]),
        ]
