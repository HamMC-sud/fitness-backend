from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
import logging
from typing import Any, Optional

from beanie.odm.fields import PydanticObjectId
from pymongo import DESCENDING

from models.content import I18nText
from models.notification_history import NotificationHistory
from schemas.notifications import NotificationHistoryCreateIn

logger = logging.getLogger("uvicorn.error")


def _strip_i18n_text(value: Optional[I18nText]) -> Optional[I18nText]:
    if value is None:
        return None
    return I18nText(
        ru=str(value.ru or "").strip(),
        en=str(value.en or "").strip(),
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def clamp_limit(limit: Optional[int]) -> int:
    if limit is None:
        return 20
    return max(1, min(int(limit), 100))


def encode_cursor(created_at: datetime, item_id: str) -> str:
    payload = {"created_at": created_at.isoformat(), "id": str(item_id)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: Optional[str]) -> Optional[dict[str, Any]]:
    if not cursor:
        return None
    raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
    payload = json.loads(raw.decode("utf-8"))
    created_at = datetime.fromisoformat(str(payload["created_at"]))
    if created_at.tzinfo is not None:
        created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
    item_id = str(payload["id"])
    return {"created_at": created_at, "id": item_id}


def _list_query(user_id: PydanticObjectId, cursor_data: Optional[dict[str, Any]]) -> dict[str, Any]:
    query: dict[str, Any] = {"user_id": user_id}
    if not cursor_data:
        return query
    return {
        "$and": [
            query,
            {
                "$or": [
                    {"created_at": {"$lt": cursor_data["created_at"]}},
                    {"created_at": cursor_data["created_at"], "_id": {"$lt": cursor_data["id"]}},
                ]
            },
        ]
    }


async def create_notification_history(user_id: PydanticObjectId, payload: NotificationHistoryCreateIn) -> NotificationHistory:
    now = utcnow()
    notification_id = str(payload.notification_id or payload.event_key or "").strip()
    if not notification_id:
        item = NotificationHistory(
            user_id=user_id,
            type=payload.type.strip(),
            title=_strip_i18n_text(payload.title) or I18nText(),
            subtitle=_strip_i18n_text(payload.subtitle),
            body=_strip_i18n_text(payload.body),
            source=(payload.source or "system").strip() or "system",
            deep_link=payload.deep_link,
            image_url=payload.image_url,
            priority=(payload.priority or "normal").strip() or "normal",
            meta=payload.meta or {},
            is_read=False,
            delivered_at=now,
            event_key=str(payload.event_key or "").strip() or None,
        )
        await item.insert()
        logger.info(
            "Notification stored: user_id=%s notification_id=%s event_key=%s delivered_at=%s seen_at=%s read_at=%s duplicate_skipped=%s",
            str(user_id),
            str(item.id),
            str(item.event_key or ""),
            item.delivered_at.isoformat() if item.delivered_at else None,
            None,
            None,
            False,
        )
        return item

    event_key = str(payload.event_key or "").strip() or None
    existing = None
    if event_key:
        existing = await NotificationHistory.find_one(
            NotificationHistory.user_id == user_id,
            NotificationHistory.event_key == event_key,
        )
    if existing is None:
        existing = await NotificationHistory.find_one(
            NotificationHistory.user_id == user_id,
            NotificationHistory.id == notification_id,
        )
    if existing:
        if existing.delivered_at is None:
            existing.delivered_at = now
        existing.updated_at = now
        await existing.save()
        logger.info(
            "Notification stored: user_id=%s notification_id=%s event_key=%s delivered_at=%s seen_at=%s read_at=%s duplicate_skipped=%s",
            str(user_id),
            str(existing.id),
            str(existing.event_key or ""),
            existing.delivered_at.isoformat() if existing.delivered_at else None,
            existing.seen_at.isoformat() if existing.seen_at else None,
            existing.read_at.isoformat() if existing.read_at else None,
            True,
        )
        return existing

    item = NotificationHistory(
        id=notification_id,
        user_id=user_id,
        event_key=event_key,
        type=payload.type.strip(),
        title=_strip_i18n_text(payload.title) or I18nText(),
        subtitle=_strip_i18n_text(payload.subtitle),
        body=_strip_i18n_text(payload.body),
        source=(payload.source or "system").strip() or "system",
        deep_link=payload.deep_link,
        image_url=payload.image_url,
        priority=(payload.priority or "normal").strip() or "normal",
        meta=payload.meta or {},
        is_read=False,
        delivered_at=now,
    )
    await item.insert()
    logger.info(
        "Notification stored: user_id=%s notification_id=%s event_key=%s delivered_at=%s seen_at=%s read_at=%s duplicate_skipped=%s",
        str(user_id),
        str(item.id),
        str(item.event_key or ""),
        item.delivered_at.isoformat() if item.delivered_at else None,
        None,
        None,
        False,
    )
    return item


async def list_notification_history(user_id: PydanticObjectId, limit: int, cursor: Optional[str]) -> tuple[list[NotificationHistory], Optional[str], bool]:
    safe_limit = clamp_limit(limit)
    cursor_data = decode_cursor(cursor)
    query = _list_query(user_id=user_id, cursor_data=cursor_data)
    rows = (
        await NotificationHistory.find(query)
        .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
        .limit(safe_limit + 1)
        .to_list()
    )
    has_more = len(rows) > safe_limit
    items = rows[:safe_limit]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(last.created_at, str(last.id))
    return items, next_cursor, has_more


async def unread_count_for_user(user_id: PydanticObjectId) -> int:
    return await NotificationHistory.find(
        {
            "user_id": user_id,
            "dismissed_at": None,
            "seen_at": None,
            "read_at": None,
        }
    ).count()


async def new_count_for_user(user_id: PydanticObjectId) -> int:
    return await NotificationHistory.find(
        {
            "user_id": user_id,
            "dismissed_at": None,
            "seen_at": None,
            "read_at": None,
        }
    ).count()


async def patch_notification_state(
    *,
    user_id: PydanticObjectId,
    notification_id: str,
    delivered: Optional[bool] = None,
    seen: Optional[bool] = None,
    read: Optional[bool] = None,
    dismissed: Optional[bool] = None,
) -> NotificationHistory:
    item = await NotificationHistory.find_one(
        NotificationHistory.user_id == user_id,
        NotificationHistory.id == notification_id,
    )
    if not item:
        raise ValueError("Notification not found")

    now = utcnow()
    if delivered:
        item.delivered_at = item.delivered_at or now
    if seen:
        item.delivered_at = item.delivered_at or now
        item.seen_at = item.seen_at or now
    if read:
        item.delivered_at = item.delivered_at or now
        item.seen_at = item.seen_at or now
        item.read_at = item.read_at or now
        item.is_read = True
    if dismissed:
        item.dismissed_at = item.dismissed_at or now
    item.updated_at = now
    await item.save()
    logger.info(
        "Notification state updated: user_id=%s notification_id=%s event_key=%s delivered_at=%s seen_at=%s read_at=%s duplicate_skipped=%s",
        str(user_id),
        str(item.id),
        str(item.event_key or ""),
        item.delivered_at.isoformat() if item.delivered_at else None,
        item.seen_at.isoformat() if item.seen_at else None,
        item.read_at.isoformat() if item.read_at else None,
        False,
    )
    return item


async def patch_all_notification_states(
    *,
    user_id: PydanticObjectId,
    seen: bool = False,
    read: bool = False,
) -> int:
    query: dict[str, Any] = {"user_id": user_id, "dismissed_at": None}
    now = utcnow()
    set_patch: dict[str, Any] = {}
    if seen:
        query["seen_at"] = None
        set_patch["delivered_at"] = now
        set_patch["seen_at"] = now
    if read:
        query["read_at"] = None
        set_patch["delivered_at"] = now
        set_patch["seen_at"] = now
        set_patch["read_at"] = now
        set_patch["is_read"] = True
    if not set_patch:
        return 0
    result = await NotificationHistory.find(query).update({"$set": set_patch})
    return int(getattr(result, "modified_count", 0) or 0)
