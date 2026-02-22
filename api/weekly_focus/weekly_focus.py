from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from api.auth.config import get_current_user
from models.workouts import WorkoutRun
from models import MeditationRun
from schemas.weekly_focus import WeeklyFocusOut, WeeklyFocusBreakdownOut, ActivityBreakdownOut, DayPointsOut

router = APIRouter(tags=["weekly-focus"])

WEEKLY_GOAL_POINTS = 50
POINTS_WORKOUT = 10
POINTS_YOGA = 10
POINTS_MEDITATION = 5


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def week_bounds_utc(tz_name: Optional[str]) -> tuple[datetime, datetime, str]:
    try:
        tz = ZoneInfo(tz_name or "UTC")
        tz_used = tz_name or "UTC"
    except Exception:
        tz = ZoneInfo("UTC")
        tz_used = "UTC"

    now_local = ensure_aware_utc(utcnow()).astimezone(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now_local.weekday())
    # Only current week up to "now" (Monday -> today), not future days.
    end_local = now_local

    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc, tz_used


def require_auth(user):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/weekly-focus", response_model=WeeklyFocusOut)
async def get_weekly_focus(current_user=Depends(get_current_user)):
    require_auth(current_user)

    tz_name = getattr(current_user, "timezone", None) or "UTC"
    start_utc, end_utc, tz_used = week_bounds_utc(tz_name)

    workout_runs = await WorkoutRun.find(
        {
            "user_id": current_user.id,
            "completed_at": {"$ne": None, "$gte": start_utc, "$lt": end_utc},
        }
    ).to_list()

    meditation_runs = await MeditationRun.find(
        {
            "user_id": current_user.id,
            "completed_at": {"$ne": None, "$gte": start_utc, "$lt": end_utc},
        }
    ).to_list()

    workouts_count = len(workout_runs)

    yoga_runs = [r for r in meditation_runs if (getattr(r, "type", "") or "").lower() == "yoga"]
    meditation_only_runs = [r for r in meditation_runs if (getattr(r, "type", "") or "").lower() == "meditation"]

    yoga_count = len(yoga_runs)
    meditation_count = len(meditation_only_runs)

    points_workouts = workouts_count * POINTS_WORKOUT
    points_yoga = yoga_count * POINTS_YOGA
    points_meditation = meditation_count * POINTS_MEDITATION

    total_points = points_workouts + points_yoga + points_meditation
    goal = WEEKLY_GOAL_POINTS
    remaining = max(0, goal - total_points)
    progress = min(1.0, total_points / goal) if goal > 0 else 0.0

    try:
        tz = ZoneInfo(tz_used)
    except Exception:
        tz = ZoneInfo("UTC")
        tz_used = "UTC"

    day_points: Dict[str, int] = {}
    day_workouts: Dict[str, int] = {}
    day_yoga: Dict[str, int] = {}
    day_meditation: Dict[str, int] = {}

    for r in workout_runs:
        ca = getattr(r, "completed_at", None)
        if not ca:
            continue
        ca_utc = ensure_aware_utc(ca)
        day_str = ca_utc.astimezone(tz).date().isoformat()

        day_workouts[day_str] = day_workouts.get(day_str, 0) + 1
        day_points[day_str] = day_points.get(day_str, 0) + POINTS_WORKOUT

    for r in meditation_runs:
        ca = getattr(r, "completed_at", None)
        if not ca:
            continue
        ca_utc = ensure_aware_utc(ca)
        day_str = ca_utc.astimezone(tz).date().isoformat()

        t = (getattr(r, "type", "") or "").lower()

        if t == "yoga":
            pts = POINTS_YOGA
            day_yoga[day_str] = day_yoga.get(day_str, 0) + 1
        elif t == "meditation":
            pts = POINTS_MEDITATION
            day_meditation[day_str] = day_meditation.get(day_str, 0) + 1
        else:
            pts = 0

        day_points[day_str] = day_points.get(day_str, 0) + pts

    days: List[DayPointsOut] = []
    start_local_date = ensure_aware_utc(start_utc).astimezone(tz).date()
    today_local = ensure_aware_utc(utcnow()).astimezone(tz).date()
    active_dates = set()

    days_count = (today_local - start_local_date).days + 1
    for i in range(max(0, days_count)):
        di_date = start_local_date + timedelta(days=i)
        di = di_date.isoformat()
        pts = day_points.get(di, 0)
        if pts > 0:
            active_dates.add(di_date)
        days.append(
            DayPointsOut(
                date=di,
                points=pts,
                workouts=day_workouts.get(di, 0),
                yoga=day_yoga.get(di, 0),
                meditation=day_meditation.get(di, 0),
            )
        )

    streak_days = 0
    d = today_local
    while d >= start_local_date and d in active_dates:
        streak_days += 1
        d = d - timedelta(days=1)

    return WeeklyFocusOut(
        week_start_utc=start_utc.replace(tzinfo=timezone.utc).isoformat(),
        week_end_utc=end_utc.replace(tzinfo=timezone.utc).isoformat(),
        timezone=tz_used,
        goal_points=goal,
        total_points=total_points,
        remaining_points=remaining,
        progress=progress,
        streak_days=streak_days,
        breakdown=WeeklyFocusBreakdownOut(
            workouts=ActivityBreakdownOut(count=workouts_count, points=points_workouts),
            yoga=ActivityBreakdownOut(count=yoga_count, points=points_yoga),
            meditation=ActivityBreakdownOut(count=meditation_count, points=points_meditation),
        ),
        days=days,
    )
