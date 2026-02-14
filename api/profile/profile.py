# api/profile/profile.py

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

# Where files are stored on disk
UPLOAD_DIR = Path("static/uploads/profile")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# What the client will use to open the image
PUBLIC_URL_PREFIX = "/static/uploads/profile"

# Allowed content-types -> file extensions
ALLOWED: Dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

MAX_BYTES = 5 * 1024 * 1024  # 5MB


def _strip_password(user: User) -> Dict[str, Any]:
    data = user.model_dump(exclude={"password_hash"})
    data["id"] = str(user.id)
    return data

def _safe_remove_old_photo(old_photo_url: str | None) -> None:
    """
    Deletes old file ONLY if it points inside our upload directory.
    This avoids deleting arbitrary files.
    """
    if not old_photo_url:
        return
    if not old_photo_url.startswith(PUBLIC_URL_PREFIX + "/"):
        return

    filename = old_photo_url.split("/")[-1]
    old_path = UPLOAD_DIR / filename

    # Ensure the file is inside UPLOAD_DIR
    try:
        old_path.resolve().relative_to(UPLOAD_DIR.resolve())
    except Exception:
        return

    try:
        if old_path.exists():
            old_path.unlink()
    except Exception:
        # Don't fail the request if deletion fails
        pass


@router.get("/profile")
async def get_profile(current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return _strip_password(current_user)


@router.put("/profile")
async def update_profile(payload: ProfileUpdateIn, current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    # ✅ merge updates into existing profile to avoid wiping photo_url or other fields
    patch = payload.model_dump(exclude_unset=True)

    base_profile = {}
    if getattr(current_user, "profile", None):
        # current_user.profile might be a Pydantic model or dict-like
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


@router.post("/profile/upload-photo")
async def upload_photo(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    ext = ALLOWED.get(file.content_type or "")
    if not ext:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only jpeg/png/webp allowed")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File too large (max 5MB)")

    # Optional: delete previous photo to avoid filling disk
    old_photo_url = None
    try:
        if getattr(current_user, "profile", None):
            old_photo_url = getattr(current_user.profile, "photo_url", None)
    except Exception:
        old_photo_url = None

    name = f"{str(current_user.id)}_{uuid.uuid4().hex}.{ext}"
    path = UPLOAD_DIR / name

    # async-safe write (no blocking)
    await anyio.to_thread.run_sync(path.write_bytes, data)

    photo_url = f"{PUBLIC_URL_PREFIX}/{name}"

    await User.find_one(User.id == current_user.id).update(
        {"$set": {"profile.photo_url": photo_url}}
    )

    # delete old photo AFTER new is saved + db updated
    _safe_remove_old_photo(old_photo_url)

    return {"photo_url": photo_url}
