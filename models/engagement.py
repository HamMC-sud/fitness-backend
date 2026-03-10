from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from .base import BaseDoc


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
