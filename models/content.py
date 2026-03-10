from __future__ import annotations

from typing import Dict, List, Optional, Any

from pydantic import BaseModel, Field, field_validator
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


class I18nText(BaseModel):
    ru: str = ""
    en: str = ""

    @field_validator("ru", "en", mode="before")
    @classmethod
    def coerce_to_str(cls, v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, list):
            return str(v[0]) if v else ""
        return str(v)


class ExerciseInstruction(BaseModel):
    step: int = Field(ge=1)
    title: I18nText
    description: I18nText


class ExerciseCommonMistake(BaseModel):
    title: I18nText
    description: I18nText


class ExerciseMedia(BaseModel):
    video_url: str
    thumbnail_url: str
    duration_seconds: int = Field(ge=1, le=3600)
    mode: ExerciseMode = ExerciseMode.reps


class ExerciseDefaults(BaseModel):
    sets: int = Field(default=4, ge=1, le=20)
    reps: Optional[int] = Field(default=None, ge=1, le=500)
    duration_seconds: Optional[int] = Field(default=None, ge=5, le=3600)
    rest_seconds_after: int = Field(default=60, ge=0, le=600)


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

    instructions: List[ExerciseInstruction] = Field(default_factory=list)
    common_mistakes: List[ExerciseCommonMistake] = Field(default_factory=list)
    ai_technique: Optional[I18nText] = None
    ai_mistakes: Optional[I18nText] = None
    status: str = "active"

    @field_validator("equipment", mode="before")
    @classmethod
    def normalize_equipment(cls, v: Any):
        return Equipment.normalize_many(v)

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
