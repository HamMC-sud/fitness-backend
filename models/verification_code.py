from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import IndexModel, ASCENDING

def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC



class VerificationCode(Document):
    email: str
    password_hash: str
    social_provider: Optional[str] = None
    social_provider_user_id: Optional[str] = None
    social_email: Optional[str] = None

    code_hash: str
    attempts: int = 0
    verified: bool = False

    created_at: datetime = Field(default_factory=now_utc)
    expires_at: datetime
    last_resend: Optional[datetime] = None

    class Settings:
        name = "verification_codes"
        indexes = [
            IndexModel([("email", ASCENDING)], unique=True),
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),  # TTL auto-delete
            IndexModel([("created_at", ASCENDING)]),
        ]
