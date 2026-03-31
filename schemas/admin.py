from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from models.content import (
    ExerciseDefaults,
    ExerciseMedia,
    ExerciseInstruction,
    ExerciseCommonMistake,
    I18nList,
    I18nText,
)
from models.enums import Difficulty, Equipment, ExerciseMode, Injury, SubscriptionStatus, WorkoutType


class AdminExerciseCreateIn(BaseModel):
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
    def normalize_equipment(cls, v):
        return Equipment.normalize_many(v)


class AdminExerciseUpdateIn(BaseModel):
    code: Optional[str] = None
    name: Optional[I18nList] = None
    description: Optional[I18nList] = None
    media: Optional[ExerciseMedia] = None
    mode: Optional[ExerciseMode] = None
    defaults: Optional[ExerciseDefaults] = None
    beginner_tip: Optional[I18nList] = None
    muscle_groups: Optional[List[str]] = None
    movement_type: Optional[str] = None
    workout_type: Optional[List[WorkoutType]] = None
    equipment: Optional[List[Equipment]] = None
    contraindications: Optional[List[Injury]] = None
    difficulty: Optional[Difficulty] = None
    calories_per_minute: Optional[float] = Field(default=None, ge=0)
    instructions: Optional[List[ExerciseInstruction]] = None
    common_mistakes: Optional[List[ExerciseCommonMistake]] = None
    ai_technique: Optional[I18nText] = None
    ai_mistakes: Optional[I18nText] = None
    status: Optional[str] = None

    @field_validator("equipment", mode="before")
    @classmethod
    def normalize_equipment(cls, v):
        if v is None:
            return None
        return Equipment.normalize_many(v)


class AdminUserItemOut(BaseModel):
    id: str
    email: Optional[str] = None
    name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    has_active_subscription: bool = False
    subscription_status: Optional[SubscriptionStatus] = None


class AdminUsersOut(BaseModel):
    items: List[AdminUserItemOut] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 20


class AdminUsersTableItemOut(BaseModel):
    user_id: str
    name: Optional[str] = None
    email: Optional[str] = None
    plan: Optional[str] = None
    date: Optional[datetime] = None
    amount: Optional[float] = None
    currency: Optional[str] = None


class AdminUsersTableOut(BaseModel):
    items: List[AdminUsersTableItemOut] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 20


class AdminUsersStatsOut(BaseModel):
    users_total: int
    users_new_7d: int
    users_with_subscription: int
    active_subscriptions: int
    in_grace_subscriptions: int


class AdminDashboardOut(BaseModel):
    users_total: int
    users_new_7d: int
    active_subscriptions: int
    in_grace_subscriptions: int
    daily_active_users: int
    workout_runs_7d: int
    meditation_runs_7d: int
    promo_codes_total: int
    promo_redemptions_total: int
    verified_revenue_total: float
    users_delta_30d_pct: float = 0.0
    active_subscriptions_delta_30d_pct: float = 0.0
    revenue_delta_30d_pct: float = 0.0
    daily_active_delta_7d_pct: float = 0.0
    revenue_current_year: Optional[int] = None
    revenue_last_year: Optional[int] = None
    revenue_years: List[int] = Field(default_factory=list)
    revenue_overview: List[Dict[str, object]] = Field(default_factory=list)
    revenue_overview_last_year: List[Dict[str, object]] = Field(default_factory=list)
    revenue_overview_years: Dict[str, List[Dict[str, object]]] = Field(default_factory=dict)
    recent_subscriptions: List[Dict[str, object]] = Field(default_factory=list)


class AdminContentAssetIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: Optional[str] = Field(default=None, max_length=120)
    asset_type: str = Field(default="video", min_length=3, max_length=32)
    status: str = Field(default="draft", min_length=3, max_length=32)
    duration_seconds: Optional[int] = Field(default=None, ge=0, le=24 * 3600)
    duration_mmss: Optional[str] = Field(default=None, max_length=16)
    file_url: Optional[str] = Field(default=None, max_length=2000)
    file_name: Optional[str] = Field(default=None, max_length=255)
    video_url: Optional[str] = Field(default=None, max_length=2000)
    audio_url: Optional[str] = Field(default=None, max_length=2000)
    image_url: Optional[str] = Field(default=None, max_length=2000)
    meta: Dict[str, object] = Field(default_factory=dict)


class AdminContentAssetUpdateIn(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    author: Optional[str] = Field(default=None, max_length=120)
    asset_type: Optional[str] = Field(default=None, min_length=3, max_length=32)
    status: Optional[str] = Field(default=None, min_length=3, max_length=32)
    duration_seconds: Optional[int] = Field(default=None, ge=0, le=24 * 3600)
    duration_mmss: Optional[str] = Field(default=None, max_length=16)
    file_url: Optional[str] = Field(default=None, max_length=2000)
    file_name: Optional[str] = Field(default=None, max_length=255)
    video_url: Optional[str] = Field(default=None, max_length=2000)
    audio_url: Optional[str] = Field(default=None, max_length=2000)
    image_url: Optional[str] = Field(default=None, max_length=2000)
    meta: Optional[Dict[str, object]] = None


class AdminContentAssetOut(BaseModel):
    id: str
    title: str
    author: Optional[str] = None
    asset_type: str
    status: str
    duration_seconds: Optional[int] = None
    duration_mmss: Optional[str] = None
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    video_url: Optional[str] = None
    audio_url: Optional[str] = None
    image_url: Optional[str] = None
    meta: Dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class AdminContentUploadOut(BaseModel):
    video_url: Optional[str] = None
    audio_url: Optional[str] = None
    image_url: Optional[str] = None
    primary_file_url: Optional[str] = None
    primary_file_name: Optional[str] = None


class AdminContentAssetsOut(BaseModel):
    items: List[AdminContentAssetOut] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 20


class AdminPromoBatchGenerateIn(BaseModel):
    campaign_name: str = Field(min_length=1, max_length=128)
    discount_percent: int = Field(ge=1, le=95)
    quantity: int = Field(ge=1)
    duration_days: int = Field(default=30, ge=1, le=3650)
    max_uses_per_code: int = Field(default=1, ge=1)
    code_length: int = Field(default=10, ge=1)


class AdminPromoBatchItemOut(BaseModel):
    id: str
    batch_code: str
    campaign_name: str
    discount_percent: int
    progress_used: int
    progress_total: int
    created_at: datetime


class AdminPromoBatchesOut(BaseModel):
    items: List[AdminPromoBatchItemOut] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 20


class AdminPromoActivationItemOut(BaseModel):
    promo_code: str
    activated_by_email: Optional[str] = None
    activated_at: datetime
    discount_percent: Optional[int] = None


class AdminPromoActivationsOut(BaseModel):
    items: List[AdminPromoActivationItemOut] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 20
