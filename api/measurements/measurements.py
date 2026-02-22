from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from api.auth.config import get_current_user
from models import AnalyticsEvent, BodyMeasurement, UserAchievement
from models.workouts import WorkoutRun, UserWorkout
from schemas.measurements import (
    CompletedAchievementOut,
    DayActivityOut,
    DayExercisesOut,
    MeasurementItemOut,
    MeasurementSaveIn,
    MeasurementSummaryOut,
)

router = APIRouter(tags=["measurements"])


def user_tz_or_utc(tz_name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def day_bounds_utc(local_day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start_local = datetime.combine(local_day, time.min).replace(tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def require_auth(user):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _build_measurement_summary(current_user, anchor_day: Optional[date] = None) -> MeasurementSummaryOut:
    tz = user_tz_or_utc(getattr(current_user, "timezone", None))

    # Ignore malformed seed docs that may miss required `date`.
    measurements = await BodyMeasurement.find(
        {
            "user_id": current_user.id,
            "date": {"$exists": True, "$ne": None},
        }
    ).sort("date").to_list()

    # Keep one record per day. Prefer non-null weight and latest updated record.
    by_day_doc = {}
    for m in measurements:
        day_value = getattr(m, "date", None)
        if isinstance(day_value, datetime):
            day_value = day_value.date()
        if not isinstance(day_value, date):
            continue

        prev = by_day_doc.get(day_value)
        if prev is None:
            by_day_doc[day_value] = m
            continue

        prev_weight = getattr(prev, "weight_kg", None)
        cur_weight = getattr(m, "weight_kg", None)
        if prev_weight is None and cur_weight is not None:
            by_day_doc[day_value] = m
            continue

        prev_updated = getattr(prev, "updated_at", None) or getattr(prev, "created_at", None)
        cur_updated = getattr(m, "updated_at", None) or getattr(m, "created_at", None)
        if cur_updated and (not prev_updated or cur_updated > prev_updated):
            by_day_doc[day_value] = m

    unique_days = sorted(by_day_doc.keys())

    measurement_items = [
        MeasurementItemOut(day=d, weight_kg=getattr(by_day_doc[d], "weight_kg", None))
        for d in unique_days
        if getattr(by_day_doc[d], "weight_kg", None) is not None
    ]

    by_days: List[DayActivityOut] = []
    exercises_by_day: List[DayExercisesOut] = []

    total_minutes = 0
    total_kkal = 0.0
    total_steps = 0

    for day in unique_days:
        start_utc, end_utc = day_bounds_utc(day, tz)

        runs = await WorkoutRun.find(
            {
                "user_id": current_user.id,
                "completed_at": {"$ne": None, "$gte": start_utc, "$lt": end_utc},
            }
        ).to_list()

        day_seconds = int(sum((getattr(r, "total_seconds", 0) or 0) for r in runs))
        day_minutes = day_seconds // 60
        day_kkal = float(sum((getattr(r, "calories_estimated", 0) or 0) for r in runs))

        events = await AnalyticsEvent.find(
            {
                "user_id": current_user.id,
                "ts": {
                    "$gte": start_utc.replace(tzinfo=timezone.utc),
                    "$lt": end_utc.replace(tzinfo=timezone.utc),
                },
            }
        ).to_list()

        day_steps = 0
        for e in events:
            props = getattr(e, "props", {}) or {}
            raw = props.get("steps", props.get("step_count", 0))
            try:
                v = int(raw)
            except Exception:
                v = 0
            if v > 0:
                day_steps += v

        by_days.append(
            DayActivityOut(
                day=day,
                minutes=day_minutes,
                kkal=day_kkal,
                steps=day_steps,
            )
        )

        total_minutes += day_minutes
        total_kkal += day_kkal
        total_steps += day_steps

        for run in runs:
            workout_name = "Workout"
            workout_type = str(getattr(run, "source", "workout") or "workout")
            workout_points = 10

            workout_ref_id = getattr(run, "workout_ref_id", None)
            if workout_ref_id:
                w = await UserWorkout.get(workout_ref_id)
                if w and getattr(w, "title", None):
                    workout_name = str(w.title)
            elif workout_type:
                workout_name = workout_type.capitalize()

            exercises_by_day.append(
                DayExercisesOut(
                    date=day,
                    workout_name=workout_name,
                    workout_type=workout_type,
                    points=workout_points,
                )
            )

    all_achievements = await UserAchievement.find(
        UserAchievement.user_id == current_user.id,
    ).sort("-updated_at").to_list()
    achieved_docs = [
        a for a in all_achievements
        if float(getattr(a, "progress", 0) or 0) == float(getattr(a, "max_progress", 100) or 100)
    ]

    completed_achievements = [
        CompletedAchievementOut(
            name=getattr(a, "name", "achievement"),
            points=int(getattr(a, "points", 0) or 0),
        )
        for a in achieved_docs
    ]

    if anchor_day is None:
        if unique_days:
            anchor_day = unique_days[-1]
        else:
            anchor_day = datetime.now(tz).date()

    return MeasurementSummaryOut(
        measurements=measurement_items,
        totals=DayActivityOut(
            day=anchor_day,
            minutes=total_minutes,
            kkal=float(total_kkal),
            steps=total_steps,
        ),
        by_days=by_days,
        completed_achievements=completed_achievements,
        exercises_by_day=exercises_by_day,
    )


@router.post("/measurements/weight")
async def save_weight(payload: MeasurementSaveIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    existing = await BodyMeasurement.find_one(
        BodyMeasurement.user_id == current_user.id,
        BodyMeasurement.date == payload.day,
    )
    if existing:
        existing.weight_kg = payload.weight_kg
        await existing.save()
    else:
        await BodyMeasurement(
            user_id=current_user.id,
            date=payload.day,
            weight_kg=payload.weight_kg,
        ).insert()

    return {
        "status": "ok",
        "day": payload.day.isoformat(),
        "weight_kg": payload.weight_kg,
    }


@router.get("/measurements/weight", response_model=MeasurementSummaryOut)
async def get_measurements_summary(current_user=Depends(get_current_user)):
    require_auth(current_user)
    return await _build_measurement_summary(current_user)
