from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import Field, AliasChoices
from pymongo import IndexModel, ASCENDING, DESCENDING

from .base import BaseDoc
from .enums import PromoStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PromoCodeBatch(BaseDoc):
    name: str
    duration_days: int = Field(ge=1, le=3650, validation_alias=AliasChoices("duration_days", "days"), serialization_alias="duration_days")
    max_uses_per_code: int = Field(ge=1, le=1_000_000)
    codes_count: int = Field(ge=1)
    created_by_admin_id: PydanticObjectId

    class Settings:
        name = "promo_code_batches"
        indexes = [
            IndexModel([("created_by_admin_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("name", ASCENDING)], unique=True),
        ]


class PromoCode(BaseDoc):
    batch_id: Optional[PydanticObjectId] = None
    code: str

    duration_days: int = Field(ge=1, le=3650, validation_alias=AliasChoices("duration_days", "days"), serialization_alias="duration_days")
    max_uses: int = Field(ge=1)

    used_count: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("used_count", "uses_count"),
        serialization_alias="used_count",
    )

    expires_at: Optional[datetime] = None
    status: PromoStatus = PromoStatus.active

    class Settings:
        name = "promo_codes"
        indexes = [
            IndexModel([("code", ASCENDING)], unique=True),
            IndexModel([("batch_id", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("expires_at", ASCENDING)]),
        ]


class PromoRedemption(BaseDoc):
    code: str
    promo_code_id: PydanticObjectId
    user_id: PydanticObjectId
    redeemed_at: datetime = Field(default_factory=utcnow)
    subscription_transaction_id: PydanticObjectId

    class Settings:
        name = "promo_redemptions"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("promo_code_id", ASCENDING)], unique=True),
            IndexModel([("promo_code_id", ASCENDING), ("redeemed_at", DESCENDING)]),
        ]
