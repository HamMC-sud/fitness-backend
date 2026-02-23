from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

from models.enums import HealthProvider


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_local_date(recorded_at: datetime, tz_name: Optional[str]) -> date:
    ts_utc = _to_utc(recorded_at)
    if tz_name:
        try:
            return ts_utc.astimezone(ZoneInfo(tz_name)).date()
        except Exception:
            return ts_utc.date()
    return ts_utc.date()


class HealthIntegrationStateOut(BaseModel):
    connected: bool = False


class HealthIntegrationsOut(BaseModel):
    appleHealth: HealthIntegrationStateOut
    googleFit: HealthIntegrationStateOut


class HealthIntegrationToggleIn(BaseModel):
    provider: HealthProvider
    connected: bool
    external_account_id: Optional[str] = Field(default=None, max_length=128)
    meta: Dict[str, Any] = Field(default_factory=dict)


class HealthIntegrationToggleOut(BaseModel):
    provider: HealthProvider
    connected: bool
    connected_at: Optional[datetime] = None
    updated_at: datetime


class HealthStepsIn(BaseModel):
    provider: HealthProvider
    steps: int = Field(ge=0)
    recorded_at: Optional[datetime] = None
    date: Optional[date] = None
    timezone: Optional[str] = Field(default=None, max_length=64)
    meta: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_time_input(self):
        if self.recorded_at is None and self.date is None:
            raise ValueError("Either recorded_at or date is required")
        return self

    def resolved_date(self) -> date:
        if self.date is not None:
            return self.date
        return _to_local_date(self.recorded_at, self.timezone)

    def normalized_recorded_at(self) -> Optional[datetime]:
        if self.recorded_at is None:
            return None
        return _to_utc(self.recorded_at)


class HealthStepsOut(BaseModel):
    provider: HealthProvider
    date: date
    steps: int
    recorded_at: Optional[datetime] = None
    timezone: Optional[str] = None
    updated_at: datetime
