from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import Field
from pymongo import IndexModel, ASCENDING, DESCENDING

from .base import BaseDoc
from .enums import SubscriptionStatus, SubscriptionSource


class SubscriptionPlan(BaseDoc):
    code: str
    duration_days: int = Field(ge=1, le=3650)
    prices: Dict[str, Dict[str, object]] = Field(default_factory=dict)
    status: str = "active"

    class Settings:
        name = "subscription_plans"
        indexes = [IndexModel([("code", ASCENDING)], unique=True)]


class Subscription(BaseDoc):
    user_id: PydanticObjectId
    status: SubscriptionStatus
    plan_code: str
    source: SubscriptionSource
    started_at: datetime
    expires_at: datetime
    grace_until: Optional[datetime] = None
    auto_renew: bool = True
    last_transaction_id: Optional[PydanticObjectId] = None

    class Settings:
        name = "subscriptions"
        indexes = [
            IndexModel([("user_id", ASCENDING)], unique=True),
            IndexModel([("expires_at", ASCENDING)]),
        ]


class SubscriptionTransaction(BaseDoc):
    user_id: PydanticObjectId
    source: SubscriptionSource
    plan_code: str
    amount: Optional[float] = None
    currency: Optional[str] = None
    store: Dict[str, object] = Field(default_factory=dict)
    promo: Dict[str, object] = Field(default_factory=dict)

    class Settings:
        name = "subscription_transactions"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
        ]
