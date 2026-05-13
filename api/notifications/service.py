from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Optional

from beanie.odm.fields import PydanticObjectId
from pymongo import DESCENDING

from models.notification_history import NotificationHistory
from schemas.notifications import NotificationHistoryCreateIn


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
    item = NotificationHistory(
        user_id=user_id,
        type=payload.type.strip(),
        title=payload.title.strip(),
        subtitle=payload.subtitle,
        body=payload.body,
        source=(payload.source or "system").strip() or "system",
        deep_link=payload.deep_link,
        image_url=payload.image_url,
        priority=(payload.priority or "normal").strip() or "normal",
        meta=payload.meta or {},
        is_read=False,
    )
    await item.insert()
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
    return await NotificationHistory.find({"user_id": user_id, "is_read": False}).count()
