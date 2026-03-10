from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AiLimitsOut(BaseModel):
    period: str
    is_premium: bool
    base_limit: Optional[int] = None
    extra_from_rewarded: Optional[int] = None
    used: Optional[int] = None
    remaining: Optional[int] = None
    can_generate: bool
    free_reroll_used: bool


class AiPlanDayOut(BaseModel):
    date: str
    type: str
    workout_template: Optional[Dict[str, Any]] = None


class AiPlanOut(BaseModel):
    id: str
    status: str
    version: int
    reroll_of_plan_id: Optional[str] = None
    days: List[AiPlanDayOut] = Field(default_factory=list)
    created_at: Optional[datetime] = None


class AiGenerateIn(BaseModel):
    prompt_meta: Dict[str, Any] = Field(default_factory=dict)


class AiGenerateOut(BaseModel):
    request_id: str
    plan: AiPlanOut


class AiRerollIn(BaseModel):
    plan_id: str
    prompt_meta: Dict[str, Any] = Field(default_factory=dict)


class AiRerollOut(BaseModel):
    request_id: str
    plan: AiPlanOut


class AiChatIn(BaseModel):
    thread_id: Optional[str] = None
    text: str = Field(min_length=1, max_length=8000)
    meta: Dict[str, Any] = Field(default_factory=dict)


class AiChatOut(BaseModel):
    thread_id: str
    user_message_id: str
    assistant_message_id: str
    assistant_text: str
    plan_message_id: Optional[str] = None
    plan_message_text: Optional[str] = None
    action: Optional[Dict[str, Any]] = None


class AiChatMessageOut(BaseModel):
    id: str
    role: str
    text: str
    created_at: Optional[datetime] = None


class AiChatHistoryOut(BaseModel):
    thread_id: str
    items: List[AiChatMessageOut] = Field(default_factory=list)


class AiDailyRecommendationOut(BaseModel):
    id: str
    date: str
    text: str
    saved: bool
    opened_at: Optional[datetime] = None
    saved_at: Optional[datetime] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class AiDailyRecommendationSaveIn(BaseModel):
    recommendation_id: Optional[str] = None
    saved: Optional[bool] = None


class RewardedGrantIn(BaseModel):
    nonce: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=32)


class RewardedGrantOut(BaseModel):
    granted: bool
    limits: AiLimitsOut


# ✅ ADD THESE for /ai/adjust-plan (premium)
class AiAdjustIn(BaseModel):
    plan_id: str
    adjustments: Dict[str, Any] = Field(default_factory=dict)
    note: Optional[str] = Field(default=None, max_length=2000)
    prompt_meta: Dict[str, Any] = Field(default_factory=dict)

class AiAdjustOut(BaseModel):
    request_id: str
    plan: AiPlanOut


class AiPlanDayCardOut(BaseModel):
    date: str
    weekday: str
    type: str
    title: str
    duration_min: Optional[int] = None
    intensity: Optional[str] = None
    focus: Optional[str] = None


class AiPlanWeekOut(BaseModel):
    week_index: int
    days: List[AiPlanDayCardOut] = Field(default_factory=list)


class AiPlanWeeksOut(BaseModel):
    plan_id: str
    weeks: List[AiPlanWeekOut] = Field(default_factory=list)


class AiPlanDayDetailOut(BaseModel):
    plan_id: str
    date: str
    type: str
    workout_template: Optional[Dict[str, Any]] = None


class AiPlanDayEditIn(BaseModel):
    duration_min: Optional[int] = Field(default=None, ge=10, le=120)
    intensity: Optional[str] = None  # low|moderate|high or beginner|intermediate|advanced
    mark_rest_day: Optional[bool] = None
    delete_session: Optional[bool] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    focus: Optional[str] = Field(default=None, min_length=1, max_length=64)


class AiSwapOptionOut(BaseModel):
    swap_id: str
    title: str
    duration_min: int
    intensity: str
    focus: str
    workout_template: Dict[str, Any] = Field(default_factory=dict)


class AiSwapOptionsOut(BaseModel):
    plan_id: str
    date: str
    items: List[AiSwapOptionOut] = Field(default_factory=list)


class AiApplySwapIn(BaseModel):
    swap_id: str = Field(min_length=1, max_length=64)
