from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from .base import BaseDoc


def _notification_id() -> str:
    return f"ntf_{uuid.uuid4().hex}"


class NotificationHistory(BaseDoc):
    id: str = Field(default_factory=_notification_id)
    user_id: PydanticObjectId
    type: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    subtitle: Optional[str] = Field(default=None, max_length=255)
    body: Optional[str] = None
    source: str = Field(default="system", max_length=64)
    deep_link: Optional[str] = Field(default=None, max_length=1024)
    image_url: Optional[str] = Field(default=None, max_length=2048)
    priority: str = Field(default="normal", max_length=32)
    meta: Dict[str, Any] = Field(default_factory=dict)
    is_read: bool = False

    class Settings:
        name = "notification_history"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("user_id", ASCENDING), ("is_read", ASCENDING)]),
        ]
