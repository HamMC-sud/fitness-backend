from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from models import ALL_MODELS

load_dotenv()

SEED_COUNT_PER_COLLECTION = 5


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def dt_day(days_ago: int) -> datetime:
    d = (utcnow_naive() - timedelta(days=days_ago)).date()
    return datetime(d.year, d.month, d.day, 0, 0, 0)


def resolve_client_and_db() -> tuple[MongoClient, str, Any]:
    mongo_uri = (os.getenv("MONGO_URI") or "").strip()
    db_name = (os.getenv("DB_NAME") or "").strip() or "fitness_db"

    if mongo_uri:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=7000, connectTimeoutMS=7000)
        default_db = client.get_default_database()
        db = default_db if default_db is not None else client[db_name]
        return client, db.name, db

    host = (os.getenv("DB_HOST") or "127.0.0.1").strip()
    port = int((os.getenv("DB_PORT") or "27017").strip())
    client = MongoClient(f"mongodb://{host}:{port}", serverSelectionTimeoutMS=7000, connectTimeoutMS=7000)
    db = client[db_name]
    return client, db_name, db


def get_collection_name(model_cls: Any) -> str:
    settings = getattr(model_cls, "Settings", None)
    name = getattr(settings, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return model_cls.__name__.lower()


def pick_id(refs: Dict[str, List[ObjectId]], key: str) -> ObjectId:
    arr = refs.get(key, [])
    if arr:
        return arr[0]
    return ObjectId()


def pick_id_by_i(refs: Dict[str, List[ObjectId]], key: str, i: int) -> ObjectId:
    arr = refs.get(key, [])
    if arr:
        return arr[i % len(arr)]
    return ObjectId()


def admin_password_hash() -> str:
    # password: Admin123!
    return "$2b$12$4WDWiS0YALm9ycO3y3H87.SGkebTVwJEdgL.gZk5pvgWNywYA/zY."


def base_doc(i: int) -> Dict[str, Any]:
    now = utcnow_naive() - timedelta(seconds=i)
    return {"created_at": now, "updated_at": now, "_seed": True}


def doc_for_collection(cname: str, i: int, refs: Dict[str, List[ObjectId]]) -> Dict[str, Any]:
    now = utcnow_naive()
    user_id = pick_id_by_i(refs, "users", i)
    exercise_id = pick_id_by_i(refs, "exercises", i)
    workout_template_id = pick_id_by_i(refs, "workout_templates", i)
    meditation_id = pick_id_by_i(refs, "meditation_items", i)
    ai_thread_id = pick_id_by_i(refs, "ai_chat_threads", i)
    tx_id = pick_id_by_i(refs, "subscription_transactions", i)
    promo_code_id = pick_id_by_i(refs, "promo_codes", i)
    batch_id = pick_id_by_i(refs, "promo_code_batches", i)
    admin_id = pick_id_by_i(refs, "admin_users", i)

    if cname == "users":
        d = base_doc(i)
        d.update(
            {
                "email": f"seed_user_{i}_{uuid.uuid4().hex[:6]}@example.com",
                "email_verified": True,
                "password_hash": admin_password_hash(),
                "region": "INTL",
                "country": "US",
                "language": "en",
                "unit_system": "metric",
                "training_rest_seconds": 60,
                "timezone": "UTC",
                "profile": {
                    "name": f"Seed User {i+1}",
                    "photo_url": None,
                    "gender": "male" if i % 2 == 0 else "female",
                    "birth_date": "1995-01-01",
                    "height_cm": 175,
                    "weight_kg": 75.0 + i,
                    "target_weight_kg": 72.0,
                    "activity_level": "beginner",
                    "goals": ["get_fitter"],
                    "preferences": ["strength"],
                    "equipment": ["bodyweight"],
                    "injuries": ["none"],
                    "schedule": {"days_per_week": 3, "session_minutes": 30},
                },
                "flags": {"onboarding_completed": True, "is_premium": False},
                "stats": {"streak_days": i, "last_activity_at": now - timedelta(days=i)},
            }
        )
        return d

    if cname == "verification_codes":
        return {
            "email": f"verify_{i}_{uuid.uuid4().hex[:6]}@example.com",
            "password_hash": admin_password_hash(),
            "code_hash": uuid.uuid4().hex,
            "attempts": 0,
            "verified": False,
            "created_at": now,
            "expires_at": now + timedelta(minutes=15),
            "last_resend": None,
            "_seed": True,
        }

    if cname == "oauth_accounts":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "provider": "google",
                "provider_user_id": f"google_uid_{i}_{uuid.uuid4().hex[:6]}",
                "email": f"oauth_{i}_{uuid.uuid4().hex[:6]}@example.com",
            }
        )
        return d

    if cname == "auth_sessions":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "refresh_token_hash": uuid.uuid4().hex,
                "device_id": f"dev_{i}",
                "ip": "127.0.0.1",
                "user_agent": "seed-agent",
                "expires_at": now + timedelta(days=30),
                "revoked_at": None,
            }
        )
        return d

    if cname == "email_otps":
        d = base_doc(i)
        d.update(
            {
                "email": f"otp_{i}_{uuid.uuid4().hex[:6]}@example.com",
                "purpose": "register",
                "code_hash": uuid.uuid4().hex,
                "attempts": 0,
                "used_at": None,
                "expires_at": now + timedelta(minutes=10),
            }
        )
        return d

    if cname == "devices":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "platform": "android",
                "region": "INTL",
                "push_provider": "fcm",
                "push_token": f"push_{i}_{uuid.uuid4().hex}",
                "app_version": "1.0.0",
                "device_model": "Pixel Seed",
                "last_used_at": now,
            }
        )
        return d

    if cname == "exercises":
        d = base_doc(i)
        d.update(
            {
                "code": f"seed_ex_{i}_{uuid.uuid4().hex[:6]}",
                "name": {"ru": [f"Упражнение {i+1}"], "en": [f"Exercise {i+1}"]},
                "description": {"ru": ["Описание"], "en": ["Description"]},
                "media": {
                    "video_url": f"https://example.com/ex/{i}.mp4",
                    "thumbnail_url": f"https://example.com/ex/{i}.jpg",
                    "duration_seconds": 40,
                    "mode": "reps",
                },
                "mode": "reps",
                "defaults": {"reps": 12, "duration_seconds": None},
                "beginner_tip": {"ru": ["Дышите ровно"], "en": ["Breathe steadily"]},
                "muscle_groups": ["core"],
                "movement_type": "strength",
                "workout_type": ["strength"],
                "equipment": ["bodyweight"],
                "contraindications": ["none"],
                "difficulty": "beginner",
                "calories_per_minute": 6.0,
                "instructions": {"ru": ["12 повторов"], "en": ["12 reps"]},
                "status": "active",
            }
        )
        return d

    if cname == "workout_templates":
        d = base_doc(i)
        d.update(
            {
                "title": {"ru": [f"Шаблон {i+1}"], "en": [f"Template {i+1}"]},
                "description": {"ru": ["Тест"], "en": ["Seed"]},
                "type": "strength",
                "level": "beginner",
                "estimated_minutes": 30,
                "steps": [
                    {
                        "order": 1,
                        "exercise_id": exercise_id,
                        "mode": "reps",
                        "reps": 10,
                        "duration_seconds": None,
                        "rest_seconds_after": 45,
                    }
                ],
                "equipment_required": ["bodyweight"],
                "status": "active",
            }
        )
        return d

    if cname == "workout_programs":
        d = base_doc(i)
        d.update(
            {
                "slug": f"seed-program-{i}-{uuid.uuid4().hex[:5]}",
                "title": {"ru": [f"Программа {i+1}"], "en": [f"Program {i+1}"]},
                "description": {"ru": ["Тест"], "en": ["Seed"]},
                "weeks": 4,
                "workouts_per_week": 3,
                "session_minutes": 30,
                "level": "beginner",
                "goals": ["get_fitter"],
                "location": "home",
                "equipment_required": ["bodyweight"],
                "preview": {"title": f"Preview {i+1}"},
                "schedule": [{"day_index": 1, "workout_template_id": workout_template_id}],
                "status": "active",
            }
        )
        return d

    if cname == "meditation_items":
        d = base_doc(i)
        d.update(
            {
                "type": "meditation",
                "title": {"ru": [f"Медитация {i+1}"], "en": [f"Meditation {i+1}"]},
                "description": {"ru": ["Тест"], "en": ["Seed"]},
                "duration_minutes": 10,
                "media": {"audio_url": f"https://example.com/med/{i}.mp3"},
                "tags": ["seed"],
                "status": "active",
            }
        )
        return d

    if cname == "user_workouts":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "title": f"User Workout {i+1}",
                "steps": [
                    {
                        "order": 1,
                        "exercise_id": exercise_id,
                        "mode": "reps",
                        "reps": 12,
                        "duration_seconds": None,
                        "rest_seconds_after": 45,
                    }
                ],
            }
        )
        return d

    if cname == "workout_runs":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "source": "custom",
                "workout_ref_id": workout_template_id,
                "program_id": None,
                "ai_plan_id": None,
                "started_at": now - timedelta(minutes=45),
                "completed_at": now - timedelta(minutes=5),
                "total_seconds": 2400,
                "calories_estimated": 300.0,
                "rating_stars": 4,
                "difficulty_feedback": "normal",
                "exercise_results": [
                    {
                        "exercise_id": exercise_id,
                        "mode": "reps",
                        "reps_done": 12,
                        "seconds_done": None,
                        "feedback": "normal",
                    }
                ],
            }
        )
        return d

    if cname == "exercise_feedback_events":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "exercise_id": exercise_id,
                "workout_run_id": pick_id_by_i(refs, "workout_runs", i),
                "feedback": "normal",
            }
        )
        return d

    if cname == "meditation_runs":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "meditation_id": meditation_id,
                "type": "meditation",
                "completed_at": now - timedelta(minutes=2),
                "seconds_done": 600,
                "points": 5,
            }
        )
        return d

    if cname == "activity_events":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "type": "workout",
                "ref_id": pick_id_by_i(refs, "workout_runs", i),
                "points": 10,
                "occurred_at": now,
                "meta": {"seed": True},
            }
        )
        return d

    if cname == "weekly_focus_weeks":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "week_start": (utcnow_naive().date() - timedelta(days=7 * i)).isoformat(),
                "points_total": 30 + i,
                "goal_points": 50,
                "is_completed": i % 2 == 0,
            }
        )
        return d

    if cname == "achievement_defs":
        d = base_doc(i)
        d.update(
            {
                "code": f"seed_ach_{i}_{uuid.uuid4().hex[:6]}",
                "title": {"ru": f"Ачивка {i+1}", "en": f"Achievement {i+1}"},
                "type": "streak",
                "target": 10 + i,
                "meta": {"seed": True},
            }
        )
        return d

    if cname == "user_achievements":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "achievement_code": f"ua_{i}_{uuid.uuid4().hex[:6]}",
                "category": "general",
                "name": f"User Achievement {i+1}",
                "logic": "seed",
                "progress": 10.0 + i,
                "max_progress": 100.0,
                "points": 5,
                "unlocked_at": None,
            }
        )
        return d

    if cname == "user_exercise_stats":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "exercise_id": exercise_id,
                "difficulty_multiplier": 1.0,
                "suggested_reps": 12,
                "suggested_rest_seconds": 45,
                "easy_streak": i,
                "last_feedback": "normal",
                "last_done_at": now,
            }
        )
        return d

    if cname == "body_measurements":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "date": dt_day(i),
                "weight_kg": 75.0 + i,
                "chest_cm": 95.0,
                "waist_cm": 82.0,
                "hips_cm": 95.0,
                "arms_cm": 33.0,
            }
        )
        return d

    if cname == "before_after_photos":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "date": dt_day(i),
                "slot": "front",
                "photo_url": f"https://example.com/photo/{i}.jpg",
                "thumb_url": f"https://example.com/photo/{i}_thumb.jpg",
                "visibility": "private",
            }
        )
        return d

    if cname == "device_push_tokens":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "provider": "fcm",
                "platform": "android",
                "token": f"seed_push_token_{i}_{uuid.uuid4().hex}",
                "device_id": f"device_{i}",
                "locale": "en-US",
                "timezone": "UTC",
                "app_version": "1.0.0",
                "last_used_at": now,
                "is_active": True,
            }
        )
        return d

    if cname == "reminders":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "type": "workout",
                "enabled": True,
                "timezone": "UTC",
                "time_hhmm": "09:30",
                "weekdays": [1, 2, 3, 4, 5],
                "snooze_minutes": 10,
                "sound": "default",
                "payload": {"seed": True},
            }
        )
        return d

    if cname == "push_delivery_logs":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "kind": "workout_reminder",
                "local_date": (utcnow_naive().date() - timedelta(days=i)).isoformat(),
                "status": "sent",
                "attempt_count": 1,
                "last_attempt_at": now,
                "last_error": None,
                "meta": {"seed": True},
            }
        )
        return d

    if cname == "analytics_events":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "anonymous_id": None,
                "name": "daily_steps",
                "ts": now.replace(tzinfo=timezone.utc),
                "props": {"steps": 7000 + i * 100},
                "device": {"platform": "android"},
                "session_id": f"session_{i}_{uuid.uuid4().hex[:6]}",
            }
        )
        return d

    if cname == "offline_download_records":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "content_type": "workout",
                "content_id": f"content_{i}",
                "device_id": f"device_{i}",
                "downloaded_at": now,
                "meta": {"seed": True},
            }
        )
        return d

    if cname == "subscription_plans":
        d = base_doc(i)
        d.update(
            {
                "code": f"seed_plan_{i}_{uuid.uuid4().hex[:5]}",
                "duration_days": 30,
                "prices": {"USD": {"amount": 9.99 + i}},
                "status": "active",
            }
        )
        return d

    if cname == "subscriptions":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "status": "active",
                "plan_code": f"seed_plan_{i}",
                "source": "web",
                "started_at": now - timedelta(days=2),
                "expires_at": now + timedelta(days=28),
                "grace_until": now + timedelta(days=58),
                "auto_renew": True,
                "last_transaction_id": tx_id if tx_id else None,
            }
        )
        return d

    if cname == "subscription_transactions":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "source": "web",
                "plan_code": f"seed_plan_{i}",
                "amount": 9.99 + i,
                "currency": "USD",
                "store": {"status": "verified", "provider_tx_id": f"tx_{i}_{uuid.uuid4().hex[:6]}"},
                "promo": {},
            }
        )
        return d

    if cname == "promo_code_batches":
        d = base_doc(i)
        d.update(
            {
                "name": f"seed_batch_{i}_{uuid.uuid4().hex[:6]}",
                "duration_days": 30,
                "max_uses_per_code": 1,
                "codes_count": 10,
                "created_by_admin_id": admin_id if admin_id else ObjectId(),
            }
        )
        return d

    if cname == "promo_codes":
        d = base_doc(i)
        d.update(
            {
                "batch_id": batch_id if batch_id else None,
                "code": f"PROMO_{i}_{uuid.uuid4().hex[:6]}",
                "duration_days": 14,
                "max_uses": 1,
                "used_count": 0,
                "expires_at": now + timedelta(days=90),
                "status": "active",
            }
        )
        return d

    if cname == "promo_redemptions":
        d = base_doc(i)
        d.update(
            {
                "code": f"PROMO_REDEMPTION_{i}",
                "promo_code_id": promo_code_id,
                "user_id": user_id,
                "redeemed_at": now,
                "subscription_transaction_id": tx_id,
            }
        )
        return d

    if cname == "ai_usage_monthly":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "period": (utcnow_naive().date() - timedelta(days=30 * i)).strftime("%Y-%m"),
                "base_limit": 1,
                "extra_from_rewarded": 1,
                "used": 0,
            }
        )
        return d

    if cname == "ai_plans":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "status": "active",
                "created_from": {"seed": True},
                "days": [
                    {
                        "date": utcnow_naive().date().isoformat(),
                        "type": "workout",
                        "workout_template": {"id": str(workout_template_id)},
                    }
                ],
                "version": 1,
                "reroll_of_plan_id": None,
            }
        )
        return d

    if cname == "ai_requests":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "type": "generate_plan",
                "status": "ok",
                "prompt_meta": {"seed": True},
                "error": None,
            }
        )
        return d

    if cname == "ai_chat_threads":
        d = base_doc(i)
        d.update({"user_id": user_id, "title": f"Seed Thread {i+1}"})
        return d

    if cname == "ai_chat_messages":
        d = base_doc(i)
        d.update(
            {
                "thread_id": ai_thread_id,
                "user_id": user_id,
                "role": "user",
                "text": f"Seed message {i+1}",
            }
        )
        return d

    if cname == "rewarded_grants":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "nonce": f"nonce_{i}_{uuid.uuid4().hex[:8]}",
                "provider": "admob",
                "granted_at": now,
            }
        )
        return d

    if cname == "notifications":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "type": "system",
                "title": {"en": "Seed Notification", "ru": "Тест уведомление"},
                "body": {"en": f"Hello {i+1}", "ru": f"Привет {i+1}"},
                "data": {"seed": True},
                "sent_at": now,
                "read_at": None,
            }
        )
        return d

    if cname == "reminder_settings":
        d = base_doc(i)
        d.update(
            {
                "user_id": user_id,
                "workout": {"enabled": True, "time": "09:30"},
                "meditation": {"enabled": True, "time": "21:00"},
                "weight": {"enabled": False},
                "save_streak": {"enabled": True},
            }
        )
        return d

    if cname == "admin_users":
        d = base_doc(i)
        d.update(
            {
                "email": f"admin_seed_{i}_{uuid.uuid4().hex[:6]}@example.com",
                "password_hash": admin_password_hash(),
                "roles": ["super_admin"],
            }
        )
        return d

    if cname == "admin_audit_logs":
        d = base_doc(i)
        d.update(
            {
                "admin_id": admin_id if admin_id else ObjectId(),
                "action": f"seed_action_{i}",
                "target": {"kind": "seed"},
                "meta": {"seed": True},
            }
        )
        return d

    if cname == "password_resets":
        return {
            "email": f"reset_{i}_{uuid.uuid4().hex[:6]}@example.com",
            "code_hash": uuid.uuid4().hex,
            "attempts": 0,
            "created_at": now,
            "expires_at": now + timedelta(minutes=15),
            "used_at": None,
            "_seed": True,
        }

    if cname == "social_accounts":
        d = base_doc(i)
        d.update(
            {
                "provider": "google",
                "provider_user_id": f"social_{i}_{uuid.uuid4().hex[:6]}",
                "user_id": user_id,
                "email": f"social_{i}_{uuid.uuid4().hex[:6]}@example.com",
            }
        )
        return d

    if cname == "user_health_integrations":
        d = base_doc(i)
        provider = "apple_health" if i % 2 == 0 else "google_fit"
        d.update(
            {
                "user_id": user_id,
                "provider": provider,
                "is_connected": True,
                "connected_at": now,
                "external_account_id": f"health_acc_{i}_{uuid.uuid4().hex[:6]}",
                "meta": {"seed": True},
            }
        )
        return d

    if cname == "user_health_steps_daily":
        d = base_doc(i)
        provider = "apple_health" if i % 2 == 0 else "google_fit"
        d.update(
            {
                "user_id": user_id,
                "provider": provider,
                "date": dt_day(i),
                "steps": 7000 + 100 * i,
                "recorded_at": now,
                "timezone": "UTC",
                "meta": {"seed": True},
            }
        )
        return d

    if cname == "content_assets":
        d = base_doc(i)
        d.update(
            {
                "title": f"Content Asset {i+1}",
                "author": "Kovi",
                "asset_type": "video" if i % 2 == 0 else "audio",
                "status": "published" if i % 2 == 0 else "draft",
                "duration_seconds": 600 + i * 60,
                "file_url": f"https://example.com/content/{i}.mp4",
                "file_name": f"content-{i}.mp4",
                "image_url": f"https://example.com/content/{i}.jpg",
                "meta": {"seed": True},
            }
        )
        return d

    # Fallback for any future collection (minimal generic doc).
    d = base_doc(i)
    d.update({"_fallback": True})
    return d


def capture_inserted_id(refs: Dict[str, List[ObjectId]], cname: str, inserted_id: ObjectId) -> None:
    refs.setdefault(cname, []).append(inserted_id)


def seed_collection(db: Any, cname: str, refs: Dict[str, List[ObjectId]]) -> tuple[int, int, str | None]:
    col = db[cname]
    inserted = 0
    errors = 0
    first_error: str | None = None
    attempts = 0
    max_attempts = SEED_COUNT_PER_COLLECTION * 30

    while inserted < SEED_COUNT_PER_COLLECTION and attempts < max_attempts:
        attempts += 1
        doc = doc_for_collection(cname, inserted, refs)
        try:
            result = col.insert_one(doc)
            capture_inserted_id(refs, cname, result.inserted_id)
            inserted += 1
        except DuplicateKeyError as e:
            errors += 1
            if first_error is None:
                first_error = f"DuplicateKeyError: {e}"
        except Exception as e:
            errors += 1
            if first_error is None:
                first_error = f"{type(e).__name__}: {e}"

    return inserted, errors, first_error


def main() -> None:
    client, db_name, db = resolve_client_and_db()
    refs: Dict[str, List[ObjectId]] = {}

    print(f"Seeding DB: {db_name}")
    print(f"Models: {len(ALL_MODELS)} | Per collection: {SEED_COUNT_PER_COLLECTION}")

    failures: List[str] = []
    total_inserted = 0
    total_errors = 0

    for model_cls in ALL_MODELS:
        cname = get_collection_name(model_cls)
        inserted, errors, first_error = seed_collection(db, cname, refs)
        total_inserted += inserted
        total_errors += errors
        print(f"{cname}: inserted {inserted}/{SEED_COUNT_PER_COLLECTION}, errors={errors}")
        if first_error:
            print(f"  first_error: {first_error}")
        if inserted != SEED_COUNT_PER_COLLECTION:
            failures.append(cname)

    print(f"Done. Total inserted: {total_inserted}, total_errors: {total_errors}")
    client.close()

    if failures:
        raise RuntimeError(f"Strict seeding failed for collections: {', '.join(failures)}")


if __name__ == "__main__":
    main()
