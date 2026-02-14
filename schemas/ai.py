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
