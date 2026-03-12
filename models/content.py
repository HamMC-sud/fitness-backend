from __future__ import annotations

from typing import Dict, List, Optional, Any

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
    class RepPlanItem(BaseModel):
        rep_no: int = Field(ge=1, le=100)
        target_reps: Optional[int] = Field(default=None, ge=1, le=500)
        target_duration_seconds: Optional[int] = Field(default=None, ge=5, le=3600)

        @model_validator(mode="after")
        def validate_target(self):
            has_reps = self.target_reps is not None
            has_time = self.target_duration_seconds is not None
            if has_reps and has_time:
                raise ValueError("RepPlanItem cannot have both target_reps and target_duration_seconds")
            if not has_reps and not has_time:
                raise ValueError("RepPlanItem must have target_reps or target_duration_seconds")
            return self

    class SetRepsPlanItem(BaseModel):
        set_no: int = Field(ge=1, le=100)
        rest_seconds_after: int = Field(default=60, ge=0, le=600)
        reps: List["ExerciseDefaults.RepPlanItem"] = Field(default_factory=list)

    class SetPlanItem(BaseModel):
        set_no: int = Field(ge=1, le=100)
        target_reps: Optional[int] = Field(default=None, ge=1, le=500)
        target_duration_seconds: Optional[int] = Field(default=None, ge=5, le=3600)
        rest_seconds_after: int = Field(default=60, ge=0, le=600)

    sets: int = Field(default=4, ge=1, le=20)
    reps: Optional[int] = Field(default=None, ge=1, le=500)
    duration_seconds: Optional[int] = Field(default=None, ge=5, le=3600)
    rest_seconds_after: int = Field(default=60, ge=0, le=600)
    sets_reps: List[SetRepsPlanItem] = Field(default_factory=list)
    set_plan: List[SetPlanItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_set_plan(self):
        if self.sets_reps:
            seen_sets = set()
            for set_item in self.sets_reps:
                if set_item.set_no in seen_sets:
                    raise ValueError("sets_reps.set_no must be unique")
                seen_sets.add(set_item.set_no)
                if not set_item.reps:
                    raise ValueError("sets_reps item must contain at least one rep")
                seen_reps = set()
                for rep in set_item.reps:
                    if rep.rep_no in seen_reps:
                        raise ValueError("sets_reps.reps.rep_no must be unique inside set")
                    seen_reps.add(rep.rep_no)

        if not self.set_plan:
            return self

        seen = set()
        for item in self.set_plan:
            if item.set_no in seen:
                raise ValueError("set_plan.set_no must be unique")
            seen.add(item.set_no)

            has_reps = item.target_reps is not None
            has_time = item.target_duration_seconds is not None
            if has_reps and has_time:
                raise ValueError("set_plan item cannot have both target_reps and target_duration_seconds")
            if not has_reps and not has_time:
                raise ValueError("set_plan item must have target_reps or target_duration_seconds")

        return self


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
