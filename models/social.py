from __future__ import annotations
from typing import Optional
from beanie.odm.fields import PydanticObjectId
from pydantic import Field
from pymongo import IndexModel, ASCENDING
from .base import BaseDoc


class SocialAccount(BaseDoc):
    provider: str
    provider_user_id: str
    user_id: PydanticObjectId
    email: Optional[str] = None

    class Settings:
        name = "social_accounts"
        indexes = [
            IndexModel([("provider", ASCENDING), ("provider_user_id", ASCENDING)], unique=True),
            IndexModel([("user_id", ASCENDING)]),
        ]
