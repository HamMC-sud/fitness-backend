import os
import re
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, Any, Dict

import bcrypt
import jwt
from beanie.odm.fields import PydanticObjectId
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer

from schemas.register import TokenOut
from models import User, AuthSession

JWT_SECRET = (os.getenv("JWT_SECRET") or "").strip()
JWT_ALGORITHM = (os.getenv("JWT_ALGORITHM", "HS256") or "HS256").strip()
ACCESS_MINUTES = int(os.getenv("JWT_ACCESS_MINUTES", "30"))
REFRESH_MINUTES = int(os.getenv("JWT_REFRESH_MINUTES", "43200"))
BCRYPT_ROUNDS = int(os.getenv("BCRYPT_ROUNDS", "12"))

if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET is missing in .env")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/token", auto_error=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now_utc() -> datetime:
    return utcnow()


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def create_access_token(sub: str, extra: Optional[dict] = None) -> str:
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": sub,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ACCESS_MINUTES)).timestamp()),
        "jti": secrets.token_hex(16),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(sub: str, extra: Optional[dict] = None) -> str:
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": sub,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=REFRESH_MINUTES)).timestamp()),
        "jti": secrets.token_hex(16),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return None


def generate_numeric_code(length: int = 4) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))


def hash_code(code: str) -> str:
    salt = bcrypt.gensalt(rounds=10)
    return bcrypt.hashpw(code.encode("utf-8"), salt).decode("utf-8")


def verify_code(code: str, code_hash: str) -> bool:
    try:
        return bcrypt.checkpw(code.encode("utf-8"), code_hash.encode("utf-8"))
    except Exception:
        return False


async def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> Optional[User]:
    if not token:
        return None

    decoded = decode_token(token)
    if not decoded or decoded.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    sub = decoded.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        user_id = PydanticObjectId(sub)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user


async def _issue_tokens_for_user(user: User, request: Optional[Request]) -> TokenOut:
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
        user_agent=(request.headers.get("user-agent") if request else None),
        ip=(request.client.host if (request and request.client) else None),
    ).insert()

    access = create_access_token(sub=user_id_str)
    return TokenOut(access_token=access, refresh_token=refresh)
