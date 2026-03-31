from __future__ import annotations

import inspect
import csv
import io
import json
import re
import uuid
import secrets
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from urllib.parse import urlparse

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordBearer
from starlette.concurrency import run_in_threadpool
from pymongo.errors import DuplicateKeyError

from api.auth.config import (
    create_access_token,
    create_refresh_token,
    decode_token,
    sha256,
    verify_password,
)
from api.subscription.subscription import (
    compute_subscription_status,
    create_plan,
    create_promo,
    create_promo_batch,
    export_promo_batch_csv,
    list_promos,
    promo_stats,
)
from models import (
    AdminUser,
    AnalyticsEvent,
    AuthSession,
    ContentAsset,
    Exercise,
    MeditationRun,
    PromoCode,
    PromoCodeBatch,
    PromoRedemption,
    Subscription,
    SubscriptionTransaction,
    User,
    WorkoutRun,
)
from models.enums import PromoStatus
from schemas.admin import (
    AdminContentAssetIn,
    AdminContentAssetOut,
    AdminContentAssetsOut,
    AdminContentAssetUpdateIn,
    AdminContentUploadOut,
    AdminDashboardOut,
    AdminExerciseCreateIn,
    AdminExerciseUpdateIn,
    AdminUserItemOut,
    AdminUsersTableItemOut,
    AdminUsersTableOut,
    AdminUsersOut,
    AdminUsersStatsOut,
    AdminPromoActivationItemOut,
    AdminPromoActivationsOut,
    AdminPromoBatchGenerateIn,
    AdminPromoBatchItemOut,
    AdminPromoBatchesOut,
)
from schemas.register import LoginIn, TokenOut
from schemas.subscription import (
    PromoBatchCreateIn,
    PromoBatchCreateOut,
    PromoCodeCreateIn,
    PromoCodeOut,
    PromoCodesOut,
    PromoStatsOut,
    SubscriptionPlanCreateIn,
    SubscriptionPlanOut,
)

router = APIRouter(prefix="/admin", tags=["admin"])
admin_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/admin/login", auto_error=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def clamp_limit(limit: int) -> int:
    return min(max(int(limit or 20), 1), 100)


ALLOWED_ASSET_TYPES = {"video", "audio", "image"}
ALLOWED_STATUS = {"draft", "published"}
MAX_VIDEO_BYTES = 300 * 1024 * 1024
MAX_AUDIO_BYTES = 100 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
CONTENT_UPLOAD_DIR = Path("statics/uploads/content")
EXERCISE_UPLOAD_DIR = Path("upload_exercises")
SAFE_UPLOAD_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
SAFE_PATH_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_-]+")
ALLOWED_MEDIA_EXTS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}


def normalize_asset_type(value: str) -> str:
    v = (value or "").strip().lower()
    if v not in ALLOWED_ASSET_TYPES:
        raise HTTPException(status_code=400, detail="asset_type must be one of: video, audio, image")
    return v


def normalize_status(value: str) -> str:
    v = (value or "").strip().lower()
    if v not in ALLOWED_STATUS:
        raise HTTPException(status_code=400, detail="status must be one of: draft, published")
    return v


def _guess_ext(content_type: Optional[str], original_name: Optional[str]) -> str:
    c = (content_type or "").lower()
    if c.startswith("video/"):
        ext = c.split("/", 1)[1]
        return f".{ext if ext else 'bin'}"
    if c.startswith("audio/"):
        ext = c.split("/", 1)[1]
        return f".{ext if ext else 'bin'}"
    if c.startswith("image/"):
        ext = c.split("/", 1)[1]
        return ".jpg" if ext == "jpeg" else f".{ext if ext else 'bin'}"

    if original_name and "." in original_name:
        return "." + original_name.rsplit(".", 1)[1].lower()
    return ".bin"


def _safe_path_segment(raw: str) -> str:
    cleaned = SAFE_PATH_SEGMENT_RE.sub("_", (raw or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "exercise"


def _uploaded_exercise_media_url(request: Request, folder: str, file_name: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/upload_exercises/{folder}/{file_name}"


async def save_exercise_media_file(
    file: UploadFile,
    exercise_folder: str,
    slot: str,
    max_bytes: int,
    request: Request,
    overwrite_existing: bool = True,
) -> tuple[str, str]:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"{slot} file is empty")
    if len(data) > max_bytes:
        raise HTTPException(status_code=400, detail=f"{slot} file exceeds size limit")

    ext = _guess_ext(file.content_type, file.filename)
    folder = _safe_path_segment(exercise_folder)
    out_dir = EXERCISE_UPLOAD_DIR / folder
    out_dir.mkdir(parents=True, exist_ok=True)

    file_name = f"{slot}{ext}"
    out_path = out_dir / file_name
    if out_path.exists() and not overwrite_existing:
        raise HTTPException(
            status_code=409,
            detail=f"File '{folder}/{file_name}' already exists. Set overwrite_existing=true to replace it.",
        )

    out_path.write_bytes(data)
    return _uploaded_exercise_media_url(request, folder, file_name), file_name


async def save_upload_file(file: UploadFile, category: str, max_bytes: int, request: Request) -> tuple[str, str]:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"{category} file is empty")
    if len(data) > max_bytes:
        raise HTTPException(status_code=400, detail=f"{category} file exceeds size limit")

    CONTENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ext = _guess_ext(file.content_type, file.filename)
    fname = f"{category}_{uuid.uuid4().hex}{ext}"
    out_path = CONTENT_UPLOAD_DIR / fname
    out_path.write_bytes(data)

    base = str(request.base_url).rstrip("/")
    url = f"{base}/statics/uploads/content/{fname}"
    return url, fname


def _normalize_desired_name(desired_name: Optional[str], file: UploadFile) -> Optional[str]:
    if not desired_name:
        return None

    name = desired_name.strip()
    if not name:
        return None

    # Prevent path traversal and disallow unsafe characters in user-provided file names.
    if Path(name).name != name or not SAFE_UPLOAD_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="Invalid file name. Use only letters, numbers, dot, dash, underscore.",
        )

    if "." not in name:
        name += _guess_ext(file.content_type, file.filename)

    return name


async def save_upload_file_with_name(
    file: UploadFile,
    category: str,
    max_bytes: int,
    request: Request,
    desired_name: Optional[str] = None,
    overwrite_existing: bool = False,
) -> tuple[str, str]:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"{category} file is empty")
    if len(data) > max_bytes:
        raise HTTPException(status_code=400, detail=f"{category} file exceeds size limit")

    CONTENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    normalized_name = _normalize_desired_name(desired_name, file)
    if normalized_name:
        fname = normalized_name
    else:
        ext = _guess_ext(file.content_type, file.filename)
        fname = f"{category}_{uuid.uuid4().hex}{ext}"

    out_path = CONTENT_UPLOAD_DIR / fname
    if out_path.exists() and not overwrite_existing:
        raise HTTPException(
            status_code=409,
            detail=f"File '{fname}' already exists. Set overwrite_existing=true to replace it.",
        )

    out_path.write_bytes(data)
    base = str(request.base_url).rstrip("/")
    url = f"{base}/statics/uploads/content/{fname}"
    return url, fname


def _extract_uploaded_name(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        path = urlparse(url).path or ""
        return Path(path).name or None
    except Exception:
        return None


def _parse_form_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    v = str(value).strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _validate_existing_media_filename(raw_name: Optional[str]) -> Optional[str]:
    if raw_name is None:
        return None
    name = str(raw_name).strip()
    if not name:
        return None
    if Path(name).name != name or not SAFE_UPLOAD_NAME_RE.match(name):
        raise ValueError("unsafe file name")
    ext = Path(name).suffix.lower()
    if ext and ext not in ALLOWED_MEDIA_EXTS:
        raise ValueError("unsupported file extension")
    file_path = CONTENT_UPLOAD_DIR / name
    if not file_path.exists():
        raise ValueError("file not found in upload directory")
    return name


def _uploaded_content_url(request: Request, file_name: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/statics/uploads/content/{file_name}"


def _parse_media_mapping_rows(raw_bytes: bytes, source_name: str) -> list[dict]:
    name = (source_name or "").lower()
    text = raw_bytes.decode("utf-8-sig", errors="replace")

    if name.endswith(".json"):
        payload = json.loads(text or "[]")
        if not isinstance(payload, list):
            raise HTTPException(status_code=400, detail="JSON must be an array of rows")
        out: list[dict] = []
        for idx, item in enumerate(payload):
            if not isinstance(item, dict):
                raise HTTPException(status_code=400, detail=f"JSON row {idx + 1} must be an object")
            out.append(item)
        return out

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV has no header")
    return [dict(r or {}) for r in reader]


def parse_duration_mmss(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    parts = v.split(":")
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="duration_mmss must be MM:SS format")
    try:
        mm = int(parts[0])
        ss = int(parts[1])
    except ValueError:
        raise HTTPException(status_code=400, detail="duration_mmss must be MM:SS format")
    if mm < 0 or ss < 0 or ss > 59:
        raise HTTPException(status_code=400, detail="duration_mmss must be MM:SS format")
    return mm * 60 + ss


def to_mmss(seconds: Optional[int]) -> Optional[str]:
    if seconds is None:
        return None
    s = int(seconds)
    if s < 0:
        return None
    mm = s // 60
    ss = s % 60
    return f"{mm:02d}:{ss:02d}"


def content_asset_to_out(doc: ContentAsset) -> AdminContentAssetOut:
    d = doc.model_dump()
    d["id"] = str(doc.id)
    d["duration_mmss"] = to_mmss(d.get("duration_seconds"))
    return AdminContentAssetOut(**d)


def code_random(length: int) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def get_model_collection(model_cls):
    settings = model_cls.get_settings()

    col = getattr(settings, "motor_collection", None)
    if col is None:
        col = getattr(settings, "pymongo_collection", None)
    if col is None:
        fn = getattr(model_cls, "get_motor_collection", None)
        if callable(fn):
            col = fn()
    if col is None:
        fn = getattr(model_cls, "get_pymongo_collection", None)
        if callable(fn):
            col = fn()
    if col is None:
        fn = getattr(model_cls, "get_collection", None)
        if callable(fn):
            col = fn()
    if col is None:
        raise RuntimeError(f"Cannot get collection for {model_cls.__name__}")
    return col


async def raw_find(model_cls, query: Optional[dict] = None, sort: Optional[list] = None, limit: Optional[int] = None) -> list[dict]:
    col = get_model_collection(model_cls)
    cursor = col.find(query or {})

    if sort:
        cursor = cursor.sort(sort)
    if limit is not None:
        cursor = cursor.limit(int(limit))

    to_list = getattr(cursor, "to_list", None)
    if callable(to_list):
        return await to_list(length=limit)

    return await run_in_threadpool(list, cursor)


async def raw_distinct(model_cls, key: str, query: Optional[dict] = None) -> list[Any]:
    col = get_model_collection(model_cls)
    result = col.distinct(key, query or {})
    if inspect.isawaitable(result):
        return await result
    return await run_in_threadpool(lambda: col.distinct(key, query or {}))


async def fetch_subscriptions_raw(query: Optional[dict] = None) -> list[dict]:
    return await raw_find(Subscription, query=query)


def compute_subscription_status_from_raw(sub: dict):
    # Avoid strict Beanie model parsing for legacy/invalid enum values in old records.
    stub = SimpleNamespace(
        expires_at=sub.get("expires_at"),
        grace_until=sub.get("grace_until"),
        auto_renew=sub.get("auto_renew", True),
    )
    return compute_subscription_status(stub)


def _pct_change(current: float, previous: float) -> float:
    if previous <= 0:
        return 100.0 if current > 0 else 0.0
    return ((current - previous) / previous) * 100.0


def _normalize_dt(dt: Optional[datetime]) -> Optional[datetime]:
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _subscription_state_at(sub: dict, ref: datetime) -> tuple[bool, bool]:
    exp = _normalize_dt(sub.get("expires_at"))
    if not exp:
        return False, False

    gu = _normalize_dt(sub.get("grace_until"))
    if ref >= exp:
        if gu and ref < gu:
            return False, True
        return False, False

    return True, False


async def get_current_admin_user(token: str = Depends(admin_oauth2_scheme)) -> AdminUser:
    decoded = decode_token(token)
    if not decoded or decoded.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token")

    sub = decoded.get("sub")
    if not sub or not isinstance(sub, str):
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        admin_id = PydanticObjectId(sub)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    admin = await AdminUser.get(admin_id)
    if not admin:
        raise HTTPException(status_code=401, detail="Admin not found")

    return admin


async def require_admin_user(admin_user=Depends(get_current_admin_user)):
    return admin_user


async def require_support_admin(admin_user=Depends(require_admin_user)):
    roles = [str(r).strip().lower() for r in (getattr(admin_user, "roles", None) or [])]
    if "support" not in roles:
        raise HTTPException(status_code=403, detail="Support role required")
    return admin_user


@router.get("/me")
async def admin_me(admin_user=Depends(require_admin_user)):
    return {
        "id": str(admin_user.id),
        "email": admin_user.email,
        "roles": list(getattr(admin_user, "roles", None) or []),
    }


@router.post("/login", response_model=TokenOut)
async def admin_login(payload: LoginIn, request: Request):
    identifier = (payload.identifier or "").strip().lower()
    if not identifier or not payload.password:
        raise HTTPException(status_code=400, detail="Identifier and password are required")

    admin = await AdminUser.find_one(AdminUser.email == identifier)
    if not admin or not admin.password_hash:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    admin_id = str(admin.id)
    refresh = create_refresh_token(sub=admin_id)
    decoded = decode_token(refresh)
    if not decoded or decoded.get("type") != "refresh":
        raise HTTPException(status_code=500, detail="Failed to create refresh token")

    expires_at = datetime.fromtimestamp(decoded["exp"], tz=timezone.utc).replace(tzinfo=None)

    await AuthSession(
        user_id=admin.id,
        refresh_token_hash=sha256(decoded["jti"]),
        expires_at=expires_at,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    ).insert()

    access = create_access_token(sub=admin_id)
    return TokenOut(access_token=access, refresh_token=refresh)


@router.post("/content/exercises")
async def admin_create_exercise(payload: AdminExerciseCreateIn, admin_user=Depends(require_support_admin)):
    code = (payload.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Exercise code is required")

    exists = await Exercise.find_one(Exercise.code == code)
    if exists:
        raise HTTPException(status_code=409, detail="Exercise code already exists")

    data = payload.model_dump()
    data["code"] = code

    doc = Exercise(**data)
    await doc.insert()
    result = doc.model_dump()
    result["id"] = str(doc.id)
    return result


@router.put("/content/exercises/{exercise_id}")
async def admin_update_exercise(
    exercise_id: PydanticObjectId,
    payload: AdminExerciseUpdateIn,
    admin_user=Depends(require_support_admin),
):
    doc = await Exercise.get(exercise_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Exercise not found")

    patch = payload.model_dump(exclude_unset=True)

    if "code" in patch:
        code = (patch.get("code") or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="Exercise code is required")

        existing = await Exercise.find_one(Exercise.code == code)
        if existing and existing.id != doc.id:
            raise HTTPException(status_code=409, detail="Exercise code already exists")
        patch["code"] = code

    for key, value in patch.items():
        setattr(doc, key, value)

    await doc.save()
    return doc


@router.post("/content/exercises/{exercise_id}/upload-media")
async def admin_upload_exercise_media(
    exercise_id: PydanticObjectId,
    request: Request,
    overwrite_existing: bool = Form(default=True),
    thumbnail_file: Optional[UploadFile] = File(default=None),
    video_file: Optional[UploadFile] = File(default=None),
    admin_user=Depends(require_support_admin),
):
    doc = await Exercise.get(exercise_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Exercise not found")

    if not thumbnail_file and not video_file:
        raise HTTPException(status_code=400, detail="thumbnail_file or video_file is required")

    folder_key = doc.code or str(doc.id)
    media = doc.media

    if thumbnail_file:
        if not (thumbnail_file.content_type or "").lower().startswith("image/"):
            raise HTTPException(status_code=400, detail="thumbnail_file must be an image/* file")
        thumb_url, _ = await save_exercise_media_file(
            thumbnail_file,
            exercise_folder=folder_key,
            slot="thumbnail",
            max_bytes=MAX_IMAGE_BYTES,
            request=request,
            overwrite_existing=overwrite_existing,
        )
        media.thumbnail_url = thumb_url

    if video_file:
        if not (video_file.content_type or "").lower().startswith("video/"):
            raise HTTPException(status_code=400, detail="video_file must be a video/* file")
        video_url, _ = await save_exercise_media_file(
            video_file,
            exercise_folder=folder_key,
            slot="video",
            max_bytes=MAX_VIDEO_BYTES,
            request=request,
            overwrite_existing=overwrite_existing,
        )
        media.video_url = video_url

    doc.media = media
    await doc.save()
    return {
        "status": "ok",
        "exercise_id": str(doc.id),
        "code": doc.code,
        "folder": _safe_path_segment(folder_key),
        "thumbnail_url": doc.media.thumbnail_url,
        "video_url": doc.media.video_url,
    }


@router.delete("/content/exercises/{exercise_id}")
async def admin_delete_exercise(exercise_id: PydanticObjectId, admin_user=Depends(require_support_admin)):
    doc = await Exercise.get(exercise_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Exercise not found")

    await doc.delete()
    return {"status": "ok"}


# @router.get("/users", response_model=AdminUsersOut)
async def admin_list_users(
    q: Optional[str] = Query(default=None),
    skip: int = 0,
    limit: int = 20,
    admin_user=Depends(require_admin_user),
):
    limit = clamp_limit(limit)
    query = User.find()

    if q:
        q = q.strip()
        if q:
            query = query.find(
                {
                    "$or": [
                        {"email": {"$regex": q, "$options": "i"}},
                        {"profile.name": {"$regex": q, "$options": "i"}},
                    ]
                }
            )

    total = await query.count()
    users = await query.sort("-created_at").skip(int(skip)).limit(limit).to_list()

    user_ids = [u.id for u in users]
    subscriptions = await fetch_subscriptions_raw({"user_id": {"$in": user_ids}}) if user_ids else []
    sub_by_user_id = {str(s.get("user_id")): s for s in subscriptions}

    items = []
    for user in users:
        status = None
        has_active = False

        sub = sub_by_user_id.get(str(user.id))
        if sub:
            status, is_active, _ = compute_subscription_status_from_raw(sub)
            has_active = bool(is_active)

        profile = getattr(user, "profile", None)
        name = getattr(profile, "name", None) if profile else None

        items.append(
            AdminUserItemOut(
                id=str(user.id),
                email=getattr(user, "email", None),
                name=name,
                created_at=user.created_at,
                updated_at=user.updated_at,
                has_active_subscription=has_active,
                subscription_status=status,
            )
        )

    return AdminUsersOut(items=items, total=int(total), skip=int(skip), limit=int(limit))


@router.get("/users/table", response_model=AdminUsersTableOut)
async def admin_users_table(
    q: Optional[str] = Query(default=None),
    skip: int = 0,
    limit: int = 20,
    admin_user=Depends(require_admin_user),
):
    limit = clamp_limit(limit)
    query = User.find()

    if q:
        q = q.strip()
        if q:
            query = query.find(
                {
                    "$or": [
                        {"email": {"$regex": q, "$options": "i"}},
                        {"profile.name": {"$regex": q, "$options": "i"}},
                    ]
                }
            )

    total = await query.count()
    users = await query.sort("-created_at").skip(int(skip)).limit(limit).to_list()
    if not users:
        return AdminUsersTableOut(items=[], total=int(total), skip=int(skip), limit=int(limit))

    user_ids = [u.id for u in users]

    subscriptions = await fetch_subscriptions_raw({"user_id": {"$in": user_ids}})
    sub_by_user_id = {str(s.get("user_id")): s for s in subscriptions}

    verified_txs = await raw_find(
        SubscriptionTransaction,
        query={"user_id": {"$in": user_ids}, "store.status": "verified"},
        sort=[("created_at", -1)],
    )
    latest_tx_by_user_id = {}
    for tx in verified_txs:
        uid = str(tx.get("user_id"))
        if uid not in latest_tx_by_user_id:
            latest_tx_by_user_id[uid] = tx

    items = []
    for user in users:
        uid = str(user.id)
        profile = getattr(user, "profile", None)
        name = getattr(profile, "name", None) if profile else None

        tx = latest_tx_by_user_id.get(uid)
        sub = sub_by_user_id.get(uid)

        plan = None
        date = None
        amount = None
        currency = None

        if tx:
            plan = tx.get("plan_code")
            date = tx.get("created_at")
            amount = tx.get("amount")
            currency = tx.get("currency")
        elif sub:
            plan = sub.get("plan_code")
            date = sub.get("created_at") or sub.get("started_at")

        items.append(
            AdminUsersTableItemOut(
                user_id=uid,
                name=name,
                email=getattr(user, "email", None),
                plan=plan,
                date=date,
                amount=amount,
                currency=currency,
            )
        )

    return AdminUsersTableOut(items=items, total=int(total), skip=int(skip), limit=int(limit))


# @router.get("/users/stats", response_model=AdminUsersStatsOut)
async def admin_users_stats(admin_user=Depends(require_admin_user)):
    now = utcnow()
    week_ago = now - timedelta(days=7)

    users_total = int(await User.find().count())
    users_new_7d = int(await User.find(User.created_at >= week_ago).count())

    subscriptions = await fetch_subscriptions_raw()
    users_with_subscription = len(subscriptions)

    active_subscriptions = 0
    in_grace_subscriptions = 0
    for sub in subscriptions:
        _, is_active, in_grace = compute_subscription_status_from_raw(sub)
        if is_active:
            active_subscriptions += 1
        if in_grace:
            in_grace_subscriptions += 1

    return AdminUsersStatsOut(
        users_total=users_total,
        users_new_7d=users_new_7d,
        users_with_subscription=users_with_subscription,
        active_subscriptions=active_subscriptions,
        in_grace_subscriptions=in_grace_subscriptions,
    )


# @router.get("/promocodes", response_model=PromoCodesOut)
async def admin_list_promocodes(
    status: Optional[PromoStatus] = None,
    skip: int = 0,
    limit: int = 20,
    q: Optional[str] = None,
    admin_user=Depends(require_admin_user),
):
    return await list_promos(status=status, skip=skip, limit=limit, q=q, current_user=admin_user)


# @router.post("/subscription/plans", response_model=SubscriptionPlanOut)
async def admin_create_subscription_plan(payload: SubscriptionPlanCreateIn, admin_user=Depends(require_admin_user)):
    return await create_plan(payload, current_user=admin_user)


# @router.post("/promocodes", response_model=PromoCodeOut)
async def admin_create_promocode(payload: PromoCodeCreateIn, admin_user=Depends(require_admin_user)):
    return await create_promo(payload, current_user=admin_user)


# @router.post("/promocodes/batches", response_model=PromoBatchCreateOut)
async def admin_create_promocode_batch(payload: PromoBatchCreateIn, admin_user=Depends(require_admin_user)):
    return await create_promo_batch(payload, current_user=admin_user)


@router.get("/promocodes/batches/{batch_id}/export")
async def admin_export_promocode_batch(batch_id: str, admin_user=Depends(require_admin_user)):
    return await export_promo_batch_csv(batch_id, current_user=admin_user)


# @router.get("/promocodes/stats", response_model=PromoStatsOut)
async def admin_promocode_stats(
    batch_id: Optional[str] = None,
    promo_code_id: Optional[str] = None,
    admin_user=Depends(require_admin_user),
):
    return await promo_stats(batch_id=batch_id, promo_code_id=promo_code_id, current_user=admin_user)


@router.post("/promocodes/batches/generate")
async def admin_generate_promocode_batch_screen(
    payload: AdminPromoBatchGenerateIn,
    admin_user=Depends(require_admin_user),
):
    name = payload.campaign_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="campaign_name is required")

    # Keep campaign names unique; return a clear API error instead of 500 on DB constraint hit.
    existing_batch = await PromoCodeBatch.find_one(PromoCodeBatch.name == name)
    if existing_batch:
        raise HTTPException(status_code=409, detail="campaign_name already exists")

    batch = PromoCodeBatch(
        name=name,
        discount_percent=int(payload.discount_percent),
        duration_days=payload.duration_days,
        max_uses_per_code=payload.max_uses_per_code,
        codes_count=payload.quantity,
        created_by_admin_id=admin_user.id,
    )
    try:
        await batch.insert()
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="campaign_name already exists")

    created = 0
    while created < int(payload.quantity):
        code = f"KV-{code_random(int(payload.code_length))}"
        doc = PromoCode(
            batch_id=batch.id,
            code=code,
            discount_percent=int(payload.discount_percent),
            duration_days=payload.duration_days,
            max_uses=payload.max_uses_per_code,
            used_count=0,
            expires_at=None,
            status=PromoStatus.active,
        )
        try:
            await doc.insert()
            created += 1
        except Exception:
            continue

    return {
        "batch_id": str(batch.id),
        "campaign_name": batch.name,
        "discount_percent": int(payload.discount_percent),
        "quantity": int(payload.quantity),
        "created_codes": int(created),
    }


@router.get("/promocodes/batches/recent", response_model=AdminPromoBatchesOut)
async def admin_recent_promocode_batches(
    skip: int = 0,
    limit: int = 20,
    admin_user=Depends(require_admin_user),
):
    limit = clamp_limit(limit)
    query = PromoCodeBatch.find().sort("-created_at")
    total = await query.count()
    batches = await query.skip(int(skip)).limit(limit).to_list()

    items = []
    for idx, b in enumerate(batches, start=1):
        codes = await PromoCode.find(PromoCode.batch_id == b.id).to_list()
        progress_total = len(codes)
        progress_used = int(sum(int(getattr(c, "used_count", 0) or 0) for c in codes))
        discount_percent = int(getattr(b, "discount_percent", 0) or 0)

        items.append(
            AdminPromoBatchItemOut(
                id=str(b.id),
                batch_code=f"B-{b.created_at.strftime('%Y%m')}-{skip + idx:03d}",
                campaign_name=b.name,
                discount_percent=discount_percent,
                progress_used=progress_used,
                progress_total=progress_total,
                created_at=b.created_at,
            )
        )

    return AdminPromoBatchesOut(items=items, total=int(total), skip=int(skip), limit=int(limit))


@router.get("/promocodes/activations", response_model=AdminPromoActivationsOut)
async def admin_promocode_activations(
    skip: int = 0,
    limit: int = 20,
    admin_user=Depends(require_admin_user),
):
    limit = clamp_limit(limit)
    query = PromoRedemption.find().sort("-redeemed_at")
    total = await query.count()
    redemptions = await query.skip(int(skip)).limit(limit).to_list()

    user_ids = [r.user_id for r in redemptions]
    users = await User.find({"_id": {"$in": user_ids}}).to_list() if user_ids else []
    email_by_user_id = {str(u.id): getattr(u, "email", None) for u in users}

    promo_ids = [r.promo_code_id for r in redemptions]
    promos = await PromoCode.find({"_id": {"$in": promo_ids}}).to_list() if promo_ids else []
    discount_by_promo_id = {str(p.id): int(getattr(p, "discount_percent", 0) or 0) for p in promos}

    items = []
    for r in redemptions:
        items.append(
            AdminPromoActivationItemOut(
                promo_code=r.code,
                activated_by_email=email_by_user_id.get(str(r.user_id)),
                activated_at=r.redeemed_at,
                discount_percent=discount_by_promo_id.get(str(r.promo_code_id)),
            )
        )

    return AdminPromoActivationsOut(items=items, total=int(total), skip=int(skip), limit=int(limit))


@router.get("/promocodes/export/all")
async def admin_export_all_promocodes_csv(admin_user=Depends(require_admin_user)):
    codes = await PromoCode.find().sort("-created_at").to_list()

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            [
                "id",
                "code",
                "batch_id",
                "discount_percent",
                "duration_days",
                "max_uses",
                "used_count",
                "status",
                "expires_at",
                "created_at",
            ]
        )
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        for c in codes:
            w.writerow(
                [
                    str(c.id),
                    c.code,
                    str(c.batch_id) if c.batch_id else "",
                    int(getattr(c, "discount_percent", 0) or 0),
                    int(c.duration_days),
                    int(c.max_uses),
                    int(c.used_count),
                    str(c.status),
                    c.expires_at.isoformat() if c.expires_at else "",
                    c.created_at.isoformat() if c.created_at else "",
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    filename = f"all-promo-codes-{utcnow().date().isoformat()}.csv"
    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/analytics/dashboard", response_model=AdminDashboardOut)
async def admin_dashboard(admin_user=Depends(require_admin_user)):
    now = utcnow()
    week_ago = now - timedelta(days=7)
    day_ago = now - timedelta(days=1)
    month_ago = now - timedelta(days=30)
    two_months_ago = now - timedelta(days=60)
    last_year = now.year - 1

    users_total = int(await User.find().count())
    users_new_7d = int(await User.find(User.created_at >= week_ago).count())
    users_new_30d = int(await User.find(User.created_at >= month_ago).count())
    users_prev_30d = int(
        await User.find(
            {
                "created_at": {
                    "$gte": two_months_ago,
                    "$lt": month_ago,
                }
            }
        ).count()
    )
    users_delta_30d_pct = _pct_change(float(users_new_30d), float(users_prev_30d))

    subscriptions = await fetch_subscriptions_raw()
    active_subscriptions = 0
    in_grace_subscriptions = 0
    active_subscriptions_prev_30d = 0
    for sub in subscriptions:
        is_active_now, in_grace_now = _subscription_state_at(sub, now)
        is_active_then, _ = _subscription_state_at(sub, month_ago)

        if is_active_now:
            active_subscriptions += 1
        if is_active_then:
            active_subscriptions_prev_30d += 1
        if in_grace_now:
            in_grace_subscriptions += 1
    active_subscriptions_delta_30d_pct = _pct_change(
        float(active_subscriptions), float(active_subscriptions_prev_30d)
    )

    workout_runs_7d = int(
        await WorkoutRun.find(
            {
                "completed_at": {"$ne": None, "$gte": week_ago},
            }
        ).count()
    )
    meditation_runs_7d = int(await MeditationRun.find(MeditationRun.completed_at >= week_ago).count())

    promo_codes_total = int(await PromoCode.find().count())
    promo_redemptions_total = int(await PromoRedemption.find().count())

    verified_txs = await raw_find(
        SubscriptionTransaction,
        query={"store.status": "verified"},
    )
    verified_revenue_total = 0.0
    revenue_30d = 0.0
    revenue_prev_30d = 0.0
    monthly_revenue_map = {m: 0.0 for m in range(1, 13)}
    monthly_revenue_map_last_year = {m: 0.0 for m in range(1, 13)}
    monthly_revenue_by_year: dict[int, dict[int, float]] = {}
    for tx in verified_txs:
        amount = tx.get("amount")
        if amount is None:
            continue
        try:
            amount_f = float(amount)
        except (TypeError, ValueError):
            continue
        verified_revenue_total += amount_f

        created_at = _normalize_dt(tx.get("created_at"))
        if created_at is None:
            continue
        if created_at >= month_ago:
            revenue_30d += amount_f
        elif two_months_ago <= created_at < month_ago:
            revenue_prev_30d += amount_f

        if created_at.year == now.year:
            monthly_revenue_map[created_at.month] += amount_f
        elif created_at.year == last_year:
            monthly_revenue_map_last_year[created_at.month] += amount_f

        y = int(created_at.year)
        if y not in monthly_revenue_by_year:
            monthly_revenue_by_year[y] = {m: 0.0 for m in range(1, 13)}
        monthly_revenue_by_year[y][created_at.month] += amount_f

    revenue_delta_30d_pct = _pct_change(revenue_30d, revenue_prev_30d)

    revenue_overview = [
        {"month": idx, "label": datetime(now.year, idx, 1).strftime("%b"), "amount": round(monthly_revenue_map[idx], 2)}
        for idx in range(1, 13)
    ]
    revenue_overview_last_year = [
        {"month": idx, "label": datetime(last_year, idx, 1).strftime("%b"), "amount": round(monthly_revenue_map_last_year[idx], 2)}
        for idx in range(1, 13)
    ]
    revenue_years = sorted(monthly_revenue_by_year.keys(), reverse=True)
    if now.year not in revenue_years:
        revenue_years.insert(0, now.year)
    revenue_overview_years = {
        str(y): [
            {"month": idx, "label": datetime(y, idx, 1).strftime("%b"), "amount": round(monthly_revenue_by_year.get(y, {m: 0.0 for m in range(1, 13)})[idx], 2)}
            for idx in range(1, 13)
        ]
        for y in revenue_years
    }

    recent_verified_txs = await raw_find(
        SubscriptionTransaction,
        query={"store.status": "verified"},
        sort=[("created_at", -1)],
        limit=5,
    )
    recent_user_ids = []
    for tx in recent_verified_txs:
        uid = tx.get("user_id")
        if uid is not None:
            recent_user_ids.append(uid)

    users_by_id = {}
    if recent_user_ids:
        user_docs = await raw_find(User, query={"_id": {"$in": recent_user_ids}})
        users_by_id = {str(u.get("_id")): u for u in user_docs}

    recent_subscriptions = []
    for tx in recent_verified_txs:
        uid = tx.get("user_id")
        user_doc = users_by_id.get(str(uid))
        profile = (user_doc or {}).get("profile") or {}
        recent_subscriptions.append(
            {
                "user_id": str(uid) if uid else None,
                "email": (user_doc or {}).get("email"),
                "name": profile.get("name"),
                "amount": tx.get("amount"),
                "currency": tx.get("currency"),
                "plan_code": tx.get("plan_code"),
                "source": tx.get("source"),
                "created_at": tx.get("created_at"),
            }
        )

    workout_active_users = set(
        str(uid)
        for uid in await raw_distinct(
            WorkoutRun,
            "user_id",
            {"completed_at": {"$ne": None, "$gte": day_ago}},
        )
        if uid is not None
    )
    meditation_active_users = set(
        str(uid)
        for uid in await raw_distinct(
            MeditationRun,
            "user_id",
            {"completed_at": {"$gte": day_ago}},
        )
        if uid is not None
    )
    analytics_active_users = set(
        str(uid)
        for uid in await raw_distinct(
            AnalyticsEvent,
            "user_id",
            {"ts": {"$gte": day_ago}, "user_id": {"$ne": None}},
        )
        if uid is not None
    )
    daily_active_users = len(workout_active_users | meditation_active_users | analytics_active_users)

    prev_day_start = day_ago - timedelta(days=7)
    prev_day_end = day_ago
    workout_prev_users = set(
        str(uid)
        for uid in await raw_distinct(
            WorkoutRun,
            "user_id",
            {"completed_at": {"$ne": None, "$gte": prev_day_start, "$lt": prev_day_end}},
        )
        if uid is not None
    )
    meditation_prev_users = set(
        str(uid)
        for uid in await raw_distinct(
            MeditationRun,
            "user_id",
            {"completed_at": {"$gte": prev_day_start, "$lt": prev_day_end}},
        )
        if uid is not None
    )
    analytics_prev_users = set(
        str(uid)
        for uid in await raw_distinct(
            AnalyticsEvent,
            "user_id",
            {"ts": {"$gte": prev_day_start, "$lt": prev_day_end}, "user_id": {"$ne": None}},
        )
        if uid is not None
    )
    daily_active_prev = len(workout_prev_users | meditation_prev_users | analytics_prev_users)
    daily_active_delta_7d_pct = _pct_change(float(daily_active_users), float(daily_active_prev))

    return AdminDashboardOut(
        users_total=users_total,
        users_new_7d=users_new_7d,
        active_subscriptions=active_subscriptions,
        in_grace_subscriptions=in_grace_subscriptions,
        daily_active_users=daily_active_users,
        workout_runs_7d=workout_runs_7d,
        meditation_runs_7d=meditation_runs_7d,
        promo_codes_total=promo_codes_total,
        promo_redemptions_total=promo_redemptions_total,
        verified_revenue_total=verified_revenue_total,
        users_delta_30d_pct=round(users_delta_30d_pct, 2),
        active_subscriptions_delta_30d_pct=round(active_subscriptions_delta_30d_pct, 2),
        revenue_delta_30d_pct=round(revenue_delta_30d_pct, 2),
        daily_active_delta_7d_pct=round(daily_active_delta_7d_pct, 2),
        revenue_current_year=now.year,
        revenue_last_year=last_year,
        revenue_years=revenue_years,
        revenue_overview=revenue_overview,
        revenue_overview_last_year=revenue_overview_last_year,
        revenue_overview_years=revenue_overview_years,
        recent_subscriptions=recent_subscriptions,
    )


@router.get("/content-library/assets", response_model=AdminContentAssetsOut)
# @router.get("/content-library", response_model=AdminContentAssetsOut)
async def admin_list_content_assets(
    q: Optional[str] = Query(default=None),
    asset_type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    skip: int = 0,
    limit: int = 20,
    admin_user=Depends(require_admin_user),
):
    limit = clamp_limit(limit)
    query = ContentAsset.find()

    if asset_type:
        query = query.find(ContentAsset.asset_type == normalize_asset_type(asset_type))
    if status:
        query = query.find(ContentAsset.status == normalize_status(status))
    if q and q.strip():
        s = q.strip()
        query = query.find(
            {
                "$or": [
                    {"title": {"$regex": s, "$options": "i"}},
                    {"author": {"$regex": s, "$options": "i"}},
                    {"file_name": {"$regex": s, "$options": "i"}},
                ]
            }
        )

    total = await query.count()
    docs = await query.sort("-created_at").skip(int(skip)).limit(limit).to_list()
    items = [content_asset_to_out(doc) for doc in docs]
    return AdminContentAssetsOut(items=items, total=int(total), skip=int(skip), limit=int(limit))


@router.get("/content-library/assets/{asset_id}", response_model=AdminContentAssetOut)
# @router.get("/content-library/{asset_id}", response_model=AdminContentAssetOut)
async def admin_get_content_asset(asset_id: PydanticObjectId, admin_user=Depends(require_admin_user)):
    doc = await ContentAsset.get(asset_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Content asset not found")
    return content_asset_to_out(doc)


@router.post("/content-library/uploads", response_model=AdminContentUploadOut)
async def admin_upload_content_files(
    request: Request,
    asset_type: str = Form(...),
    overwrite_existing: bool = Form(default=False),
    video_file_name: Optional[str] = Form(default=None),
    audio_file_name: Optional[str] = Form(default=None),
    image_file_name: Optional[str] = Form(default=None),
    video_file: Optional[UploadFile] = File(default=None),
    audio_file: Optional[UploadFile] = File(default=None),
    image_file: Optional[UploadFile] = File(default=None),
    admin_user=Depends(require_admin_user),
):
    t = normalize_asset_type(asset_type)

    video_url = None
    audio_url = None
    image_url = None
    primary_file_url = None
    primary_file_name = None

    if t == "video":
        if not video_file:
            raise HTTPException(status_code=400, detail="video_file is required for video content")
        if not (video_file.content_type or "").lower().startswith("video/"):
            raise HTTPException(status_code=400, detail="video_file must be a video/* file")
        video_url, video_name = await save_upload_file_with_name(
            video_file,
            "video",
            MAX_VIDEO_BYTES,
            request,
            desired_name=video_file_name,
            overwrite_existing=overwrite_existing,
        )
        primary_file_url = video_url
        primary_file_name = video_name

    elif t == "audio":
        if not audio_file:
            raise HTTPException(status_code=400, detail="audio_file is required for audio content")
        if not (audio_file.content_type or "").lower().startswith("audio/"):
            raise HTTPException(status_code=400, detail="audio_file must be an audio/* file")
        audio_url, audio_name = await save_upload_file_with_name(
            audio_file,
            "audio",
            MAX_AUDIO_BYTES,
            request,
            desired_name=audio_file_name,
            overwrite_existing=overwrite_existing,
        )
        primary_file_url = audio_url
        primary_file_name = audio_name

        if video_file:
            if not (video_file.content_type or "").lower().startswith("video/"):
                raise HTTPException(status_code=400, detail="video_file must be a video/* file")
            video_url, _ = await save_upload_file_with_name(
                video_file,
                "video",
                MAX_VIDEO_BYTES,
                request,
                desired_name=video_file_name,
                overwrite_existing=overwrite_existing,
            )
        if image_file:
            if not (image_file.content_type or "").lower().startswith("image/"):
                raise HTTPException(status_code=400, detail="image_file must be an image/* file")
            image_url, _ = await save_upload_file_with_name(
                image_file,
                "image",
                MAX_IMAGE_BYTES,
                request,
                desired_name=image_file_name,
                overwrite_existing=overwrite_existing,
            )

    elif t == "image":
        if not image_file:
            raise HTTPException(status_code=400, detail="image_file is required for image content")
        if not (image_file.content_type or "").lower().startswith("image/"):
            raise HTTPException(status_code=400, detail="image_file must be an image/* file")
        image_url, image_name = await save_upload_file_with_name(
            image_file,
            "image",
            MAX_IMAGE_BYTES,
            request,
            desired_name=image_file_name,
            overwrite_existing=overwrite_existing,
        )
        primary_file_url = image_url
        primary_file_name = image_name

        if audio_file:
            if not (audio_file.content_type or "").lower().startswith("audio/"):
                raise HTTPException(status_code=400, detail="audio_file must be an audio/* file")
            audio_url, _ = await save_upload_file_with_name(
                audio_file,
                "audio",
                MAX_AUDIO_BYTES,
                request,
                desired_name=audio_file_name,
                overwrite_existing=overwrite_existing,
            )

    return AdminContentUploadOut(
        video_url=video_url,
        audio_url=audio_url,
        image_url=image_url,
        primary_file_url=primary_file_url,
        primary_file_name=primary_file_name,
    )


@router.post("/content-library/assets/{asset_id}/replace-media", response_model=AdminContentAssetOut)
# @router.post("/content-library/{asset_id}/replace-media", response_model=AdminContentAssetOut)
async def admin_replace_content_asset_media(
    asset_id: PydanticObjectId,
    request: Request,
    overwrite_existing: bool = Form(default=True),
    video_file: Optional[UploadFile] = File(default=None),
    audio_file: Optional[UploadFile] = File(default=None),
    image_file: Optional[UploadFile] = File(default=None),
    admin_user=Depends(require_admin_user),
):
    doc = await ContentAsset.get(asset_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Content asset not found")

    if not any([video_file, audio_file, image_file]):
        raise HTTPException(status_code=400, detail="At least one file is required")

    if video_file:
        if not (video_file.content_type or "").lower().startswith("video/"):
            raise HTTPException(status_code=400, detail="video_file must be a video/* file")
        desired_name = _extract_uploaded_name(doc.video_url) or (
            doc.file_name if doc.asset_type == "video" else None
        )
        video_url, video_name = await save_upload_file_with_name(
            video_file,
            "video",
            MAX_VIDEO_BYTES,
            request,
            desired_name=desired_name,
            overwrite_existing=overwrite_existing,
        )
        doc.video_url = video_url
        if doc.asset_type == "video":
            doc.file_url = video_url
            doc.file_name = video_name

    if audio_file:
        if not (audio_file.content_type or "").lower().startswith("audio/"):
            raise HTTPException(status_code=400, detail="audio_file must be an audio/* file")
        desired_name = _extract_uploaded_name(doc.audio_url) or (
            doc.file_name if doc.asset_type == "audio" else None
        )
        audio_url, audio_name = await save_upload_file_with_name(
            audio_file,
            "audio",
            MAX_AUDIO_BYTES,
            request,
            desired_name=desired_name,
            overwrite_existing=overwrite_existing,
        )
        doc.audio_url = audio_url
        if doc.asset_type == "audio":
            doc.file_url = audio_url
            doc.file_name = audio_name

    if image_file:
        if not (image_file.content_type or "").lower().startswith("image/"):
            raise HTTPException(status_code=400, detail="image_file must be an image/* file")
        desired_name = _extract_uploaded_name(doc.image_url) or (
            doc.file_name if doc.asset_type == "image" else None
        )
        image_url, image_name = await save_upload_file_with_name(
            image_file,
            "image",
            MAX_IMAGE_BYTES,
            request,
            desired_name=desired_name,
            overwrite_existing=overwrite_existing,
        )
        doc.image_url = image_url
        if doc.asset_type == "image":
            doc.file_url = image_url
            doc.file_name = image_name

    await doc.save()
    return content_asset_to_out(doc)


@router.post("/content-library/exercises/media-mapping/import")
async def admin_import_exercise_media_mapping(
    request: Request,
    mapping_file: UploadFile = File(...),
    dry_run: str = Form(default="true"),
    strict: str = Form(default="false"),
    admin_user=Depends(require_admin_user),
):
    raw = await mapping_file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="mapping_file is empty")
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="mapping_file exceeds 5MB")

    rows = _parse_media_mapping_rows(raw, mapping_file.filename or "")
    if not rows:
        return {
            "dry_run": _parse_form_bool(dry_run, True),
            "strict": _parse_form_bool(strict, False),
            "total_rows": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "results": [],
        }

    dry_run_bool = _parse_form_bool(dry_run, True)
    strict_bool = _parse_form_bool(strict, False)

    results: list[dict] = []
    updated = 0
    skipped = 0
    errors = 0

    for i, row in enumerate(rows, start=1):
        row_result: dict[str, Any] = {"row": i}
        try:
            code = (row.get("exercise_code") or row.get("code") or "").strip()
            if not code:
                raise ValueError("exercise_code is required")
            row_result["exercise_code"] = code

            video_name = _validate_existing_media_filename(
                row.get("video_file_name") or row.get("video") or row.get("video_filename")
            )
            thumb_name = _validate_existing_media_filename(
                row.get("thumbnail_file_name")
                or row.get("thumbnail")
                or row.get("thumbnail_filename")
                or row.get("image_file_name")
                or row.get("image")
            )

            duration_raw = row.get("duration_seconds")
            duration_seconds = None
            if duration_raw not in (None, ""):
                duration_seconds = int(duration_raw)
                if duration_seconds <= 0 or duration_seconds > 3600:
                    raise ValueError("duration_seconds must be in range 1..3600")

            if not any([video_name, thumb_name, duration_seconds is not None]):
                raise ValueError("nothing to update for this row")

            doc = await Exercise.find_one(Exercise.code == code)
            if not doc:
                raise ValueError("exercise not found")

            changes: dict[str, Any] = {}
            if video_name:
                changes["video_url"] = _uploaded_content_url(request, video_name)
            if thumb_name:
                changes["thumbnail_url"] = _uploaded_content_url(request, thumb_name)
            if duration_seconds is not None:
                changes["duration_seconds"] = duration_seconds

            if not dry_run_bool:
                media = doc.media
                if "video_url" in changes:
                    media.video_url = changes["video_url"]
                if "thumbnail_url" in changes:
                    media.thumbnail_url = changes["thumbnail_url"]
                if "duration_seconds" in changes:
                    media.duration_seconds = changes["duration_seconds"]
                doc.media = media
                await doc.save()

            row_result["status"] = "updated"
            row_result["changes"] = changes
            updated += 1
        except Exception as e:
            msg = str(e)
            row_result["status"] = "error"
            row_result["error"] = msg if msg else "unknown error"
            errors += 1
            if strict_bool:
                results.append(row_result)
                break
        results.append(row_result)

    if errors:
        skipped = len(rows) - (updated + errors)
    else:
        skipped = len(rows) - updated

    return {
        "dry_run": dry_run_bool,
        "strict": strict_bool,
        "total_rows": len(rows),
        "updated": updated,
        "skipped": max(0, skipped),
        "errors": errors,
        "results": results,
    }


@router.post("/content-library/assets", response_model=AdminContentAssetOut)
# @router.post("/content-library", response_model=AdminContentAssetOut)
async def admin_create_content_asset(payload: AdminContentAssetIn, admin_user=Depends(require_admin_user)):
    duration_seconds = payload.duration_seconds
    if payload.duration_mmss is not None:
        duration_seconds = parse_duration_mmss(payload.duration_mmss)

    doc = ContentAsset(
        title=payload.title.strip(),
        author=(payload.author.strip() if payload.author else None),
        asset_type=normalize_asset_type(payload.asset_type),
        status=normalize_status(payload.status),
        duration_seconds=duration_seconds,
        file_url=payload.file_url,
        file_name=payload.file_name,
        video_url=payload.video_url,
        audio_url=payload.audio_url,
        image_url=payload.image_url,
        meta=payload.meta or {},
    )
    await doc.insert()
    return content_asset_to_out(doc)


@router.put("/content-library/assets/{asset_id}", response_model=AdminContentAssetOut)
# @router.put("/content-library/{asset_id}", response_model=AdminContentAssetOut)
async def admin_update_content_asset(
    asset_id: PydanticObjectId,
    payload: AdminContentAssetUpdateIn,
    admin_user=Depends(require_admin_user),
):
    doc = await ContentAsset.get(asset_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Content asset not found")

    patch = payload.model_dump(exclude_unset=True)

    if "duration_mmss" in patch:
        patch["duration_seconds"] = parse_duration_mmss(patch.pop("duration_mmss"))
    if "title" in patch and patch["title"] is not None:
        patch["title"] = patch["title"].strip()
    if "author" in patch and patch["author"] is not None:
        patch["author"] = patch["author"].strip()
    if "asset_type" in patch and patch["asset_type"] is not None:
        patch["asset_type"] = normalize_asset_type(patch["asset_type"])
    if "status" in patch and patch["status"] is not None:
        patch["status"] = normalize_status(patch["status"])

    for k, v in patch.items():
        setattr(doc, k, v)

    await doc.save()
    return content_asset_to_out(doc)


@router.delete("/content-library/assets/{asset_id}")
# @router.delete("/content-library/{asset_id}")
async def admin_delete_content_asset(asset_id: PydanticObjectId, admin_user=Depends(require_admin_user)):
    doc = await ContentAsset.get(asset_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Content asset not found")
    await doc.delete()
    return {"status": "ok"}


@router.get("/content-library/assets/export/csv")
# @router.get("/content-library/export/csv")
async def admin_export_content_assets_csv(
    q: Optional[str] = Query(default=None),
    asset_type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    admin_user=Depends(require_admin_user),
):
    query = ContentAsset.find()

    if asset_type:
        query = query.find(ContentAsset.asset_type == normalize_asset_type(asset_type))
    if status:
        query = query.find(ContentAsset.status == normalize_status(status))
    if q and q.strip():
        s = q.strip()
        query = query.find(
            {
                "$or": [
                    {"title": {"$regex": s, "$options": "i"}},
                    {"author": {"$regex": s, "$options": "i"}},
                    {"file_name": {"$regex": s, "$options": "i"}},
                ]
            }
        )

    docs = await query.sort("-created_at").to_list()

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            [
                "id",
                "title",
                "author",
                "asset_type",
                "status",
                "duration_seconds",
                "duration_mmss",
                "file_name",
                "file_url",
                "video_url",
                "audio_url",
                "image_url",
                "created_at",
            ]
        )
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        for doc in docs:
            w.writerow(
                [
                    str(doc.id),
                    doc.title,
                    doc.author or "",
                    doc.asset_type,
                    doc.status,
                    doc.duration_seconds or "",
                    to_mmss(doc.duration_seconds) or "",
                    doc.file_name or "",
                    doc.file_url or "",
                    doc.video_url or "",
                    doc.audio_url or "",
                    doc.image_url or "",
                    doc.created_at.isoformat() if doc.created_at else "",
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    filename = f"content_assets_{utcnow().date().isoformat()}.csv"
    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
