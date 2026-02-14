from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, Header, HTTPException
from pymongo.errors import DuplicateKeyError

from api.auth.config import get_current_user
from models.engagement import AnalyticsEvent, DevicePushToken, OfflineDownloadRecord, PushDeliveryLog, Reminder
from models.subscription import Subscription
from models.enums import SubscriptionStatus
from models.workouts import WorkoutRun
from models.meditation_run import MeditationRun
from models.users import User
from schemas.engagement import (
    AnalyticsBatchIn,
    AnalyticsEventIn,
    AnalyticsIngestOut,
    DeleteOut,
    OfflineAuthorizeIn,
    OfflineAuthorizeOut,
    OfflineEntitlementOut,
    OfflineReportIn,
    PushRegisterIn,
    PushRegisterOut,
    PushSendIn,
    PushSendOut,
    PushTokenOut,
    PushTokensOut,
    PushUnregisterIn,
    ReminderIn,
    ReminderOut,
    RemindersOut,
    ReminderUpdateIn,
    StreakRunOut,
)

router = APIRouter(tags=["engagement"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def require_auth(user):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")


def clamp_int(v: int, lo: int, hi: int) -> int:
    return min(max(v, lo), hi)


def to_push_out(x: DevicePushToken) -> PushTokenOut:
    return PushTokenOut(
        id=str(x.id),
        provider=x.provider,
        platform=x.platform,
        token=x.token,
        device_id=x.device_id,
        locale=x.locale,
        timezone=x.timezone,
        app_version=x.app_version,
        last_used_at=x.last_used_at,
    )


def to_reminder_out(r: Reminder) -> ReminderOut:
    return ReminderOut(
        id=str(r.id),
        type=r.type,
        enabled=r.enabled,
        timezone=r.timezone,
        time_hhmm=r.time_hhmm,
        weekdays=r.weekdays or [],
        snooze_minutes=r.snooze_minutes,
        sound=r.sound,
        payload=r.payload or {},
        created_at=getattr(r, "created_at", None),
        updated_at=getattr(r, "updated_at", None),
    )


async def get_entitlement(user_id: PydanticObjectId) -> OfflineEntitlementOut:
    sub = await Subscription.find_one(Subscription.user_id == user_id)
    if not sub:
        return OfflineEntitlementOut(is_premium=False, in_grace=False, expires_at=None, grace_until=None, can_download=False)

    now = utcnow()
    exp = getattr(sub, "expires_at", None)
    gu = getattr(sub, "grace_until", None)

    if exp and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if gu and gu.tzinfo is None:
        gu = gu.replace(tzinfo=timezone.utc)

    if exp and exp > now:
        return OfflineEntitlementOut(is_premium=True, in_grace=False, expires_at=exp, grace_until=gu, can_download=True)

    if gu and gu > now:
        return OfflineEntitlementOut(is_premium=False, in_grace=True, expires_at=exp, grace_until=gu, can_download=True)

    st = getattr(sub, "status", None)
    if st in (SubscriptionStatus.active, SubscriptionStatus.grace):
        return OfflineEntitlementOut(is_premium=False, in_grace=False, expires_at=exp, grace_until=gu, can_download=False)

    return OfflineEntitlementOut(is_premium=False, in_grace=False, expires_at=exp, grace_until=gu, can_download=False)


def load_fcm_service_account() -> Optional[dict]:
    raw = os.getenv("FCM_SERVICE_ACCOUNT_JSON", "").strip()
    path = os.getenv("FCM_SERVICE_ACCOUNT_PATH", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            return None
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def fcm_access_token(sa: dict) -> Optional[str]:
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request

        scopes = ["https://www.googleapis.com/auth/firebase.messaging"]
        creds = service_account.Credentials.from_service_account_info(sa, scopes=scopes)
        creds.refresh(Request())
        return creds.token
    except Exception:
        return None


def send_fcm(token: str, title: str, body: str, data: Dict[str, Any]) -> Tuple[bool, str]:
    mode = (os.getenv("PUSH_MODE", "stub") or "stub").strip().lower()
    if mode == "stub":
        return True, ""

    sa = load_fcm_service_account()
    project_id = os.getenv("FCM_PROJECT_ID", "").strip()
    if not sa or not project_id:
        return False, "FCM not configured"

    at = fcm_access_token(sa)
    if not at:
        return False, "FCM auth failed (install google-auth)"

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    payload = {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            "data": {k: str(v) for k, v in (data or {}).items()},
        }
    }
    r = requests.post(url, headers={"Authorization": f"Bearer {at}"}, json=payload, timeout=12)
    if 200 <= r.status_code < 300:
        return True, ""
    try:
        return False, r.text[:1000]
    except Exception:
        return False, f"FCM error status={r.status_code}"


def send_push(provider: str, token: str, title: str, body: str, data: Dict[str, Any]) -> Tuple[bool, str]:
    p = (provider or "").strip().lower()
    if p in ("fcm", "firebase", "google"):
        return send_fcm(token, title, body, data)
    if p in ("rustore", "rustore_push"):
        mode = (os.getenv("PUSH_MODE", "stub") or "stub").strip().lower()
        if mode == "stub":
            return True, ""
        return False, "RuStore push not implemented"
    mode = (os.getenv("PUSH_MODE", "stub") or "stub").strip().lower()
    if mode == "stub":
        return True, ""
    return False, f"Unknown provider: {provider}"


def tz_for_user(user: User, reminder_tz: Optional[str]) -> ZoneInfo:
    tz_name = (reminder_tz or getattr(user, "timezone", None) or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def day_bounds_utc(tz: ZoneInfo, local_d: date) -> Tuple[datetime, datetime]:
    start_local = datetime.combine(local_d, time.min).replace(tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


async def has_activity_today(user_id: PydanticObjectId, start_utc: datetime, end_utc: datetime) -> bool:
    w = await WorkoutRun.find({"user_id": user_id, "completed_at": {"$ne": None, "$gte": start_utc, "$lt": end_utc}}).count()
    if w and int(w) > 0:
        return True
    m = await MeditationRun.find({"user_id": user_id, "completed_at": {"$ne": None, "$gte": start_utc, "$lt": end_utc}}).count()
    return bool(m and int(m) > 0)


async def get_tokens(user_id: PydanticObjectId) -> List[DevicePushToken]:
    return await DevicePushToken.find(DevicePushToken.user_id == user_id).sort("-last_used_at").limit(10).to_list()


def streak_text(lang: str) -> Tuple[str, str]:
    l = (lang or "en").lower()
    if l.startswith("ru"):
        return "Prime Fitness", "Сохрани стрик: сделай тренировку или медитацию сегодня 💪"
    return "Prime Fitness", "Save your streak: do a workout or meditation today 💪"


async def upsert_log(user_id: PydanticObjectId, kind: str, local_date: str) -> PushDeliveryLog:
    log = await PushDeliveryLog.find_one(
        PushDeliveryLog.user_id == user_id,
        PushDeliveryLog.kind == kind,
        PushDeliveryLog.local_date == local_date,
    )
    if log:
        return log
    log = PushDeliveryLog(
        user_id=user_id,
        kind=kind,
        local_date=local_date,
        status="pending",
        attempt_count=0,
        last_attempt_at=None,
        last_error=None,
        meta={},
    )
    try:
        await log.insert()
    except DuplicateKeyError:
        log = await PushDeliveryLog.find_one(
            PushDeliveryLog.user_id == user_id,
            PushDeliveryLog.kind == kind,
            PushDeliveryLog.local_date == local_date,
        )
        if log:
            return log
        raise
    return log


@router.post("/push/register", response_model=PushRegisterOut)
async def push_register(payload: PushRegisterIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    token = payload.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Invalid token")

    doc = await DevicePushToken.find_one(DevicePushToken.token == token)
    if doc:
        doc.user_id = current_user.id
        doc.provider = payload.provider
        doc.platform = payload.platform
        doc.device_id = payload.device_id
        doc.locale = payload.locale
        doc.timezone = payload.timezone
        doc.app_version = payload.app_version
        doc.last_used_at = utcnow()
        await doc.save()
        return PushRegisterOut(status="ok", token_id=str(doc.id))

    doc = DevicePushToken(
        user_id=current_user.id,
        provider=payload.provider,
        platform=payload.platform,
        token=token,
        device_id=payload.device_id,
        locale=payload.locale,
        timezone=payload.timezone,
        app_version=payload.app_version,
        last_used_at=utcnow(),
    )
    await doc.insert()
    return PushRegisterOut(status="ok", token_id=str(doc.id))


@router.post("/push/unregister", response_model=PushRegisterOut)
async def push_unregister(payload: PushUnregisterIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    token = payload.token.strip()
    doc = await DevicePushToken.find_one(DevicePushToken.token == token)
    if not doc:
        return PushRegisterOut(status="ok", token_id="")

    if doc.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    await doc.delete()
    return PushRegisterOut(status="ok", token_id=str(doc.id))


@router.get("/push/me", response_model=PushTokensOut)
async def push_me(current_user=Depends(get_current_user)):
    require_auth(current_user)
    items = await DevicePushToken.find(DevicePushToken.user_id == current_user.id).sort("-last_used_at").to_list()
    return PushTokensOut(items=[to_push_out(x) for x in items])


@router.post("/push/send-test", response_model=PushSendOut)
async def push_send_test(payload: PushSendIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    tokens = await get_tokens(current_user.id)
    if not tokens:
        return PushSendOut(sent=0, failed=0, results=[])

    results: List[Dict[str, Any]] = []
    sent = 0
    failed = 0

    for t in tokens:
        ok, err = send_push(t.provider, t.token, payload.title, payload.body, payload.data or {})
        if ok:
            sent += 1
        else:
            failed += 1
        results.append({"provider": t.provider, "platform": t.platform, "ok": ok, "error": err})

    return PushSendOut(sent=sent, failed=failed, results=results)


@router.post("/push/streak/run", response_model=StreakRunOut)
async def push_streak_run(x_internal_token: Optional[str] = Header(default=None)):
    secret = (os.getenv("PUSH_INTERNAL_TOKEN", "") or "").strip()
    if secret and (x_internal_token or "").strip() != secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    now = utcnow()
    reminders = await Reminder.find({"type": "streak_save", "enabled": True}).to_list()

    processed_users = 0
    sent_users = 0
    skipped_not_due = 0
    skipped_has_activity = 0
    skipped_no_tokens = 0
    skipped_disabled = 0
    skipped_already_sent = 0
    errors = 0

    for r in reminders:
        processed_users += 1

        user = await User.get(r.user_id)
        if not user:
            skipped_disabled += 1
            continue

        tz = tz_for_user(user, r.timezone)
        now_local = now.astimezone(tz)
        if now_local.hour < 21:
            skipped_not_due += 1
            continue

        local_d = now_local.date()
        local_date_str = local_d.isoformat()

        log = await upsert_log(user.id, "streak_save", local_date_str)
        if log.status == "sent":
            skipped_already_sent += 1
            continue

        if log.attempt_count >= 3 and log.last_attempt_at:
            last = log.last_attempt_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (now - last) < timedelta(hours=4):
                skipped_already_sent += 1
                continue

        start_utc, end_utc = day_bounds_utc(tz, local_d)
        if await has_activity_today(user.id, start_utc, end_utc):
            log.status = "skipped_has_activity"
            log.last_attempt_at = now
            log.last_error = None
            await log.save()
            skipped_has_activity += 1
            continue

        tokens = await get_tokens(user.id)
        if not tokens:
            log.status = "skipped_no_tokens"
            log.last_attempt_at = now
            log.last_error = None
            await log.save()
            skipped_no_tokens += 1
            continue

        log.attempt_count = clamp_int(int(log.attempt_count or 0) + 1, 0, 1000)
        log.last_attempt_at = now
        await log.save()

        title, body = streak_text(getattr(user, "language", "en") or "en")
        data = {"type": "streak_save", "date": local_date_str}

        any_ok = False
        last_err = ""

        for t in tokens:
            ok, err = send_push(t.provider, t.token, title, body, data)
            if ok:
                any_ok = True
            else:
                last_err = err or last_err

        if any_ok:
            log.status = "sent"
            log.last_error = None
            await log.save()
            sent_users += 1
        else:
            log.status = "failed"
            log.last_error = (last_err or "send failed")[:2000]
            await log.save()
            errors += 1

    return StreakRunOut(
        processed_users=processed_users,
        sent_users=sent_users,
        skipped_not_due=skipped_not_due,
        skipped_has_activity=skipped_has_activity,
        skipped_no_tokens=skipped_no_tokens,
        skipped_disabled=skipped_disabled,
        skipped_already_sent=skipped_already_sent,
        errors=errors,
    )


@router.get("/reminders", response_model=RemindersOut)
async def list_reminders(current_user=Depends(get_current_user)):
    require_auth(current_user)
    items = await Reminder.find(Reminder.user_id == current_user.id).sort("-created_at").to_list()
    return RemindersOut(items=[to_reminder_out(r) for r in items])


@router.get("/reminders/{reminder_id}", response_model=ReminderOut)
async def get_reminder(reminder_id: str, current_user=Depends(get_current_user)):
    require_auth(current_user)
    try:
        rid = PydanticObjectId(reminder_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid reminder_id")

    doc = await Reminder.get(rid)
    if not doc or doc.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return to_reminder_out(doc)


@router.post("/reminders", response_model=ReminderOut)
async def create_reminder(payload: ReminderIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    doc = Reminder(
        user_id=current_user.id,
        type=payload.type,
        enabled=payload.enabled,
        timezone=payload.timezone,
        time_hhmm=payload.time_hhmm,
        weekdays=payload.weekdays or [],
        snooze_minutes=payload.snooze_minutes,
        sound=payload.sound,
        payload=payload.payload or {},
    )
    await doc.insert()
    return to_reminder_out(doc)


@router.put("/reminders/{reminder_id}", response_model=ReminderOut)
async def update_reminder(reminder_id: str, payload: ReminderIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    try:
        rid = PydanticObjectId(reminder_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid reminder_id")

    doc = await Reminder.get(rid)
    if not doc or doc.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Reminder not found")

    doc.type = payload.type
    doc.enabled = payload.enabled
    doc.timezone = payload.timezone
    doc.time_hhmm = payload.time_hhmm
    doc.weekdays = payload.weekdays or []
    doc.snooze_minutes = payload.snooze_minutes
    doc.sound = payload.sound
    doc.payload = payload.payload or {}
    await doc.save()
    return to_reminder_out(doc)


@router.post("/reminders/{reminder_id}", response_model=ReminderOut)
async def update_reminder_post(reminder_id: str, payload: ReminderIn, current_user=Depends(get_current_user)):
    return await update_reminder(reminder_id, payload, current_user)


@router.patch("/reminders/{reminder_id}", response_model=ReminderOut)
async def patch_reminder(reminder_id: str, payload: ReminderUpdateIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    try:
        rid = PydanticObjectId(reminder_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid reminder_id")

    doc = await Reminder.get(rid)
    if not doc or doc.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Reminder not found")

    if payload.type is not None:
        doc.type = payload.type
    if payload.enabled is not None:
        doc.enabled = payload.enabled
    if payload.timezone is not None:
        doc.timezone = payload.timezone
    if payload.time_hhmm is not None:
        doc.time_hhmm = payload.time_hhmm
    if payload.weekdays is not None:
        doc.weekdays = payload.weekdays or []
    if payload.snooze_minutes is not None:
        doc.snooze_minutes = payload.snooze_minutes
    if payload.sound is not None:
        doc.sound = payload.sound
    if payload.payload is not None:
        doc.payload = payload.payload or {}

    await doc.save()
    return to_reminder_out(doc)


@router.delete("/reminders/{reminder_id}", response_model=DeleteOut)
async def delete_reminder(reminder_id: str, current_user=Depends(get_current_user)):
    require_auth(current_user)

    try:
        rid = PydanticObjectId(reminder_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid reminder_id")

    doc = await Reminder.get(rid)
    if not doc or doc.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Reminder not found")

    await doc.delete()
    return DeleteOut(status="ok")


@router.post("/analytics/event", response_model=AnalyticsIngestOut)
async def analytics_event(payload: AnalyticsEventIn, current_user=Depends(get_current_user)):
    user_id = getattr(current_user, "id", None) if current_user else None

    ts = payload.ts or utcnow()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    doc = AnalyticsEvent(
        user_id=user_id,
        anonymous_id=payload.anonymous_id,
        name=payload.name,
        ts=ts,
        props=payload.props or {},
        device=payload.device or {},
        session_id=payload.session_id,
    )
    await doc.insert()
    return AnalyticsIngestOut(status="ok", accepted=1)


@router.post("/analytics/events", response_model=AnalyticsIngestOut)
async def analytics_events(payload: AnalyticsBatchIn, current_user=Depends(get_current_user)):
    user_id = getattr(current_user, "id", None) if current_user else None

    docs = []
    for e in payload.events:
        ts = e.ts or utcnow()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        docs.append(
            AnalyticsEvent(
                user_id=user_id,
                anonymous_id=e.anonymous_id,
                name=e.name,
                ts=ts,
                props=e.props or {},
                device=e.device or {},
                session_id=e.session_id,
            )
        )

    if docs:
        await AnalyticsEvent.insert_many(docs)

    return AnalyticsIngestOut(status="ok", accepted=len(docs))


@router.get("/offline/entitlement", response_model=OfflineEntitlementOut)
async def offline_entitlement(current_user=Depends(get_current_user)):
    require_auth(current_user)
    return await get_entitlement(current_user.id)


@router.post("/offline/authorize", response_model=OfflineAuthorizeOut)
async def offline_authorize(payload: OfflineAuthorizeIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    ent = await get_entitlement(current_user.id)
    if not ent.can_download:
        return OfflineAuthorizeOut(can_download=False, until=None)

    until = ent.expires_at if ent.is_premium else ent.grace_until
    return OfflineAuthorizeOut(can_download=True, until=until)


@router.post("/offline/report", response_model=AnalyticsIngestOut)
async def offline_report(payload: OfflineReportIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    accepted = 0
    for it in payload.items or []:
        rec = OfflineDownloadRecord(
            user_id=current_user.id,
            content_type=it.content_type,
            content_id=it.content_id,
            device_id=it.device_id or payload.device_id,
            meta=it.meta or {},
        )
        await rec.insert()
        accepted += 1

    return AnalyticsIngestOut(status="ok", accepted=accepted)


def reminder_text(rem_type: str, lang: str) -> Tuple[str, str]:
    l = (lang or "en").lower()
    ru = l.startswith("ru")
    title = "Prime Fitness"

    t = (rem_type or "").strip().lower()
    if t == "workout":
        return (title, "Время тренировки 💪" if ru else "Workout time 💪")
    if t == "meditation":
        return (title, "Время медитации 🧘" if ru else "Meditation time 🧘")
    if t == "weight":
        return (title, "Пора измерить вес ⚖️" if ru else "Time to log your weight ⚖️")
    if t == "streak_save":
        return streak_text(lang)

    return (title, "Напоминание 🔔" if ru else "Reminder 🔔")


def parse_hhmm(hhmm: str) -> Optional[Tuple[int, int]]:
    s = (hhmm or "").strip()
    if ":" not in s:
        return None
    a, b = s.split(":", 1)
    try:
        h = int(a)
        m = int(b)
    except Exception:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h, m


def weekday_matches(weekdays: List[int], now_local: datetime) -> bool:
    if not weekdays:
        return True
    wd0 = now_local.weekday()
    wd1 = wd0 + 1
    s = set(int(x) for x in weekdays if isinstance(x, int) or str(x).isdigit())
    if any(1 <= x <= 7 for x in s):
        return wd1 in s
    return wd0 in s


def is_due_now(now_local: datetime, hhmm: str, weekdays: List[int]) -> bool:
    parsed = parse_hhmm(hhmm)
    if not parsed:
        return False
    if not weekday_matches(weekdays or [], now_local):
        return False

    h, m = parsed
    due_local = datetime.combine(now_local.date(), time(hour=h, minute=m)).replace(tzinfo=now_local.tzinfo)
    return due_local <= now_local < (due_local + timedelta(minutes=2))


@router.post("/push/reminders/run")
async def push_reminders_run(x_internal_token: Optional[str] = Header(default=None)):
    secret = (os.getenv("PUSH_INTERNAL_TOKEN", "") or "").strip()
    if secret and (x_internal_token or "").strip() != secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    now = utcnow()
    reminders = await Reminder.find({"enabled": True}).to_list()

    processed = 0
    due = 0
    sent_users = 0
    skipped_not_due = 0
    skipped_no_tokens = 0
    skipped_disabled_user = 0
    skipped_already_sent = 0
    skipped_has_activity = 0
    errors = 0

    for r in reminders:
        processed += 1

        user = await User.get(r.user_id)
        if not user:
            skipped_disabled_user += 1
            continue

        tz = tz_for_user(user, r.timezone)
        now_local = now.astimezone(tz)

        if not is_due_now(now_local, r.time_hhmm, r.weekdays or []):
            skipped_not_due += 1
            continue

        due += 1
        local_date_str = now_local.date().isoformat()
        kind = f"reminder:{(r.type or '').strip().lower()}:{str(r.id)}"

        log = await upsert_log(user.id, kind, local_date_str)
        if log.status == "sent":
            skipped_already_sent += 1
            continue

        if kind == "reminder:streak_save":
            start_utc, end_utc = day_bounds_utc(tz, now_local.date())
            if await has_activity_today(user.id, start_utc, end_utc):
                log.status = "skipped_has_activity"
                log.last_attempt_at = now
                log.last_error = None
                await log.save()
                skipped_has_activity += 1
                continue

        tokens = await get_tokens(user.id)
        if not tokens:
            log.status = "skipped_no_tokens"
            log.last_attempt_at = now
            log.last_error = None
            await log.save()
            skipped_no_tokens += 1
            continue

        log.attempt_count = clamp_int(int(log.attempt_count or 0) + 1, 0, 1000)
        log.last_attempt_at = now
        await log.save()

        title, body = reminder_text(r.type, getattr(user, "language", "en") or "en")
        data = {"type": (r.type or "reminder"), "date": local_date_str}

        any_ok = False
        last_err = ""

        for t in tokens:
            ok, err = send_push(t.provider, t.token, title, body, data)
            if ok:
                any_ok = True
            else:
                last_err = err or last_err

        if any_ok:
            log.status = "sent"
            log.last_error = None
            await log.save()
            sent_users += 1
        else:
            log.status = "failed"
            log.last_error = (last_err or "send failed")[:2000]
            await log.save()
            errors += 1

    return {
        "processed": processed,
        "due": due,
        "sent_users": sent_users,
        "skipped_not_due": skipped_not_due,
        "skipped_no_tokens": skipped_no_tokens,
        "skipped_disabled_user": skipped_disabled_user,
        "skipped_already_sent": skipped_already_sent,
        "skipped_has_activity": skipped_has_activity,
        "errors": errors,
    }
