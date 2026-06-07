import logging
import re
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm

from api.auth.config import (
    _issue_tokens_for_user,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_numeric_code,
    get_current_user,
    hash_code,
    hash_password,
    verify_code,
    verify_password,
)
from models import AuthSession, EmailOTP, OAuthAccount, SocialAccount, User
from schemas.register import (
    ChangePasswordIn,
    ForgotPasswordIn,
    LoginIn,
    LogoutIn,
    RefreshIn,
    ResetPasswordIn,
    TokenOut,
)
from utils.email_sender import send_verification_email

router = APIRouter()
logger = logging.getLogger(__name__)

PHONE_RE = re.compile(r"^\+[1-9]\d{1,14}$")
PASSWORD_RESET_TTL_SECONDS = 15 * 60
MAX_RESET_ATTEMPTS = 5


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_phone(phone: str) -> str:
    phone = re.sub(r"[\s\-\(\)]", "", phone.strip())
    if not phone.startswith("+") or not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="Phone must be E.164 format")
    return phone


def validate_password_requirements(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long")


async def _get_user_by_identifier(ident: str) -> Optional[User]:
    if "@" in ident:
        return await User.find_one(User.email == ident.lower())
    if not hasattr(User, "phone"):
        return None
    phone = normalize_phone(ident)
    return await User.find_one(User.phone == phone)


async def _revoke_all_sessions_for_user(user_id) -> None:
    await AuthSession.find(AuthSession.user_id == user_id).update({"$set": {"revoked_at": utcnow()}})


async def _get_reset_otp(email: str) -> Optional[EmailOTP]:
    return await EmailOTP.find_one(EmailOTP.email == email, EmailOTP.purpose == "reset_password")


async def token(form: OAuth2PasswordRequestForm = Depends(), request: Request = None):
    user = await _get_user_by_identifier(form.username.strip())
    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return await _issue_tokens_for_user(user, request)


@router.post("/login", response_model=TokenOut)
async def login(payload: LoginIn, request: Request):
    user = await _get_user_by_identifier(payload.identifier.strip())
    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return await _issue_tokens_for_user(user, request)


@router.post("/refresh-token", response_model=TokenOut)
@router.post("/refresh", response_model=TokenOut)
async def refresh_token(payload: RefreshIn, request: Request):
    decoded = decode_token(payload.refresh_token)
    if not decoded or decoded.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    jti = decoded.get("jti")
    sub = decoded.get("sub")
    exp = decoded.get("exp")
    if not isinstance(jti, str) or not isinstance(sub, str) or exp is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    session = await AuthSession.find_one(AuthSession.refresh_token_hash == sha256(jti))
    if not session or session.revoked_at is not None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if session.expires_at < utcnow():
        raise HTTPException(status_code=401, detail="Refresh token expired")

    try:
        user_id = PydanticObjectId(sub)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    session.revoked_at = utcnow()
    await session.save()

    new_refresh = create_refresh_token(sub=sub)
    new_decoded = decode_token(new_refresh)
    if not new_decoded or new_decoded.get("type") != "refresh":
        raise HTTPException(status_code=500, detail="Failed to create refresh token")

    new_jti = new_decoded.get("jti")
    new_exp = new_decoded.get("exp")
    if not isinstance(new_jti, str) or not new_exp:
        raise HTTPException(status_code=500, detail="Failed to create refresh token")

    new_expires_at = datetime.fromtimestamp(int(new_exp), tz=timezone.utc).replace(tzinfo=None)
    await AuthSession(
        user_id=user.id,
        refresh_token_hash=sha256(new_jti),
        expires_at=new_expires_at,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    ).insert()

    new_access = create_access_token(sub=sub)
    return TokenOut(access_token=new_access, refresh_token=new_refresh)


@router.post("/logout", status_code=200)
async def logout(payload: LogoutIn):
    decoded = decode_token(payload.refresh_token)
    if not decoded or decoded.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    jti = decoded.get("jti")
    if not isinstance(jti, str):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    session = await AuthSession.find_one(AuthSession.refresh_token_hash == sha256(jti))
    if session and session.revoked_at is None:
        session.revoked_at = utcnow()
        await session.save()

    return {"status": "ok"}


@router.post("/forgot-password", status_code=200)
async def forgot_password(payload: ForgotPasswordIn, background_tasks: BackgroundTasks):
    email = payload.email.lower().strip()
    user = await User.find_one(User.email == email)
    if not user:
        return {"status": "success"}

    code = generate_numeric_code(6)
    expires_at = utcnow() + timedelta(seconds=PASSWORD_RESET_TTL_SECONDS)
    otp = await _get_reset_otp(email)

    if otp:
        otp.code_hash = hash_code(code)
        otp.expires_at = expires_at
        otp.attempts = 0
        otp.used_at = None
        otp.created_at = utcnow()
        await otp.save()
    else:
        await EmailOTP(
            email=email,
            purpose="reset_password",
            code_hash=hash_code(code),
            attempts=0,
            used_at=None,
            expires_at=expires_at,
        ).insert()

    background_tasks.add_task(send_verification_email, email, code, PASSWORD_RESET_TTL_SECONDS)
    return {"status": "success"}


@router.post("/reset-password", status_code=200)
async def reset_password(payload: ResetPasswordIn):
    email = payload.email.lower().strip()
    code = payload.code.strip()
    validate_password_requirements(payload.new_password)

    if payload.confirm_new_password is not None and payload.new_password != payload.confirm_new_password:
        raise HTTPException(status_code=400, detail="New password and confirm password do not match")

    otp = await _get_reset_otp(email)
    if not otp:
        raise HTTPException(status_code=400, detail="Invalid reset request")
    if otp.used_at is not None:
        raise HTTPException(status_code=400, detail="Reset already used")
    if otp.expires_at < utcnow():
        await otp.delete()
        raise HTTPException(status_code=400, detail="Reset expired")
    if otp.attempts >= MAX_RESET_ATTEMPTS:
        await otp.delete()
        raise HTTPException(status_code=429, detail="Too many attempts")
    if not verify_code(code, otp.code_hash):
        otp.attempts += 1
        await otp.save()
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Invalid code",
                "remaining_attempts": max(0, MAX_RESET_ATTEMPTS - otp.attempts),
            },
        )

    user = await User.find_one(User.email == email)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid reset request")

    user.password_hash = hash_password(payload.new_password)
    await user.save()

    otp.used_at = utcnow()
    await otp.save()
    await _revoke_all_sessions_for_user(user.id)
    return {"status": "success"}


@router.post("/change-password", status_code=200)
async def change_password(
    payload: ChangePasswordIn,
    current_user: User = Depends(get_current_user),
):
    if not current_user or not current_user.password_hash:
        raise HTTPException(status_code=400, detail="Password login is not available for this account")

    validate_password_requirements(payload.new_password)
    if payload.confirm_new_password is not None and payload.new_password != payload.confirm_new_password:
        raise HTTPException(status_code=400, detail="New password and confirm password do not match")
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if verify_password(payload.new_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="New password must be different from current password")

    current_user.password_hash = hash_password(payload.new_password)
    await current_user.save()
    await _revoke_all_sessions_for_user(current_user.id)
    return {"status": "success"}


@router.delete("/delete-account", status_code=200)
async def delete_account(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    await _revoke_all_sessions_for_user(current_user.id)
    await SocialAccount.find(SocialAccount.user_id == current_user.id).delete()
    await OAuthAccount.find(OAuthAccount.user_id == current_user.id).delete()
    await current_user.delete()
    return {"status": "ok"}
