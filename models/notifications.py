from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import Field
from pymongo import IndexModel, ASCENDING, DESCENDING

from .base import BaseDoc
from .enums import NotificationType


class Notification(BaseDoc):
    user_id: PydanticObjectId
    type: NotificationType

    title: Dict[str, str] = Field(default_factory=dict)
    body: Dict[str, str] = Field(default_factory=dict)
    data: Dict[str, object] = Field(default_factory=dict)

    sent_at: Optional[datetime] = None
    read_at: Optional[datetime] = None

    class Settings:
        name = "notifications"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("read_at", ASCENDING)]),
        ]


class ReminderSettings(BaseDoc):
    user_id: PydanticObjectId

    workout: Dict[str, object] = Field(default_factory=dict)
    meditation: Dict[str, object] = Field(default_factory=dict)
    weight: Dict[str, object] = Field(default_factory=dict)
    save_streak: Dict[str, object] = Field(default_factory=dict)

    class Settings:
        name = "reminder_settings"
        indexes = [
            IndexModel([("user_id", ASCENDING)], unique=True),
        ]
