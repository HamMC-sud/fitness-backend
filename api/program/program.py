from __future__ import annotations

from typing import Optional, Any

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder

from models.enums import WorkoutType, Difficulty, Equipment
from models.content import Exercise

router = APIRouter(tags=["content"])


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

    ex_data = jsonable_encoder(ex)
    media = getattr(ex, "media", None)
    duration_seconds = int(getattr(media, "duration_seconds", 0) or 0) if media else 0
    if duration_seconds <= 0 and getattr(ex, "defaults", None):
        duration_seconds = int(getattr(ex.defaults, "duration_seconds", 0) or 0)

    calories = 0.0
    if getattr(ex, "calories_per_minute", None):
        calories = float(ex.calories_per_minute or 0) * (duration_seconds / 60.0)

    mode_value = str(getattr(ex, "mode", ""))
    default_sets = int(getattr(getattr(ex, "defaults", None), "sets", 4) or 4) if getattr(ex, "defaults", None) else 4
    default_reps = getattr(getattr(ex, "defaults", None), "reps", None) if getattr(ex, "defaults", None) else None
    default_rest = int(getattr(getattr(ex, "defaults", None), "rest_seconds_after", 60) or 60) if getattr(ex, "defaults", None) else 60
    step_reps = int(default_reps) if (mode_value == "reps" and default_reps is not None) else None
    step_duration = int(duration_seconds) if mode_value == "time" else None

    return {
        "id": str(ex.id),
        "exercise": ex_data,  # full exercise payload for detailed UI
        "title": {
            "ru": _pick_i18n_text(getattr(ex, "name", None), "ru"),
            "en": _pick_i18n_text(getattr(ex, "name", None), "en"),
        },
        "description": {
            "ru": _pick_i18n_text(getattr(ex, "description", None), "ru"),
            "en": _pick_i18n_text(getattr(ex, "description", None), "en"),
        },
        "worktype": (
            str(ex.workout_type[0].value if hasattr(ex.workout_type[0], "value") else ex.workout_type[0])
            if getattr(ex, "workout_type", None)
            else None
        ),
        "level": str(ex.difficulty.value if hasattr(ex.difficulty, "value") else ex.difficulty),
        "cover_image": getattr(media, "thumbnail_url", None) if media else None,
        "exercise_count": 1,
        "total_seconds": duration_seconds,
        "total_minutes": _to_minutes(duration_seconds),
        "total_calories": round(calories, 1),
        "exercises": [
            {
                "order": 1,
                "exercise_id": str(ex.id),
                "mode": mode_value,
                "sets": default_sets,
                "reps": step_reps,
                "duration_seconds": step_duration,
                "rest_seconds_after": default_rest,
                "calories_estimated": round(calories, 1),
            }
        ],
    }
