from __future__ import annotations

from typing import Optional, Any

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import AliasChoices, BaseModel, Field

from models.enums import WorkoutType, Difficulty, Equipment, ExerciseMode
from models.content import Exercise

router = APIRouter(tags=["content"])


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


def _to_minutes(seconds: int) -> int:
    return max(1, int(round(seconds / 60))) if seconds > 0 else 0


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


@router.get("/discover/worktypes/{worktype}")
async def discover_worktype_details(
    worktype: str,
    level: Optional[Difficulty] = None,
    equipment: Optional[Equipment] = None,
    status: str = "active",
):
    try:
        wtype = WorkoutType((worktype or "").strip().lower())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid worktype")

    filters: list[Any] = [Exercise.status == status, {"workout_type": wtype.value}]
    if level:
        filters.append(Exercise.difficulty == level)
    if equipment:
        filters.append({"equipment": {"$in": equipment_db_aliases(equipment)}})

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
        media = getattr(ex, "media", None)
        total_seconds = int(getattr(media, "duration_seconds", 0) or 0)
        if total_seconds <= 0:
            total_seconds = int(getattr(ex, "defaults", None).duration_seconds or 0) if getattr(ex, "defaults", None) else 0
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
                "total_minutes": _to_minutes(total_seconds),
                "total_calories": round(total_calories, 1),
                "exercise_id": str(ex.id),
                "exercise_code": getattr(ex, "code", None),
            }
        )

    return {
        "worktype": wtype.value,
        "category_image": category_image,
        "totals": {
            "workouts": len(items),
            "total_minutes": _to_minutes(sum_seconds),
            "total_calories": round(sum_calories, 1),
        },
        "items": items,
    }


@router.get("/discover/workouts/{template_id}")
async def discover_workout_details(template_id: PydanticObjectId):
    ex = await Exercise.get(template_id)
    if not ex or ex.status != "active":
        raise HTTPException(status_code=404, detail="Workout not found")

    media = getattr(ex, "media", None)
    duration_seconds = int(getattr(media, "duration_seconds", 0) or 0) if media else 0
    if duration_seconds <= 0 and getattr(ex, "defaults", None):
        duration_seconds = int(getattr(ex.defaults, "duration_seconds", 0) or 0)

    calories = 0.0
    if getattr(ex, "calories_per_minute", None):
        calories = float(ex.calories_per_minute or 0) * (duration_seconds / 60.0)

    mode_value = str(getattr(ex, "mode", ""))
    step_duration = int(duration_seconds) if mode_value == "time" else None
    set_plan = _resolve_set_plan(ex, step_duration or duration_seconds)
    sets_payload = _build_sets_payload(ex, set_plan)
    set_summaries = [
        {
            "set_id": int(s.get("set_no", 0)),
            "reps_count": len(s.get("reps", []) or []),
        }
        for s in sets_payload
    ]
    total_sets = len(sets_payload)
    rest_seconds_after_exercise = int(
        (sets_payload[-1].get("rest_seconds_after", 0) if sets_payload else 0) or 0
    )
    total_reps = 0
    for s in sets_payload:
        for rep in (s.get("reps") or []):
            target = rep.get("target")
            if target is not None:
                total_reps += int(target)
            else:
                total_reps += 1
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
            "sets": total_sets,
            "reps": total_reps,
            "rest_seconds_after_exercise": rest_seconds_after_exercise,
            "total_seconds": duration_seconds,
            "total_minutes": _to_minutes(duration_seconds),
            "total_calories": round(calories, 1),
        },
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
