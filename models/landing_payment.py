from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import EmailStr, Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from .base import BaseDoc


class LandingYooKassaOrder(BaseDoc):
    order_uid: str = Field(min_length=32, max_length=64)
    fio: str = Field(min_length=2, max_length=120)
    email: EmailStr
    tariff: str = Field(min_length=1, max_length=64)
    plan_code: str = Field(min_length=1, max_length=64)
    promocode: Optional[str] = Field(default=None, max_length=64)
    discount_percent: int = Field(default=0, ge=0, le=95)
    amount: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    return_url: str = Field(min_length=1, max_length=2048)
    yookassa_payment_id: str = Field(min_length=1, max_length=128)
    yookassa_status: str = Field(min_length=1, max_length=64)
    confirmation_url: str = Field(min_length=1, max_length=2048)
    linked_user_id: Optional[PydanticObjectId] = None
    activated_at: Optional[datetime] = None
    activation_error: Optional[str] = Field(default=None, max_length=255)
    metadata: Dict[str, object] = Field(default_factory=dict)
    payload: Dict[str, object] = Field(default_factory=dict)

    class Settings:
        name = "landing_yookassa_orders"
        indexes = [
            IndexModel([("order_uid", ASCENDING)], unique=True),
            IndexModel([("yookassa_payment_id", ASCENDING)], unique=True),
            IndexModel([("created_at", DESCENDING)]),
            IndexModel([("email", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("linked_user_id", ASCENDING), ("created_at", DESCENDING)]),
        ]
