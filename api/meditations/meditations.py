from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth.config import get_current_user
from models.content import MeditationItem
from models import MeditationRun
from schemas.meditations import (
    MeditationListOut,
    MeditationItemOut,
    MeditationCreateIn,
    MeditationUpdateIn,
    MeditationCompleteIn,
    MeditationRunOut,
    MeditationRunListOut,
)


router = APIRouter(tags=["meditations"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_type(v: str) -> str:
    t = (v or "").strip().lower()
    if t not in ("meditation", "yoga"):
        raise HTTPException(status_code=400, detail="type must be 'meditation' or 'yoga'")
    return t


def clamp_limit(limit: int) -> int:
    return min(max(limit, 1), 100)


@router.get("/meditations", response_model=MeditationListOut)
async def list_meditations(
    q: Optional[str] = Query(default=None),
    type: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    status: str = Query(default="active"),
    skip: int = 0,
    limit: int = 20,
):
    limit = clamp_limit(limit)

    base: Dict[str, Any] = {"status": status}
    if type is not None:
        base["type"] = normalize_type(type)
    if tag:
        base["tags"] = tag

    flt: Dict[str, Any] = base
    if q:
        flt = {
            "$and": [
                base,
                {
                    "$or": [
                        {"title.ru": {"$regex": q, "$options": "i"}},
                        {"title.en": {"$regex": q, "$options": "i"}},
                    ]
                },
            ]
        }

    query = MeditationItem.find(flt).sort("-created_at")
    total = await query.count()
    items = await query.skip(skip).limit(limit).to_list()

    out_items = []
    for it in items:
        d = it.model_dump()
        d["id"] = str(it.id)
        out_items.append(MeditationItemOut(**d))

    return MeditationListOut(items=out_items, total=total, skip=skip, limit=limit)




@router.get("/meditations/history", response_model=MeditationRunListOut)
async def list_meditation_history(
    current_user=Depends(get_current_user),
    skip: int = 0,
    limit: int = 20,
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    limit = clamp_limit(limit)
    query = MeditationRun.find({"user_id": current_user.id}).sort("-completed_at")
    total = await query.count()
    runs = await query.skip(skip).limit(limit).to_list()

    items = []
    for r in runs:
        items.append(
            MeditationRunOut(
                id=str(r.id),
                meditation_id=str(r.meditation_id),
                type=r.type,
                completed_at=r.completed_at,
                seconds_done=r.seconds_done,
                points=r.points,
            )
        )

    return MeditationRunListOut(items=items, total=total, skip=skip, limit=limit)




@router.get("/meditations/{item_id}", response_model=MeditationItemOut)
async def get_meditation(item_id: PydanticObjectId):
    doc = await MeditationItem.get(item_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    d = doc.model_dump()
    d["id"] = str(doc.id)
    return MeditationItemOut(**d)


@router.post("/meditations", response_model=MeditationItemOut)
async def create_meditation(payload: MeditationCreateIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    t = normalize_type(payload.type)
    doc = MeditationItem(
        type=t,
        title=payload.title.model_dump(),
        description=payload.description.model_dump(),
        duration_minutes=payload.duration_minutes,
        media=payload.media,
        tags=payload.tags,
        status=payload.status,
    )
    await doc.insert()
    d = doc.model_dump()
    d["id"] = str(doc.id)
    return MeditationItemOut(**d)



@router.put("/meditations/{item_id}", response_model=MeditationItemOut)
async def update_meditation(item_id: PydanticObjectId, payload: MeditationUpdateIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    doc = await MeditationItem.get(item_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")

    patch = payload.model_dump(exclude_unset=True)

    if "type" in patch and patch["type"] is not None:
        patch["type"] = normalize_type(patch["type"])

    for k, v in patch.items():
        setattr(doc, k, v)

    await doc.save()
    d = doc.model_dump()
    d["id"] = str(doc.id)
    return MeditationItemOut(**d)


@router.delete("/meditations/{item_id}")
async def delete_meditation(item_id: PydanticObjectId, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    doc = await MeditationItem.get(item_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")

    await doc.delete()
    return {"status": "ok"}


@router.post("/meditations/{item_id}/complete", response_model=MeditationRunOut)
async def complete_meditation(item_id: PydanticObjectId, payload: MeditationCompleteIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    item = await MeditationItem.get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")

    t = normalize_type(item.type)
    points = 10 if t == "yoga" else 5

    seconds_done = int(payload.seconds_done or 0)
    if seconds_done <= 0:
        seconds_done = int(item.duration_minutes) * 60

    run = MeditationRun(
        user_id=current_user.id,
        meditation_id=item.id,
        type=t,
        completed_at=utcnow(),
        seconds_done=seconds_done,
        points=points,
    )
    await run.insert()

    return MeditationRunOut(
        id=str(run.id),
        meditation_id=str(item.id),
        type=t,
        completed_at=run.completed_at,
        seconds_done=run.seconds_done,
        points=run.points,
    )