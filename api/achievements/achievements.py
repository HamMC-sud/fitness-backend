from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import DuplicateKeyError

from api.auth.config import get_current_user
from models import Achievement, UserAchievement
from schemas.achievements import AchievementProgressListOut, AchievementProgressOut

router = APIRouter(tags=["achievements"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clamp_to_max(progress: float, max_progress: float) -> float:
    return max(0.0, min(float(progress), float(max_progress)))


async def _get_active_catalog() -> List[Achievement]:
    docs = await Achievement.find(Achievement.active == True).to_list()
    docs.sort(key=lambda x: (str(getattr(x, "category", "")), int(getattr(x, "order", 0))))
    return docs


async def _ensure_user_achievements(user_id, catalog_docs: List[Achievement]) -> dict[str, UserAchievement]:
    docs = await UserAchievement.find(UserAchievement.user_id == user_id).to_list()
    by_code = {d.achievement_code: d for d in docs}

    for c in catalog_docs:
        aid = c.achievement_code
        if aid in by_code:
            continue

        doc = UserAchievement(
            user_id=user_id,
            achievement_code=aid,
            category=c.category,
            name=c.name_en,
            logic=c.logic,
            progress=0.0,
            max_progress=float(getattr(c, "max_progress", 100) or 100),
            points=int(getattr(c, "points", 0) or 0),
            unlocked_at=None,
        )
        try:
            await doc.insert()
            by_code[aid] = doc
        except DuplicateKeyError:
            existing = await UserAchievement.find_one(
                UserAchievement.user_id == user_id,
                UserAchievement.achievement_code == aid,
            )
            if existing:
                by_code[aid] = existing

    return by_code


async def _sync_streak_achievements_from_stats(
    current_user,
    catalog_docs: List[Achievement],
    by_code: dict[str, UserAchievement],
) -> None:
    """
    Keep streak achievements in sync with current user streak on read.
    This avoids stale progress values when users open achievements screens.
    """
    try:
        streak_days = int(getattr(getattr(current_user, "stats", None), "streak_days", 0) or 0)
    except Exception:
        streak_days = 0
    streak_days = max(0, streak_days)

    for c in catalog_docs:
        code = str(getattr(c, "achievement_code", "") or "")
        if not code.startswith("str_"):
            continue

        doc = by_code.get(code)
        if not doc:
            continue

        max_progress = float(getattr(c, "max_progress", 100) or 100)
        target_progress = max(0.0, min(float(streak_days), max_progress))
        current_progress = float(getattr(doc, "progress", 0) or 0)
        if target_progress <= current_progress:
            continue

        doc.progress = target_progress
        if target_progress >= max_progress and getattr(doc, "unlocked_at", None) is None:
            doc.unlocked_at = utcnow()
        await doc.save()


def _to_progress_out(catalog: Achievement, user_doc: Optional[UserAchievement]) -> AchievementProgressOut:
    max_progress = float(getattr(catalog, "max_progress", 100) or 100)
    progress = float(getattr(user_doc, "progress", 0) or 0) if user_doc else 0.0
    progress = _clamp_to_max(progress, max_progress)

    return AchievementProgressOut(
        achievement_id=catalog.achievement_code,
        progress=progress,
        max_progress=max_progress,
        points=int(getattr(catalog, "points", 0) or 0),
        date=getattr(user_doc, "unlocked_at", None) if user_doc else None,
    )


@router.get("/achievements/progress", response_model=AchievementProgressListOut)
async def get_achievements_progress(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    catalog_docs = await _get_active_catalog()
    if not catalog_docs:
        return AchievementProgressListOut(items=[])

    by_code = await _ensure_user_achievements(current_user.id, catalog_docs)
    await _sync_streak_achievements_from_stats(current_user, catalog_docs, by_code)
    items = [_to_progress_out(c, by_code.get(c.achievement_code)) for c in catalog_docs]
    return AchievementProgressListOut(items=items)


@router.get("/achievements/progress/{achievement_id}", response_model=AchievementProgressOut)
async def get_achievement_progress(achievement_id: str, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    target = await Achievement.find_one(
        Achievement.achievement_code == achievement_id,
        Achievement.active == True,
    )
    if not target:
        raise HTTPException(status_code=404, detail="Achievement not found")

    by_code = await _ensure_user_achievements(current_user.id, [target])
    await _sync_streak_achievements_from_stats(current_user, [target], by_code)
    return _to_progress_out(target, by_code.get(achievement_id))
