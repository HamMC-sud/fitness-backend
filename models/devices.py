from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import Field
from pymongo import IndexModel, ASCENDING

from .base import BaseDoc, utcnow
from .enums import Platform, Region, PushProvider


class Device(BaseDoc):
    user_id: PydanticObjectId

    platform: Platform
    region: Region
    push_provider: PushProvider

    push_token: str
    app_version: str
    device_model: Optional[str] = None
    last_used_at: datetime = Field(default_factory=utcnow)
    
    class Settings:
        name = "devices"
        indexes = [
            IndexModel([("user_id", ASCENDING)]),
            IndexModel([("push_token", ASCENDING)], unique=True),
        ]
