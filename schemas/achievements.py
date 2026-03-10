from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class AchievementProgressOut(BaseModel):
    achievement_id: str
    progress: float = Field(ge=0)
    max_progress: float = Field(ge=1)
    points: int = Field(ge=0)
    date: Optional[datetime] = None


class AchievementProgressListOut(BaseModel):
    items: List[AchievementProgressOut] = Field(default_factory=list)
