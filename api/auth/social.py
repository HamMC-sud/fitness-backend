from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set

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

VK_ANDROID_CLIENT_ID = (os.getenv("VK_ANDROID_CLIENT_ID") or "54622591").strip()
VK_IOS_CLIENT_ID = (os.getenv("VK_IOS_CLIENT_ID") or "54622592").strip()
VK_ALLOWED_AUDIENCES = {value for value in {VK_ANDROID_CLIENT_ID, VK_IOS_CLIENT_ID} if value}
VK_PUBLIC_INFO_URL = (os.getenv("VK_PUBLIC_INFO_URL") or "https://id.vk.com/oauth2/public_info").strip()
VK_USER_INFO_URL = (os.getenv("VK_USER_INFO_URL") or "https://id.vk.com/oauth2/user_info").strip()
APPLE_CLIENT_ID = (os.getenv("APPLE_CLIENT_ID") or "com.kovi.fitnessapp").strip()
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
APPLE_JWKS_URL = (os.getenv("APPLE_JWKS_URL") or "https://appleid.apple.com/auth/keys").strip()
APPLE_ISSUER = "https://appleid.apple.com"
REGISTRATION_COMPLETE_TTL_SECONDS = int(os.getenv("REGISTRATION_COMPLETE_TTL", "1800"))
SOCIAL_PASSWORD_SENTINEL = "**SOCIAL**"
SOCIAL_REGISTRATION_EMAIL_DOMAIN = "social.local"
SOCIAL_HTTP_TIMEOUT_SECONDS = float(os.getenv("SOCIAL_HTTP_TIMEOUT_SECONDS", "10"))
APPLE_JWKS_CACHE_TTL_SECONDS = int(os.getenv("APPLE_JWKS_CACHE_TTL_SECONDS", "3600"))

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
_APPLE_JWKS_CACHE: Dict[str, Any] = {"keys": [], "expires_at": 0.0}


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
    logger.info(
        "Auth session created: provider=%s user_id=%s refresh_expires_at=%s",
        "social",
        user_id_str,
        expires_at.isoformat(),
    )

    access = create_access_token(sub=user_id_str)
    return TokenOut(access_token=access, refresh_token=refresh)


def _tail_token(token: str) -> str:
    stripped = token.strip()
    return stripped[-4:] if stripped else ""


def _truncate_log_value(value: Any, max_chars: int = 500) -> str:
    text_value = str(value)
    return text_value if len(text_value) <= max_chars else f"{text_value[:max_chars]}...<truncated>"


def _safe_keys(value: Any) -> List[str]:
    if not isinstance(value, dict):
        return []
    return sorted(str(key) for key in value.keys())


def _debug_identifier(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return f"hash={sha256(raw)[:12]} tail={raw[-4:]}"


def _hash_identifier(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    return sha256(raw)[:12]


def _get_request_id(request: Request) -> str:
    return (
        request.headers.get("x-request-id")
        or request.headers.get("x-correlation-id")
        or uuid.uuid4().hex[:12]
    )


def _decode_unverified_jwt(id_token: str, provider: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        claims = jwt.decode(
            id_token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_aud": False,
                "verify_iss": False,
            },
        )
    except Exception:
        logger.warning(
            "%s token decode failed: request_id=%s token_tail=%s",
            provider,
            request_id,
            _tail_token(id_token),
        )
        raise HTTPException(status_code=401, detail=f"{provider} token invalid")

    if not isinstance(claims, dict):
        logger.warning(
            "%s token decoded to unexpected payload: request_id=%s payload_type=%s",
            provider,
            request_id,
            type(claims).__name__,
        )
        raise HTTPException(status_code=401, detail=f"{provider} token invalid")
    return claims


def _require_valid_exp(claims: Dict[str, Any], provider: str, request_id: Optional[str] = None) -> None:
    exp_raw = claims.get("exp")
    try:
        exp = int(exp_raw)
    except (TypeError, ValueError):
        logger.warning(
            "%s token has invalid exp: request_id=%s exp=%s",
            provider,
            request_id,
            exp_raw,
        )
        raise HTTPException(status_code=401, detail=f"{provider} token invalid")

    now_ts = int(datetime.now(timezone.utc).timestamp())
    if exp <= now_ts:
        logger.warning(
            "%s token expired: request_id=%s exp=%s now=%s",
            provider,
            request_id,
            exp,
            now_ts,
        )
        raise HTTPException(status_code=401, detail=f"{provider} token expired")


async def _post_social_json(
    url: str,
    payload: Dict[str, Any],
    provider: str,
    error_detail: str,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=SOCIAL_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        logger.warning(
            "%s provider HTTP request failed: request_id=%s url=%s error=%s",
            provider,
            request_id,
            url,
            repr(exc),
        )
        raise HTTPException(status_code=502, detail=error_detail)

    logger.info(
        "%s provider response received: request_id=%s url=%s status_code=%s",
        provider,
        request_id,
        url,
        response.status_code,
    )

    try:
        data = response.json()
    except ValueError:
        logger.warning(
            "%s provider returned non-JSON response: request_id=%s url=%s status_code=%s body=%s",
            provider,
            request_id,
            url,
            response.status_code,
            _truncate_log_value(response.text),
        )
        raise HTTPException(status_code=502, detail=error_detail)

    if not isinstance(data, dict):
        logger.warning(
            "%s provider returned unexpected JSON type: request_id=%s url=%s status_code=%s data_type=%s",
            provider,
            request_id,
            url,
            response.status_code,
            type(data).__name__,
        )
        raise HTTPException(status_code=502, detail=error_detail)

    if data.get("error"):
        logger.warning(
            "%s provider error: request_id=%s url=%s status_code=%s error=%s description=%s",
            provider,
            request_id,
            url,
            response.status_code,
            data.get("error"),
            data.get("error_description"),
        )
        error_code = str(data.get("error") or "")
        if error_code == "invalid_token":
            raise HTTPException(status_code=401, detail=f"{provider} token invalid")
        raise HTTPException(status_code=502, detail=error_detail)

    if response.status_code >= 400:
        logger.warning(
            "%s provider returned failed HTTP status: request_id=%s url=%s status_code=%s response_keys=%s body=%s",
            provider,
            request_id,
            url,
            response.status_code,
            _safe_keys(data),
            _truncate_log_value(data),
        )
        if response.status_code in {401, 403}:
            raise HTTPException(status_code=401, detail=f"{provider} token invalid")
        raise HTTPException(status_code=502, detail=error_detail)

    logger.info(
        "%s provider JSON accepted: request_id=%s url=%s response_keys=%s",
        provider,
        request_id,
        url,
        _safe_keys(data),
    )
    return data


async def vk_verify_id_token(id_token: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    logger.info(
        "VK id_token verification started: request_id=%s id_token_tail=%s allowed_client_ids=%s",
        request_id,
        _tail_token(id_token),
        sorted(VK_ALLOWED_AUDIENCES),
    )

    try:
        header = jwt.get_unverified_header(id_token)
        logger.info(
            "VK id_token header decoded: request_id=%s alg=%s kid=%s",
            request_id,
            header.get("alg"),
            header.get("kid"),
        )
    except Exception:
        logger.warning(
            "VK id_token header decode failed: request_id=%s id_token_tail=%s",
            request_id,
            _tail_token(id_token),
        )

    claims = _decode_unverified_jwt(id_token, "VK", request_id=request_id)
    raw_aud = str(claims.get("aud") or "").strip()
    sub = claims.get("sub")
    exp_raw = claims.get("exp")
    now_ts = int(datetime.now(timezone.utc).timestamp())

    logger.info(
        "VK id_token decoded: request_id=%s raw_aud=%s sub_present=%s sub=%s exp=%s now=%s claim_keys=%s",
        request_id,
        raw_aud,
        bool(sub),
        _debug_identifier(sub),
        exp_raw,
        now_ts,
        _safe_keys(claims),
    )

    if not sub:
        logger.warning(
            "VK token missing sub: request_id=%s raw_aud=%s claim_keys=%s",
            request_id,
            raw_aud,
            _safe_keys(claims),
        )
        raise HTTPException(status_code=401, detail="VK token invalid")

    _require_valid_exp(claims, "VK", request_id=request_id)

    if not VK_ALLOWED_AUDIENCES:
        logger.error("VK client ids are not configured: request_id=%s", request_id)
        raise HTTPException(status_code=500, detail="VK client id missing")

    client_ids_to_try: List[str] = []
    if raw_aud and raw_aud in VK_ALLOWED_AUDIENCES:
        client_ids_to_try.append(raw_aud)

    for client_id in sorted(VK_ALLOWED_AUDIENCES):
        if client_id not in client_ids_to_try:
            client_ids_to_try.append(client_id)

    logger.info(
        "VK public_info verification will try client ids: request_id=%s raw_aud=%s client_ids_to_try=%s",
        request_id,
        raw_aud,
        client_ids_to_try,
    )

    last_http_error: Optional[HTTPException] = None
    for client_id in client_ids_to_try:
        logger.info(
            "VK public_info verification request: request_id=%s url=%s client_id=%s id_token_tail=%s",
            request_id,
            VK_PUBLIC_INFO_URL,
            client_id,
            _tail_token(id_token),
        )
        try:
            public_info = await _post_social_json(
                VK_PUBLIC_INFO_URL,
                {"client_id": client_id, "id_token": id_token},
                "VK",
                "VK verification unavailable",
                request_id=request_id,
            )
        except HTTPException as exc:
            last_http_error = exc
            logger.warning(
                "VK public_info verification failed: request_id=%s client_id=%s status_code=%s detail=%s id_token_tail=%s",
                request_id,
                client_id,
                exc.status_code,
                exc.detail,
                _tail_token(id_token),
            )
            if exc.status_code not in {401, 403}:
                raise
            continue

        claims["_verified_client_id"] = client_id
        logger.info(
            "VK public_info verification succeeded: request_id=%s verified_client_id=%s raw_aud=%s sub=%s response_keys=%s",
            request_id,
            client_id,
            raw_aud,
            _debug_identifier(sub),
            _safe_keys(public_info),
        )
        return claims

    logger.warning(
        "VK public_info verification failed for all client ids: request_id=%s raw_aud=%s tried_client_ids=%s last_status_code=%s last_detail=%s",
        request_id,
        raw_aud,
        client_ids_to_try,
        last_http_error.status_code if last_http_error else None,
        last_http_error.detail if last_http_error else None,
    )
    raise HTTPException(status_code=401, detail="VK token invalid")


async def vk_fetch_user_info(access_token: str, client_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    logger.info(
        "VK user_info request started: request_id=%s url=%s client_id=%s access_token_tail=%s",
        request_id,
        VK_USER_INFO_URL,
        client_id,
        _tail_token(access_token),
    )
    data = await _post_social_json(
        VK_USER_INFO_URL,
        {"client_id": client_id, "access_token": access_token},
        "VK",
        "VK user info unavailable",
        request_id=request_id,
    )
    user = data.get("user")
    if isinstance(user, dict):
        logger.info(
            "VK user_info returned user object: request_id=%s response_keys=%s user_keys=%s candidate_user_id=%s email_present=%s",
            request_id,
            _safe_keys(data),
            _safe_keys(user),
            _debug_identifier(user.get("user_id") or user.get("sub") or user.get("id")),
            bool(user.get("email")),
        )
        return user

    logger.info(
        "VK user_info returned flat payload: request_id=%s response_keys=%s candidate_user_id=%s email_present=%s",
        request_id,
        _safe_keys(data),
        _debug_identifier(data.get("user_id") or data.get("sub") or data.get("id")),
        bool(data.get("email")),
    )
    return data


async def vk_resolve_identity(
    access_token: str,
    id_token: str,
    request_id: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    logger.info(
        "VK identity resolving started: request_id=%s access_token_tail=%s id_token_tail=%s",
        request_id,
        _tail_token(access_token),
        _tail_token(id_token),
    )
    claims = await vk_verify_id_token(id_token, request_id=request_id)
    raw_aud = str(claims.get("aud") or "").strip()
    client_id = str(claims.get("_verified_client_id") or "").strip()
    sub = str(claims.get("sub") or "").strip()

    if not client_id:
        logger.warning(
            "VK verified client id missing after id_token verification: request_id=%s raw_aud=%s sub=%s",
            request_id,
            raw_aud,
            _debug_identifier(sub),
        )
        raise HTTPException(status_code=401, detail="VK token invalid")

    logger.info(
        "VK id_token identity resolved: request_id=%s raw_aud=%s verified_client_id=%s sub=%s",
        request_id,
        raw_aud,
        client_id,
        _debug_identifier(sub),
    )

    user_info = await vk_fetch_user_info(access_token, client_id, request_id=request_id)

    user_id = user_info.get("user_id") or user_info.get("sub") or user_info.get("id")
    logger.info(
        "VK user_info identity candidate: request_id=%s verified_client_id=%s user_info_keys=%s candidate_user_id=%s id_token_sub=%s",
        request_id,
        client_id,
        _safe_keys(user_info),
        _debug_identifier(user_id),
        _debug_identifier(sub),
    )
    if str(user_id or "").strip() and str(user_id).strip() != sub:
        logger.warning(
            "VK token subject mismatch: request_id=%s verified_client_id=%s id_token_sub=%s user_info_user_id=%s",
            request_id,
            client_id,
            _debug_identifier(sub),
            _debug_identifier(user_id),
        )
        raise HTTPException(status_code=401, detail="VK token subject mismatch")

    email = user_info.get("email")
    logger.info(
        "VK identity resolving succeeded: request_id=%s verified_client_id=%s provider_user_id=%s email_present=%s",
        request_id,
        client_id,
        _debug_identifier(sub),
        bool(email),
    )
    return {"provider_user_id": sub, "email": str(email).lower().strip() if email else None}


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
        async with httpx.AsyncClient(timeout=SOCIAL_HTTP_TIMEOUT_SECONDS) as client:
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


async def _fetch_apple_jwks(force_refresh: bool = False) -> List[Dict[str, Any]]:
    now_ts = time.time()
    if not force_refresh and _APPLE_JWKS_CACHE["keys"] and _APPLE_JWKS_CACHE["expires_at"] > now_ts:
        return _APPLE_JWKS_CACHE["keys"]

    try:
        async with httpx.AsyncClient(timeout=SOCIAL_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(APPLE_JWKS_URL)
            response.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Apple verification unavailable")

    try:
        data = response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Apple verification unavailable")

    keys = data.get("keys")
    if not isinstance(keys, list) or not keys:
        raise HTTPException(status_code=502, detail="Apple verification unavailable")

    _APPLE_JWKS_CACHE["keys"] = keys
    _APPLE_JWKS_CACHE["expires_at"] = now_ts + APPLE_JWKS_CACHE_TTL_SECONDS
    return keys


async def apple_verify_id_token(id_token: str) -> Dict[str, Any]:
    if not APPLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="APPLE_CLIENT_ID missing")

    try:
        header = jwt.get_unverified_header(id_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Apple token invalid")

    if header.get("alg") != "RS256":
        raise HTTPException(status_code=401, detail="Apple token invalid")

    kid = str(header.get("kid") or "").strip()
    if not kid:
        raise HTTPException(status_code=401, detail="Apple token invalid")

    for force_refresh in (False, True):
        keys = await _fetch_apple_jwks(force_refresh=force_refresh)
        matching_key = next((key for key in keys if key.get("kid") == kid), None)
        if not matching_key:
            if force_refresh:
                raise HTTPException(status_code=401, detail="Apple token invalid")
            continue

        try:
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(matching_key))
            claims = jwt.decode(
                id_token,
                public_key,
                algorithms=["RS256"],
                audience=APPLE_CLIENT_ID,
                issuer=APPLE_ISSUER,
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Apple token expired")
        except jwt.InvalidAudienceError:
            raise HTTPException(status_code=401, detail="Apple token audience mismatch")
        except jwt.InvalidIssuerError:
            raise HTTPException(status_code=401, detail="Apple token issuer mismatch")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Apple token invalid")

        if not isinstance(claims, dict):
            raise HTTPException(status_code=401, detail="Apple token invalid")
        return claims

    raise HTTPException(status_code=401, detail="Apple token invalid")


async def create_social_user(
    *,
    provider: str,
    provider_user_id: str,
    email: Optional[str],
    region: str,
    country: str,
    language: str,
    timezone: str,
    email_verified: bool,
) -> User:
    user = User(
        email=email,
        email_verified=email_verified if email else False,
        password_hash=None,
        region=region,
        country=country,
        language=language,
        timezone=timezone,
    )
    try:
        await user.insert()
    except DuplicateKeyError:
        existing_user = await User.find_one(User.email == email) if email else None
        if existing_user:
            user = existing_user
        else:
            raise HTTPException(status_code=409, detail="User already exists")

    try:
        await SocialAccount(
            provider=provider,
            provider_user_id=provider_user_id,
            user_id=user.id,
            email=email,
        ).insert()
    except DuplicateKeyError:
        existing_link = await SocialAccount.find_one(
            SocialAccount.provider == provider,
            SocialAccount.provider_user_id == provider_user_id,
        )
        if not existing_link:
            raise HTTPException(status_code=409, detail="Social account already linked")
        linked_user = await User.get(existing_link.user_id)
        if not linked_user:
            raise HTTPException(status_code=409, detail="Social account already linked")
        return linked_user

    return user


def _build_social_registration_key(provider: str, provider_user_id: str) -> str:
    return f"{provider}.{sha256(provider_user_id)}@{SOCIAL_REGISTRATION_EMAIL_DOMAIN}"


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
    email_present = bool(str(email).strip()) if email is not None else False
    logger.info(
        "SocialAccount lookup started: provider=%s provider_user_id_hash=%s email_present=%s",
        provider,
        _hash_identifier(provider_user_id),
        email_present,
    )
    link = await SocialAccount.find_one(
        SocialAccount.provider == provider,
        SocialAccount.provider_user_id == provider_user_id,
    )
    logger.info(
        "SocialAccount lookup result: provider=%s provider_user_id_hash=%s link_found=%s linked_user_id=%s",
        provider,
        _hash_identifier(provider_user_id),
        bool(link),
        str(link.user_id) if link else None,
    )
    if link:
        user = await User.get(link.user_id)
        logger.info(
            "Linked social user lookup result: provider=%s link_found=%s user_found=%s user_id=%s",
            provider,
            bool(link),
            bool(user),
            str(user.id) if user else None,
        )
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


async def prepare_social_registration(
    *,
    provider: str,
    provider_user_id: str,
    email: Optional[str],
) -> str:
    email_lc = email.lower().strip() if email else ""
    registration_key = email_lc or _build_social_registration_key(provider, provider_user_id)
    record = await VerificationCode.find_one(VerificationCode.email == registration_key)
    expires_at = now_utc() + timedelta(seconds=REGISTRATION_COMPLETE_TTL_SECONDS)
    social_code_hash = hash_code(generate_numeric_code(6))

    if record:
        record.verified = True
        record.password_hash = SOCIAL_PASSWORD_SENTINEL
        record.social_provider = provider
        record.social_provider_user_id = provider_user_id
        record.social_email = email_lc or None
        record.code_hash = social_code_hash
        record.attempts = 0
        record.created_at = now_utc()
        record.expires_at = expires_at
        record.last_resend = None
        await record.save()
        return registration_key

    try:
        await VerificationCode(
            email=registration_key,
            password_hash=SOCIAL_PASSWORD_SENTINEL,
            social_provider=provider,
            social_provider_user_id=provider_user_id,
            social_email=email_lc or None,
            code_hash=social_code_hash,
            attempts=0,
            verified=True,
            created_at=now_utc(),
            expires_at=expires_at,
            last_resend=None,
        ).insert()
    except DuplicateKeyError:
        logger.info("VerificationCode duplicate detected during social profile preparation for %s", registration_key)
        record = await VerificationCode.find_one(VerificationCode.email == registration_key)
        if not record:
            raise HTTPException(status_code=500, detail="Failed to prepare social profile completion")
        record.verified = True
        record.password_hash = SOCIAL_PASSWORD_SENTINEL
        record.social_provider = provider
        record.social_provider_user_id = provider_user_id
        record.social_email = email_lc or None
        record.code_hash = social_code_hash
        record.attempts = 0
        record.created_at = now_utc()
        record.expires_at = expires_at
        record.last_resend = None
        await record.save()
    return registration_key


@router.post("/auth/social/vk", response_model=TokenOut)
async def vk_login(payload: VkSocialIn, request: Request):
    request_id = _get_request_id(request)
    logger.info(
        "Social login started: provider=%s request_id=%s has_access_token=%s has_id_token=%s",
        "vk",
        request_id,
        bool(payload.access_token),
        bool(payload.id_token),
    )
    logger.info(
        "VK login started: request_id=%s has_access_token=%s has_id_token=%s payload_email_present=%s region=%s country=%s language=%s timezone=%s client_ip=%s user_agent=%s",
        request_id,
        bool(payload.access_token),
        bool(payload.id_token),
        bool(payload.email),
        payload.region.value,
        payload.country,
        payload.language.value,
        payload.timezone,
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
    )

    try:
        identity = await vk_resolve_identity(payload.access_token, payload.id_token, request_id=request_id)
    except HTTPException as exc:
        logger.warning(
            "VK login failed: request_id=%s status_code=%s detail=%s access_token_tail=%s id_token_tail=%s",
            request_id,
            exc.status_code,
            exc.detail,
            _tail_token(payload.access_token),
            _tail_token(payload.id_token),
        )
        raise
    except Exception:
        logger.exception(
            "VK login unexpected error: request_id=%s access_token_tail=%s id_token_tail=%s",
            request_id,
            _tail_token(payload.access_token),
            _tail_token(payload.id_token),
        )
        raise

    resolved_email = identity["email"] or (str(payload.email).lower().strip() if payload.email else None)

    logger.info(
        "Social identity resolved: provider=%s request_id=%s provider_user_id_hash=%s email_present=%s",
        "vk",
        request_id,
        _hash_identifier(identity.get("provider_user_id")),
        bool(resolved_email),
    )
    logger.info(
        "VK login identity resolved: request_id=%s provider_user_id=%s identity_email_present=%s payload_email_present=%s",
        request_id,
        _debug_identifier(identity.get("provider_user_id")),
        bool(identity.get("email")),
        bool(payload.email),
    )
    user = await get_or_link_social_user(
        provider="vk",
        provider_user_id=identity["provider_user_id"] or "",
        email=resolved_email,
        region=payload.region.value,
        country=payload.country,
        language=payload.language.value,
        timezone=payload.timezone,
        email_verified=False,
    )
    if user:
        logger.info(
            "Social login tokens issued: provider=%s user_id=%s",
            "vk",
            str(user.id),
        )
        logger.info(
            "VK login existing user found: request_id=%s user_id=%s provider_user_id=%s",
            request_id,
            _debug_identifier(user.id),
            _debug_identifier(identity.get("provider_user_id")),
        )
        return await issue_tokens_for_user(user, request)

    registration_email = await prepare_social_registration(
        provider="vk",
        provider_user_id=identity["provider_user_id"] or "",
        email=resolved_email,
    )
    logger.info(
        "Social login profile required: provider=%s provider_user_id_hash=%s registration_email=%s",
        "vk",
        _hash_identifier(identity.get("provider_user_id")),
        registration_email,
    )
    logger.info(
        "VK login requires profile completion: request_id=%s provider_user_id=%s email_present=%s",
        request_id,
        _debug_identifier(identity.get("provider_user_id")),
        bool(resolved_email),
    )
    raise HTTPException(
        status_code=428,
        detail={
            "code": "PROFILE_REQUIRED",
            "email": registration_email,
            "provider": "vk",
            "message": "Complete profile using /register/complete",
        },
    )


@router.post("/auth/social/google", response_model=TokenOut)
async def google_login(payload: GoogleSocialIn, request: Request):
    request_id = _get_request_id(request)
    logger.info(
        "Social login started: provider=%s request_id=%s has_access_token=%s has_id_token=%s",
        "google",
        request_id,
        None,
        bool(payload.id_token),
    )
    claims = await google_verify_id_token(payload.id_token)
    sub = claims.get("sub")
    email = claims.get("email")
    email_verified = bool(claims.get("email_verified")) if "email_verified" in claims else False

    if not sub:
        raise HTTPException(status_code=401, detail="Google token invalid")

    logger.info(
        "Social identity resolved: provider=%s request_id=%s provider_user_id_hash=%s email_present=%s",
        "google",
        request_id,
        _hash_identifier(sub),
        bool(email),
    )

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
        logger.info(
            "Social login tokens issued: provider=%s user_id=%s",
            "google",
            str(user.id),
        )
        return await issue_tokens_for_user(user, request)

    registration_email = await prepare_social_registration(
        provider="google",
        provider_user_id=str(sub),
        email=str(email).lower().strip() if email else None,
    )
    logger.info(
        "Social login profile required: provider=%s provider_user_id_hash=%s registration_email=%s",
        "google",
        _hash_identifier(sub),
        registration_email,
    )
    raise HTTPException(
        status_code=428,
        detail={
            "code": "PROFILE_REQUIRED",
            "email": registration_email,
            "provider": "google",
            "message": "Complete profile using /register/complete",
        },
    )


@router.post("/auth/social/apple", response_model=TokenOut)
async def apple_login(payload: AppleSocialIn, request: Request):
    request_id = _get_request_id(request)
    logger.info(
        "Social login started: provider=%s request_id=%s has_access_token=%s has_id_token=%s",
        "apple",
        request_id,
        None,
        bool(payload.id_token),
    )
    claims = await apple_verify_id_token(payload.id_token)
    sub = claims.get("sub")
    email = claims.get("email") or (str(payload.email).lower() if payload.email else None)

    if not sub:
        raise HTTPException(status_code=401, detail="Apple token invalid")

    logger.info(
        "Social identity resolved: provider=%s request_id=%s provider_user_id_hash=%s email_present=%s",
        "apple",
        request_id,
        _hash_identifier(sub),
        bool(email),
    )

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
        logger.info(
            "Social login tokens issued: provider=%s user_id=%s",
            "apple",
            str(user.id),
        )
        return await issue_tokens_for_user(user, request)

    registration_email = await prepare_social_registration(
        provider="apple",
        provider_user_id=str(sub),
        email=str(email).lower().strip() if email else None,
    )
    logger.info(
        "Social login profile required: provider=%s provider_user_id_hash=%s registration_email=%s",
        "apple",
        _hash_identifier(sub),
        registration_email,
    )
    raise HTTPException(
        status_code=428,
        detail={
            "code": "PROFILE_REQUIRED",
            "email": registration_email,
            "provider": "apple",
            "message": "Complete profile using /register/complete",
        },
    )
