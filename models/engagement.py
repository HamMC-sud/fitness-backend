from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from .base import BaseDoc


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DevicePushToken(BaseDoc):
    user_id: PydanticObjectId
    provider: str = Field(min_length=1, max_length=32)
    platform: str = Field(min_length=1, max_length=16)
    token: str = Field(min_length=8, max_length=4096)
    device_id: Optional[str] = Field(default=None, max_length=128)
    locale: Optional[str] = Field(default=None, max_length=16)
    timezone: Optional[str] = Field(default=None, max_length=64)
    app_version: Optional[str] = Field(default=None, max_length=32)
    last_used_at: Optional[datetime] = None
    is_active: bool = Field(default=True)
    class Settings:
        name = "device_push_tokens"
        indexes = [
            IndexModel([("token", ASCENDING)], unique=True),
            IndexModel([("user_id", ASCENDING), ("provider", ASCENDING), ("platform", ASCENDING)]),
            IndexModel([("user_id", ASCENDING), ("last_used_at", DESCENDING)]),
        ]


class Reminder(BaseDoc):
    user_id: PydanticObjectId
    type: str = Field(min_length=1, max_length=32)
    enabled: bool = True
    timezone: str = Field(default="UTC", max_length=64)
    time_hhmm: str = Field(min_length=4, max_length=5)
    weekdays: List[int] = Field(default_factory=list)
    snooze_minutes: Optional[int] = Field(default=None, ge=1, le=240)
    sound: Optional[str] = Field(default=None, max_length=64)
    payload: Dict[str, Any] = Field(default_factory=dict)

    class Settings:
        name = "reminders"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("type", ASCENDING)]),
            IndexModel([("user_id", ASCENDING), ("enabled", ASCENDING)]),
            IndexModel([("type", ASCENDING), ("enabled", ASCENDING)]),
        ]


class PushDeliveryLog(BaseDoc):
    user_id: PydanticObjectId
    kind: str = Field(min_length=1, max_length=64)
    local_date: str = Field(min_length=10, max_length=10)
    status: str = Field(default="pending", max_length=32)
    attempt_count: int = Field(default=0, ge=0)
    last_attempt_at: Optional[datetime] = None
    last_error: Optional[str] = Field(default=None, max_length=2000)
    meta: Dict[str, Any] = Field(default_factory=dict)

    class Settings:
        name = "push_delivery_logs"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("kind", ASCENDING), ("local_date", ASCENDING)], unique=True),
            IndexModel([("kind", ASCENDING), ("local_date", ASCENDING), ("status", ASCENDING)]),
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
        ]


class AnalyticsEvent(BaseDoc):
    user_id: Optional[PydanticObjectId] = None
    anonymous_id: Optional[str] = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    ts: datetime = Field(default_factory=utcnow)
    props: Dict[str, Any] = Field(default_factory=dict)
    device: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = Field(default=None, max_length=128)

    class Settings:
        name = "analytics_events"
        indexes = [
            IndexModel([("name", ASCENDING), ("ts", DESCENDING)]),
            IndexModel([("user_id", ASCENDING), ("ts", DESCENDING)]),
            IndexModel([("anonymous_id", ASCENDING), ("ts", DESCENDING)]),
        ]


class OfflineDownloadRecord(BaseDoc):
    user_id: PydanticObjectId
    content_type: str = Field(min_length=1, max_length=32)
    content_id: str = Field(min_length=1, max_length=128)
    device_id: Optional[str] = Field(default=None, max_length=128)
    downloaded_at: datetime = Field(default_factory=utcnow)
    meta: Dict[str, Any] = Field(default_factory=dict)

    class Settings:
        name = "offline_download_records"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("downloaded_at", DESCENDING)]),
            IndexModel([("user_id", ASCENDING), ("content_type", ASCENDING), ("content_id", ASCENDING)]),
        ]
