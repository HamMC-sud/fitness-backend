from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth.config import get_current_user
from api.notifications.service import clamp_limit, create_notification_history, list_notification_history, unread_count_for_user
from models.users import User
from schemas.notifications import (
    NotificationHistoryCreateIn,
    NotificationHistoryItemOut,
    NotificationHistoryListOut,
    NotificationHistoryPaginationOut,
    ReminderSettingsIn,
    ReminderSettingsOut,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _require_auth(user: Optional[User]) -> User:
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


def _to_out(item) -> NotificationHistoryItemOut:
    return NotificationHistoryItemOut(
        id=str(item.id),
        user_id=str(item.user_id),
        type=item.type,
        title=item.title,
        subtitle=item.subtitle,
        body=item.body,
        source=item.source,
        deep_link=item.deep_link,
        image_url=item.image_url,
        priority=item.priority,
        meta=item.meta or {},
        is_read=bool(item.is_read),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _reminder_settings_out(user: User) -> ReminderSettingsOut:
    reminder_settings = getattr(user, "reminder_settings", None)
    return ReminderSettingsOut(
        enabled=bool(getattr(reminder_settings, "enabled", False)),
        days_of_week=list(getattr(reminder_settings, "days_of_week", []) or []),
        time=str(getattr(reminder_settings, "time", "09:00") or "09:00"),
        timezone=str(
            getattr(reminder_settings, "timezone", None)
            or getattr(user, "timezone", None)
            or "UTC"
        ),
        notification_permission=getattr(reminder_settings, "notification_permission", None),
        updated_at=getattr(reminder_settings, "updated_at", None),
    )


@router.post("/history", response_model=NotificationHistoryItemOut, status_code=201)
async def create_history_item(
    payload: NotificationHistoryCreateIn,
    current_user: User = Depends(get_current_user),
):
    user = _require_auth(current_user)
    item = await create_notification_history(user_id=user.id, payload=payload)
    return _to_out(item)


@router.get("/history", response_model=NotificationHistoryListOut)
async def get_history(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
):
    user = _require_auth(current_user)
    safe_limit = clamp_limit(limit)
    try:
        items, next_cursor, has_more = await list_notification_history(
            user_id=user.id,
            limit=safe_limit,
            cursor=cursor,
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    unread_count = await unread_count_for_user(user.id)
    return NotificationHistoryListOut(
        items=[_to_out(item) for item in items],
        pagination=NotificationHistoryPaginationOut(
            limit=safe_limit,
            next_cursor=next_cursor,
            has_more=has_more,
        ),
        unread_count=unread_count,
    )


@router.get("/reminders", response_model=ReminderSettingsOut)
@router.get("/reminder-settings", response_model=ReminderSettingsOut)
async def get_reminder_settings(current_user: User = Depends(get_current_user)):
    user = _require_auth(current_user)
    return _reminder_settings_out(user)


@router.put("/reminders", response_model=ReminderSettingsOut)
@router.patch("/reminders", response_model=ReminderSettingsOut)
@router.put("/reminder-settings", response_model=ReminderSettingsOut)
@router.patch("/reminder-settings", response_model=ReminderSettingsOut)
async def save_reminder_settings(
    payload: ReminderSettingsIn,
    current_user: User = Depends(get_current_user),
):
    user = _require_auth(current_user)
    existing = _reminder_settings_out(user)

    merged = ReminderSettingsOut(
        enabled=payload.enabled if payload.enabled is not None else existing.enabled,
        days_of_week=payload.days_of_week if payload.days_of_week is not None else existing.days_of_week,
        time=payload.time if payload.time is not None else existing.time,
        timezone=payload.timezone if payload.timezone is not None else existing.timezone,
        notification_permission=(
            payload.notification_permission
            if payload.notification_permission is not None
            else existing.notification_permission
        ),
        updated_at=datetime.utcnow(),
    )

    await User.find_one(User.id == user.id).update(
        {
            "$set": {
                "reminder_settings.enabled": merged.enabled,
                "reminder_settings.days_of_week": merged.days_of_week,
                "reminder_settings.time": merged.time,
                "reminder_settings.timezone": merged.timezone,
                "reminder_settings.notification_permission": merged.notification_permission,
                "reminder_settings.updated_at": merged.updated_at,
            }
        }
    )

    refreshed = await User.get(user.id)
    if not refreshed:
        raise HTTPException(status_code=404, detail="User not found")
    return _reminder_settings_out(refreshed)
