from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError

from api.auth.config import get_current_user
from models import Subscription, User, UserProfile
from api.subscription.subscription import sub_to_out
from schemas.profile import ProfileUpdateIn
from utils.profile_image import normalize_profile_photo_value

router = APIRouter(tags=["profile"])
logger = logging.getLogger("uvicorn.error")


def _safe_user_id(user: User | None) -> str:
    return str(getattr(user, "id", "") or "")


def _safe_email(user: User | None) -> str:
    email = str(getattr(user, "email", "") or "")
    if not email or "@" not in email:
        return ""
    name, domain = email.split("@", 1)
    return f"{name[:2]}***@{domain}"


async def _profile_response(user: User) -> Dict[str, Any]:
    logger.info(
        "Profile response build started: user_id=%s email=%s language=%s has_profile=%s",
        _safe_user_id(user),
        _safe_email(user),
        str(getattr(user, "language", "") or ""),
        bool(getattr(user, "profile", None)),
    )

    data = user.model_dump(exclude={"password_hash"})
    data["id"] = str(user.id)
    data["is_fully_ready"] = bool(getattr(user, "profile", None)) and bool(
        getattr(user, "onboarding_required_completed", False)
        if getattr(user, "onboarding_required", True)
        else True
    )

    subscription = await Subscription.find_one(Subscription.user_id == user.id)
    data["subscription"] = sub_to_out(subscription).model_dump() if subscription else None

    logger.info(
        "Profile response build finished: user_id=%s is_fully_ready=%s has_subscription=%s subscription_status=%s",
        _safe_user_id(user),
        bool(data["is_fully_ready"]),
        bool(subscription),
        str(getattr(subscription, "status", "") or "") if subscription else "",
    )

    return data


@router.get("/profile")
async def get_profile(current_user: User = Depends(get_current_user)):
    if not current_user:
        logger.warning("Profile fetch rejected: unauthorized")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    logger.info(
        "Profile fetch started: user_id=%s email=%s language=%s onboarding_required=%s onboarding_completed=%s last_onboarding_step=%s",
        _safe_user_id(current_user),
        _safe_email(current_user),
        str(getattr(current_user, "language", "") or ""),
        bool(getattr(current_user, "onboarding_required", True)),
        bool(getattr(current_user, "onboarding_required_completed", False)),
        str(getattr(current_user, "last_onboarding_step", "") or ""),
    )

    response = await _profile_response(current_user)

    logger.info(
        "Profile fetch finished: user_id=%s returned_keys=%s",
        _safe_user_id(current_user),
        sorted(response.keys()),
    )

    return response


@router.put("/profile")
@router.patch("/profile")
async def update_profile(payload: ProfileUpdateIn, current_user: User = Depends(get_current_user)):
    if not current_user:
        logger.warning("Profile update rejected: unauthorized")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    incoming_patch = payload.model_dump(exclude_unset=True)

    logger.info(
        "Profile update started: user_id=%s email=%s incoming_keys=%s",
        _safe_user_id(current_user),
        _safe_email(current_user),
        sorted(incoming_patch.keys()),
    )

    if not incoming_patch:
        logger.info(
            "Profile update skipped: empty payload user_id=%s",
            _safe_user_id(current_user),
        )
        return await _profile_response(current_user)

    user_patch: Dict[str, Any] = {}

    if "language" in incoming_patch:
        old_language = str(getattr(current_user, "language", "") or "")
        new_language = str(incoming_patch.get("language") or "")

        user_patch["language"] = incoming_patch.pop("language")

        logger.info(
            "Profile language update requested: user_id=%s from=%s to=%s",
            _safe_user_id(current_user),
            old_language,
            new_language,
        )

    patch = incoming_patch

    onboarding_completed = patch.pop("onboarding_required_completed", None) if "onboarding_required_completed" in patch else None
    onboarding_version = patch.pop("onboarding_version", None) if "onboarding_version" in patch else None
    last_onboarding_step = patch.pop("last_onboarding_step", None) if "last_onboarding_step" in patch else None

    logger.info(
        "Profile update extracted onboarding fields: user_id=%s onboarding_completed=%s onboarding_version=%s last_onboarding_step=%s remaining_profile_keys=%s",
        _safe_user_id(current_user),
        str(onboarding_completed) if onboarding_completed is not None else "not_provided",
        str(onboarding_version or "") if onboarding_version is not None else "not_provided",
        str(last_onboarding_step or "") if last_onboarding_step is not None else "not_provided",
        sorted(patch.keys()),
    )

    base_profile = {}
    if getattr(current_user, "profile", None):
        try:
            base_profile = current_user.profile.model_dump()
            logger.info(
                "Profile base profile loaded via model_dump: user_id=%s base_profile_keys=%s",
                _safe_user_id(current_user),
                sorted(base_profile.keys()),
            )
        except Exception:
            logger.exception(
                "Profile base profile model_dump failed, fallback to dict: user_id=%s",
                _safe_user_id(current_user),
            )
            base_profile = dict(current_user.profile)

    if "schedule" in patch:
        base_schedule = base_profile.get("schedule", {}) if isinstance(base_profile.get("schedule"), dict) else {}
        incoming_schedule = patch["schedule"] if isinstance(patch["schedule"], dict) else {}

        logger.info(
            "Profile schedule merge: user_id=%s base_schedule_keys=%s incoming_schedule_keys=%s",
            _safe_user_id(current_user),
            sorted(base_schedule.keys()),
            sorted(incoming_schedule.keys()),
        )

        patch["schedule"] = {**base_schedule, **incoming_schedule}

    if "photo_url" in patch and patch["photo_url"]:
        old_photo = str(base_profile.get("photo_url") or "")
        patch["photo_url"] = normalize_profile_photo_value(
            patch["photo_url"],
            existing_photo_url=base_profile.get("photo_url"),
        )

        logger.info(
            "Profile photo normalized: user_id=%s had_old_photo=%s has_new_photo=%s",
            _safe_user_id(current_user),
            bool(old_photo),
            bool(patch["photo_url"]),
        )

    if "training_rest_seconds" in incoming_patch:
        old_rest = getattr(current_user, "training_rest_seconds", None)
        new_rest = incoming_patch.pop("training_rest_seconds")

        user_patch["training_rest_seconds"] = new_rest

        logger.info(
            "Profile training_rest_seconds update requested: user_id=%s from=%s to=%s",
            _safe_user_id(current_user),
            str(old_rest),
            str(new_rest),
        )

    set_patch: Dict[str, Any] = {}

    if patch:
        try:
            merged_profile = UserProfile(**{**base_profile, **patch})
        except ValidationError as exc:
            logger.warning(
                "Profile validation failed: user_id=%s profile_patch_keys=%s errors=%s",
                _safe_user_id(current_user),
                sorted(patch.keys()),
                exc.errors(),
            )
            raise HTTPException(status_code=400, detail=exc.errors()) from exc

        set_patch["profile"] = merged_profile.model_dump()

        logger.info(
            "Profile merged successfully: user_id=%s profile_patch_keys=%s",
            _safe_user_id(current_user),
            sorted(patch.keys()),
        )

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

        logger.info(
            "Profile onboarding completion update requested: user_id=%s completed=%s",
            _safe_user_id(current_user),
            completed_flag,
        )

    if user_patch:
        set_patch.update(user_patch)

    logger.info(
        "Profile update final patch prepared: user_id=%s set_keys=%s",
        _safe_user_id(current_user),
        sorted(set_patch.keys()),
    )

    if set_patch:
        try:
            await User.find_one(User.id == current_user.id).update({"$set": set_patch})
        except Exception:
            logger.exception(
                "Profile patch failed: user_id=%s email=%s set_keys=%s",
                _safe_user_id(current_user),
                _safe_email(current_user),
                sorted(set_patch.keys()),
            )
            raise

        logger.info(
            "Profile patch success: user_id=%s set_keys=%s selected_language=%s onboarding_completed=%s onboarding_version=%s last_onboarding_step=%s",
            _safe_user_id(current_user),
            sorted(set_patch.keys()),
            str(user_patch.get("language") or getattr(current_user, "language", "") or ""),
            bool(set_patch.get("onboarding_required_completed", getattr(current_user, "onboarding_required_completed", False))),
            str(set_patch.get("onboarding_version", getattr(current_user, "onboarding_version", "")) or ""),
            str(set_patch.get("last_onboarding_step", getattr(current_user, "last_onboarding_step", "")) or ""),
        )

    updated_user = await User.get(current_user.id)

    if not updated_user:
        logger.error(
            "Profile update finished but user not found: user_id=%s",
            _safe_user_id(current_user),
        )
        raise HTTPException(status_code=404, detail="User not found")

    logger.info(
        "Profile update finished: user_id=%s language_before=%s language_after=%s onboarding_completed_after=%s",
        _safe_user_id(updated_user),
        str(getattr(current_user, "language", "") or ""),
        str(getattr(updated_user, "language", "") or ""),
        bool(getattr(updated_user, "onboarding_required_completed", False)),
    )

    return await _profile_response(updated_user)
