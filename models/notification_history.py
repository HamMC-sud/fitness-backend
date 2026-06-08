from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import Field, model_validator
from pymongo import ASCENDING, DESCENDING, IndexModel

from .base import BaseDoc
from .content import I18nText


def _notification_id() -> str:
    return f"ntf_{uuid.uuid4().hex}"


class NotificationHistory(BaseDoc):
    id: str = Field(default_factory=_notification_id)
    user_id: PydanticObjectId
    event_key: Optional[str] = Field(default=None, max_length=255)
    type: str = Field(min_length=1, max_length=128)
    title: I18nText
    subtitle: Optional[I18nText] = None
    body: Optional[I18nText] = None
    source: str = Field(default="system", max_length=64)
    deep_link: Optional[str] = Field(default=None, max_length=1024)
    image_url: Optional[str] = Field(default=None, max_length=2048)
    priority: str = Field(default="normal", max_length=32)
    meta: Dict[str, Any] = Field(default_factory=dict)
    is_read: bool = False
    delivered_at: Optional[datetime] = None
    seen_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None

    @staticmethod
    def _coerce_i18n_text(value: Any) -> Optional[I18nText]:
        if value is None:
            return None
        if isinstance(value, I18nText):
            return value
        if isinstance(value, dict):
            return I18nText(**value)
        text = str(value).strip()
        return I18nText(ru=text, en=text)

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        row = dict(data)
        if "title" in row:
            row["title"] = cls._coerce_i18n_text(row.get("title"))
        if "subtitle" in row:
            row["subtitle"] = cls._coerce_i18n_text(row.get("subtitle"))
        if "body" in row:
            row["body"] = cls._coerce_i18n_text(row.get("body"))
        return row

    def model_post_init(self, __context: Any) -> None:
        self.title = self._coerce_i18n_text(self.title) or I18nText()
        self.subtitle = self._coerce_i18n_text(self.subtitle)
        self.body = self._coerce_i18n_text(self.body)

    class Settings:
        name = "notification_history"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("user_id", ASCENDING), ("is_read", ASCENDING)]),
            IndexModel([("user_id", ASCENDING), ("seen_at", ASCENDING)]),
            IndexModel([("user_id", ASCENDING), ("read_at", ASCENDING)]),
            IndexModel([("user_id", ASCENDING), ("_id", ASCENDING)], unique=True),
            IndexModel(
                [("user_id", ASCENDING), ("event_key", ASCENDING)],
                unique=True,
                partialFilterExpression={"event_key": {"$type": "string"}},
            ),
        ]
