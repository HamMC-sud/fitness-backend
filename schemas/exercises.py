from typing import List, Optional
from pydantic import BaseModel
from beanie.odm.fields import PydanticObjectId

from models.enums import (
    Difficulty,
    WorkoutType,
    Equipment,
    Injury,
    ExerciseMode,
    MuscleGroup,
)


class I18nTextOut(BaseModel):
    ru: str
    en: str


class ExerciseMediaOut(BaseModel):
    mode: ExerciseMode
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration_seconds: Optional[int] = None


class ExerciseOut(BaseModel):
    id: PydanticObjectId
    code: str

    name: I18nTextOut
    status: str

    difficulty: Difficulty
    workout_type: List[WorkoutType]

    equipment: List[Equipment]
    muscle_groups: List[MuscleGroup]

    contraindications: List[Injury]

    movement_type: Optional[str]
    media: Optional[ExerciseMediaOut] = None


class ExerciseListOut(BaseModel):
    items: List[ExerciseOut]
    total: int
    skip: int
    limit: int


class ExerciseCategoryOut(BaseModel):
    key: str
    label: str
    count: int


class ExerciseCategoriesOut(BaseModel):
    items: List[ExerciseCategoryOut]
