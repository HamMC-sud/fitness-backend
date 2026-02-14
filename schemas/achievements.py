from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field


class I18nText(BaseModel):
    ru: str
    en: str


class AchievementItemOut(BaseModel):
    key: str
    category: str
    title: I18nText
    description: I18nText
    unit: str
    current: float
    target: float
    progress: float = Field(ge=0.0, le=1.0)
    unlocked: bool
    unlocked_at: Optional[datetime] = None


class AchievementsOut(BaseModel):
    items: List[AchievementItemOut]
    totals: dict
