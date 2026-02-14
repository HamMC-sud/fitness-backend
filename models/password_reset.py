from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import EmailStr, Field
from pymongo import IndexModel, ASCENDING


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PasswordReset(Document):
    email: EmailStr
    code_hash: str
    attempts: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    used_at: Optional[datetime] = None

    class Settings:
        name = "password_resets"
        indexes = [
            IndexModel([("email", ASCENDING)]),
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),  # TTL
        ]
