
from __future__ import annotations

from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError

from api.auth.config import get_current_user
from models import User, UserProfile
from schemas.profile import ProfileUpdateIn, ProfileSettingsUpdateIn, ProfileSettingsOut

router = APIRouter(tags=["profile"])

def _strip_password(user: User) -> Dict[str, Any]:
    data = user.model_dump(exclude={"password_hash"})
    data["id"] = str(user.id)
    return data


def _settings_out(user: User) -> ProfileSettingsOut:
    return ProfileSettingsOut(
        unit_system=user.unit_system,
        training_rest_seconds=user.training_rest_seconds,
        language=user.language,
    )

@router.get("/profile")
async def get_profile(current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return _strip_password(current_user)


@router.put("/profile")
@router.patch("/profile")
async def update_profile(payload: ProfileUpdateIn, current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    patch = payload.model_dump(exclude_unset=True)
    if not patch:
        return _strip_password(current_user)

    base_profile = {}
    if getattr(current_user, "profile", None):
        try:
            base_profile = current_user.profile.model_dump()
        except Exception:
            base_profile = dict(current_user.profile)

    if "schedule" in patch:
        base_schedule = base_profile.get("schedule", {}) if isinstance(base_profile.get("schedule"), dict) else {}
        patch["schedule"] = {**base_schedule, **patch["schedule"]}

    try:
        merged_profile = UserProfile(**{**base_profile, **patch})
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc

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


@router.get("/profile/settings", response_model=ProfileSettingsOut)
async def get_profile_settings(current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return _settings_out(current_user)


@router.put("/profile/settings", response_model=ProfileSettingsOut)
@router.patch("/profile/settings", response_model=ProfileSettingsOut)
async def update_profile_settings(
    payload: ProfileSettingsUpdateIn,
    current_user: User = Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    patch = payload.model_dump(exclude_unset=True)
    if not patch:
        return _settings_out(current_user)

    if "training_rest_seconds" in patch and patch["training_rest_seconds"] not in {30, 60, 90}:
        raise HTTPException(
            status_code=400,
            detail="training_rest_seconds must be one of: 30, 60, 90",
        )

    await User.find_one(User.id == current_user.id).update({"$set": patch})

    updated_user = await User.get(current_user.id)
    if not updated_user:
        raise HTTPException(status_code=404, detail="User not found")

    return _settings_out(updated_user)
