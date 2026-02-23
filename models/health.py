from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from .base import BaseDoc
from .enums import HealthProvider


class UserHealthIntegration(BaseDoc):
    user_id: PydanticObjectId
    provider: HealthProvider
    is_connected: bool = False
    connected_at: Optional[datetime] = None
    external_account_id: Optional[str] = Field(default=None, max_length=128)
    meta: Dict[str, Any] = Field(default_factory=dict)

    class Settings:
        name = "user_health_integrations"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("provider", ASCENDING)], unique=True),
            IndexModel([("user_id", ASCENDING), ("updated_at", DESCENDING)]),
        ]


class UserHealthStepDaily(BaseDoc):
    user_id: PydanticObjectId
    provider: HealthProvider
    date: date
    steps: int = Field(ge=0)
    recorded_at: Optional[datetime] = None
    timezone: Optional[str] = Field(default=None, max_length=64)
    meta: Dict[str, Any] = Field(default_factory=dict)

    class Settings:
        name = "user_health_steps_daily"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("provider", ASCENDING), ("date", ASCENDING)], unique=True),
            IndexModel([("user_id", ASCENDING), ("date", DESCENDING)]),
        ]
