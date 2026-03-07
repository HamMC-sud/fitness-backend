import os
from datetime import timedelta

from fastapi import (
    APIRouter,
    HTTPException,
    status,
    BackgroundTasks,
)

from models import User, VerificationCode
from models.users import UserProfile
from schemas.register import RegisterStartIn, RegisterVerifyIn, ResendCodeIn, RegisterCompleteIn
from api.auth.config import hash_password, hash_code, verify_code, now_utc, generate_numeric_code ,create_access_token, create_refresh_token
from utils.email_sender import send_verification_email
from utils.profile_image import normalize_profile_photo_value

router = APIRouter()

CODE_TTL_SECONDS = int(os.getenv("VERIFICATION_CODE_TTL", "300"))
CODE_LENGTH = int(os.getenv("CODE_LENGTH", "4"))
MAX_ATTEMPTS = int(os.getenv("MAX_VERIFICATION_ATTEMPTS", "5"))


@router.post("/register/start", status_code=status.HTTP_200_OK)
async def start_registration(payload: RegisterStartIn, background_tasks: BackgroundTasks):
    email = payload.email.lower().strip()

    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long")

    code = generate_numeric_code(CODE_LENGTH)
    expires_at = now_utc() + timedelta(seconds=CODE_TTL_SECONDS)


    record = await VerificationCode.find_one(VerificationCode.email == email)
    if record:
        record.email = email
        record.password_hash = hash_password(payload.password)
        record.code_hash = hash_code(code)
        record.attempts = 0
        record.created_at = now_utc()
        record.expires_at = expires_at
        record.last_resend = None
        record.verified = False
        await record.save()
    else:
        await VerificationCode(
            email=email,
            password_hash=hash_password(payload.password),
            code_hash=hash_code(code),
            attempts=0,
            created_at=now_utc(),
            expires_at=expires_at,
            verified=False,
        ).insert()

    background_tasks.add_task(send_verification_email, email, code, CODE_TTL_SECONDS)
    return {
        "status": "success",
    }


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
    await record.save()

    return {
        "status": "success",
        "message": "Email verified successfully. Please complete registration with profile information.",
        "email": record.email,
    }


@router.post("/register/resend-code", status_code=status.HTTP_200_OK)
async def resend_code(payload: ResendCodeIn, background_tasks: BackgroundTasks):
    email = payload.email.lower().strip()

    record = await VerificationCode.find_one(VerificationCode.email == email)
    if not record:
        raise HTTPException(status_code=400, detail="No pending verification found. Start registration again.")

    if record.last_resend:
        seconds = (now_utc() - record.last_resend).total_seconds()
        if seconds < 60:
            raise HTTPException(status_code=429, detail="Please wait before requesting a new code")

    new_code = generate_numeric_code(CODE_LENGTH)
    record.code_hash = hash_code(new_code)
    record.attempts = 0
    record.last_resend = now_utc()
    record.expires_at = now_utc() + timedelta(seconds=CODE_TTL_SECONDS)
    await record.save()
    background_tasks.add_task(send_verification_email, email, new_code, CODE_TTL_SECONDS)

    return {
        "status": "code_resent",
        "email": email,
        "expires_in_seconds": CODE_TTL_SECONDS,
    }


@router.post("/register/complete", status_code=status.HTTP_201_CREATED)
async def complete_registration(payload: RegisterCompleteIn):
    email = payload.email.lower().strip()

    record = await VerificationCode.find_one(
        VerificationCode.email == email,
        VerificationCode.verified == True,
    )
    if not record:
        existing = await VerificationCode.find_one(VerificationCode.email == email)
        if existing and not existing.verified:
            raise HTTPException(
                status_code=400,
                detail="Email verification is pending. Complete verification first.",
            )
        raise HTTPException(
            status_code=400,
            detail="Email not verified. For social signup use the exact email returned in 428 PROFILE_REQUIRED response.",
        )

    profile = payload.profile.model_copy(deep=True)

    if profile.photo_url:
        profile.photo_url = normalize_profile_photo_value(profile.photo_url)

    pwd_hash = record.password_hash
    if pwd_hash == "__SOCIAL__":
        pwd_hash = None

    user = User(
        email=record.email,
        email_verified=True,
        password_hash=pwd_hash,
        profile=profile,
    )
    try:
        await user.insert()
    except Exception as e:
        if "duplicate key error" in str(e).lower():
            raise HTTPException(status_code=409, detail="User already exists")
        raise HTTPException(status_code=500, detail=f"Failed to create user: {str(e)[:120]}")
    await record.delete()

    # ✅ Генерация токенов (ВАЖНО: sub = str(user.id))
    access_token = create_access_token(
        sub=str(user.id),
        extra={"email": user.email},
    )

    refresh_token = create_refresh_token(
        sub=str(user.id),
    )

    return {
        "status": "success",
        "access_token": access_token,
        "refresh_token": refresh_token,
    }
