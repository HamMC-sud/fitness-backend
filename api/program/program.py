from __future__ import annotations

import re
from typing import Optional, Dict, Any

from beanie.odm.fields import PydanticObjectId
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from api.auth.config import get_current_user
from models.enums import WorkoutType, Difficulty, Equipment
from models.content import WorkoutTemplate, WorkoutProgram
from schemas.programs import (
    WorkoutTemplateCreateIn,
    WorkoutTemplateUpdateIn,
    WorkoutProgramCreateIn,
    WorkoutProgramUpdateIn,
)

router = APIRouter(tags=["content"])

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
        filters.append(WorkoutTemplate.equipment_required == equipment)

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

@router.post("/templates")
async def create_template(payload: WorkoutTemplateCreateIn, user=Depends(get_current_user)):
    require_auth(user)
    doc = WorkoutTemplate(**payload.model_dump())
    await doc.insert()
    return doc

@router.put("/templates/{template_id}")
async def update_template(template_id: PydanticObjectId, payload: WorkoutTemplateUpdateIn, user=Depends(get_current_user)):
    require_auth(user)
    doc = await WorkoutTemplate.get(template_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Template not found")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(doc, k, v)

    await doc.save()
    return doc

@router.delete("/templates/{template_id}")
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
    location: Optional[str] = None,
    equipment: Optional[Equipment] = None,
    status: str = "active",
    skip: int = 0,
    limit: int = 20,
):
    limit = clamp_limit(limit)
    filters = [WorkoutProgram.status == status]

    if level:
        filters.append(WorkoutProgram.level == level)
    if location:
        filters.append(WorkoutProgram.location == location)
    if equipment:
        filters.append(WorkoutProgram.equipment_required == equipment)

    query = WorkoutProgram.find(*filters).sort("-created_at")
    if q:
        query = query.find(regex_search_title(q))

    return {
        "items": await query.skip(skip).limit(limit).to_list(),
        "total": await query.count(),
        "skip": skip,
        "limit": limit,
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

@router.post("/programs")
async def create_program(payload: WorkoutProgramCreateIn, user=Depends(get_current_user)):
    require_auth(user)

    data = payload.model_dump()
    data["slug"] = data.get("slug") or make_slug(payload.title.en)

    if await WorkoutProgram.find_one(WorkoutProgram.slug == data["slug"]):
        raise HTTPException(status_code=409, detail="Slug already exists")

    doc = WorkoutProgram(**data)
    await doc.insert()
    return doc

@router.put("/programs/{program_id}")
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

@router.delete("/programs/{program_id}")
async def delete_program(program_id: PydanticObjectId, user=Depends(get_current_user)):
    require_auth(user)
    doc = await WorkoutProgram.get(program_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Program not found")
    await doc.delete()
    return {"status": "ok"}
