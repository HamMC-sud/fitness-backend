from __future__ import annotations

import re
from typing import Optional, Any

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import AliasChoices, BaseModel, Field

from models.enums import WorkoutType, Difficulty, Equipment, ExerciseMode
from models.content import Exercise
from utils.fitness_metrics import build_metrics_block, seconds_to_minutes

router = APIRouter(tags=["content"])

_DISCOVER_WORKTYPE_ALIASES: dict[str, tuple[WorkoutType, Optional[Equipment]]] = {
    # Direct labels
    "strength": (WorkoutType.strength, None),
    "cardio": (WorkoutType.cardio, None),
    "hiit": (WorkoutType.hiit, None),
    "yoga": (WorkoutType.yoga, None),
    "stretching": (WorkoutType.stretching, None),
    # Common grouped labels from client UI
    "cardio_hiit": (WorkoutType.cardio, None),
    "core": (WorkoutType.strength, None),
    "core_abs": (WorkoutType.strength, None),
    "core_and_abs": (WorkoutType.strength, None),
    "abs": (WorkoutType.strength, None),
    "bodyweight": (WorkoutType.strength, Equipment.home),
    "dumbbells": (WorkoutType.strength, Equipment.gym),
    "mobility": (WorkoutType.stretching, None),
    "relaxation": (WorkoutType.yoga, None),
    "legs_glutes": (WorkoutType.strength, None),
    "upper_body": (WorkoutType.strength, None),
    "arms": (WorkoutType.strength, None),
}


def _normalize_discover_worktype(value: str) -> tuple[WorkoutType, Optional[Equipment]]:
    raw_input = str(value or "").strip()
    if not raw_input:
        raise ValueError("empty worktype")

    # Accept camelCase/PascalCase inputs from clients as well.
    raw = re.sub(r"(?<!^)(?=[A-Z])", "_", raw_input).lower()

    try:
        return WorkoutType.normalize(raw), None
    except Exception:
        pass

    token = (
        raw.replace("-", "_")
        .replace(" ", "_")
        .replace("&", "_")
        .replace("/", "_")
    )
    token = "_".join(part for part in token.split("_") if part)
    mapped = _DISCOVER_WORKTYPE_ALIASES.get(token)
    if mapped is None:
        raise ValueError(f"Unsupported worktype: {value}")
    return mapped


class SimilarExerciseIn(BaseModel):
    exercise_id: PydanticObjectId = Field(
        validation_alias=AliasChoices("exercise_id", "id"),
    )
    level: Difficulty
    workouttype: WorkoutType = Field(
        validation_alias=AliasChoices("workouttype", "workoutType"),
    )
    reps: int = Field(ge=1, le=500)


def equipment_db_aliases(equipment: Equipment) -> list[str]:
    if equipment == Equipment.home:
        return [
            Equipment.home.value,
            "Home",
            "No equipment",
            "no equipment",
            "bodyweight",
            "resistance_bands",
            "Resistance bands",
            "bands",
        ]
    return [
        Equipment.gym.value,
        "Gym",
        "Dumbbells",
        "dumbbells",
        "Pull-up bar",
        "pullup_bar",
        "pull_up_bar",
        "Barbell & Bench",
        "barbell_bench",
        "barbell_and_bench",
    ]


def _pick_i18n_text(i18n_obj: Any, lang: str = "en") -> str:
    data = jsonable_encoder(i18n_obj or {})
    value = data.get(lang)
    if isinstance(value, list):
        return str(value[0]) if value else ""
    if isinstance(value, str):
        return value
    fallback = data.get("en") or data.get("ru")
    if isinstance(fallback, list):
        return str(fallback[0]) if fallback else ""
    if isinstance(fallback, str):
        return fallback
    return ""


def _resolve_set_plan(ex: Exercise, step_duration_default: int) -> list[dict[str, Any]]:
    defaults = getattr(ex, "defaults", None)
    mode_value = str(getattr(ex, "mode", ""))
    default_sets = int(getattr(defaults, "sets", 4) or 4) if defaults else 4
    default_reps = getattr(defaults, "reps", None) if defaults else None
    default_rest = int(getattr(defaults, "rest_seconds_after", 60) or 60) if defaults else 60
    default_duration = int(getattr(defaults, "duration_seconds", 0) or 0) if defaults else 0
    if default_duration <= 0:
        default_duration = int(step_duration_default or 0)

    raw_plan = list(getattr(defaults, "set_plan", []) or []) if defaults else []
    if raw_plan:
        normalized: list[dict[str, Any]] = []
        for item in raw_plan:
            set_no = int(getattr(item, "set_no", 0) or 0)
            if set_no <= 0:
                continue
            target_reps = getattr(item, "target_reps", None)
            target_seconds = getattr(item, "target_duration_seconds", None)
            rest_after = int(getattr(item, "rest_seconds_after", default_rest) or default_rest)
            normalized.append(
                {
                    "set_no": set_no,
                    "target_reps": int(target_reps) if target_reps is not None else None,
                    "target_duration_seconds": int(target_seconds) if target_seconds is not None else None,
                    "rest_seconds_after": rest_after,
                }
            )
        if normalized:
            return sorted(normalized, key=lambda x: x["set_no"])

    fallback: list[dict[str, Any]] = []
    for i in range(1, default_sets + 1):
        fallback.append(
            {
                "set_no": i,
                "target_reps": int(default_reps) if (mode_value == "reps" and default_reps is not None) else None,
                "target_duration_seconds": int(default_duration) if mode_value == "time" else None,
                "rest_seconds_after": default_rest,
            }
        )
    return fallback


def _build_sets_payload(ex: Exercise, set_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    media = getattr(ex, "media", None)
    mode_value = str(getattr(ex, "mode", ""))
    media_duration = int(getattr(media, "duration_seconds", 0) or 0) if media else 0
    video_url = getattr(media, "video_url", None) if media else None
    thumbnail_url = getattr(media, "thumbnail_url", None) if media else None
    defaults = getattr(ex, "defaults", None)

    # New column: defaults.sets_reps allows several reps objects per set.
    raw_sets_reps = list(getattr(defaults, "sets_reps", []) or []) if defaults else []
    if raw_sets_reps:
        out_new: list[dict[str, Any]] = []
        for set_item in sorted(raw_sets_reps, key=lambda x: int(getattr(x, "set_no", 0) or 0)):
            set_no = int(getattr(set_item, "set_no", 0) or 0)
            if set_no <= 0:
                continue
            rest_after = int(getattr(set_item, "rest_seconds_after", 60) or 60)
            reps_items = list(getattr(set_item, "reps", []) or [])
            reps_payload: list[dict[str, Any]] = []
            for rep_item in sorted(reps_items, key=lambda x: int(getattr(x, "rep_no", 0) or 0)):
                rep_no = int(getattr(rep_item, "rep_no", 0) or 0)
                if rep_no <= 0:
                    continue
                target_reps = getattr(rep_item, "target_reps", None)
                target_seconds = getattr(rep_item, "target_duration_seconds", None)
                if target_seconds is None and media_duration > 0:
                    target_seconds = media_duration
                reps_payload.append(
                    {
                        "rep_no": rep_no,
                        "mode": mode_value,
                        "target": int(target_reps) if target_reps is not None else None,
                        "duration_seconds": int(target_seconds) if target_seconds is not None else None,
                        "video_url": video_url,
                        "thumbnail_url": thumbnail_url,
                    }
                )
            if reps_payload:
                out_new.append(
                    {
                        "set_no": set_no,
                        "rest_seconds_after": rest_after,
                        "reps": reps_payload,
                    }
                )
        if out_new:
            return out_new

    out: list[dict[str, Any]] = []
    for p in set_plan:
        target_duration = p.get("target_duration_seconds")
        if target_duration is None and media_duration > 0:
            target_duration = media_duration

        out.append(
            {
                "set_no": int(p["set_no"]),
                "rest_seconds_after": int(p["rest_seconds_after"]),
                "reps": [
                    {
                        "rep_no": 1,
                        "mode": mode_value,
                        "target": p.get("target_reps"),
                        "duration_seconds": int(target_duration) if target_duration is not None else None,
                        "video_url": video_url,
                        "thumbnail_url": thumbnail_url,
                    }
                ],
            }
        )
    return out


def _aggregate_sets_metrics(sets_payload: list[dict[str, Any]]) -> dict[str, int]:
    total_sets = len(sets_payload)
    total_intervals = 0
    total_reps_target = 0
    timed_intervals = 0
    timed_intervals_seconds = 0

    for s in sets_payload:
        reps = s.get("reps", []) or []
        total_intervals += len(reps)
        for rep in reps:
            target = rep.get("target")
            duration_seconds = rep.get("duration_seconds")

            if target is not None:
                total_reps_target += int(target)
            if duration_seconds is not None:
                timed_intervals += 1
                timed_intervals_seconds += int(duration_seconds)

    return {
        "total_sets": total_sets,
        "total_intervals": total_intervals,
        "total_reps_target": total_reps_target,
        "timed_intervals": timed_intervals,
        "timed_intervals_seconds": timed_intervals_seconds,
    }


def _exercise_base_duration_seconds(ex: Exercise) -> int:
    media = getattr(ex, "media", None)
    duration_seconds = int(getattr(media, "duration_seconds", 0) or 0) if media else 0
    if duration_seconds <= 0 and getattr(ex, "defaults", None):
        duration_seconds = int(getattr(ex.defaults, "duration_seconds", 0) or 0)
    return max(0, duration_seconds)


def _derive_exercise_workout_metrics(ex: Exercise) -> dict[str, Any]:
    mode_value = str(getattr(ex, "mode", ""))
    base_duration_seconds = _exercise_base_duration_seconds(ex)
    step_duration = int(base_duration_seconds) if mode_value == ExerciseMode.time.value else None

    set_plan = _resolve_set_plan(ex, step_duration or base_duration_seconds)
    sets_payload = _build_sets_payload(ex, set_plan)
    metrics = _aggregate_sets_metrics(sets_payload)

    rest_between_sets_seconds = 0
    if len(sets_payload) > 1:
        for s in sets_payload[:-1]:
            rest_between_sets_seconds += int((s.get("rest_seconds_after", 0) or 0))

    rest_seconds_after_exercise = int((sets_payload[-1].get("rest_seconds_after", 0) if sets_payload else 0) or 0)
    active_seconds = int(metrics["timed_intervals_seconds"])
    planned_total_seconds = active_seconds + rest_between_sets_seconds

    # Fallback for reps-only templates where explicit timed intervals are absent.
    total_seconds = planned_total_seconds if planned_total_seconds > 0 else base_duration_seconds
    total_reps = int(metrics["total_reps_target"]) if mode_value == ExerciseMode.reps.value else 0

    set_summaries = [
        {
            "set_id": int(s.get("set_no", 0)),
            "reps_count": len(s.get("reps", []) or []),
        }
        for s in sets_payload
    ]

    return {
        "mode": mode_value,
        "sets_payload": sets_payload,
        "set_summaries": set_summaries,
        "total_sets": int(metrics["total_sets"]),
        "total_intervals": int(metrics["total_intervals"]),
        "total_reps": total_reps,
        "timed_intervals": int(metrics["timed_intervals"]),
        "timed_intervals_seconds": active_seconds,
        "rest_between_sets_seconds": int(rest_between_sets_seconds),
        "rest_seconds_after_exercise": rest_seconds_after_exercise,
        "planned_total_seconds": int(total_seconds),
    }


@router.get("/discover/worktypes/{worktype}")
async def discover_worktype_details(
    worktype: str,
    level: Optional[Difficulty] = None,
    equipment: Optional[Equipment] = None,
    status: str = "active",
):
    try:
        wtype, implied_equipment = _normalize_discover_worktype(worktype)
    except Exception:
        supported = sorted(_DISCOVER_WORKTYPE_ALIASES.keys())
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Invalid worktype",
                "received": worktype,
                "supported_labels": supported,
            },
        )
    effective_equipment = equipment or implied_equipment

    filters: list[Any] = [Exercise.status == status, {"workout_type": wtype.value}]
    if level:
        filters.append(Exercise.difficulty == level)
    if effective_equipment:
        filters.append({"equipment": {"$in": equipment_db_aliases(effective_equipment)}})

    exercises = await Exercise.find(*filters).sort("-created_at").to_list()
    if not exercises:
        return {
            "worktype": wtype.value,
            "category_image": None,
            "totals": {
                "workouts": 0,
                "total_minutes": 0,
                "total_calories": 0.0,
            },
            "items": [],
        }

    items: list[dict[str, Any]] = []
    sum_seconds = 0
    sum_calories = 0.0
    category_image = None

    for ex in exercises:
        wm = _derive_exercise_workout_metrics(ex)
        media = getattr(ex, "media", None)
        total_seconds = int(wm["planned_total_seconds"])
        total_calories = 0.0
        if getattr(ex, "calories_per_minute", None):
            total_calories = float(ex.calories_per_minute or 0) * (total_seconds / 60.0)

        cover_image = getattr(media, "thumbnail_url", None) if media else None
        sum_seconds += total_seconds
        sum_calories += total_calories
        if not category_image and cover_image:
            category_image = cover_image

        items.append(
            {
                "id": str(ex.id),
                "title": {
                    "ru": _pick_i18n_text(getattr(ex, "name", None), "ru"),
                    "en": _pick_i18n_text(getattr(ex, "name", None), "en"),
                },
                "description": {
                    "ru": _pick_i18n_text(getattr(ex, "description", None), "ru"),
                    "en": _pick_i18n_text(getattr(ex, "description", None), "en"),
                },
                "level": str(ex.difficulty.value if hasattr(ex.difficulty, "value") else ex.difficulty),
                "worktype": wtype.value,
                "cover_image": cover_image,
                "exercise_count": 1,
                "total_seconds": total_seconds,
                "total_minutes": seconds_to_minutes(total_seconds),
                "sets": int(wm["total_sets"]),
                "intervals": int(wm["total_intervals"]),
                "reps": int(wm["total_reps"]),
                "timed_intervals_seconds": int(wm["timed_intervals_seconds"]),
                "total_calories": round(total_calories, 1),
                "metrics": build_metrics_block(
                    total_seconds=total_seconds,
                    total_calories=round(total_calories, 1),
                    total_sets=int(wm["total_sets"]),
                    total_reps=int(wm["total_reps"]),
                    total_intervals=int(wm["total_intervals"]),
                    timed_intervals=int(wm["timed_intervals"]),
                    timed_intervals_seconds=int(wm["timed_intervals_seconds"]),
                    rest_between_sets_seconds=int(wm["rest_between_sets_seconds"]),
                ),
                "exercise_id": str(ex.id),
                "exercise_code": getattr(ex, "code", None),
            }
        )

    return {
        "worktype": wtype.value,
        "category_image": category_image,
        "totals": {
            "workouts": len(items),
            "total_minutes": seconds_to_minutes(sum_seconds),
            "total_calories": round(sum_calories, 1),
        },
        "items": items,
    }


@router.get("/discover/workouts/{template_id}")
async def discover_workout_details(template_id: PydanticObjectId):
    ex = await Exercise.get(template_id)
    if not ex:
        raise HTTPException(status_code=404, detail="Workout template not found")
    if ex.status != "active":
        raise HTTPException(status_code=404, detail="Workout template is inactive")

    wm = _derive_exercise_workout_metrics(ex)
    media = getattr(ex, "media", None)
    duration_seconds = int(wm["planned_total_seconds"])

    calories = 0.0
    if getattr(ex, "calories_per_minute", None):
        calories = float(ex.calories_per_minute or 0) * (duration_seconds / 60.0)

    set_summaries = wm["set_summaries"]
    ai_tip = getattr(ex, "ai_technique", None)

    return {
        "id": str(ex.id),
        "localization": {
            "title": {
                "ru": _pick_i18n_text(getattr(ex, "name", None), "ru"),
                "en": _pick_i18n_text(getattr(ex, "name", None), "en"),
            },
            "description": {
                "ru": _pick_i18n_text(getattr(ex, "description", None), "ru"),
                "en": _pick_i18n_text(getattr(ex, "description", None), "en"),
            },
        },
        "title": {
            "ru": _pick_i18n_text(getattr(ex, "name", None), "ru"),
            "en": _pick_i18n_text(getattr(ex, "name", None), "en"),
        },
        "image": getattr(media, "thumbnail_url", None) if media else None,
        "ai_coach_tip": {
            "ru": _pick_i18n_text(ai_tip, "ru"),
            "en": _pick_i18n_text(ai_tip, "en"),
        },
        "worktype": (
            str(ex.workout_type[0].value if hasattr(ex.workout_type[0], "value") else ex.workout_type[0])
            if getattr(ex, "workout_type", None)
            else None
        ),
        "level": str(ex.difficulty.value if hasattr(ex.difficulty, "value") else ex.difficulty),
        "totals": {
            "sets": int(wm["total_sets"]),
            "reps": int(wm["total_reps"]),
            "intervals": int(wm["total_intervals"]),
            "timed_intervals": int(wm["timed_intervals"]),
            "timed_intervals_seconds": int(wm["timed_intervals_seconds"]),
            "rest_between_sets_seconds": int(wm["rest_between_sets_seconds"]),
            "rest_seconds_after_exercise": int(wm["rest_seconds_after_exercise"]),
            "total_seconds": duration_seconds,
            "total_minutes": seconds_to_minutes(duration_seconds),
            "total_calories": round(calories, 1),
        },
        "metrics": build_metrics_block(
            total_seconds=duration_seconds,
            total_calories=round(calories, 1),
            total_sets=int(wm["total_sets"]),
            total_reps=int(wm["total_reps"]),
            total_intervals=int(wm["total_intervals"]),
            timed_intervals=int(wm["timed_intervals"]),
            timed_intervals_seconds=int(wm["timed_intervals_seconds"]),
            rest_between_sets_seconds=int(wm["rest_between_sets_seconds"]),
        ),
        "sets": set_summaries,
    }


async def _discover_similar_workouts(exercise_id: PydanticObjectId, payload: SimilarExerciseIn):
    source = await Exercise.get(exercise_id)
    if not source or source.status != "active":
        raise HTTPException(status_code=404, detail="Source exercise not found")

    source_mode = getattr(source, "mode", None)
    source_worktype = payload.workouttype.value
    source_level = payload.level

    filters: list[Any] = [
        Exercise.status == "active",
        Exercise.difficulty == source_level,
        {"_id": {"$ne": source.id}},
    ]

    filters.append({"workout_type": source_worktype})
    if source_mode:
        filters.append(Exercise.mode == source_mode)
    items = await Exercise.find(*filters).sort("-created_at").limit(int(payload.reps)).to_list()

    out_items: list[dict[str, Any]] = []
    for ex in items:
        media = getattr(ex, "media", None)
        duration_seconds = int(getattr(media, "duration_seconds", 0) or 0) if media else 0
        if duration_seconds <= 0:
            duration_seconds = int(getattr(getattr(ex, "defaults", None), "duration_seconds", 0) or 0)
        out_items.append(
            {
                "video_url": getattr(media, "video_url", None) if media else None,
                "duration_seconds": duration_seconds,
            }
        )

    return out_items

@router.post("/discover/workouts/similar")
async def discover_similar_workouts_by_body(payload: SimilarExerciseIn):
    return await _discover_similar_workouts(payload.exercise_id, payload)
