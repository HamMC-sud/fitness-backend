from __future__ import annotations

from typing import Optional

from pydantic import Field
from pymongo import ASCENDING, IndexModel

from .base import BaseDoc


class Achievement(BaseDoc):
    achievement_code: str = Field(min_length=1, max_length=64)
    category: str = Field(min_length=1, max_length=32)
    order: int = Field(default=1, ge=1)

    name_ru: str = ""
    name_en: str = Field(min_length=1, max_length=200)
    description_ru: str = ""
    description_en: str = ""
    logic: Optional[str] = Field(default=None, max_length=1000)

    max_progress: float = Field(default=100, ge=1)
    points: int = Field(default=0, ge=0)
    active: bool = True

    class Settings:
        name = "achievements"
        indexes = [
            IndexModel([("achievement_code", ASCENDING)], unique=True),
            IndexModel([("category", ASCENDING), ("order", ASCENDING)]),
            IndexModel([("active", ASCENDING)]),
        ]
