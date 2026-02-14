from typing import Optional

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth.config import get_current_user
from models import Exercise
from models.enums import Difficulty, WorkoutType, Equipment, Injury, ExerciseMode
from schemas.exercises import ExerciseListOut, ExerciseOut

router = APIRouter()


@router.get("/exercises", response_model=ExerciseListOut)
async def list_exercises(
    current_user=Depends(get_current_user),
    q: Optional[str] = Query(default=None),
    status: str = Query(default="active"),
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

    if difficulty is not None:
        filters.append(Exercise.difficulty == difficulty)

    if mode is not None:
        filters.append(Exercise.media.mode == mode)

    if workout_type is not None:
        filters.append(Exercise.workout_type == workout_type)

    if equipment is not None:
        filters.append(Exercise.equipment == equipment)

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
