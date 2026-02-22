import os
import uuid
import base64
import binascii
from pathlib import Path
from datetime import timedelta
from typing import Optional

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

router = APIRouter()

CODE_TTL_SECONDS = int(os.getenv("VERIFICATION_CODE_TTL", "300"))
CODE_LENGTH = int(os.getenv("CODE_LENGTH", "4"))
MAX_ATTEMPTS = int(os.getenv("MAX_VERIFICATION_ATTEMPTS", "5"))

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024

STATICS_DIR = Path("statics")
STATICS_DIR.mkdir(parents=True, exist_ok=True)


def _detect_image_ext(image_bytes: bytes) -> Optional[str]:
    if image_bytes.startswith(b"\xFF\xD8\xFF"):
        return ".jpg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return ".webp"
    return None


def _parse_data_uri_base64(value: str) -> tuple[Optional[str], str]:
    value = value.strip()

    if value.startswith("data:"):
        try:
            header, b64_data = value.split(",", 1)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid image data URI format")

        if ";base64" not in header:
            raise HTTPException(status_code=400, detail="Image must be base64 encoded")

        mime_part = header.split(";")[0]
        mime_type = mime_part.replace("data:", "").lower().strip()
        return mime_type, b64_data.strip()

    return None, value


def save_base64_profile_image(base64_value: str) -> str:
    mime_type, b64_data = _parse_data_uri_base64(base64_value)

    try:
        image_bytes = base64.b64decode(b64_data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid base64 image")

    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image data")

    if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Image too large. Max size is {MAX_IMAGE_SIZE_BYTES // (1024 * 1024)} MB"
        )

    ext_from_bytes = _detect_image_ext(image_bytes)

    ext_from_mime = None
    if mime_type:
        mime_to_ext = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        ext_from_mime = mime_to_ext.get(mime_type)

    ext = ext_from_bytes or ext_from_mime
    if not ext or ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported image format. Use jpg, png, or webp")

    if ext_from_mime and ext_from_bytes and ext_from_mime != ext_from_bytes:
        raise HTTPException(status_code=400, detail="Image MIME type does not match file content")

    folder_name = uuid.uuid4().hex
    folder_path = STATICS_DIR / folder_name
    folder_path.mkdir(parents=True, exist_ok=True)

    filename = f"image{ext}"
    file_path = folder_path / filename

    with open(file_path, "wb") as f:
        f.write(image_bytes)

    return f"http://26.214.57.127:8000/statics/{folder_name}/{filename}"


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
        raise HTTPException(status_code=400, detail="Email not verified. Please verify your email first.")

    profile = payload.profile.model_copy(deep=True)

    if profile.photo_url:
        profile.photo_url = save_base64_profile_image(profile.photo_url)

    user = User(
        email=record.email,
        email_verified=True,
        password_hash=record.password_hash,
        profile=profile,
    )

    try:
        await user.insert()
    except Exception as e:
        if "duplicate key error" in str(e).lower():
            raise HTTPException(status_code=409, detail="User already exists")
        raise HTTPException(status_code=500, detail=f"Failed to create user: {str(e)[:120]}")

    # ✅ Генерация токенов (ВАЖНО: sub = str(user.id))
    access_token = create_access_token(
        sub=str(user.id),
        extra={"email": user.email},
    )

    refresh_token = create_refresh_token(
        sub=str(user.id),
    )

    await record.delete()

    return {
        "status": "success",
        "access_token": access_token,
        "refresh_token": refresh_token,
    }
