from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie.odm.fields import PydanticObjectId
from pydantic import EmailStr, Field
from pymongo import IndexModel, ASCENDING, DESCENDING

from .base import BaseDoc


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OAuthAccount(BaseDoc):
    user_id: PydanticObjectId
    provider: str
    provider_user_id: str
    email: Optional[EmailStr] = None

    class Settings:
        name = "oauth_accounts"
        indexes = [
            IndexModel([("user_id", ASCENDING)]),
            IndexModel([("provider", ASCENDING), ("provider_user_id", ASCENDING)], unique=True),
        ]


class AuthSession(BaseDoc):
    user_id: PydanticObjectId
    refresh_token_hash: str

    device_id: Optional[str] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    revoked_at: Optional[datetime] = None

    class Settings:
        name = "auth_sessions"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("expires_at", DESCENDING)]),
            IndexModel([("refresh_token_hash", ASCENDING)], unique=True),
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),
        ]


class EmailOTP(BaseDoc):
    email: EmailStr
    purpose: str
    code_hash: str

    attempts: int = 0
    used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime

    class Settings:
        name = "email_otps"
        indexes = [
            IndexModel([("email", ASCENDING), ("purpose", ASCENDING)]),
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),
        ]
