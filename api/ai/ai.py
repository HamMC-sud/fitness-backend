from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from api.auth.config import get_current_user
from models import (
    AiUsageMonthly,
    AiPlan,
    AiRequest,
    AiChatThread,
    AiChatMessage,
    RewardedGrant,
    Subscription,
)
from models.enums import AiRequestType, AiRequestStatus, SubscriptionStatus
from schemas.ai import (
    AiAdjustIn,
    AiAdjustOut,
    AiLimitsOut,
    AiGenerateIn,
    AiGenerateOut,
    AiRerollIn,
    AiRerollOut,
    AiChatIn,
    AiChatOut,
    AiPlanOut,
    RewardedGrantIn,
    RewardedGrantOut,
)

router = APIRouter(tags=["ai"])


# ============================================================
# Time helpers
# ============================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def period_yyyy_mm(dt: datetime) -> str:
    """
    AI limits are calculated per UTC month.
    This is intentional and MUST stay stable.
    """
    return dt.strftime("%Y-%m")


def month_bounds_utc(dt: datetime) -> tuple[datetime, datetime]:
    start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


# ============================================================
# Subscription / Premium
# ============================================================

async def is_premium_user(user_id: PydanticObjectId) -> bool:
    """
    Premium if:
    - active subscription
    - grace period is still valid
    """
    sub = await Subscription.find_one(Subscription.user_id == user_id)
    if not sub:
        return False

    now = utcnow()

    grace_until = getattr(sub, "grace_until", None)
    if grace_until:
        if grace_until.tzinfo is None:
            grace_until = grace_until.replace(tzinfo=timezone.utc)
        if grace_until > now:
            return True

    expires_at = getattr(sub, "expires_at", None)
    if expires_at:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at > now:
            return True

    return getattr(sub, "status", None) in (
        SubscriptionStatus.active,
        SubscriptionStatus.grace,
    )


# ============================================================
# Usage & limits
# ============================================================

async def get_or_create_usage(user_id: PydanticObjectId, period: str) -> AiUsageMonthly:
    """
    Free users only.
    Premium users must NOT create usage rows.
    """
    rec = await AiUsageMonthly.find_one(
        AiUsageMonthly.user_id == user_id,
        AiUsageMonthly.period == period,
    )
    if rec:
        return rec

    rec = AiUsageMonthly(
        user_id=user_id,
        period=period,
        base_limit=1,
        extra_from_rewarded=0,
        used=0,
    )
    await rec.insert()
    return rec


async def free_reroll_used_in_period(
    user_id: PydanticObjectId,
    start_utc: datetime,
    end_utc: datetime,
) -> bool:
    return await AiRequest.find_one(
        {
            "user_id": user_id,
            "type": AiRequestType.reroll,
            "created_at": {"$gte": start_utc, "$lt": end_utc},
        }
    ) is not None


def plan_to_out(plan: AiPlan) -> AiPlanOut:
    return AiPlanOut(
        id=str(plan.id),
        status=plan.status,
        version=plan.version,
        reroll_of_plan_id=str(plan.reroll_of_plan_id) if plan.reroll_of_plan_id else None,
        days=[d.model_dump() for d in (plan.days or [])],
        created_at=plan.created_at,
    )


async def build_limits(user_id: PydanticObjectId) -> AiLimitsOut:
    now = utcnow()
    period = period_yyyy_mm(now)
    start_utc, end_utc = month_bounds_utc(now)

    premium = await is_premium_user(user_id)
    reroll_used = await free_reroll_used_in_period(user_id, start_utc, end_utc)

    if premium:
        return AiLimitsOut(
            period=period,
            is_premium=True,
            base_limit=None,
            extra_from_rewarded=None,
            used=None,
            remaining=None,
            can_generate=True,
            free_reroll_used=reroll_used,
        )

    usage = await get_or_create_usage(user_id, period)
    base_limit = int(usage.base_limit or 1)
    extra = int(usage.extra_from_rewarded or 0)
    used = int(usage.used or 0)

    total = base_limit + extra
    remaining = max(0, total - used)

    return AiLimitsOut(
        period=period,
        is_premium=False,
        base_limit=base_limit,
        extra_from_rewarded=extra,
        used=used,
        remaining=remaining,
        can_generate=remaining > 0,
        free_reroll_used=reroll_used,
    )


# ============================================================
# Endpoints
# ============================================================

@router.get("/ai/limits", response_model=AiLimitsOut)
async def ai_limits(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")
    return await build_limits(current_user.id)


@router.post("/ai/rewarded/grant", response_model=RewardedGrantOut)
async def ai_rewarded_grant(payload: RewardedGrantIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    nonce = (payload.nonce or "").strip()
    provider = (payload.provider or "").strip()

    if not nonce or len(nonce) > 128:
        raise HTTPException(400, "Invalid nonce")
    if not provider or len(provider) > 32:
        raise HTTPException(400, "Invalid provider")

    existing = await RewardedGrant.find_one(RewardedGrant.nonce == nonce)
    if existing:
        return RewardedGrantOut(granted=False, limits=await build_limits(current_user.id))

    now = utcnow()
    await RewardedGrant(
        user_id=current_user.id,
        nonce=nonce,
        provider=provider,
        granted_at=now,
    ).insert()

    if not await is_premium_user(current_user.id):
        period = period_yyyy_mm(now)
        usage = await get_or_create_usage(current_user.id, period)
        usage.extra_from_rewarded += 1
        await usage.save()

    return RewardedGrantOut(granted=True, limits=await build_limits(current_user.id))


@router.post("/ai/generate-plan", response_model=AiGenerateOut)
async def ai_generate_plan(payload: AiGenerateIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    premium = await is_premium_user(current_user.id)
    now = utcnow()

    if not premium:
        period = period_yyyy_mm(now)
        usage = await get_or_create_usage(current_user.id, period)
        if usage.used >= (usage.base_limit + usage.extra_from_rewarded):
            raise HTTPException(403, "AI limit reached")
        usage.used += 1
        await usage.save()

    await AiRequest(
        user_id=current_user.id,
        type=AiRequestType.generate_plan,
        status=AiRequestStatus.ok,
        prompt_meta=payload.prompt_meta or {},
    ).insert()

    plan = AiPlan(
        user_id=current_user.id,
        status="active",
        created_from=payload.prompt_meta or {},
        days=[],
        version=1,
        reroll_of_plan_id=None,
    )
    await plan.insert()

    return AiGenerateOut(plan=plan_to_out(plan))


@router.post("/ai/reroll-plan", response_model=AiRerollOut)
async def ai_reroll_plan(payload: AiRerollIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    now = utcnow()
    start_utc, end_utc = month_bounds_utc(now)

    if not await is_premium_user(current_user.id):
        if await free_reroll_used_in_period(current_user.id, start_utc, end_utc):
            raise HTTPException(403, "Free reroll already used")

    plan = await AiPlan.get(PydanticObjectId(payload.plan_id))
    if not plan or plan.user_id != current_user.id:
        raise HTTPException(404, "Plan not found")

    await AiRequest(
        user_id=current_user.id,
        type=AiRequestType.reroll,
        status=AiRequestStatus.ok,
        prompt_meta=payload.prompt_meta or {},
    ).insert()

    new_plan = AiPlan(
        user_id=current_user.id,
        status="active",
        created_from=payload.prompt_meta or {},
        days=[],
        version=plan.version + 1,
        reroll_of_plan_id=plan.id,
    )
    await new_plan.insert()

    return AiRerollOut(plan=plan_to_out(new_plan))


@router.get("/ai/plan", response_model=AiPlanOut)
async def get_current_plan(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    plan = await AiPlan.find_one(
        AiPlan.user_id == current_user.id,
        AiPlan.status == "active",
    ).sort("-created_at")

    if not plan:
        raise HTTPException(404, "No active plan")

    return plan_to_out(plan)


@router.post("/ai/chat", response_model=AiChatOut)
async def ai_chat(payload: AiChatIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    if not await is_premium_user(current_user.id):
        raise HTTPException(403, "Premium required")

    await AiRequest(
        user_id=current_user.id,
        type=AiRequestType.chat,
        status=AiRequestStatus.ok,
        prompt_meta=payload.meta or {},
    ).insert()

    thread = None
    if payload.thread_id:
        try:
            thread = await AiChatThread.get(PydanticObjectId(payload.thread_id))
        except Exception:
            thread = None

    if not thread or thread.user_id != current_user.id:
        thread = AiChatThread(user_id=current_user.id)
        await thread.insert()

    await AiChatMessage(
        thread_id=thread.id,
        user_id=current_user.id,
        role="user",
        text=payload.text,
    ).insert()

    assistant_text = "AI stub: connect Yandex GPT here"

    await AiChatMessage(
        thread_id=thread.id,
        user_id=current_user.id,
        role="assistant",
        text=assistant_text,
    ).insert()

    return AiChatOut(
        thread_id=str(thread.id),
        assistant_text=assistant_text,
    )


@router.post("/ai/adjust-plan", response_model=AiAdjustOut)
async def ai_adjust_plan(payload: AiAdjustIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    if not await is_premium_user(current_user.id):
        raise HTTPException(403, "Premium required")

    await AiRequest(
        user_id=current_user.id,
        type=AiRequestType.adjust,
        status=AiRequestStatus.ok,
        prompt_meta=payload.prompt_meta or {},
    ).insert()

    plan = AiPlan(
        user_id=current_user.id,
        status="active",
        created_from=payload.prompt_meta or {},
        days=[],
        version=1,
        reroll_of_plan_id=None,
    )
    await plan.insert()

    return AiAdjustOut(plan=plan_to_out(plan))
