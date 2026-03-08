from __future__ import annotations

import asyncio
import re
from typing import Optional, Dict, Any, List

from beanie.odm.fields import PydanticObjectId
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from api.auth.config import get_current_user
from models.enums import WorkoutType, Difficulty, Equipment
from models.content import Exercise, WorkoutTemplate, WorkoutProgram, MeditationItem
from schemas.programs import (
    WorkoutTemplateCreateIn,
    WorkoutTemplateUpdateIn,
    WorkoutProgramCreateIn,
    WorkoutProgramUpdateIn,
)


class DiscoverCategoryOut(BaseModel):
    key: str
    label: str
    count: int


class DiscoverCategoriesOut(BaseModel):
    workouts: List[DiscoverCategoryOut]
    mind_body: List[DiscoverCategoryOut]

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

def clamp_limit(limit: int) -> int:
    return min(max(limit, 1), 100)

def make_slug(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text or "program"

def regex_search_title(q: str) -> Dict[str, Any]:
    return {
        "$or": [
            {"title.ru": {"$regex": q, "$options": "i"}},
            {"title.en": {"$regex": q, "$options": "i"}},
        ]
    }


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


def _template_metrics(template: WorkoutTemplate, ex_by_id: dict[str, Exercise]) -> dict[str, Any]:
    total_seconds = 0
    total_calories = 0.0
    cover_image = None
    steps = list(template.steps or [])

    for step in steps:
        ex = ex_by_id.get(str(getattr(step, "exercise_id", "")))
        step_seconds = int(getattr(step, "duration_seconds", 0) or 0)
        if step_seconds <= 0 and ex and getattr(ex, "media", None):
            step_seconds = int(getattr(ex.media, "duration_seconds", 0) or 0)
        rest_seconds = int(getattr(step, "rest_seconds_after", 0) or 0)

        total_seconds += step_seconds + rest_seconds

        if ex and getattr(ex, "calories_per_minute", None):
            total_calories += float(ex.calories_per_minute or 0) * (step_seconds / 60.0)

        if not cover_image and ex and getattr(ex, "media", None):
            cover_image = getattr(ex.media, "thumbnail_url", None)

    if total_seconds <= 0:
        total_seconds = int(getattr(template, "estimated_minutes", 0) or 0) * 60

    return {
        "total_seconds": total_seconds,
        "total_minutes": _to_minutes(total_seconds),
        "total_calories": round(total_calories, 1),
        "cover_image": cover_image,
        "exercise_count": len(steps),
    }



async def expand_templates(program: WorkoutProgram) -> Dict[str, Any]:
    data = jsonable_encoder(program)
    ids: list[ObjectId] = []
    for s in (program.schedule or []):
        tid = getattr(s, "workout_template_id", None)
        if tid:
            try:
                ids.append(ObjectId(str(tid)))
            except Exception:
                pass

    ids = list(dict.fromkeys(ids))
    templates = await WorkoutTemplate.find({"_id": {"$in": ids}}).to_list() if ids else []
    data["templates"] = {str(t.id): jsonable_encoder(t) for t in templates}

    return data

def require_auth(user):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

@router.get("/templates")
async def list_templates(
    q: Optional[str] = None,
    type: Optional[WorkoutType] = None,
    level: Optional[Difficulty] = None,
    equipment: Optional[Equipment] = None,
    status: str = "active",
    skip: int = 0,
    limit: int = 20,
):
    limit = clamp_limit(limit)
    filters = [WorkoutTemplate.status == status]

    if type:
        filters.append(WorkoutTemplate.type == type)
    if level:
        filters.append(WorkoutTemplate.level == level)
    if equipment:
        filters.append({"equipment_required": {"$in": equipment_db_aliases(equipment)}})

    query = WorkoutTemplate.find(*filters).sort("-created_at")
    if q:
        query = query.find(regex_search_title(q))

    return {
        "items": await query.skip(skip).limit(limit).to_list(),
        "total": await query.count(),
        "skip": skip,
        "limit": limit,
    }

@router.get("/templates/{template_id}")
async def get_template(template_id: PydanticObjectId):
    t = await WorkoutTemplate.get(template_id)
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return t

async def create_template(payload: WorkoutTemplateCreateIn, user=Depends(get_current_user)):
    require_auth(user)
    doc = WorkoutTemplate(**payload.model_dump())
    await doc.insert()
    return doc

async def update_template(template_id: PydanticObjectId, payload: WorkoutTemplateUpdateIn, user=Depends(get_current_user)):
    require_auth(user)
    doc = await WorkoutTemplate.get(template_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Template not found")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(doc, k, v)

    await doc.save()
    return doc

async def delete_template(template_id: PydanticObjectId, user=Depends(get_current_user)):
    require_auth(user)
    doc = await WorkoutTemplate.get(template_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Template not found")
    await doc.delete()
    return {"status": "ok"}

@router.get("/programs")
async def list_programs(
    q: Optional[str] = None,
    level: Optional[Difficulty] = None,
    interest: Optional[str] = None,
    equipment: Optional[Equipment] = None,
    status: str = "active",
    skip: int = 0,
    limit: int = 20,
):
    limit = clamp_limit(limit)
    filters = [WorkoutProgram.status == status]

    if level:
        filters.append(WorkoutProgram.level == level)
    if interest:
        filters.append(WorkoutProgram.interest == interest)
    if equipment:
        filters.append({"equipment_required": {"$in": equipment_db_aliases(equipment)}})

    query = WorkoutProgram.find(*filters).sort("-created_at")
    if q:
        query = query.find(regex_search_title(q))

    return {
        "items": await query.skip(skip).limit(limit).to_list(),
        "total": await query.count(),
        "skip": skip,
        "limit": limit,
    }


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

    filters: list[Any] = [WorkoutTemplate.status == status, WorkoutTemplate.type == wtype]
    if level:
        filters.append(WorkoutTemplate.level == level)
    if equipment:
        filters.append({"equipment_required": {"$in": equipment_db_aliases(equipment)}})

    templates = await WorkoutTemplate.find(*filters).sort("-created_at").to_list()
    if not templates:
        return {
            "worktype": wtype.value,
            "category_image": None,
            "totals": {
                "workouts": 0,
                "total_seconds": 0,
                "total_minutes": 0,
                "total_calories": 0.0,
            },
            "items": [],
        }

    exercise_ids: list[ObjectId] = []
    for t in templates:
        for s in (t.steps or []):
            sid = getattr(s, "exercise_id", None)
            if sid:
                try:
                    exercise_ids.append(ObjectId(str(sid)))
                except Exception:
                    pass
    exercise_ids = list(dict.fromkeys(exercise_ids))

    exercises = await Exercise.find({"_id": {"$in": exercise_ids}}).to_list() if exercise_ids else []
    ex_by_id = {str(e.id): e for e in exercises}

    items: list[dict[str, Any]] = []
    sum_seconds = 0
    sum_calories = 0.0
    category_image = None

    for t in templates:
        m = _template_metrics(t, ex_by_id)
        sum_seconds += int(m["total_seconds"])
        sum_calories += float(m["total_calories"])
        if not category_image and m["cover_image"]:
            category_image = m["cover_image"]

        items.append(
            {
                "id": str(t.id),
                "title": {
                    "ru": _pick_i18n_text(t.title, "ru"),
                    "en": _pick_i18n_text(t.title, "en"),
                },
                "level": str(t.level.value if hasattr(t.level, "value") else t.level),
                "worktype": wtype.value,
                "cover_image": m["cover_image"],
                "exercise_count": m["exercise_count"],
                "total_seconds": m["total_seconds"],
                "total_minutes": m["total_minutes"],
                "total_calories": m["total_calories"],
            }
        )

    return {
        "worktype": wtype.value,
        "category_image": category_image,
        "totals": {
            "workouts": len(items),
            "total_seconds": sum_seconds,
            "total_minutes": _to_minutes(sum_seconds),
            "total_calories": round(sum_calories, 1),
        },
        "items": items,
    }


@router.get("/discover/workouts/{template_id}")
async def discover_workout_details(template_id: PydanticObjectId):
    template = await WorkoutTemplate.get(template_id)
    if not template or template.status != "active":
        raise HTTPException(status_code=404, detail="Workout not found")

    step_ids: list[ObjectId] = []
    for s in (template.steps or []):
        sid = getattr(s, "exercise_id", None)
        if sid:
            try:
                step_ids.append(ObjectId(str(sid)))
            except Exception:
                pass
    step_ids = list(dict.fromkeys(step_ids))

    exercises = await Exercise.find({"_id": {"$in": step_ids}}).to_list() if step_ids else []
    ex_by_id = {str(e.id): e for e in exercises}

    details: list[dict[str, Any]] = []
    total_seconds = 0
    total_calories = 0.0
    cover_image = None

    ordered_steps = sorted(list(template.steps or []), key=lambda x: int(getattr(x, "order", 0) or 0))
    for idx, step in enumerate(ordered_steps, start=1):
        ex = ex_by_id.get(str(getattr(step, "exercise_id", "")))
        step_seconds = int(getattr(step, "duration_seconds", 0) or 0)
        if step_seconds <= 0 and ex and getattr(ex, "media", None):
            step_seconds = int(getattr(ex.media, "duration_seconds", 0) or 0)
        rest_seconds = int(getattr(step, "rest_seconds_after", 0) or 0)
        total_seconds += step_seconds + rest_seconds

        calories = 0.0
        if ex and getattr(ex, "calories_per_minute", None):
            calories = float(ex.calories_per_minute or 0) * (step_seconds / 60.0)
            total_calories += calories

        if not cover_image and ex and getattr(ex, "media", None):
            cover_image = getattr(ex.media, "thumbnail_url", None)

        details.append(
            {
                "order": idx,
                "exercise_id": str(getattr(step, "exercise_id", "")),
                "mode": str(getattr(step, "mode", "")),
                "reps": getattr(step, "reps", None),
                "duration_seconds": step_seconds,
                "rest_seconds_after": rest_seconds,
                "calories_estimated": round(calories, 1),
                "exercise": {
                    "code": ex.code if ex else None,
                    "name": {
                        "ru": _pick_i18n_text(ex.name, "ru") if ex else "",
                        "en": _pick_i18n_text(ex.name, "en") if ex else "",
                    },
                    "thumbnail_url": getattr(ex.media, "thumbnail_url", None) if ex and getattr(ex, "media", None) else None,
                    "video_url": getattr(ex.media, "video_url", None) if ex and getattr(ex, "media", None) else None,
                    "difficulty": str(ex.difficulty.value if ex and hasattr(ex.difficulty, "value") else getattr(ex, "difficulty", "")) if ex else None,
                },
            }
        )

    if total_seconds <= 0:
        total_seconds = int(getattr(template, "estimated_minutes", 0) or 0) * 60

    return {
        "id": str(template.id),
        "title": {
            "ru": _pick_i18n_text(template.title, "ru"),
            "en": _pick_i18n_text(template.title, "en"),
        },
        "worktype": str(template.type.value if hasattr(template.type, "value") else template.type),
        "level": str(template.level.value if hasattr(template.level, "value") else template.level),
        "cover_image": cover_image,
        "exercise_count": len(details),
        "total_seconds": total_seconds,
        "total_minutes": _to_minutes(total_seconds),
        "total_calories": round(total_calories, 1),
        "exercises": details,
    }

@router.get("/programs/by-id/{program_id}")
async def get_program_by_id(program_id: PydanticObjectId, expand: bool = False):
    p = await WorkoutProgram.get(program_id)
    if not p:
        raise HTTPException(status_code=404, detail="Program not found")
    return await expand_templates(p) if expand else p

@router.get("/programs/{id_or_slug}")
async def get_program(id_or_slug: str, expand: bool = False):
    p = None

    if re.fullmatch(r"[0-9a-fA-F]{24}", id_or_slug):
        try:
            p = await WorkoutProgram.get(PydanticObjectId(id_or_slug))
        except Exception:
            p = None

    if not p:
        p = await WorkoutProgram.find_one(WorkoutProgram.slug == id_or_slug)

    if not p:
        raise HTTPException(status_code=404, detail="Program not found")

    return await expand_templates(p) if expand else p

async def create_program(payload: WorkoutProgramCreateIn, user=Depends(get_current_user)):
    require_auth(user)

    data = payload.model_dump()
    data["slug"] = data.get("slug") or make_slug(payload.title.en)

    if await WorkoutProgram.find_one(WorkoutProgram.slug == data["slug"]):
        raise HTTPException(status_code=409, detail="Slug already exists")

    doc = WorkoutProgram(**data)
    await doc.insert()
    return doc

async def update_program(program_id: PydanticObjectId, payload: WorkoutProgramUpdateIn, user=Depends(get_current_user)):
    require_auth(user)
    doc = await WorkoutProgram.get(program_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Program not found")

    patch = payload.model_dump(exclude_unset=True)
    if "slug" in patch and patch["slug"] != doc.slug:
        if await WorkoutProgram.find_one(WorkoutProgram.slug == patch["slug"]):
            raise HTTPException(status_code=409, detail="Slug already exists")

    for k, v in patch.items():
        setattr(doc, k, v)

    await doc.save()
    return doc

async def delete_program(program_id: PydanticObjectId, user=Depends(get_current_user)):
    require_auth(user)
    doc = await WorkoutProgram.get(program_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Program not found")
    await doc.delete()
    return {"status": "ok"}
