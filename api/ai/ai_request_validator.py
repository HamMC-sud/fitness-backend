from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from beanie.odm.fields import PydanticObjectId

from models import AiUsageMonthly


@dataclass(frozen=True)
class PlanGenerationAccess:
    is_premium: bool
    can_generate: bool
    usage: Optional[AiUsageMonthly]
    used: Optional[int]
    limit: Optional[int]
    remaining: Optional[int]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def period_yyyy_mm(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


async def get_or_create_usage(user_id: PydanticObjectId, period: str) -> AiUsageMonthly:
    usage = await AiUsageMonthly.find_one(
        AiUsageMonthly.user_id == user_id,
        AiUsageMonthly.period == period,
    )
    if usage:
        return usage

    usage = AiUsageMonthly(
        user_id=user_id,
        period=period,
        base_limit=1,
        extra_from_rewarded=0,
        used=0,
    )
    await usage.insert()
    return usage


async def get_plan_generation_access(
    *,
    user_id: PydanticObjectId,
    is_premium: bool,
) -> PlanGenerationAccess:
    if is_premium:
        return PlanGenerationAccess(
            is_premium=True,
            can_generate=True,
            usage=None,
            used=None,
            limit=None,
            remaining=None,
        )

    usage = await get_or_create_usage(user_id, period_yyyy_mm(utcnow()))
    base_limit = int(getattr(usage, "base_limit", 0) or 0)
    extra = int(getattr(usage, "extra_from_rewarded", 0) or 0)
    used = int(getattr(usage, "used", 0) or 0)
    limit = base_limit + extra
    remaining = max(0, limit - used)

    return PlanGenerationAccess(
        is_premium=False,
        can_generate=remaining > 0,
        usage=usage,
        used=used,
        limit=limit,
        remaining=remaining,
    )
