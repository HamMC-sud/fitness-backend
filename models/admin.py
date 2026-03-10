from __future__ import annotations

from typing import List

from pydantic import Field
from pymongo import IndexModel, ASCENDING

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
