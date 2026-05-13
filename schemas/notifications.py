from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


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
