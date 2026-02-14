from typing import Optional, List, Dict, Any
from beanie.odm.fields import PydanticObjectId
from pydantic import BaseModel, Field, field_validator, model_validator
from models.enums import WorkoutType, Difficulty, Equipment, ExerciseMode

class LocalizedText(BaseModel):
    ru: Optional[str] = None
    en: Optional[str] = None

    @field_validator("ru", "en", mode="before")
    @classmethod
    def coerce_list_to_str(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, list):
            return v[0] if v else None
        return str(v)


class WorkoutTemplateOut(BaseModel):
    id: PydanticObjectId
    title: LocalizedText
    type: WorkoutType
    level: Difficulty
    equipment_required: List[Equipment]
    status: str


class WorkoutProgramOut(BaseModel):
    id: PydanticObjectId
    slug: str
    title: LocalizedText
    level: Difficulty
    location: Optional[str] = None
    equipment_required: List[Equipment]
    status: str
    schedule: Optional[List[Dict[str, Any]]] = None


class WorkoutProgramExpandedOut(WorkoutProgramOut):
    templates: Dict[str, WorkoutTemplateOut]


class PaginatedTemplatesOut(BaseModel):
    items: List[WorkoutTemplateOut]
    total: int
    skip: int
    limit: int


class PaginatedProgramsOut(BaseModel):
    items: List[WorkoutProgramOut]
    total: int
    skip: int
    limit: int


class I18nTextIn(BaseModel):
    ru: str
    en: str


class WorkoutStepIn(BaseModel):
    order: int = Field(ge=1)
    exercise_id: str
    mode: ExerciseMode
    reps: Optional[int] = Field(default=None, ge=1, le=500)
    duration_seconds: Optional[int] = Field(default=None, ge=5, le=3600)
    rest_seconds_after: int = Field(default=45, ge=0, le=600)

    @field_validator("exercise_id")
    @classmethod
    def validate_exercise_id(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("exercise_id is required")
        return v

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


class WorkoutTemplateCreateIn(BaseModel):
    title: I18nTextIn
    description: Optional[I18nTextIn] = None
    type: WorkoutType
    level: Difficulty
    estimated_minutes: int = Field(ge=5, le=180)
    steps: List[WorkoutStepIn] = Field(default_factory=list)
    equipment_required: List[Equipment] = Field(default_factory=list)
    status: str = "active"


class WorkoutTemplateUpdateIn(BaseModel):
    title: Optional[I18nTextIn] = None
    description: Optional[I18nTextIn] = None
    type: Optional[WorkoutType] = None
    level: Optional[Difficulty] = None
    estimated_minutes: Optional[int] = Field(default=None, ge=5, le=180)
    steps: Optional[List[WorkoutStepIn]] = None
    equipment_required: Optional[List[Equipment]] = None
    status: Optional[str] = None


class ProgramScheduleItemIn(BaseModel):
    day_index: int = Field(ge=1, le=7)
    workout_template_id: str


class WorkoutProgramCreateIn(BaseModel):
    slug: Optional[str] = None
    title: I18nTextIn
    description: Optional[I18nTextIn] = None
    weeks: int = Field(ge=1, le=52)
    workouts_per_week: int = Field(ge=1, le=7)
    session_minutes: int = Field(ge=15, le=120)
    level: Difficulty
    goals: List[str] = Field(default_factory=list)
    location: str = "home"
    equipment_required: List[Equipment] = Field(default_factory=list)
    preview: Dict[str, Optional[str]] = Field(default_factory=dict)
    schedule: List[ProgramScheduleItemIn] = Field(default_factory=list)
    status: str = "active"

    @field_validator("location")
    @classmethod
    def validate_location(cls, v: str) -> str:
        v = (v or "").lower().strip()
        if v not in ("home", "gym"):
            raise ValueError("location must be 'home' or 'gym'")
        return v


class WorkoutProgramUpdateIn(BaseModel):
    slug: Optional[str] = None
    title: Optional[I18nTextIn] = None
    description: Optional[I18nTextIn] = None
    weeks: Optional[int] = Field(default=None, ge=1, le=52)
    workouts_per_week: Optional[int] = Field(default=None, ge=1, le=7)
    session_minutes: Optional[int] = Field(default=None, ge=15, le=120)
    level: Optional[Difficulty] = None
    goals: Optional[List[str]] = None
    location: Optional[str] = None
    equipment_required: Optional[List[Equipment]] = None
    preview: Optional[Dict[str, Optional[str]]] = None
    schedule: Optional[List[ProgramScheduleItemIn]] = None
    status: Optional[str] = None
