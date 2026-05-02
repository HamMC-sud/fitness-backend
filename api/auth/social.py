from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Set

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import EmailStr
from pymongo.errors import DuplicateKeyError

from api.auth.config import create_access_token, create_refresh_token, decode_token
from api.auth.config import generate_numeric_code, hash_code, now_utc
from models import AuthSession, SocialAccount, User, VerificationCode
from schemas.register import TokenOut
from schemas.social import AppleSocialIn, GoogleSocialIn, VkSocialIn

router = APIRouter(tags=["auth-social"])
logger = logging.getLogger(__name__)

VK_API_VERSION = os.getenv("VK_API_VERSION", "5.131")
APPLE_CLIENT_ID = (os.getenv("APPLE_CLIENT_ID") or "").strip()
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"

def _collect_google_audiences() -> Set[str]:
    values = [
        *(os.getenv("GOOGLE_CLIENT_IDS", "").split(",")),
        os.getenv("GOOGLE_WEB_CLIENT_ID", ""),
        os.getenv("GOOGLE_ANDROID_CLIENT_ID", ""),
        os.getenv("GOOGLE_IOS_CLIENT_ID", ""),
        os.getenv("WEB_CLIENT_ID", ""),
        os.getenv("ANDROID_CLIENT_ID", ""),
        os.getenv("IOS_CLIENT_ID", ""),
    ]
    return {v.strip() for v in values if v and v.strip()}


GOOGLE_ALLOWED_AUDIENCES: Set[str] = _collect_google_audiences()


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


async def issue_tokens_for_user(user: User, request: Request) -> TokenOut:
    user_id_str = str(user.id)

    refresh = create_refresh_token(sub=user_id_str)
    dec = decode_token(refresh)
    if not dec or dec.get("type") != "refresh" or not dec.get("jti") or not dec.get("exp"):
        raise HTTPException(status_code=500, detail="Failed to create refresh token")

    expires_at = datetime.fromtimestamp(int(dec["exp"]), tz=timezone.utc).replace(tzinfo=None)
    jti_hash = sha256(str(dec["jti"]))

    await AuthSession(
        user_id=user.id,
        refresh_token_hash=jti_hash,
        expires_at=expires_at,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    ).insert()

    access = create_access_token(sub=user_id_str)
    return TokenOut(access_token=access, refresh_token=refresh)


async def vk_fetch_user_id(access_token: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.vk.com/method/users.get",
                params={"access_token": access_token, "v": VK_API_VERSION},
            )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="VK verification unavailable")

    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="VK token invalid")

    data = r.json()
    resp = data.get("response")
    if not isinstance(resp, list) or not resp:
        raise HTTPException(status_code=401, detail="VK token invalid")

    uid = resp[0].get("id")
    if not uid:
        raise HTTPException(status_code=401, detail="VK token invalid")
    return str(uid)


async def google_verify_id_token(id_token: str) -> Dict[str, Any]:
    decoded_claims: Dict[str, Any] = {}
    try:
        decoded_claims = jwt.decode(
            id_token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_aud": False,
                "verify_iss": False,
            },
        )
    except Exception:
        logger.warning("Google ID token decode failed")

    logger.info(
        "Google ID token decoded claims: aud=%s azp=%s iss=%s sub=%s",
        decoded_claims.get("aud"),
        decoded_claims.get("azp"),
        decoded_claims.get("iss"),
        decoded_claims.get("sub"),
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(GOOGLE_TOKENINFO_URL, params={"id_token": id_token})
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Google verification unavailable")

    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Google token invalid")

    claims = r.json()
    if not isinstance(claims, dict):
        raise HTTPException(status_code=401, detail="Google token invalid")

    aud = str(claims.get("aud") or "").strip()
    if GOOGLE_ALLOWED_AUDIENCES and aud not in GOOGLE_ALLOWED_AUDIENCES:
        logger.warning(
            "Google token audience mismatch: expected_aud=%s actual_aud=%s azp=%s iss=%s sub=%s",
            sorted(GOOGLE_ALLOWED_AUDIENCES),
            aud,
            claims.get("azp"),
            claims.get("iss"),
            claims.get("sub"),
        )
        raise HTTPException(status_code=401, detail="Google token audience mismatch")

    iss = str(claims.get("iss") or "").strip()
    if iss not in {"accounts.google.com", "https://accounts.google.com"}:
        logger.warning(
            "Google token issuer mismatch: expected_iss=%s actual_iss=%s aud=%s azp=%s sub=%s",
            ["accounts.google.com", "https://accounts.google.com"],
            iss,
            aud,
            claims.get("azp"),
            claims.get("sub"),
        )
        raise HTTPException(status_code=401, detail="Google token issuer mismatch")

    exp_raw = claims.get("exp")
    try:
        exp = int(exp_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Google token invalid")

    now_ts = int(datetime.now(timezone.utc).timestamp())
    if exp <= now_ts:
        logger.warning(
            "Google token expired: exp=%s now=%s aud=%s azp=%s iss=%s sub=%s",
            exp,
            now_ts,
            aud,
            claims.get("azp"),
            iss,
            claims.get("sub"),
        )
        raise HTTPException(status_code=401, detail="Google token expired")

    logger.info(
        "Google token verified: expected_aud=%s actual_aud=%s azp=%s iss=%s sub=%s",
        sorted(GOOGLE_ALLOWED_AUDIENCES),
        aud,
        claims.get("azp"),
        iss,
        claims.get("sub"),
    )

    return claims


def apple_verify_id_token(id_token: str) -> Dict[str, Any]:
    if not APPLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="APPLE_CLIENT_ID missing")

    try:
        jwks_client = jwt.PyJWKClient(APPLE_JWKS_URL)
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)

        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=APPLE_CLIENT_ID,
            issuer="https://appleid.apple.com",
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Apple token invalid")

    if not isinstance(claims, dict):
        raise HTTPException(status_code=401, detail="Apple token invalid")

    return claims


async def get_or_link_social_user(
    provider: str,
    provider_user_id: str,
    email: Optional[EmailStr],
    region: str,
    country: str,
    language: str,
    timezone: str,
    email_verified: bool,
) -> Optional[User]:
    link = await SocialAccount.find_one(
        SocialAccount.provider == provider,
        SocialAccount.provider_user_id == provider_user_id,
    )
    if link:
        user = await User.get(link.user_id)
        if not user:
            await link.delete()
        else:
            return user

    email_lc = str(email).lower() if email else None

    user: Optional[User] = None
    if email_lc:
        user = await User.find_one(User.email == email_lc)

    if not user:
        return None
    elif email_verified and not user.email_verified:
        user.email_verified = True
        await user.save()

    existing_link = await SocialAccount.find_one(
        SocialAccount.provider == provider,
        SocialAccount.provider_user_id == provider_user_id,
    )
    if existing_link:
        if existing_link.user_id != user.id:
            raise HTTPException(status_code=409, detail="Social account already linked")
        return user

    try:
        await SocialAccount(
            provider=provider,
            provider_user_id=provider_user_id,
            user_id=user.id,
            email=email_lc,
        ).insert()
    except DuplicateKeyError:
        existing_link = await SocialAccount.find_one(
            SocialAccount.provider == provider,
            SocialAccount.provider_user_id == provider_user_id,
        )
        if existing_link and existing_link.user_id == user.id:
            return user
        raise HTTPException(status_code=409, detail="Social account already linked")

    return user


async def mark_social_email_verified_for_complete(email: str) -> None:
    email_lc = email.lower().strip()
    record = await VerificationCode.find_one(VerificationCode.email == email_lc)
    expires_at = now_utc() + timedelta(days=7)
    social_code_hash = hash_code(generate_numeric_code(6))

    if record:
        record.verified = True
        record.password_hash = "__SOCIAL__"
        record.code_hash = social_code_hash
        record.attempts = 0
        record.expires_at = expires_at
        record.last_resend = None
        await record.save()
    else:
        await VerificationCode(
            email=email_lc,
            password_hash="__SOCIAL__",
            code_hash=social_code_hash,
            attempts=0,
            verified=True,
            created_at=now_utc(),
            expires_at=expires_at,
            last_resend=None,
        ).insert()


@router.post("/auth/social/vk", response_model=TokenOut)
async def vk_login(payload: VkSocialIn, request: Request):
    uid = await vk_fetch_user_id(payload.access_token)
    user = await get_or_link_social_user(
        provider="vk",
        provider_user_id=uid,
        email=payload.email,
        region=payload.region.value,
        country=payload.country,
        language=payload.language.value,
        timezone=payload.timezone,
        email_verified=False,
    )
    if user:
        return await issue_tokens_for_user(user, request)

    email = str(payload.email).lower().strip() if payload.email else ""
    if not email:
        raise HTTPException(status_code=400, detail="Email required to complete profile")
    await mark_social_email_verified_for_complete(email)
    raise HTTPException(
        status_code=428,
        detail={
            "code": "PROFILE_REQUIRED",
            "email": email,
            "message": "Complete profile using /register/complete",
        },
    )


@router.post("/auth/social/google", response_model=TokenOut)
async def google_login(payload: GoogleSocialIn, request: Request):
    claims = await google_verify_id_token(payload.id_token)
    sub = claims.get("sub")
    email = claims.get("email")
    email_verified = bool(claims.get("email_verified")) if "email_verified" in claims else False

    if not sub:
        raise HTTPException(status_code=401, detail="Google token invalid")

    user = await get_or_link_social_user(
        provider="google",
        provider_user_id=str(sub),
        email=email,
        region=payload.region.value,
        country=payload.country,
        language=payload.language.value,
        timezone=payload.timezone,
        email_verified=email_verified,
    )
    if user:
        return await issue_tokens_for_user(user, request)

    email_lc = str(email).lower().strip() if email else ""
    if not email_lc:
        raise HTTPException(status_code=400, detail="Email required to complete profile")
    await mark_social_email_verified_for_complete(email_lc)
    raise HTTPException(
        status_code=428,
        detail={
            "code": "PROFILE_REQUIRED",
            "email": email_lc,
            "message": "Complete profile using /register/complete",
        },
    )


@router.post("/auth/social/apple", response_model=TokenOut)
async def apple_login(payload: AppleSocialIn, request: Request):
    claims = apple_verify_id_token(payload.id_token)
    sub = claims.get("sub")
    email = claims.get("email") or (str(payload.email).lower() if payload.email else None)

    if not sub:
        raise HTTPException(status_code=401, detail="Apple token invalid")

    user = await get_or_link_social_user(
        provider="apple",
        provider_user_id=str(sub),
        email=email,
        region=payload.region.value,
        country=payload.country,
        language=payload.language.value,
        timezone=payload.timezone,
        email_verified=True if email else False,
    )
    if user:
        return await issue_tokens_for_user(user, request)

    email_lc = str(email).lower().strip() if email else ""
    if not email_lc:
        raise HTTPException(status_code=400, detail="Email required to complete profile")
    await mark_social_email_verified_for_complete(email_lc)
    raise HTTPException(
        status_code=428,
        detail={
            "code": "PROFILE_REQUIRED",
            "email": email_lc,
            "message": "Complete profile using /register/complete",
        },
    )
