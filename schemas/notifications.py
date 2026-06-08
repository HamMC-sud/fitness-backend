from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator
from models.content import I18nText


def _to_i18n_text(value: Any) -> Optional[I18nText]:
    if value is None:
        return None
    if isinstance(value, I18nText):
        return value
    if isinstance(value, dict):
        return I18nText(**value)
    text = str(value).strip()
    return I18nText(ru=text, en=text)


def _pick_i18n_text(value: Optional[I18nText], language: str) -> Optional[str]:
    if value is None:
        return None
    lang = "ru" if str(language or "").lower().startswith("ru") else "en"
    localized = getattr(value, lang, "") or getattr(value, "en", "") or getattr(value, "ru", "")
    return localized or None


class NotificationHistoryCreateIn(BaseModel):
    notification_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    event_key: Optional[str] = Field(default=None, min_length=1, max_length=255)
    type: str = Field(min_length=1, max_length=128)
    title: I18nText
    subtitle: Optional[I18nText] = None
    body: Optional[I18nText] = None
    source: str = Field(default="system", max_length=64)
    deep_link: Optional[str] = Field(default=None, max_length=1024)
    image_url: Optional[str] = Field(default=None, max_length=2048)
    priority: str = Field(default="normal", max_length=32)
    meta: Dict[str, Any] = Field(default_factory=dict)
    created_at_client: Optional[datetime] = None

    @field_validator("title", "subtitle", "body", mode="before")
    @classmethod
    def _normalize_i18n_text(cls, value: Any) -> Optional[I18nText]:
        return _to_i18n_text(value)

    @field_validator("title")
    @classmethod
    def _validate_title_not_empty(cls, value: I18nText) -> I18nText:
        if not (value.ru or value.en):
            raise ValueError("title must not be empty")
        return value

    @field_validator("meta", mode="before")
    @classmethod
    def _ensure_meta_object(cls, value: Any) -> Dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("meta must be an object")
        return value


class NotificationHistoryItemOut(BaseModel):
    id: str
    notification_id: str
    user_id: str
    event_key: Optional[str] = None
    type: str
    title: str
    title_i18n: I18nText
    subtitle: Optional[str] = None
    subtitle_i18n: Optional[I18nText] = None
    body: Optional[str] = None
    body_i18n: Optional[I18nText] = None
    source: str
    deep_link: Optional[str] = None
    image_url: Optional[str] = None
    priority: str
    meta: Dict[str, Any] = Field(default_factory=dict)
    is_read: bool
    is_seen: bool
    is_new: bool
    delivered_at: Optional[datetime] = None
    seen_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_notification(cls, item: Any, language: str = "en") -> "NotificationHistoryItemOut":
        title_i18n = _to_i18n_text(getattr(item, "title", None)) or I18nText()
        subtitle_i18n = _to_i18n_text(getattr(item, "subtitle", None))
        body_i18n = _to_i18n_text(getattr(item, "body", None))
        return cls(
            id=str(item.id),
            notification_id=str(item.id),
            user_id=str(item.user_id),
            event_key=getattr(item, "event_key", None),
            type=item.type,
            title=_pick_i18n_text(title_i18n, language) or "",
            title_i18n=title_i18n,
            subtitle=_pick_i18n_text(subtitle_i18n, language),
            subtitle_i18n=subtitle_i18n,
            body=_pick_i18n_text(body_i18n, language),
            body_i18n=body_i18n,
            source=item.source,
            deep_link=item.deep_link,
            image_url=item.image_url,
            priority=item.priority,
            meta=item.meta or {},
            is_read=bool(item.read_at or item.is_read),
            is_seen=bool(item.seen_at),
            is_new=not bool(item.seen_at or item.read_at or item.dismissed_at),
            delivered_at=getattr(item, "delivered_at", None),
            seen_at=getattr(item, "seen_at", None),
            read_at=getattr(item, "read_at", None),
            dismissed_at=getattr(item, "dismissed_at", None),
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class NotificationHistoryPaginationOut(BaseModel):
    limit: int
    next_cursor: Optional[str] = None
    has_more: bool


class NotificationHistoryListOut(BaseModel):
    items: List[NotificationHistoryItemOut] = Field(default_factory=list)
    pagination: NotificationHistoryPaginationOut
    unread_count: int
    new_count: int


class NotificationCountOut(BaseModel):
    unread_count: int
    new_count: int


class NotificationStatePatchIn(BaseModel):
    delivered: Optional[bool] = None
    seen: Optional[bool] = None
    read: Optional[bool] = None
    dismissed: Optional[bool] = None


class ReminderSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    days_of_week: Optional[List[int]] = None
    time: Optional[str] = None
    timezone: Optional[str] = None
    notification_permission: Optional[bool] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, value: Any):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "days_of_week" not in data:
            alias_value = data.get("days")
            if alias_value is None:
                alias_value = data.get("weekdays")
            data["days_of_week"] = alias_value
        if "time" not in data and "reminder_time" in data:
            data["time"] = data.get("reminder_time")
        return data

    @field_validator("days_of_week")
    @classmethod
    def _validate_days_of_week(cls, value: Optional[List[int]]) -> Optional[List[int]]:
        if value is None:
            return None
        normalized: list[int] = []
        for raw in value:
            day = int(raw)
            if day < 0 or day > 6:
                raise ValueError("days_of_week items must be in range 0..6")
            if day not in normalized:
                normalized.append(day)
        return sorted(normalized)

    @field_validator("time")
    @classmethod
    def _validate_time(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        parts = text.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("time must be in HH:MM format")
        hour = int(parts[0])
        minute = int(parts[1])
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("time must be in HH:MM format")
        return f"{hour:02d}:{minute:02d}"

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            raise ValueError("timezone must not be empty")
        try:
            ZoneInfo(text)
        except Exception as exc:
            raise ValueError("Invalid timezone") from exc
        return text


class ReminderSettingsOut(BaseModel):
    enabled: bool
    days_of_week: List[int] = Field(default_factory=list)
    time: str
    timezone: str
    notification_permission: Optional[bool] = None
    updated_at: Optional[datetime] = None
