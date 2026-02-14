from __future__ import annotations
import os
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import EmailStr

from api.auth.config import create_access_token, create_refresh_token, decode_token
from models import User, AuthSession
from models import SocialAccount
from schemas.register import TokenOut
from schemas.social import VkSocialIn, GoogleSocialIn, AppleSocialIn

router = APIRouter(tags=["auth-social"])

VK_API_VERSION = os.getenv("VK_API_VERSION", "5.131")
APPLE_CLIENT_ID = os.getenv("APPLE_CLIENT_ID") or ""
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def issue_tokens_for_user(user: User, request: Request) -> TokenOut:
    user_id_str = str(user.id)

    refresh = create_refresh_token(sub=user_id_str)
    dec = decode_token(refresh)
    if not dec or dec.get("type") != "refresh":
        raise HTTPException(status_code=500, detail="Failed to create refresh token")

    expires_at = datetime.utcfromtimestamp(dec["exp"])
    jti_hash = sha256(dec["jti"])

    await AuthSession(
        user_id=user.id,
        refresh_jti_hash=jti_hash,
        expires_at=expires_at,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    ).insert()

    access = create_access_token(sub=user_id_str)
    return TokenOut(access_token=access, refresh_token=refresh)


async def vk_fetch_user_id(access_token: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            "https://api.vk.com/method/users.get",
            params={"access_token": access_token, "v": VK_API_VERSION},
        )
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
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(GOOGLE_TOKENINFO_URL, params={"id_token": id_token})
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Google token invalid")
    return r.json()


def apple_verify_id_token(id_token: str) -> Dict[str, Any]:
    if not APPLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="APPLE_CLIENT_ID missing")

    jwks_client = jwt.PyJWKClient(APPLE_JWKS_URL)
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)

    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=APPLE_CLIENT_ID,
        issuer="https://appleid.apple.com",
    )
    return claims


async def get_or_create_social_user(
    provider: str,
    provider_user_id: str,
    email: Optional[EmailStr],
    region: str,
    country: str,
    language: str,
    timezone: str,
    email_verified: bool,
) -> User:
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

    user: Optional[User] = None
    if email:
        user = await User.find_one(User.email == str(email).lower())

    if not user:
        if not email:
            raise HTTPException(status_code=400, detail="Email required to create account")

        user = User(
            email=str(email).lower(),
            email_verified=email_verified,
            password_hash=None,
            region=region,
            country=country,
            language=language,
            timezone=timezone,
            profile=None,
        )
        await user.insert()

    await SocialAccount(
        provider=provider,
        provider_user_id=provider_user_id,
        user_id=user.id,
        email=str(email).lower() if email else None,
    ).insert()

    return user


@router.post("/auth/social/vk", response_model=TokenOut)
async def vk_login(payload: VkSocialIn, request: Request):
    uid = await vk_fetch_user_id(payload.access_token)
    user = await get_or_create_social_user(
        provider="vk",
        provider_user_id=uid,
        email=payload.email,
        region=payload.region.value,
        country=payload.country,
        language=payload.language.value,
        timezone=payload.timezone,
        email_verified=False,
    )
    return await issue_tokens_for_user(user, request)


@router.post("/auth/social/google", response_model=TokenOut)
async def google_login(payload: GoogleSocialIn, request: Request):
    claims = await google_verify_id_token(payload.id_token)
    sub = claims.get("sub")
    email = claims.get("email")
    email_verified = bool(claims.get("email_verified")) if "email_verified" in claims else False

    if not sub:
        raise HTTPException(status_code=401, detail="Google token invalid")

    user = await get_or_create_social_user(
        provider="google",
        provider_user_id=str(sub),
        email=email,
        region=payload.region.value,
        country=payload.country,
        language=payload.language.value,
        timezone=payload.timezone,
        email_verified=email_verified,
    )
    return await issue_tokens_for_user(user, request)


@router.post("/auth/social/apple", response_model=TokenOut)
async def apple_login(payload: AppleSocialIn, request: Request):
    claims = apple_verify_id_token(payload.id_token)
    sub = claims.get("sub")
    email = claims.get("email") or (str(payload.email).lower() if payload.email else None)

    if not sub:
        raise HTTPException(status_code=401, detail="Apple token invalid")

    user = await get_or_create_social_user(
        provider="apple",
        provider_user_id=str(sub),
        email=email,
        region=payload.region.value,
        country=payload.country,
        language=payload.language.value,
        timezone=payload.timezone,
        email_verified=True if email else False,
    )
    return await issue_tokens_for_user(user, request)
