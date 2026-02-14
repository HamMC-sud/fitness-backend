from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import IndexModel, ASCENDING, DESCENDING

from .base import BaseDoc
from .enums import AiRequestType, AiRequestStatus


class AiUsageMonthly(BaseDoc):
    user_id: PydanticObjectId
    period: str

    base_limit: int = 1
    extra_from_rewarded: int = 0
    used: int = 0

    class Settings:
        name = "ai_usage_monthly"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("period", ASCENDING)], unique=True),
        ]


class AiPlanDay(BaseModel):
    date: str
    type: str
    workout_template: Optional[Dict[str, object]] = None


class AiPlan(BaseDoc):
    user_id: PydanticObjectId
    status: str = "active"

    created_from: Dict[str, object] = Field(default_factory=dict)
    days: List[AiPlanDay] = Field(default_factory=list)

    version: int = 1
    reroll_of_plan_id: Optional[PydanticObjectId] = None

    class Settings:
        name = "ai_plans"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
        ]


class AiRequest(BaseDoc):
    user_id: PydanticObjectId
    type: AiRequestType
    status: AiRequestStatus = AiRequestStatus.ok

    prompt_meta: Dict[str, object] = Field(default_factory=dict)
    error: Optional[str] = None

    class Settings:
        name = "ai_requests"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("type", ASCENDING), ("created_at", DESCENDING)]),
        ]


class AiChatThread(BaseDoc):
    user_id: PydanticObjectId
    title: Optional[str] = None

    class Settings:
        name = "ai_chat_threads"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("updated_at", DESCENDING)]),
        ]


class AiChatMessage(BaseDoc):
    thread_id: PydanticObjectId
    user_id: PydanticObjectId
    role: str
    text: str

    class Settings:
        name = "ai_chat_messages"
        indexes = [
            IndexModel([("thread_id", ASCENDING), ("created_at", ASCENDING)]),
        ]


class RewardedGrant(BaseDoc):
    user_id: PydanticObjectId
    nonce: str
    provider: str
    granted_at: datetime

    class Settings:
        name = "rewarded_grants"
        indexes = [
            IndexModel([("nonce", ASCENDING)], unique=True),
            IndexModel([("user_id", ASCENDING), ("granted_at", DESCENDING)]),
        ]
