from typing import List, Optional
from pydantic import BaseModel
from beanie.odm.fields import PydanticObjectId

from models.enums import (
    Difficulty,
    WorkoutType,
    Equipment,
    Injury,
    ExerciseMode,
    Location,
    MuscleGroup,
)


class I18nTextOut(BaseModel):
    ru: str
    en: str


class ExerciseMediaOut(BaseModel):
    mode: ExerciseMode
    video_url: Optional[str] = None
    image_url: Optional[str] = None


class ExerciseOut(BaseModel):
    id: PydanticObjectId
    code: str

    name: I18nTextOut
    status: str  # ⚠️ make Enum if possible

    difficulty: Difficulty
    workout_type: List[WorkoutType]

    equipment: List[Equipment]
    location: List[Location]  # ✅ REQUIRED

    muscle_groups: List[MuscleGroup]  # ✅ ENUM

    contraindications: List[Injury]

    movement_type: Optional[str]
    media: Optional[ExerciseMediaOut] = None


class ExerciseListOut(BaseModel):
    items: List[ExerciseOut]
    total: int
    skip: int
    limit: int
