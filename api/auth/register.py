import logging
import os
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pymongo.errors import DuplicateKeyError

from api.auth.config import (
    _issue_tokens_for_user,
    generate_numeric_code,
    hash_code,
    hash_password,
    now_utc,
    verify_code,
)
from models import User, VerificationCode
from schemas.register import RegisterCompleteIn, RegisterStartIn, RegisterVerifyIn
from utils.email_sender import send_verification_email
from utils.profile_image import normalize_profile_photo_value

router = APIRouter()
logger = logging.getLogger(__name__)

CODE_TTL_SECONDS = int(os.getenv("VERIFICATION_CODE_TTL", "300"))
CODE_LENGTH = int(os.getenv("CODE_LENGTH", "4"))
MAX_ATTEMPTS = int(os.getenv("MAX_VERIFICATION_ATTEMPTS", "5"))
REGISTRATION_COMPLETE_TTL_SECONDS = int(os.getenv("REGISTRATION_COMPLETE_TTL", "1800"))
SOCIAL_PASSWORD_SENTINELS = {"__SOCIAL__", "**SOCIAL**"}


async def _upsert_verification_code(
    *,
    email: str,
    password_hash_value: str,
    code_hash_value: str,
    expires_at,
    verified: bool,
) -> VerificationCode:
    record = await VerificationCode.find_one(VerificationCode.email == email)
    if record:
        record.password_hash = password_hash_value
        record.code_hash = code_hash_value
        record.attempts = 0
        record.created_at = now_utc()
        record.expires_at = expires_at
        record.last_resend = None
        record.verified = verified
        await record.save()
        return record

    try:
        record = VerificationCode(
            email=email,
            password_hash=password_hash_value,
            code_hash=code_hash_value,
            attempts=0,
            created_at=now_utc(),
            expires_at=expires_at,
            verified=verified,
            last_resend=None,
        )
        await record.insert()
        return record
    except DuplicateKeyError:
        logger.info("VerificationCode duplicate detected during create; retrying update for %s", email)
        record = await VerificationCode.find_one(VerificationCode.email == email)
        if not record:
            raise HTTPException(status_code=500, detail="Failed to prepare verification code")
        record.password_hash = password_hash_value
        record.code_hash = code_hash_value
        record.attempts = 0
        record.created_at = now_utc()
        record.expires_at = expires_at
        record.last_resend = None
        record.verified = verified
        await record.save()
        return record


@router.post("/register/start", status_code=status.HTTP_200_OK)
async def start_registration(payload: RegisterStartIn, background_tasks: BackgroundTasks):
    email = payload.email.lower().strip()

    existing_user = await User.find_one(User.email == email)
    if existing_user:
        raise HTTPException(status_code=409, detail="User already exists")

    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long")

    code = generate_numeric_code(CODE_LENGTH)
    expires_at = now_utc() + timedelta(seconds=CODE_TTL_SECONDS)

    await _upsert_verification_code(
        email=email,
        password_hash_value=hash_password(payload.password),
        code_hash_value=hash_code(code),
        expires_at=expires_at,
        verified=False,
    )

    background_tasks.add_task(send_verification_email, email, code, CODE_TTL_SECONDS)
    return {"status": "success"}


@router.post("/register/verify", status_code=status.HTTP_201_CREATED)
async def verify_and_register(payload: RegisterVerifyIn):
    email = payload.email.lower().strip()
    code = payload.code.strip()

    if not code.isdigit() or len(code) != CODE_LENGTH:
        raise HTTPException(status_code=400, detail=f"Code must be {CODE_LENGTH} digits")

    record = await VerificationCode.find_one(VerificationCode.email == email)
    if not record:
        raise HTTPException(status_code=400, detail="No pending verification found. Start registration again.")

    if record.expires_at < now_utc():
        await record.delete()
        raise HTTPException(status_code=400, detail="Code expired. Start registration again.")

    if record.attempts >= MAX_ATTEMPTS:
        await record.delete()
        raise HTTPException(status_code=429, detail="Too many attempts. Start registration again.")

    if not verify_code(code, record.code_hash):
        record.attempts += 1
        await record.save()
        remaining = max(0, MAX_ATTEMPTS - record.attempts)
        raise HTTPException(status_code=400, detail={"message": "Invalid code", "remaining_attempts": remaining})

    record.verified = True
    record.expires_at = now_utc() + timedelta(seconds=REGISTRATION_COMPLETE_TTL_SECONDS)
    await record.save()

    return {
        "status": "success",
        "message": "Email verified successfully. Please complete registration with profile information.",
        "email": record.email,
        "expires_at": record.expires_at,
    }


@router.post("/register/complete", status_code=status.HTTP_201_CREATED)
async def complete_registration(payload: RegisterCompleteIn, request: Request):
    email = payload.email.lower().strip()

    existing_user = await User.find_one(User.email == email)
    if existing_user:
        raise HTTPException(status_code=409, detail="User already exists")

    record = await VerificationCode.find_one(VerificationCode.email == email)
    if not record:
        raise HTTPException(status_code=400, detail="Email verification not found. Start registration again.")
    if not record.verified:
        raise HTTPException(status_code=400, detail="Email verification is pending. Complete verification first.")
    if record.expires_at < now_utc():
        await record.delete()
        raise HTTPException(status_code=400, detail="Registration session expired. Start registration again.")

    profile = payload.profile.model_copy(deep=True)
    if profile.photo_url:
        profile.photo_url = normalize_profile_photo_value(profile.photo_url)

    password_hash_value = None if record.password_hash in SOCIAL_PASSWORD_SENTINELS else record.password_hash
    user = User(
        email=record.email,
        email_verified=True,
        password_hash=password_hash_value,
        profile=profile,
    )

    try:
        await user.insert()
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="User already exists")
    except Exception:
        logger.exception("Failed to create user during registration completion for %s", email)
        raise HTTPException(status_code=500, detail="Failed to create user")

    await record.delete()
    tokens = await _issue_tokens_for_user(user, request)
    return {
        "status": "success",
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
    }
