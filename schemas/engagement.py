from __future__ import annotations
from pydantic import BaseModel, Field

from datetime import datetime
from typing import Any, Dict, List, Optional

class PushSendIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=400)
    data: Dict[str, Any] = Field(default_factory=dict)


class PushSendOut(BaseModel):
    sent: int
    failed: int
    results: List[Dict[str, Any]] = Field(default_factory=list)


class StreakRunOut(BaseModel):
    processed_users: int
    sent_users: int
    skipped_not_due: int
    skipped_has_activity: int
    skipped_no_tokens: int
    skipped_disabled: int
    skipped_already_sent: int
    errors: int



class PushRegisterIn(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    platform: str = Field(min_length=1, max_length=16)
    token: str = Field(min_length=8, max_length=4096)
    device_id: Optional[str] = Field(default=None, max_length=128)
    locale: Optional[str] = Field(default=None, max_length=16)
    timezone: Optional[str] = Field(default=None, max_length=64)
    app_version: Optional[str] = Field(default=None, max_length=32)


class PushRegisterOut(BaseModel):
    status: str
    token_id: str


class PushUnregisterIn(BaseModel):
    token: str = Field(min_length=8, max_length=4096)


class PushTokenOut(BaseModel):
    id: str
    provider: str
    platform: str
    token: str
    device_id: Optional[str] = None
    locale: Optional[str] = None
    timezone: Optional[str] = None
    app_version: Optional[str] = None
    last_used_at: datetime


class PushTokensOut(BaseModel):
    items: List[PushTokenOut] = Field(default_factory=list)


class ReminderIn(BaseModel):
    type: str = Field(min_length=1, max_length=32)
    enabled: bool = True
    timezone: str = Field(default="UTC", max_length=64)
    time_hhmm: str = Field(min_length=4, max_length=5)
    weekdays: List[int] = Field(default_factory=list)
    snooze_minutes: Optional[int] = Field(default=None, ge=1, le=240)
    sound: Optional[str] = Field(default=None, max_length=64)
    payload: Dict[str, Any] = Field(default_factory=dict)


class ReminderOut(BaseModel):
    id: str
    type: str
    enabled: bool
    timezone: str
    time_hhmm: str
    weekdays: List[int]
    snooze_minutes: Optional[int] = None
    sound: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class RemindersOut(BaseModel):
    items: List[ReminderOut] = Field(default_factory=list)


class AnalyticsEventIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    ts: Optional[datetime] = None
    props: Dict[str, Any] = Field(default_factory=dict)
    device: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = Field(default=None, max_length=128)
    anonymous_id: Optional[str] = Field(default=None, max_length=128)


class AnalyticsBatchIn(BaseModel):
    events: List[AnalyticsEventIn] = Field(min_length=1, max_length=50)


class AnalyticsIngestOut(BaseModel):
    status: str
    accepted: int


class OfflineEntitlementOut(BaseModel):
    is_premium: bool
    in_grace: bool
    expires_at: Optional[datetime] = None
    grace_until: Optional[datetime] = None
    can_download: bool


class OfflineAuthorizeIn(BaseModel):
    content_type: str = Field(min_length=1, max_length=32)
    content_id: str = Field(min_length=1, max_length=128)
    device_id: Optional[str] = Field(default=None, max_length=128)
    meta: Dict[str, Any] = Field(default_factory=dict)


class OfflineAuthorizeOut(BaseModel):
    can_download: bool
    until: Optional[datetime] = None


class OfflineReportIn(BaseModel):
    device_id: Optional[str] = Field(default=None, max_length=128)
    items: List[OfflineAuthorizeIn] = Field(default_factory=list)


class ReminderUpdateIn(BaseModel):
    type: Optional[str] = Field(default=None, min_length=1, max_length=32)
    enabled: Optional[bool] = None
    timezone: Optional[str] = Field(default=None, max_length=64)
    time_hhmm: Optional[str] = Field(default=None, min_length=4, max_length=5)
    weekdays: Optional[List[int]] = None
    snooze_minutes: Optional[int] = Field(default=None, ge=1, le=240)
    sound: Optional[str] = Field(default=None, max_length=64)
    payload: Optional[Dict[str, Any]] = None


class DeleteOut(BaseModel):
    status: str