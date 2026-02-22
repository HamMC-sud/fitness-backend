
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Dict , Any

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from api.auth.config import get_current_user
from models import User, UserProfile
from schemas.profile import ProfileUpdateIn

router = APIRouter(tags=["profile"])

def _strip_password(user: User) -> Dict[str, Any]:
    data = user.model_dump(exclude={"password_hash"})
    data["id"] = str(user.id)
    return data

@router.get("/profile")
async def get_profile(current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return _strip_password(current_user)


@router.put("/profile")
async def update_profile(payload: ProfileUpdateIn, current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    patch = payload.model_dump(exclude_unset=True)

    base_profile = {}
    if getattr(current_user, "profile", None):
        try:
            base_profile = current_user.profile.model_dump()
        except Exception:
            base_profile = dict(current_user.profile)

    merged_profile = UserProfile(**{**base_profile, **patch})

    await User.find_one(User.id == current_user.id).update(
        {
            "$set": {
                "profile": merged_profile.model_dump(),
                "flags.onboarding_completed": True,
            }
        }
    )

    updated_user = await User.get(current_user.id)
    if not updated_user:
        raise HTTPException(status_code=404, detail="User not found")

    return _strip_password(updated_user)