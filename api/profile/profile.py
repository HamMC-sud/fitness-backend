
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError

from api.auth.config import get_current_user
from models import User, UserProfile
from schemas.profile import ProfileUpdateIn
from utils.profile_image import normalize_profile_photo_value

router = APIRouter(tags=["profile"])
logger = logging.getLogger(__name__)

def _strip_password(user: User) -> Dict[str, Any]:
    data = user.model_dump(exclude={"password_hash"})
    data["id"] = str(user.id)
    data["is_fully_ready"] = bool(getattr(user, "profile", None)) and bool(
        getattr(user, "onboarding_required_completed", False)
        if getattr(user, "onboarding_required", True)
        else True
    )
    return data


@router.get("/profile")
async def get_profile(current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    logger.info(
        "Profile fetch: user_id=%s profile.language=%s onboarding_required=%s onboarding_completed=%s last_onboarding_step=%s",
        str(current_user.id),
        str(getattr(current_user, "language", "")),
        bool(getattr(current_user, "onboarding_required", True)),
        bool(getattr(current_user, "onboarding_required_completed", False)),
        str(getattr(current_user, "last_onboarding_step", "") or ""),
    )
    return _strip_password(current_user)


@router.put("/profile")
@router.patch("/profile")
async def update_profile(payload: ProfileUpdateIn, current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    incoming_patch = payload.model_dump(exclude_unset=True)
    if not incoming_patch:
        return _strip_password(current_user)

    user_patch: Dict[str, Any] = {}
    if "language" in incoming_patch:
        user_patch["language"] = incoming_patch.pop("language")
        logger.info(
            "Profile language update requested: user_id=%s from=%s to=%s",
            str(current_user.id),
            str(getattr(current_user, "language", "")),
            str(user_patch["language"]),
        )

    patch = incoming_patch
    onboarding_completed = patch.pop("onboarding_required_completed", None) if "onboarding_required_completed" in patch else None
    onboarding_version = patch.pop("onboarding_version", None) if "onboarding_version" in patch else None
    last_onboarding_step = patch.pop("last_onboarding_step", None) if "last_onboarding_step" in patch else None

    base_profile = {}
    if getattr(current_user, "profile", None):
        try:
            base_profile = current_user.profile.model_dump()
        except Exception:
            base_profile = dict(current_user.profile)

    if "schedule" in patch:
        base_schedule = base_profile.get("schedule", {}) if isinstance(base_profile.get("schedule"), dict) else {}
        patch["schedule"] = {**base_schedule, **patch["schedule"]}

    if "photo_url" in patch and patch["photo_url"]:
        patch["photo_url"] = normalize_profile_photo_value(
            patch["photo_url"],
            existing_photo_url=base_profile.get("photo_url"),
        )

    if "training_rest_seconds" in incoming_patch:
        user_patch["training_rest_seconds"] = incoming_patch.pop("training_rest_seconds")

    set_patch: Dict[str, Any] = {}
    if patch:
        try:
            merged_profile = UserProfile(**{**base_profile, **patch})
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.errors()) from exc
        set_patch["profile"] = merged_profile.model_dump()

    if onboarding_version is not None:
        set_patch["onboarding_version"] = onboarding_version.strip()
    if last_onboarding_step is not None:
        set_patch["last_onboarding_step"] = last_onboarding_step.strip()
    if onboarding_completed is not None:
        completed_flag = bool(onboarding_completed)
        set_patch["onboarding_required_completed"] = completed_flag
        set_patch["onboarding_required_completed_at"] = (
            datetime.now(timezone.utc) if completed_flag else None
        )
        set_patch["flags.onboarding_completed"] = completed_flag

    if user_patch:
        set_patch.update(user_patch)

    if set_patch:
        try:
            await User.find_one(User.id == current_user.id).update({"$set": set_patch})
        except Exception:
            logger.exception(
                "Profile patch failed: user_id=%s selected_language=%s payload_keys=%s",
                str(current_user.id),
                str(user_patch.get("language") or getattr(current_user, "language", "")),
                sorted(set_patch.keys()),
            )
            raise
        logger.info(
            "Profile patch success: user_id=%s profile.language=%s selected_language=%s onboarding_completed=%s onboarding_version=%s last_onboarding_step=%s",
            str(current_user.id),
            str(getattr(current_user, "language", "")),
            str(user_patch.get("language") or getattr(current_user, "language", "")),
            bool(set_patch.get("onboarding_required_completed", getattr(current_user, "onboarding_required_completed", False))),
            str(set_patch.get("onboarding_version", getattr(current_user, "onboarding_version", "")) or ""),
            str(set_patch.get("last_onboarding_step", getattr(current_user, "last_onboarding_step", "")) or ""),
        )

    updated_user = await User.get(current_user.id)
    if not updated_user:
        raise HTTPException(status_code=404, detail="User not found")

    return _strip_password(updated_user)
