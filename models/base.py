from __future__ import annotations

from datetime import datetime, timezone
from beanie import Document
from pydantic import Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BaseDoc(Document):
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    async def touch(self) -> None:
        self.updated_at = utcnow()
        await self.save()
