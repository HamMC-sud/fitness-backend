from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator


class NotificationHistoryCreateIn(BaseModel):
    type: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    subtitle: Optional[str] = Field(default=None, max_length=255)
    body: Optional[str] = None
    source: str = Field(default="system", max_length=64)
    deep_link: Optional[str] = Field(default=None, max_length=1024)
    image_url: Optional[str] = Field(default=None, max_length=2048)
    priority: str = Field(default="normal", max_length=32)
    meta: Dict[str, Any] = Field(default_factory=dict)
    created_at_client: Optional[datetime] = None

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
    user_id: str
    type: str
    title: str
    subtitle: Optional[str] = None
    body: Optional[str] = None
    source: str
    deep_link: Optional[str] = None
    image_url: Optional[str] = None
    priority: str
    meta: Dict[str, Any] = Field(default_factory=dict)
    is_read: bool
    created_at: datetime
    updated_at: datetime


class NotificationHistoryPaginationOut(BaseModel):
    limit: int
    next_cursor: Optional[str] = None
    has_more: bool


class NotificationHistoryListOut(BaseModel):
    items: List[NotificationHistoryItemOut] = Field(default_factory=list)
    pagination: NotificationHistoryPaginationOut
    unread_count: int


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
