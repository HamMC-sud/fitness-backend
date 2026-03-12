from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
import math
from copy import deepcopy

from api.auth.config import get_current_user
from models import UserWorkout, WorkoutRun, Exercise, UserAchievement
from models.enums import ExerciseMode
from models.workouts import Feedback  # ✅ enum
from schemas.workout import (
    WorkoutCreateIn,
    WorkoutUpdateIn,
    WorkoutStartOut,
    WorkoutCompleteIn,
    WorkoutSetProgressIn,
    WorkoutSetProgressOut,
    HistoryStatsOut,
)

router = APIRouter()
history_router = APIRouter()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def user_tz_or_utc(tz_name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _normalize_tz_name(value: Optional[str]) -> Optional[str]:
    tz_name = str(value or "").strip()
    if not tz_name:
        return None
    try:
        ZoneInfo(tz_name)
        return tz_name
    except Exception:
        return None


def _effective_tz_name(current_user, request: Optional[Request]) -> str:
    user_tz = _normalize_tz_name(getattr(current_user, "timezone", None))
    if user_tz and user_tz.upper() != "UTC":
        return user_tz

    header_tz = _normalize_tz_name(request.headers.get("X-Timezone") if request else None)
    if header_tz:
        return header_tz

    return user_tz or "UTC"


def day_bounds_utc(local_day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start_local = datetime.combine(local_day, time.min).replace(tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


async def _workout_streak_snapshot(user_id: PydanticObjectId, tz_name: Optional[str]) -> tuple[bool, int, Optional[datetime]]:
    tz = user_tz_or_utc(tz_name)
    now_local = ensure_aware_utc(utcnow()).astimezone(tz)
    today_local = now_local.date()
    today_start_utc, today_end_utc = day_bounds_utc(today_local, tz)

    has_today = bool(
        await WorkoutRun.find(
            {
                "user_id": user_id,
                "completed_at": {"$ne": None, "$gte": today_start_utc, "$lt": today_end_utc},
            }
        ).count()
    )

    runs = (
        await WorkoutRun.find(
            WorkoutRun.user_id == user_id,
            WorkoutRun.completed_at != None,  # noqa: E711
        )
        .sort("-completed_at")
        .to_list()
    )
    if not runs:
        return has_today, 0, None

    active_dates = set()
    for r in runs:
        if not r.completed_at:
            continue
        active_dates.add(ensure_aware_utc(r.completed_at).astimezone(tz).date())

    streak_days = 0
    d = today_local
    while d in active_dates:
        streak_days += 1
        d = d - timedelta(days=1)

    return has_today, streak_days, runs[0].completed_at


class WorkoutFeedbackIn(BaseModel):
    difficulty: str = Field(..., description="easy|normal|hard (also accepts ru: легко/нормально/тяжело)")


INACTIVITY_DAYS = 14


def _is_inactive(last_completed_at: Optional[datetime]) -> bool:
    if not last_completed_at:
        return True  # never trained → intro
    return (utcnow() - last_completed_at) >= timedelta(days=INACTIVITY_DAYS)


def _normalize_feedback(v: str) -> Feedback:
    if v is None:
        raise HTTPException(status_code=400, detail="difficulty is required")

    s = str(v).strip().lower()

    ru_map = {"легко": "easy", "нормально": "normal", "тяжело": "hard"}
    s = ru_map.get(s, s)
    try:
        return Feedback(s)
    except Exception:
        pass

    candidates = [s, s.upper(), s.replace("-", "_"), s.replace("-", "_").upper()]
    for c in candidates:
        try:
            return Feedback[c]
        except Exception:
            continue

    raise HTTPException(status_code=400, detail="difficulty must be easy|normal|hard")



def _apply_signals_to_steps(
    steps: list[dict],
    *,
    needs_intro: bool,
    load_adjustment: Optional[str],
) -> list[dict]:
    """
    Returns adjusted workout steps for runtime usage.
    Does NOT mutate original steps.
    """
    result = []

    for step in steps:
        s = deepcopy(step)

        reps = s.get("reps")
        duration = s.get("duration_seconds")
        rest = s.get("rest_seconds_after")

        # 1️⃣ INTRO HAS PRIORITY
        if needs_intro:
            if reps is not None:
                s["reps"] = max(5, math.floor(reps * 0.7))
            if duration is not None:
                s["duration_seconds"] = max(15, math.floor(duration * 0.7))
            if rest is not None:
                s["rest_seconds_after"] = math.ceil(rest * 1.3)

        # 2️⃣ LOAD ADJUSTMENT (only if NOT intro)
        elif load_adjustment == "increase":
            if reps is not None:
                s["reps"] = reps + 2
            elif duration is not None:
                s["duration_seconds"] = math.floor(duration * 1.1)

        elif load_adjustment == "decrease":
            if reps is not None:
                s["reps"] = max(5, reps - 2)
            elif duration is not None:
                s["duration_seconds"] = max(15, math.floor(duration * 0.9))

        result.append(s)

    return result


def _fb_to_str(fb: Feedback) -> str:
    return getattr(fb, "value", str(fb))


STREAK_ACHIEVEMENTS: list[dict[str, object]] = [
    {"id": "str_003", "name": "Spark", "logic": "streak >= 3", "max_progress": 3, "points": 50},
    {"id": "str_007", "name": "On Fire", "logic": "streak >= 7", "max_progress": 7, "points": 100},
    {"id": "str_014", "name": "Two Weeks Strong", "logic": "streak >= 14", "max_progress": 14, "points": 150},
    {"id": "str_030", "name": "Unstoppable", "logic": "streak >= 30", "max_progress": 30, "points": 200},
    {"id": "str_090", "name": "90 Day Grind", "logic": "streak >= 90", "max_progress": 90, "points": 250},
    {"id": "str_365", "name": "Yearly Warrior", "logic": "streak >= 365", "max_progress": 365, "points": 300},
]


async def _upsert_user_achievement_progress(
    *,
    user_id: PydanticObjectId,
    achievement_code: str,
    category: str,
    name: str,
    logic: str,
    progress: float,
    max_progress: float,
    points: int,
) -> None:
    safe_progress = max(0.0, min(float(progress), float(max_progress)))
    doc = await UserAchievement.find_one(
        UserAchievement.user_id == user_id,
        UserAchievement.achievement_code == achievement_code,
    )

    if doc:
        # Keep progress monotonic to avoid losing unlocked achievements.
        doc.progress = max(float(getattr(doc, "progress", 0) or 0), safe_progress)
        doc.max_progress = float(max_progress)
        doc.points = int(points)
        if not getattr(doc, "category", None):
            doc.category = category
        if not getattr(doc, "name", None):
            doc.name = name
        if not getattr(doc, "logic", None):
            doc.logic = logic
        if doc.progress >= doc.max_progress and getattr(doc, "unlocked_at", None) is None:
            doc.unlocked_at = utcnow()
        await doc.save()
        return

    await UserAchievement(
        user_id=user_id,
        achievement_code=achievement_code,
        category=category,
        name=name,
        logic=logic,
        progress=safe_progress,
        max_progress=float(max_progress),
        points=int(points),
        unlocked_at=utcnow() if safe_progress >= float(max_progress) else None,
    ).insert()


async def _sync_streak_achievements(user_id: PydanticObjectId, streak_days: int) -> None:
    streak = max(0, int(streak_days or 0))
    for row in STREAK_ACHIEVEMENTS:
        await _upsert_user_achievement_progress(
            user_id=user_id,
            achievement_code=str(row["id"]),
            category="A",
            name=str(row["name"]),
            logic=str(row["logic"]),
            progress=float(streak),
            max_progress=float(row["max_progress"]),
            points=int(row["points"]),
        )


async def _get_owned_workout_or_404(workout_id: PydanticObjectId, user_id: PydanticObjectId) -> UserWorkout:
    w = await UserWorkout.get(workout_id)
    if not w:
        raise HTTPException(status_code=404, detail="Workout not found")
    if w.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return w


def _calculate_load_adjustment(feedbacks: list[Feedback]) -> Optional[str]:
    if not feedbacks:
        return None

    if feedbacks[-1] == Feedback.hard:
        return "decrease"

    last_three = [f for f in feedbacks[-3:] if f != Feedback.normal]
    if len(last_three) == 3 and all(f == Feedback.easy for f in last_three):
        return "increase"

    return None


async def _get_open_run_for_workout(workout_id: PydanticObjectId, user_id: PydanticObjectId) -> Optional[WorkoutRun]:
    runs = (
        await WorkoutRun.find(
            WorkoutRun.user_id == user_id,
            WorkoutRun.workout_ref_id == workout_id,
            WorkoutRun.completed_at == None,  # noqa: E711
        )
        .sort("-started_at")
        .limit(1)
        .to_list()
    )
    return runs[0] if runs else None


async def _get_last_run_for_workout(workout_id: PydanticObjectId, user_id: PydanticObjectId) -> Optional[WorkoutRun]:
    # prefer open run
    open_run = await _get_open_run_for_workout(workout_id, user_id)
    if open_run:
        return open_run

    runs = (
        await WorkoutRun.find(
            WorkoutRun.user_id == user_id,
            WorkoutRun.workout_ref_id == workout_id,
        )
        .sort("-started_at")
        .limit(1)
        .to_list()
    )
    return runs[0] if runs else None


async def _resolve_run_and_workout_for_id(id_value: PydanticObjectId, user_id: PydanticObjectId) -> tuple[WorkoutRun, Optional[UserWorkout]]:
    run = await WorkoutRun.get(id_value)
    if run:
        if run.user_id != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        workout = await UserWorkout.get(run.workout_ref_id) if run.workout_ref_id else None
        return run, workout

    workout = await UserWorkout.get(id_value)
    if workout:
        if workout.user_id != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
    else:
        exercise = await Exercise.get(id_value)
        if not exercise or getattr(exercise, "status", "active") != "active":
            raise HTTPException(status_code=404, detail="Workout not found")
        workout = await _create_user_workout_from_exercise(exercise, user_id)

    run = await _get_open_run_for_workout(workout.id, user_id)
    if not run:
        run = await _create_run_for_workout(workout, user_id)
    return run, workout


def _pick_workout_title_from_exercise(exercise: Exercise) -> str:
    name = getattr(exercise, "name", None)
    for lang in ("en", "ru"):
        values = getattr(name, lang, None) if name else None
        if isinstance(values, list) and values:
            text = str(values[0]).strip()
            if text:
                return text
    code = str(getattr(exercise, "code", "") or "").strip()
    return code or "Single Exercise"


async def _create_user_workout_from_exercise(exercise: Exercise, user_id: PydanticObjectId) -> UserWorkout:
    mode = getattr(exercise, "mode", ExerciseMode.reps)
    defaults = getattr(exercise, "defaults", None)
    media = getattr(exercise, "media", None)

    sets = int(getattr(defaults, "sets", 1) or 1)
    reps = getattr(defaults, "reps", None)
    duration = getattr(defaults, "duration_seconds", None)
    if duration is None:
        duration = int(getattr(media, "duration_seconds", 0) or 0) or None
    rest_seconds_after = int(getattr(defaults, "rest_seconds_after", 45) or 45)

    step: dict = {
        "order": 1,
        "exercise_id": exercise.id,
        "mode": mode,
        "sets": sets,
        "rest_seconds_after": rest_seconds_after,
    }
    if mode == ExerciseMode.reps:
        step["reps"] = int(reps or 1)
        step["duration_seconds"] = None
    else:
        step["duration_seconds"] = max(5, int(duration or 30))
        step["reps"] = None

    workout = UserWorkout(
        user_id=user_id,
        title=_pick_workout_title_from_exercise(exercise),
        steps=[step],
    )
    await workout.insert()
    return workout


async def _create_run_for_workout(workout: UserWorkout, user_id: PydanticObjectId) -> WorkoutRun:
    run = WorkoutRun(
        user_id=user_id,
        source="custom",
        workout_ref_id=workout.id,
        started_at=utcnow(),
        completed_at=None,
        exercise_results=[],
    )
    await run.insert()
    return run


def _validate_mode_payload(mode: ExerciseMode, reps_done: Optional[int], seconds_done: Optional[int]) -> None:
    if mode == ExerciseMode.reps:
        if reps_done is None:
            raise HTTPException(status_code=400, detail="reps_done is required when mode=reps")
        if seconds_done is not None:
            raise HTTPException(status_code=400, detail="seconds_done must be null when mode=reps")
        return

    if mode == ExerciseMode.time:
        if seconds_done is None:
            raise HTTPException(status_code=400, detail="seconds_done is required when mode=time")
        if reps_done is not None:
            raise HTTPException(status_code=400, detail="reps_done must be null when mode=time")


def _step_mode_by_exercise(workout: Optional[UserWorkout]) -> dict[PydanticObjectId, ExerciseMode]:
    mapping: dict[PydanticObjectId, ExerciseMode] = {}
    if workout is None:
        return mapping
    for s in workout.steps:
        mapping[s.exercise_id] = s.mode
    return mapping


def _step_sets_by_exercise(workout: Optional[UserWorkout]) -> dict[PydanticObjectId, int]:
    mapping: dict[PydanticObjectId, int] = {}
    if workout is None:
        return mapping
    for s in workout.steps:
        mapping[s.exercise_id] = int(getattr(s, "sets", 1) or 1)
    return mapping


def _validate_complete_exercise_id(payload: WorkoutCompleteIn, workout: Optional[UserWorkout]) -> None:
    if workout is None:
        return

    step_ids = {str(s.exercise_id) for s in (workout.steps or [])}
    if step_ids and str(payload.exercise_id) not in step_ids:
        raise HTTPException(status_code=400, detail=f"exercise_id not found in workout: {payload.exercise_id}")


def _normalize_result_item(item) -> dict:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if isinstance(item, dict):
        return dict(item)
    return dict(item or {})


def _set_entry_key(item: dict) -> tuple[str, Optional[int]]:
    return str(item.get("exercise_id")), item.get("set_no")


def _upsert_set_entry(existing_items: list[dict], incoming_item: dict) -> list[dict]:
    incoming_key = _set_entry_key(incoming_item)
    merged = []
    replaced = False
    for it in existing_items:
        if _set_entry_key(it) == incoming_key:
            merged.append(incoming_item)
            replaced = True
        else:
            merged.append(it)
    if not replaced:
        merged.append(incoming_item)
    return merged


async def _complete_run(run: WorkoutRun, payload: WorkoutCompleteIn, current_user, tz_name: Optional[str] = None):
    user_id = current_user.id
    if run.completed_at is not None:
        raise HTTPException(status_code=400, detail="Run already completed")

    run.completed_at = utcnow()
    run.total_seconds = payload.total_seconds
    run.calories_estimated = payload.calories_estimated
    run.rating_stars = payload.rating_stars
    run.difficulty_feedback = payload.difficulty_feedback  # ✅ already Feedback enum in schema
    await run.save()
    has_completed_today, streak_days, last_activity_at = await _workout_streak_snapshot(
        user_id=user_id,
        tz_name=tz_name or getattr(current_user, "timezone", None),
    )

    # Keep user stats in sync with workout streak logic.
    try:
        if getattr(current_user, "stats", None) is not None:
            current_user.stats.streak_days = int(streak_days)
            current_user.stats.last_activity_at = last_activity_at
            await current_user.save()
    except Exception:
        pass

    try:
        await _sync_streak_achievements(user_id=user_id, streak_days=streak_days)
    except Exception:
        # Do not fail workout completion if achievement sync fails.
        pass

    return {
        "status": "ok",
        "run_id": str(run.id),
        "completed_at": run.completed_at.isoformat(),
        "has_completed_today": has_completed_today,
        "streak_days": streak_days,
        "last_activity_at": last_activity_at.isoformat() if last_activity_at else None,
    }


# Removed: not used by frontend
async def create_workout(payload: WorkoutCreateIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    steps = sorted(payload.steps, key=lambda s: s.order)

    w = UserWorkout(
        user_id=current_user.id,
        title=payload.title,
        steps=[s.model_dump() for s in steps],
    )
    await w.insert()
    return w


# Removed: not used by frontend
async def list_workouts(current_user=Depends(get_current_user), skip: int = 0, limit: int = 20):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    limit = min(max(limit, 1), 100)

    items = (
        await UserWorkout.find(UserWorkout.user_id == current_user.id)
        .sort("-created_at")
        .skip(skip)
        .limit(limit)
        .to_list()
    )
    return {"items": items, "skip": skip, "limit": limit}


# Removed: not used by frontend
async def get_workout(id: PydanticObjectId, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return await _get_owned_workout_or_404(id, current_user.id)


# Removed: not used by frontend
async def update_workout(workout_id: PydanticObjectId, payload: WorkoutUpdateIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    w = await _get_owned_workout_or_404(workout_id, current_user.id)

    if payload.title is not None:
        w.title = payload.title

    if payload.steps is not None:
        steps = sorted(payload.steps, key=lambda s: s.order)
        w.steps = [s.model_dump() for s in steps]
    w.updated_at = utcnow()
    await w.save()
    return w


# Removed: not used by frontend
async def delete_workout(workout_id: PydanticObjectId, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    w = await _get_owned_workout_or_404(workout_id, current_user.id)
    await w.delete()
    return {"status": "ok"}


@router.post("/workouts/{workout_id}/start", response_model=WorkoutStartOut)
async def start_workout(workout_id: PydanticObjectId, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    w = await _get_owned_workout_or_404(workout_id, current_user.id)

    last_completed_runs = (
        await WorkoutRun.find(
            WorkoutRun.user_id == current_user.id,
            WorkoutRun.workout_ref_id == w.id,
            WorkoutRun.completed_at != None,  # noqa: E711
        )
        .sort("-completed_at")
        .limit(1)
        .to_list()
    )

    last_completed_at = last_completed_runs[0].completed_at if last_completed_runs else None
    needs_intro = _is_inactive(last_completed_at)

    last_adjustment_runs = (
        await WorkoutRun.find(
            WorkoutRun.user_id == current_user.id,
            WorkoutRun.workout_ref_id == w.id,
            WorkoutRun.load_adjustment != None,  # noqa: E711
        )
        .sort("-started_at")
        .limit(1)
        .to_list()
    )

    raw_adjustment = last_adjustment_runs[0].load_adjustment if last_adjustment_runs else None

    effective_adjustment = None if needs_intro else raw_adjustment

    run = await _create_run_for_workout(w, current_user.id)

    run.needs_intro = needs_intro
    run.load_adjustment = effective_adjustment
    await run.save()

    effective_steps = _apply_signals_to_steps(
    w.steps,
    needs_intro=needs_intro,
    load_adjustment=effective_adjustment,
)

    return WorkoutStartOut(
        run_id=run.id,
        started_at=run.started_at,
        needs_intro=needs_intro,
        load_adjustment=effective_adjustment,
        steps=effective_steps,
    )


@router.post("/workouts/{id}/set-progress", response_model=WorkoutSetProgressOut, status_code=status.HTTP_200_OK)
async def set_progress_workout(id: PydanticObjectId, payload: WorkoutSetProgressIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    run, workout = await _resolve_run_and_workout_for_id(id, current_user.id)
    if run.completed_at is not None:
        raise HTTPException(status_code=400, detail="Run already completed")

    _validate_mode_payload(payload.mode, payload.reps_done, payload.seconds_done)

    step_modes = _step_mode_by_exercise(workout)
    step_sets = _step_sets_by_exercise(workout)
    if step_modes:
        expected_mode = step_modes.get(payload.exercise_id)
        if expected_mode is None:
            raise HTTPException(status_code=400, detail=f"exercise_id not found in workout: {payload.exercise_id}")
        if expected_mode != payload.mode:
            raise HTTPException(status_code=400, detail=f"mode mismatch for exercise_id: {payload.exercise_id}")
        expected_sets = int(step_sets.get(payload.exercise_id, 1) or 1)
        if int(payload.set_no) > expected_sets:
            raise HTTPException(status_code=400, detail=f"set_no exceeds configured sets for exercise: {payload.exercise_id}")

    entry = payload.model_dump()
    existing_results = [_normalize_result_item(x) for x in (run.exercise_results or [])]
    run.exercise_results = _upsert_set_entry(existing_results, entry)
    await run.save()

    logged_sets_for_exercise = sum(
        1
        for it in (_normalize_result_item(x) for x in (run.exercise_results or []))
        if str(it.get("exercise_id")) == str(payload.exercise_id)
    )
    return WorkoutSetProgressOut(
        status="ok",
        run_id=str(run.id),
        exercise_id=str(payload.exercise_id),
        set_no=payload.set_no,
        logged_sets_for_exercise=logged_sets_for_exercise,
    )



@router.post("/workouts/{id}/complete", status_code=status.HTTP_200_OK)
async def complete_workout(
    id: PydanticObjectId,
    payload: WorkoutCompleteIn,
    request: Request,
    current_user=Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    run, workout = await _resolve_run_and_workout_for_id(id, current_user.id)
    _validate_complete_exercise_id(payload, workout)
    tz_name = _effective_tz_name(current_user, request)
    return await _complete_run(run, payload, current_user, tz_name=tz_name)


# Removed: not used by frontend
async def workout_feedback(workout_id: PydanticObjectId, payload: WorkoutFeedbackIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    w = await _get_owned_workout_or_404(workout_id, current_user.id)

    fb = _normalize_feedback(payload.difficulty)  # ✅ Feedback enum

    run = await _get_last_run_for_workout(w.id, current_user.id)
    if not run:
        run = await _create_run_for_workout(w, current_user.id)

    run.difficulty_feedback = fb
    await run.save()
    recent_runs = (
        await WorkoutRun.find(
            WorkoutRun.user_id == current_user.id,
            WorkoutRun.workout_ref_id == w.id,
            WorkoutRun.difficulty_feedback != None,
        )
        .sort("-started_at")
        .limit(3)
        .to_list()
    )

    # order oldest → newest
    recent_runs = list(reversed(recent_runs))
    recent_feedbacks = [r.difficulty_feedback for r in recent_runs]

    adjustment = _calculate_load_adjustment(recent_feedbacks)

    # store server decision (minimal, non-breaking)
    run.load_adjustment = adjustment  # "increase" | "decrease" | None
    await run.save()

    # ---- NEW LOGIC ENDS HERE ----

    return {
        "status": "ok",
        "workout_id": str(w.id),
        "run_id": str(run.id),
        "difficulty": _fb_to_str(fb),
        "adjustment": adjustment,
    }



# Removed: not used by frontend
async def history_list(current_user=Depends(get_current_user), skip: int = 0, limit: int = 20):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    limit = min(max(limit, 1), 100)

    items = (
        await WorkoutRun.find(
            WorkoutRun.user_id == current_user.id,
            WorkoutRun.completed_at != None,  # noqa: E711
        )
        .sort("-completed_at")
        .skip(skip)
        .limit(limit)
        .to_list()
    )
    return {"items": items, "skip": skip, "limit": limit}


# Removed: not used by frontend
async def history_stats(request: Request, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    runs = (
        await WorkoutRun.find(
            WorkoutRun.user_id == current_user.id,
            WorkoutRun.completed_at != None,  # noqa: E711
        )
        .sort("-completed_at")
        .limit(500)
        .to_list()
    )

    if not runs:
        return HistoryStatsOut(
            total_completed=0,
            total_seconds=0,
            total_calories_estimated=0.0,
            streak_days=0,
            has_completed_today=False,
            last_activity_at=None,
        )

    total_completed = len(runs)
    total_seconds = int(sum((r.total_seconds or 0) for r in runs))
    total_calories = float(sum((r.calories_estimated or 0) for r in runs))
    has_completed_today, streak, last_activity_at = await _workout_streak_snapshot(
        user_id=current_user.id,
        tz_name=_effective_tz_name(current_user, request),
    )

    return HistoryStatsOut(
        total_completed=total_completed,
        total_seconds=total_seconds,
        total_calories_estimated=total_calories,
        streak_days=streak,
        has_completed_today=has_completed_today,
        last_activity_at=last_activity_at,
    )


