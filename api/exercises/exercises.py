import asyncio
from typing import Optional

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth.config import get_current_user
from models import Exercise
from models.enums import Difficulty, WorkoutType, Equipment, Injury, ExerciseMode
from schemas.exercises import ExerciseListOut, ExerciseOut, ExerciseCategoriesOut, ExerciseCategoryOut

router = APIRouter()


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


# Maps category key → MongoDB filter dict
_CATEGORY_FILTERS: dict[str, dict] = {
    "legs_glutes": {"muscle_groups": {"$in": ["quads", "glutes", "hamstrings", "calves"]}},
    "upper_body":  {"muscle_groups": {"$in": ["chest", "back", "shoulders"]}},
    "arms":        {"muscle_groups": {"$in": ["biceps", "triceps"]}},
    "core_abs":    {"muscle_groups": {"$in": ["core"]}},
    "cardio_hiit": {"workout_type": {"$in": ["cardio", "hiit"]}},
    "stretching":  {"workout_type": "stretching"},
    "yoga":        {"workout_type": "yoga"},
}

_CATEGORY_LABELS: dict[str, str] = {
    "legs_glutes": "Legs & Glutes",
    "upper_body":  "Upper Body",
    "arms":        "Arms",
    "core_abs":    "Core & Abs",
    "cardio_hiit": "Cardio & HIIT",
    "stretching":  "Stretching",
    "yoga":        "Yoga",
}

_CATEGORY_ORDER = ["legs_glutes", "upper_body", "arms", "core_abs", "cardio_hiit", "stretching", "yoga"]


@router.get("/exercises/categories", response_model=ExerciseCategoriesOut)
async def list_exercise_categories(
    current_user=Depends(get_current_user),
    status: str = Query(default="active"),
):
    async def _count(key: str) -> int:
        f = {"status": status, **_CATEGORY_FILTERS[key]}
        return await Exercise.find(f).count()

    counts = await asyncio.gather(*[_count(k) for k in _CATEGORY_ORDER])

    return ExerciseCategoriesOut(
        items=[
            ExerciseCategoryOut(key=k, label=_CATEGORY_LABELS[k], count=c)
            for k, c in zip(_CATEGORY_ORDER, counts)
        ]
    )


@router.get("/exercises", response_model=ExerciseListOut)
async def list_exercises(
    current_user=Depends(get_current_user),
    q: Optional[str] = Query(default=None),
    status: str = Query(default="active"),
    category: Optional[str] = Query(default=None),
    difficulty: Optional[Difficulty] = None,
    mode: Optional[ExerciseMode] = None,
    workout_type: Optional[WorkoutType] = None,
    equipment: Optional[Equipment] = None,
    contraindication: Optional[Injury] = None,
    muscle_group: Optional[str] = None,
    movement_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
):
    limit = min(max(limit, 1), 100)

    filters = [Exercise.status == status]

    if category is not None:
        cat_filter = _CATEGORY_FILTERS.get(category)
        if not cat_filter:
            raise HTTPException(400, f"Unknown category '{category}'. Valid: {', '.join(_CATEGORY_ORDER)}")
        filters.append(cat_filter)

    if difficulty is not None:
        filters.append(Exercise.difficulty == difficulty)

    if mode is not None:
        filters.append({"media.mode": mode.value})

    if workout_type is not None:
        filters.append(Exercise.workout_type == workout_type)

    if equipment is not None:
        filters.append({"equipment": {"$in": equipment_db_aliases(equipment)}})

    if contraindication is not None:
        filters.append(Exercise.contraindications == contraindication)

    if muscle_group is not None:
        filters.append(Exercise.muscle_groups == muscle_group)

    if movement_type is not None:
        filters.append(Exercise.movement_type == movement_type)

    query = Exercise.find(*filters)

    if q:
        query = query.find({"$text": {"$search": q}})

    items = await query.skip(skip).limit(limit).to_list()
    total = await query.count()

    return {
        "items": items,
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/exercises/{exercise_id}", response_model=ExerciseOut)
async def get_exercise(exercise_id: PydanticObjectId):
    exercise = await Exercise.get(exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    return exercise


@router.get("/exercises/by-code/{code}", response_model=ExerciseOut)
async def get_exercise_by_code(code: str):
    exercise = await Exercise.find_one(Exercise.code == code)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    return exercise
