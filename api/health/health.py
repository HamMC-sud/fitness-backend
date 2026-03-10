from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import DuplicateKeyError

from api.auth.config import get_current_user
from models import User, UserHealthStepDaily
from schemas.health import (
    HealthStepsIn,
    HealthStepsOut,
)

router = APIRouter(tags=["health"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def require_auth(user):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/health/steps", response_model=HealthStepsOut)
async def upsert_health_steps(
    payload: HealthStepsIn,
    current_user: User = Depends(get_current_user),
):
    require_auth(current_user)
    now = utcnow()
    target_date = payload.resolved_date()

    doc = await UserHealthStepDaily.find_one(
        UserHealthStepDaily.user_id == current_user.id,
        UserHealthStepDaily.provider == payload.provider,
        UserHealthStepDaily.date == target_date,
    )

    if not doc:
        doc = UserHealthStepDaily(
            user_id=current_user.id,
            provider=payload.provider,
            date=target_date,
            steps=payload.steps,
            recorded_at=payload.normalized_recorded_at(),
            timezone=payload.timezone,
            meta=payload.meta or {},
            updated_at=now,
        )
        try:
            await doc.insert()
        except DuplicateKeyError:
            doc = await UserHealthStepDaily.find_one(
                UserHealthStepDaily.user_id == current_user.id,
                UserHealthStepDaily.provider == payload.provider,
                UserHealthStepDaily.date == target_date,
            )
            if not doc:
                raise HTTPException(status_code=500, detail="Failed to save steps")

    doc.steps = payload.steps
    doc.recorded_at = payload.normalized_recorded_at()
    doc.timezone = payload.timezone
    doc.meta = payload.meta or {}
    doc.updated_at = now
    await doc.save()

    return HealthStepsOut(
        provider=doc.provider,
        date=doc.date,
        steps=doc.steps,
        recorded_at=doc.recorded_at,
        timezone=doc.timezone,
        updated_at=doc.updated_at,
    )
