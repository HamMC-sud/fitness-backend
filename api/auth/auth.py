import hashlib
import secrets
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi.security import OAuth2PasswordRequestForm
from fastapi import APIRouter, HTTPException, status, Depends, Request
from beanie.odm.fields import PydanticObjectId

from schemas.register import (
    LoginIn,
    TokenOut,
    RefreshIn,
    LogoutIn,
    ForgotPasswordIn,
    ResetPasswordIn,
)
from models import User, AuthSession, EmailOTP
from api.auth.config import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
    hash_password,
    get_current_user,
    _issue_tokens_for_user,
)

router = APIRouter()

PHONE_RE = re.compile(r"^\+[1-9]\d{1,14}$")

PASSWORD_RESET_TTL_SECONDS = 15 * 60
MAX_RESET_ATTEMPTS = 5


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def normalize_phone(phone: str) -> str:
    phone = re.sub(r"[\s\-\(\)]", "", phone.strip())
    if not phone.startswith("+") or not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="Phone must be E.164 format")
    return phone


def generate_otp(length: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))


async def _get_user_by_identifier(ident: str) -> Optional[User]:
    if "@" in ident:
        return await User.find_one(User.email == ident.lower())
    phone = normalize_phone(ident)
    return await User.find_one(User.phone == phone)


# ---------------- AUTH ----------------

@router.post("/token", response_model=TokenOut)
async def token(form: OAuth2PasswordRequestForm = Depends(), request: Request = None):
    user = await _get_user_by_identifier(form.username.strip())
    if not user or not user.password_hash:
        raise HTTPException(401, "Invalid credentials")

    if not verify_password(form.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")

    return await _issue_tokens_for_user(user, request)


@router.post("/login", response_model=TokenOut)
async def login(payload: LoginIn, request: Request):
    user = await _get_user_by_identifier(payload.identifier.strip())
    if not user or not user.password_hash:
        raise HTTPException(401, "Invalid credentials")

    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")

    user_id = str(user.id)

    refresh = create_refresh_token(sub=user_id)
    decoded = decode_token(refresh)
    if not decoded or decoded.get("type") != "refresh":
        raise HTTPException(500, "Failed to create refresh token")

    expires_at = (
        datetime.fromtimestamp(decoded["exp"], tz=timezone.utc)
        .replace(tzinfo=None)
    )

    await AuthSession(
        user_id=user.id,
        refresh_token_hash=sha256(decoded["jti"]),
        expires_at=expires_at,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    ).insert()

    access = create_access_token(sub=user_id)
    return TokenOut(access_token=access, refresh_token=refresh)


@router.post("/refresh-token", response_model=TokenOut)
async def refresh_token(payload: RefreshIn, request: Request):
    decoded = decode_token(payload.refresh_token)
    if not decoded or decoded.get("type") != "refresh":
        raise HTTPException(401, "Invalid refresh token")

    old_hash = sha256(decoded["jti"])
    session = await AuthSession.find_one(AuthSession.refresh_token_hash == old_hash)

    if not session or session.revoked_at is not None:
        raise HTTPException(401, "Refresh token revoked")

    if session.expires_at < utcnow():
        raise HTTPException(401, "Refresh token expired")

    session.revoked_at = utcnow()
    await session.save()

    user_id = decoded["sub"]

    new_refresh = create_refresh_token(sub=user_id)
    new_decoded = decode_token(new_refresh)

    new_expires_at = (
        datetime.fromtimestamp(new_decoded["exp"], tz=timezone.utc)
        .replace(tzinfo=None)
    )

    await AuthSession(
        user_id=PydanticObjectId(user_id),
        refresh_token_hash=sha256(new_decoded["jti"]),
        expires_at=new_expires_at,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    ).insert()

    new_access = create_access_token(sub=user_id)
    return TokenOut(access_token=new_access, refresh_token=new_refresh)


@router.post("/logout", status_code=200)
async def logout(payload: LogoutIn):
    decoded = decode_token(payload.refresh_token)
    if not decoded or decoded.get("type") != "refresh":
        raise HTTPException(401, "Invalid refresh token")

    h = sha256(decoded["jti"])
    session = await AuthSession.find_one(AuthSession.refresh_token_hash == h)

    if session and session.revoked_at is None:
        session.revoked_at = utcnow()
        await session.save()

    return {"status": "ok"}


# ---------------- PASSWORD RESET (NO CODE) ----------------

@router.post("/forgot-password", status_code=200)
async def forgot_password(payload: ForgotPasswordIn):
    email = payload.email.lower().strip()

    user = await User.find_one(User.email == email)
    if not user:
        return {"status": "not found"}

    expires_at = utcnow() + timedelta(seconds=PASSWORD_RESET_TTL_SECONDS)

    otp = await EmailOTP.find_one({"email": email, "purpose": "reset_password"})
    if otp:
        otp.expires_at = expires_at
        otp.attempts = 0
        otp.used_at = None
        await otp.save()
    else:
        await EmailOTP(
            email=email,
            purpose="reset_password",
            expires_at=expires_at,
        ).insert()

    return {"status": "success"}


@router.post("/reset-password", status_code=200)
async def reset_password(payload: ResetPasswordIn):
    email = payload.email.lower().strip()
    new_password = payload.new_password

    if len(new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters long")

    otp = await EmailOTP.find_one({"email": email, "purpose": "reset_password"})
    if not otp:
        raise HTTPException(400, "Invalid reset request")

    if otp.used_at is not None:
        raise HTTPException(400, "Reset already used")

    if otp.expires_at < utcnow():
        await otp.delete()
        raise HTTPException(400, "Reset expired")

    user = await User.find_one(User.email == email)
    if not user:
        raise HTTPException(400, "Invalid reset request")

    user.password_hash = hash_password(new_password)
    await user.save()

    otp.used_at = utcnow()
    await otp.save()

    await AuthSession.find(
        AuthSession.user_id == user.id
    ).update({"$set": {"revoked_at": utcnow()}})

    return {"status": "success"}


@router.delete("/delete-account", status_code=200)
async def delete_account(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    await AuthSession.find(
        AuthSession.user_id == current_user.id
    ).update({"$set": {"revoked_at": utcnow()}})

    await current_user.delete()
    return {"status": "ok"}
