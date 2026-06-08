from __future__ import annotations

from datetime import datetime
from typing import Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth.config import get_current_user
from api.notifications.service import (
    clamp_limit,
    create_notification_history,
    list_notification_history,
    new_count_for_user,
    patch_all_notification_states,
    patch_notification_state,
    unread_count_for_user,
)
from models.users import User
from schemas.notifications import (
    NotificationCountOut,
    NotificationHistoryCreateIn,
    NotificationHistoryItemOut,
    NotificationHistoryListOut,
    NotificationHistoryPaginationOut,
    NotificationStatePatchIn,
    ReminderSettingsIn,
    ReminderSettingsOut,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])
logger = logging.getLogger("uvicorn.error")


def _require_auth(user: Optional[User]) -> User:
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


def _to_out(item, language: str = "en") -> NotificationHistoryItemOut:
    return NotificationHistoryItemOut.from_notification(item, language=language)


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
@router.post("", response_model=NotificationHistoryItemOut, status_code=201)
async def create_history_item(
    payload: NotificationHistoryCreateIn,
    current_user: User = Depends(get_current_user),
):
    user = _require_auth(current_user)
    item = await create_notification_history(user_id=user.id, payload=payload)
    return _to_out(item, language=str(getattr(user, "language", "en") or "en"))


@router.get("/history", response_model=NotificationHistoryListOut)
@router.get("", response_model=NotificationHistoryListOut)
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
    new_count = await new_count_for_user(user.id)
    return NotificationHistoryListOut(
        items=[_to_out(item, language=str(getattr(user, "language", "en") or "en")) for item in items],
        pagination=NotificationHistoryPaginationOut(
            limit=safe_limit,
            next_cursor=next_cursor,
            has_more=has_more,
        ),
        unread_count=unread_count,
        new_count=new_count,
    )


@router.get("/count", response_model=NotificationCountOut)
async def get_notification_counts(current_user: User = Depends(get_current_user)):
    user = _require_auth(current_user)
    return NotificationCountOut(
        unread_count=await unread_count_for_user(user.id),
        new_count=await new_count_for_user(user.id),
    )


@router.patch("/read-all")
async def mark_all_notifications_read(current_user: User = Depends(get_current_user)):
    user = _require_auth(current_user)
    updated = await patch_all_notification_states(user_id=user.id, read=True)
    logger.info("Notifications read-all: user_id=%s updated=%s", str(user.id), updated)
    return {
        "updated": updated,
        "counts": {
            "unread_count": await unread_count_for_user(user.id),
            "new_count": await new_count_for_user(user.id),
        },
    }


@router.patch("/seen-all")
async def mark_all_notifications_seen(current_user: User = Depends(get_current_user)):
    user = _require_auth(current_user)
    updated = await patch_all_notification_states(user_id=user.id, seen=True)
    logger.info("Notifications seen-all: user_id=%s updated=%s", str(user.id), updated)
    return {
        "updated": updated,
        "counts": {
            "unread_count": await unread_count_for_user(user.id),
            "new_count": await new_count_for_user(user.id),
        },
    }


@router.patch("/history/{notification_id}", response_model=NotificationHistoryItemOut)
async def patch_notification(
    notification_id: str,
    payload: NotificationStatePatchIn,
    current_user: User = Depends(get_current_user),
):
    user = _require_auth(current_user)
    try:
        item = await patch_notification_state(
            user_id=user.id,
            notification_id=notification_id,
            delivered=payload.delivered,
            seen=payload.seen,
            read=payload.read,
            dismissed=payload.dismissed,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Notification not found")
    return _to_out(item, language=str(getattr(user, "language", "en") or "en"))


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
