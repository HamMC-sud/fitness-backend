from __future__ import annotations

from typing import Dict, List

from beanie.odm.fields import PydanticObjectId
from pydantic import Field
from pymongo import IndexModel, ASCENDING, DESCENDING

from .base import BaseDoc


class AdminUser(BaseDoc):
    email: str
    password_hash: str
    roles: List[str] = Field(default_factory=list)

    class Settings:
        name = "admin_users"
        indexes = [
            IndexModel([("email", ASCENDING)], unique=True),
        ]


class AdminAuditLog(BaseDoc):
    admin_id: PydanticObjectId
    action: str
    target: Dict[str, object] = Field(default_factory=dict)
    meta: Dict[str, object] = Field(default_factory=dict)

    class Settings:
        name = "admin_audit_logs"
        indexes = [
            IndexModel([("admin_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("action", ASCENDING), ("created_at", DESCENDING)]),
        ]
