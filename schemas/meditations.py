from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class I18nList(BaseModel):
    ru: List[str] = Field(default_factory=list)
    en: List[str] = Field(default_factory=list)


class MeditationItemOut(BaseModel):
    id: str
    type: str
    title: I18nList
    description: Optional[I18nList] = None
    duration_minutes: int
    media: Dict[str, Optional[str]]
    tags: List[str]
    status: str
    created_at: datetime
    updated_at: datetime


class MeditationListOut(BaseModel):
    items: List[MeditationItemOut]
    total: int
    skip: int
    limit: int


class MeditationCreateIn(BaseModel):
    type: str
    title: I18nList
    description: Optional[I18nList] = None
    duration_minutes: int = Field(ge=1, le=180)
    media: Dict[str, Optional[str]] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    status: str = "active"


class MeditationUpdateIn(BaseModel):
    type: Optional[str] = None
    title: Optional[I18nList] = None
    description: Optional[I18nList] = None
    duration_minutes: Optional[int] = Field(default=None, ge=1, le=180)
    media: Optional[Dict[str, Optional[str]]] = None
    tags: Optional[List[str]] = None
    status: Optional[str] = None


class MeditationCompleteIn(BaseModel):
    seconds_done: Optional[int] = Field(default=None, ge=0, le=60 * 60 * 6)


class MeditationRunOut(BaseModel):
    id: str
    meditation_id: str
    type: str
    completed_at: datetime
    seconds_done: int
    points: int


class MeditationRunListOut(BaseModel):
    items: List[MeditationRunOut]
    total: int
    skip: int
    limit: int
