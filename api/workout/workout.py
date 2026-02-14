from __future__ import annotations

from datetime import datetime, timedelta ,timezone
from typing import Optional

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
import math
from copy import deepcopy

from api.auth.config import get_current_user
from models import UserWorkout, WorkoutRun, ExerciseFeedbackEvent
from models.workouts import Feedback  # ✅ enum
from schemas.workout import (
    WorkoutCreateIn,
    WorkoutUpdateIn,
    WorkoutStartOut,
    WorkoutCompleteIn,
    HistoryStatsOut,
)

router = APIRouter()
history_router = APIRouter()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
        rest = s.get("rest_seconds")

        # 1️⃣ INTRO HAS PRIORITY
        if needs_intro:
            if reps is not None:
                s["reps"] = max(5, math.floor(reps * 0.7))
            if duration is not None:
                s["duration_seconds"] = max(15, math.floor(duration * 0.7))
            if rest is not None:
                s["rest_seconds"] = math.ceil(rest * 1.3)

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


async def _complete_run(run: WorkoutRun, payload: WorkoutCompleteIn, user_id: PydanticObjectId):
    if run.completed_at is not None:
        raise HTTPException(status_code=400, detail="Run already completed")

    run.completed_at = utcnow()
    run.total_seconds = payload.total_seconds
    run.calories_estimated = payload.calories_estimated
    run.rating_stars = payload.rating_stars
    run.difficulty_feedback = payload.difficulty_feedback  # ✅ already Feedback enum in schema
    run.exercise_results = [r.model_dump() for r in payload.exercise_results]

    await run.save()

    # exercise-level feedback events (existing behavior)
    events = []
    for r in payload.exercise_results:
        if r.feedback is None:
            continue
        events.append(
            ExerciseFeedbackEvent(
                user_id=user_id,
                exercise_id=r.exercise_id,
                workout_run_id=run.id,
                feedback=r.feedback,
            )
        )

    if events:
        try:
            await ExerciseFeedbackEvent.insert_many(events)
        except Exception:
            for e in events:
                await e.insert()

    return {"status": "ok", "run_id": str(run.id), "completed_at": run.completed_at.isoformat()}


@router.post("/workouts", status_code=status.HTTP_201_CREATED)
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


@router.get("/workouts")
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


@router.get("/workouts/{id}")
async def get_workout(id: PydanticObjectId, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return await _get_owned_workout_or_404(id, current_user.id)


@router.put("/workouts/{workout_id}")
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


@router.delete("/workouts/{workout_id}", status_code=status.HTTP_200_OK)
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



@router.post("/workouts/{id}/complete", status_code=status.HTTP_200_OK)
async def complete_workout(id: PydanticObjectId, payload: WorkoutCompleteIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # 1) try treat as run_id
    run = await WorkoutRun.get(id)
    if run:
        if run.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Forbidden")
        return await _complete_run(run, payload, current_user.id)

    # 2) treat as workout_id
    w = await _get_owned_workout_or_404(id, current_user.id)
    run = await _get_open_run_for_workout(w.id, current_user.id)
    if not run:
        run = await _create_run_for_workout(w, current_user.id)

    return await _complete_run(run, payload, current_user.id)


@router.post("/workouts/{workout_id}/feedback", status_code=status.HTTP_200_OK)
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



@router.get("/history")
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


@router.get("/history/stats", response_model=HistoryStatsOut)
async def history_stats(current_user=Depends(get_current_user)):
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
            last_activity_at=None,
        )

    total_completed = len(runs)
    total_seconds = int(sum((r.total_seconds or 0) for r in runs))
    total_calories = float(sum((r.calories_estimated or 0) for r in runs))
    last_activity_at = runs[0].completed_at

    days = set()
    for r in runs:
        if r.completed_at:
            days.add(r.completed_at.date())

    last_day = max(days)
    streak = 0
    d = last_day
    while d in days:
        streak += 1
        d = d - timedelta(days=1)

    return HistoryStatsOut(
        total_completed=total_completed,
        total_seconds=total_seconds,
        total_calories_estimated=total_calories,
        streak_days=streak,
        last_activity_at=last_activity_at,
    )
