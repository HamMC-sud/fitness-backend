from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from api.auth.config import get_current_user
from schemas.achievements import AchievementsOut, AchievementItemOut, I18nText
from models.workouts import WorkoutRun


router = APIRouter(tags=["achievements"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_user_tz(user) -> ZoneInfo:
    tz_name = getattr(user, "timezone", None) or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def clamp_progress(current: float, target: float) -> float:
    if target <= 0:
        return 0.0
    return max(0.0, min(1.0, current / target))


def compute_streak(dates: set) -> int:
    if not dates:
        return 0
    last_day = max(dates)
    streak = 1
    while (last_day - timedelta(days=streak)) in dates:
        streak += 1
    return streak


def nth_completed_at(sorted_completed: List[datetime], n: int) -> Optional[datetime]:
    if n <= 0 or len(sorted_completed) < n:
        return None
    return sorted_completed[n - 1]


def milestone_item(
    key: str,
    category: str,
    title_ru: str,
    title_en: str,
    desc_ru: str,
    desc_en: str,
    unit: str,
    current: float,
    target: float,
    unlocked_at: Optional[datetime],
) -> AchievementItemOut:
    unlocked = current >= target
    return AchievementItemOut(
        key=key,
        category=category,
        title=I18nText(ru=title_ru, en=title_en),
        description=I18nText(ru=desc_ru, en=desc_en),
        unit=unit,
        current=current,
        target=target,
        progress=clamp_progress(current, target),
        unlocked=unlocked,
        unlocked_at=unlocked_at if unlocked else None,
    )


@router.get("/achievements", response_model=AchievementsOut)
async def get_achievements(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    tz = get_user_tz(current_user)

    runs = await WorkoutRun.find({
        "user_id": current_user.id,
        "completed_at": {"$ne": None},
    }).to_list()

    completed_ats: List[datetime] = []
    total_seconds = 0
    total_calories = 0.0
    days_set = set()

    for r in runs:
        ca = getattr(r, "completed_at", None)
        if ca:
            ca_utc = ensure_aware_utc(ca)
            completed_ats.append(ca_utc)
            days_set.add(ca_utc.astimezone(tz).date())

        ts = getattr(r, "total_seconds", None)
        if isinstance(ts, int) and ts >= 0:
            total_seconds += ts

        kcal = getattr(r, "calories_estimated", None)
        if isinstance(kcal, (int, float)) and kcal >= 0:
            total_calories += float(kcal)

    completed_ats.sort()
    workouts_total = len(completed_ats)
    streak_days = compute_streak(days_set)
    hours_total = total_seconds / 3600.0

    items: List[AchievementItemOut] = []

    workout_targets = [1, 10, 50, 100, 500]
    for t in workout_targets:
        ua = nth_completed_at(completed_ats, t)
        items.append(
            milestone_item(
                key=f"workouts_{t}",
                category="workouts",
                title_ru=f"{t} тренировк(а/и)",
                title_en=f"{t} workouts",
                desc_ru=f"Завершите {t} тренировк(а/и)",
                desc_en=f"Complete {t} workouts",
                unit="workouts",
                current=float(workouts_total),
                target=float(t),
                unlocked_at=ua,
            )
        )

    streak_targets = [7, 30, 100]
    last_completed_at = completed_ats[-1] if completed_ats else None
    for t in streak_targets:
        items.append(
            milestone_item(
                key=f"streak_{t}",
                category="streak",
                title_ru=f"Серия {t} дней",
                title_en=f"{t}-day streak",
                desc_ru=f"Тренируйтесь {t} дней подряд",
                desc_en=f"Train {t} days in a row",
                unit="days",
                current=float(streak_days),
                target=float(t),
                unlocked_at=last_completed_at,
            )
        )

    calorie_targets = [1000, 5000, 10000]
    for t in calorie_targets:
        items.append(
            milestone_item(
                key=f"calories_{t}",
                category="calories",
                title_ru=f"{t} ккал",
                title_en=f"{t} kcal",
                desc_ru=f"Сожгите {t} ккал",
                desc_en=f"Burn {t} kcal",
                unit="kcal",
                current=float(total_calories),
                target=float(t),
                unlocked_at=last_completed_at,
            )
        )

    hour_targets = [10, 50, 100]
    for t in hour_targets:
        items.append(
            milestone_item(
                key=f"time_{t}h",
                category="time",
                title_ru=f"{t} часов",
                title_en=f"{t} hours",
                desc_ru=f"Потренируйтесь суммарно {t} часов",
                desc_en=f"Train for {t} total hours",
                unit="hours",
                current=float(hours_total),
                target=float(t),
                unlocked_at=last_completed_at,
            )
        )

    return AchievementsOut(
        items=items,
        totals={
            "workouts_total": workouts_total,
            "streak_days": streak_days,
            "total_seconds": total_seconds,
            "total_hours": hours_total,
            "total_calories": total_calories,
        },
    )
