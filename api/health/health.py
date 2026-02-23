from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import DuplicateKeyError

from api.auth.config import get_current_user
from models import User, UserHealthIntegration, UserHealthStepDaily
from models.enums import HealthProvider
from schemas.health import (
    HealthIntegrationStateOut,
    HealthIntegrationsOut,
    HealthIntegrationToggleIn,
    HealthIntegrationToggleOut,
    HealthStepsIn,
    HealthStepsOut,
)

router = APIRouter(tags=["health"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def require_auth(user):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/health/integrations", response_model=HealthIntegrationsOut)
async def get_health_integrations(current_user: User = Depends(get_current_user)):
    require_auth(current_user)

    items = await UserHealthIntegration.find(UserHealthIntegration.user_id == current_user.id).to_list()
    by_provider = {x.provider: x for x in items}

    apple = by_provider.get(HealthProvider.apple_health)
    google = by_provider.get(HealthProvider.google_fit)

    return HealthIntegrationsOut(
        appleHealth=HealthIntegrationStateOut(connected=bool(apple and apple.is_connected)),
        googleFit=HealthIntegrationStateOut(connected=bool(google and google.is_connected)),
    )


@router.patch("/health/integrations", response_model=HealthIntegrationToggleOut)
async def toggle_health_integration(
    payload: HealthIntegrationToggleIn,
    current_user: User = Depends(get_current_user),
):
    require_auth(current_user)
    now = utcnow()

    doc = await UserHealthIntegration.find_one(
        UserHealthIntegration.user_id == current_user.id,
        UserHealthIntegration.provider == payload.provider,
    )

    if not doc:
        doc = UserHealthIntegration(
            user_id=current_user.id,
            provider=payload.provider,
            is_connected=payload.connected,
            connected_at=now if payload.connected else None,
            external_account_id=payload.external_account_id,
            meta=payload.meta or {},
            updated_at=now,
        )
        try:
            await doc.insert()
        except DuplicateKeyError:
            doc = await UserHealthIntegration.find_one(
                UserHealthIntegration.user_id == current_user.id,
                UserHealthIntegration.provider == payload.provider,
            )
            if not doc:
                raise HTTPException(status_code=500, detail="Failed to update integration")

    doc.is_connected = payload.connected
    doc.connected_at = now if payload.connected else None
    doc.external_account_id = payload.external_account_id
    doc.meta = payload.meta or {}
    doc.updated_at = now
    await doc.save()

    return HealthIntegrationToggleOut(
        provider=doc.provider,
        connected=doc.is_connected,
        connected_at=doc.connected_at,
        updated_at=doc.updated_at,
    )


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
