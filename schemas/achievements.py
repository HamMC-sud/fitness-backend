from __future__ import annotations

from datetime import datetime
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


class UserAchievementIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    progress: float = Field(default=0, ge=0)
    points: int = Field(default=0, ge=0)
    achievement_code: Optional[str] = Field(default=None, min_length=1, max_length=120)


class UserAchievementPatchIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    progress: Optional[float] = Field(default=None, ge=0)
    points: Optional[int] = Field(default=None, ge=0)


class UserAchievementOut(BaseModel):
    id: str
    achievement_code: str
    name: str
    progress: float
    max_progress: float
    points: int
    unlocked_at: Optional[datetime] = None


class UserAchievementsListOut(BaseModel):
    items: List[UserAchievementOut] = Field(default_factory=list)


class AchievementCatalogItemOut(BaseModel):
    id: str
    category: str
    name_ru: str
    name_en: str
    description_ru: str
    description_en: str
    logic: str
    points: int = 50
    progress: float = Field(default=0, ge=0)
    max_progress: float = Field(default=100, ge=1)
    unlocked: bool = False
    unlocked_at: Optional[datetime] = None


class AchievementCategoryOut(BaseModel):
    category: str
    items: List[AchievementCatalogItemOut] = Field(default_factory=list)
    points_per_achievement: int = 50


class AchievementCatalogOut(BaseModel):
    categories: List[AchievementCategoryOut] = Field(default_factory=list)
    points_per_achievement: int = 50


class AchievementPushIn(BaseModel):
    achievement_id: str = Field(min_length=1, max_length=120)
    progress: float = Field(ge=0)


class AchievementPushOut(BaseModel):
    status: str
    achievement: AchievementCatalogItemOut


class AchievementPushBatchItemIn(BaseModel):
    achievement_id: str = Field(min_length=1, max_length=120)
    progress: float = Field(ge=0)


class AchievementPushBatchIn(BaseModel):
    items: List[AchievementPushBatchItemIn] = Field(min_length=1, max_length=200)


class AchievementPushBatchOut(BaseModel):
    status: str
    achievements: List[AchievementCatalogItemOut] = Field(default_factory=list)


class AchievementProgressOut(BaseModel):
    achievement_id: str
    progress: float = Field(ge=0)
    max_progress: float = Field(ge=1)
    points: int = Field(ge=0)
    date: Optional[datetime] = None


class AchievementProgressListOut(BaseModel):
    items: List[AchievementProgressOut] = Field(default_factory=list)
