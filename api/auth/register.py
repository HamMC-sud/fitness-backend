import os
import re
import secrets
from datetime import datetime, timezone, timedelta
import bcrypt
from fastapi import APIRouter, HTTPException, status
from models import User 
from models import VerificationCode 
from schemas.register import RegisterStartIn, RegisterVerifyIn, ResendCodeIn, RegisterCompleteIn
from api.auth.config import hash_password, hash_code, verify_code , now_utc , generate_numeric_code

router = APIRouter()

CODE_TTL_SECONDS = int(os.getenv("VERIFICATION_CODE_TTL", "300"))
CODE_LENGTH = int(os.getenv("CODE_LENGTH", "4"))
MAX_ATTEMPTS = int(os.getenv("MAX_VERIFICATION_ATTEMPTS", "5"))




@router.post("/register/start", status_code=status.HTTP_200_OK)
async def start_registration(payload: RegisterStartIn):
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

    return {
        "code": code,
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
async def resend_code(payload: ResendCodeIn):
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

    return {
        "status": "code_resent",
        "email": email,
        "expires_in_seconds": CODE_TTL_SECONDS,
        "code_length": CODE_LENGTH,
        "code": new_code,  # <-- dev mode
    }


@router.post("/register/complete", status_code=status.HTTP_201_CREATED)
async def complete_registration(payload: RegisterCompleteIn):
    email = payload.email.lower().strip()

    record = await VerificationCode.find_one(VerificationCode.email == email and VerificationCode.verified == True)
    if not record:
        raise HTTPException(status_code=400, detail="Email not verified. Please verify your email first.")

    user = User(
        email=record.email,
        email_verified=True,
        password_hash=record.password_hash,
        profile=payload.profile,
    )

    try:
        await user.insert()
    except Exception as e:
        if "duplicate key error" in str(e).lower():
            raise HTTPException(status_code=409, detail="User already exists")
        raise HTTPException(status_code=500, detail=f"Failed to create user: {str(e)[:120]}")

    await record.delete()

    return {
        "status": "success",
        "message": "User registered successfully",
        "user_id": str(user.id),
        "email": user.email,
    }
