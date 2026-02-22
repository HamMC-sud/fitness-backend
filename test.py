import base64
import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict

from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

DEFAULT_TARGET_USER_ID = "699b746dbb015925d637610a"
SEED_COUNT_PER_COLLECTION = 10
POINTS_PER_ACHIEVEMENT = 50

ACHIEVEMENT_MAX = {
    "str_003": 3, "str_007": 7, "str_014": 14, "str_030": 30, "str_090": 90, "str_365": 365,
    "str_perf_mo": 30, "str_weekend": 4, "str_early": 10, "str_night": 10,
    "mil_run_5k": 5, "mil_run_10k": 10, "mil_run_21k": 21, "mil_run_42k": 42,
    "mil_hike_100": 100, "mil_hike_500": 500, "mil_cal_1k": 1000, "mil_cal_10k": 10000,
    "mil_cal_100k": 100000, "mil_vol_iron": 10000, "mil_vol_tank": 100000, "mil_everest": 8848,
    "ch_pushup": 500, "ch_plank": 3600, "ch_squat": 1000, "ch_pullup": 100, "ch_cardio": 86400,
    "ch_core": 50, "ch_legday": 10, "ch_hiit": 20, "ch_yoga": 30, "ch_flex": 18000,
    "fun_tcode": 1, "fun_burpee": 50, "fun_plank": 180, "fun_run": 500, "fun_night": 1, "fun_social": 5,
    "eq_db_50": 50, "eq_kb_50": 50, "eq_bw_100": 100, "eq_bar_50": 50, "eq_bench": 100,
    "time_10h": 600, "time_50h": 3000, "time_100h": 6000, "time_500h": 30000, "time_1k_h": 60000,
}


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _decode_jwt_sub(token: str) -> str | None:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        data = base64.urlsafe_b64decode(payload.encode("utf-8"))
        obj = json.loads(data.decode("utf-8"))
        return obj.get("sub")
    except Exception:
        return None


def resolve_target_user_id() -> ObjectId:
    # Pinned target user for deterministic seeding across connected collections.
    return ObjectId(DEFAULT_TARGET_USER_ID)


def resolve_client_and_db() -> tuple[MongoClient, str, Any]:
    mongo_uri = (os.getenv("MONGO_URI") or "").strip()
    db_name = (os.getenv("DB_NAME") or "").strip() or "fitness_db"

    if mongo_uri:
        client = MongoClient(mongo_uri)
        db = client.get_default_database() or client[db_name]
        return client, db.name, db

    host = (os.getenv("DB_HOST") or "127.0.0.1").strip()
    port = int((os.getenv("DB_PORT") or "27017").strip())
    client = MongoClient(f"mongodb://{host}:{port}")
    db = client[db_name]
    return client, db_name, db


def set_nested(doc: Dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    cur = doc
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def unique_value_for_field(collection_name: str, field: str, index_num: int) -> Any:
    suffix = f"{collection_name}_{field}_{index_num}_{uuid.uuid4().hex[:10]}"

    f = field.lower()
    if f == "email":
        return f"seed_{suffix}@example.com"
    if f.endswith("_id") and f != "user_id":
        return ObjectId()
    if f in {"created_at", "updated_at", "expires_at", "started_at", "completed_at", "occurred_at", "ts"}:
        return utcnow_naive()
    if f in {
        "points",
        "progress",
        "used",
        "attempts",
        "duration_days",
        "max_uses",
        "used_count",
        "session_minutes",
        "days_per_week",
        "weight_kg",
        "height_cm",
    }:
        return index_num + 1
    return suffix


def build_seed_doc(
    collection_name: str,
    index_info: Dict[str, Any],
    item_num: int,
    batch_id: str,
    target_user_id: ObjectId,
) -> Dict[str, Any]:
    now = utcnow_naive()
    doc: Dict[str, Any] = {
        "user_id": target_user_id,
        "_seed": True,
        "_seed_batch": batch_id,
        "_seed_item": item_num,
        "created_at": now,
        "updated_at": now,
    }

    for idx in index_info.values():
        if not idx.get("unique"):
            continue

        keys = [k for k, _ in idx.get("key", []) if k != "_id"]
        for k in keys:
            if k == "user_id":
                set_nested(doc, k, target_user_id)
            else:
                set_nested(doc, k, unique_value_for_field(collection_name, k, item_num))

    if collection_name == "body_measurements":
        # avoid invalid BSON `date` type; use datetime midnight
        d = now - timedelta(days=item_num)
        doc["date"] = d.replace(hour=0, minute=0, second=0, microsecond=0)
        doc.setdefault("weight_kg", 80.0 - item_num * 0.2)

    if collection_name == "user_achievements":
        doc.setdefault("achievement_code", f"seed_{batch_id}_{item_num}")
        doc.setdefault("name", "seed achievement")
        doc.setdefault("progress", 0.0)
        doc.setdefault("max_progress", 100.0)
        doc.setdefault("points", 0)

    return doc


def seed_collection(col, count: int, target_user_id: ObjectId) -> tuple[int, int]:
    inserted = 0
    attempts = 0
    errors = 0
    max_attempts = count * 30
    batch_id = uuid.uuid4().hex[:12]
    idx_info = col.index_information()

    while inserted < count and attempts < max_attempts:
        attempts += 1
        doc = build_seed_doc(col.name, idx_info, inserted, batch_id, target_user_id)
        try:
            col.insert_one(doc)
            inserted += 1
        except DuplicateKeyError:
            continue
        except Exception:
            errors += 1
            continue

    return inserted, errors


def to_day(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def ensure_non_empty_measurements_payload(db, target_user_id: ObjectId) -> None:
    now = utcnow_naive()

    raw = list(
        db.body_measurements.find(
            {"user_id": target_user_id, "date": {"$exists": True, "$ne": None}}
        ).sort("date", 1)
    )

    unique_days: list[date] = []
    seen = set()
    for x in raw:
        d = to_day(x.get("date"))
        if d and d not in seen:
            seen.add(d)
            unique_days.append(d)

    while len(unique_days) < 10:
        unique_days.append((now - timedelta(days=len(unique_days))).date())

    unique_days = sorted(unique_days)[-10:]

    for i, d in enumerate(unique_days):
        start = datetime(d.year, d.month, d.day, 0, 0, 0)
        end = start + timedelta(days=1)
        midday = datetime(d.year, d.month, d.day, 12, 0, 0)

        weight = round(82.0 - i * 0.3, 1)
        m = db.body_measurements.find_one({"user_id": target_user_id, "date": start})
        if m:
            db.body_measurements.update_one(
                {"_id": m["_id"]},
                {"$set": {"weight_kg": weight, "updated_at": now}},
            )
        else:
            db.body_measurements.insert_one(
                {
                    "user_id": target_user_id,
                    "date": start,
                    "weight_kg": weight,
                    "created_at": now,
                    "updated_at": now,
                    "_seed": True,
                }
            )

        has_run = db.workout_runs.count_documents(
            {
                "user_id": target_user_id,
                "completed_at": {"$gte": start, "$lt": end},
            }
        )
        if has_run == 0:
            db.workout_runs.insert_one(
                {
                    "user_id": target_user_id,
                    "source": "custom",
                    "workout_ref_id": ObjectId(),
                    "program_id": None,
                    "ai_plan_id": None,
                    "started_at": midday - timedelta(minutes=40),
                    "completed_at": midday,
                    "total_seconds": 40 * 60,
                    "calories_estimated": 320.0,
                    "rating_stars": 4,
                    "difficulty_feedback": "normal",
                    "exercise_results": [
                        {"exercise_id": ObjectId(), "mode": "reps", "reps_done": 15, "seconds_done": None, "feedback": "normal"},
                        {"exercise_id": ObjectId(), "mode": "reps", "reps_done": 20, "seconds_done": None, "feedback": "normal"},
                    ],
                    "created_at": midday,
                    "updated_at": midday,
                    "_seed": True,
                }
            )

        has_steps = db.analytics_events.count_documents(
            {
                "user_id": target_user_id,
                "ts": {
                    "$gte": start.replace(tzinfo=timezone.utc),
                    "$lt": end.replace(tzinfo=timezone.utc),
                },
                "$or": [{"props.steps": {"$gt": 0}}, {"props.step_count": {"$gt": 0}}],
            }
        )
        if has_steps == 0:
            db.analytics_events.insert_one(
                {
                    "user_id": target_user_id,
                    "anonymous_id": None,
                    "name": "daily_steps",
                    "ts": midday.replace(tzinfo=timezone.utc),
                    "props": {"steps": 8000 + i * 250},
                    "device": {"platform": "android"},
                    "session_id": f"seed_steps_{d.isoformat()}",
                    "created_at": midday,
                    "updated_at": midday,
                    "_seed": True,
                }
            )

    for i in range(10):
        code = f"seed_completed_{i+1:02d}"
        db.user_achievements.update_one(
            {"user_id": target_user_id, "achievement_code": code},
            {
                "$set": {
                    "category": "seed",
                    "name": f"Seed Completed {i+1}",
                    "logic": "progress == max_progress",
                    "progress": 100.0,
                    "max_progress": 100.0,
                    "points": 50,
                    "unlocked_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )


def ensure_non_empty_all_domains(db, target_user_id: ObjectId) -> None:
    now = utcnow_naive()

    # User
    db.users.update_one(
        {"_id": target_user_id},
        {
            "$set": {
                "email_verified": True,
                "timezone": "UTC",
                "country": "US",
                "language": "en",
                "updated_at": now,
            },
            "$setOnInsert": {
                "email": f"seed_{str(target_user_id)}@example.com",
                "created_at": now,
            },
        },
        upsert=True,
    )

    # Exercises
    exercise_ids = []
    for i in range(10):
        code = f"seed_ex_{i+1:02d}"
        ex_doc = {
            "code": code,
            "name": {"ru": [f"Упражнение {i+1}"], "en": [f"Exercise {i+1}"]},
            "description": {"ru": [f"Описание {i+1}"], "en": [f"Description {i+1}"]},
            "media": {
                "video_url": f"https://example.com/video/{code}.mp4",
                "thumbnail_url": f"https://example.com/thumb/{code}.jpg",
                "duration_seconds": 40,
                "mode": "reps",
            },
            "mode": "reps",
            "defaults": {"reps": 12, "duration_seconds": None},
            "beginner_tip": {"ru": ["Дышите ровно"], "en": ["Breathe steadily"]},
            "muscle_groups": ["core"],
            "movement_type": "strength",
            "workout_type": ["home"],
            "equipment": ["none"],
            "contraindications": [],
            "difficulty": "beginner",
            "calories_per_minute": 6.0,
            "instructions": {"ru": ["Повторите 12 раз"], "en": ["Repeat 12 reps"]},
            "status": "active",
            "updated_at": now,
        }
        db.exercises.update_one(
            {"code": code},
            {"$set": ex_doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        ex = db.exercises.find_one({"code": code}, {"_id": 1})
        if ex:
            exercise_ids.append(ex["_id"])

    # User workouts
    workout_ids = []
    for i in range(10):
        title = f"Seed Workout {i+1}"
        ex_id = exercise_ids[i % len(exercise_ids)] if exercise_ids else ObjectId()
        step = {
            "order": 1,
            "exercise_id": ex_id,
            "mode": "reps",
            "reps": 12,
            "duration_seconds": None,
            "rest_seconds_after": 45,
        }
        db.user_workouts.update_one(
            {"user_id": target_user_id, "title": title},
            {
                "$set": {"steps": [step], "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        w = db.user_workouts.find_one({"user_id": target_user_id, "title": title}, {"_id": 1})
        if w:
            workout_ids.append(w["_id"])

    # Workout runs + analytics steps + meditation runs
    for i in range(10):
        day = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        completed = day.replace(hour=12, minute=0)
        started = completed - timedelta(minutes=40)
        workout_ref = workout_ids[i % len(workout_ids)] if workout_ids else ObjectId()
        ex_id = exercise_ids[i % len(exercise_ids)] if exercise_ids else ObjectId()

        db.workout_runs.update_one(
            {"user_id": target_user_id, "workout_ref_id": workout_ref, "completed_at": completed},
            {
                "$set": {
                    "source": "custom",
                    "program_id": None,
                    "ai_plan_id": None,
                    "started_at": started,
                    "total_seconds": 40 * 60,
                    "calories_estimated": 320.0,
                    "rating_stars": 4,
                    "difficulty_feedback": "normal",
                    "exercise_results": [
                        {"exercise_id": ex_id, "mode": "reps", "reps_done": 15, "seconds_done": None, "feedback": "normal"}
                    ],
                    "updated_at": completed,
                },
                "$setOnInsert": {"created_at": completed},
            },
            upsert=True,
        )

        db.analytics_events.update_one(
            {"user_id": target_user_id, "name": "daily_steps", "session_id": f"seed_steps_{i+1:02d}"},
            {
                "$set": {
                    "ts": completed.replace(tzinfo=timezone.utc),
                    "props": {"steps": 7000 + i * 300},
                    "device": {"platform": "android"},
                    "updated_at": completed,
                },
                "$setOnInsert": {"created_at": completed},
            },
            upsert=True,
        )

        med_code = f"seed_med_{i+1:02d}"
        db.meditation_items.update_one(
            {"title.en": [f"Meditation {i+1}"]},
            {
                "$set": {
                    "type": "meditation",
                    "title": {"ru": [f"Медитация {i+1}"], "en": [f"Meditation {i+1}"]},
                    "description": {"ru": ["Тест"], "en": ["Seed"]},
                    "duration_minutes": 10,
                    "media": {"audio_url": f"https://example.com/{med_code}.mp3"},
                    "tags": ["seed"],
                    "status": "active",
                    "updated_at": completed,
                },
                "$setOnInsert": {"created_at": completed},
            },
            upsert=True,
        )
        med = db.meditation_items.find_one({"title.en": [f"Meditation {i+1}"]}, {"_id": 1})
        if med:
            db.meditation_runs.update_one(
                {"user_id": target_user_id, "meditation_id": med["_id"], "completed_at": completed},
                {
                    "$set": {
                        "type": "meditation",
                        "seconds_done": 600,
                        "points": 5,
                        "updated_at": completed,
                    },
                    "$setOnInsert": {"created_at": completed},
                },
                upsert=True,
            )

    # Subscription
    db.subscription_plans.update_one(
        {"code": "seed_month"},
        {
            "$set": {
                "duration_days": 30,
                "prices": {"USD": {"amount": 9.99}},
                "status": "active",
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    db.subscriptions.update_one(
        {"user_id": target_user_id},
        {
            "$set": {
                "status": "active",
                "plan_code": "seed_month",
                "source": "manual",
                "started_at": now - timedelta(days=3),
                "expires_at": now + timedelta(days=27),
                "grace_until": now + timedelta(days=57),
                "auto_renew": True,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    # Engagement / reminders / push
    db.reminders.update_one(
        {"user_id": target_user_id, "type": "workout"},
        {
            "$set": {
                "enabled": True,
                "timezone": "UTC",
                "time_hhmm": "21:00",
                "weekdays": [1, 2, 3, 4, 5, 6, 7],
                "payload": {},
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    db.device_push_tokens.update_one(
        {"user_id": target_user_id, "provider": "fcm", "platform": "android"},
        {
            "$set": {
                "token": f"seed_token_{str(target_user_id)}",
                "device_id": "seed-device",
                "locale": "en-US",
                "timezone": "UTC",
                "app_version": "1.0.0",
                "last_used_at": now,
                "is_active": True,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    # AI
    period = now.strftime("%Y-%m")
    db.ai_usage_monthly.update_one(
        {"user_id": target_user_id, "period": period},
        {
            "$set": {"base_limit": 1, "extra_from_rewarded": 1, "used": 0, "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    db.ai_plans.update_one(
        {"user_id": target_user_id, "status": "active"},
        {
            "$set": {
                "created_from": {"seed": True},
                "days": [{"date": now.date().isoformat(), "type": "workout", "workout_template": None}],
                "version": 1,
                "reroll_of_plan_id": None,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


def ensure_achievement_logic_data(db, target_user_id: ObjectId) -> None:
    """
    Seed achievement progress logic:
    - Every achievement has max_progress from ACHIEVEMENT_MAX
    - Some achievements are completed (progress == max_progress, date set)
    - Others are in-progress (progress < max_progress, date null)
    """
    now = utcnow_naive()
    ids = sorted(ACHIEVEMENT_MAX.keys())

    for idx, achievement_id in enumerate(ids):
        max_progress = float(ACHIEVEMENT_MAX[achievement_id])
        completed = (idx % 3 == 0)  # deterministic split
        progress = max_progress if completed else max(0.0, round(max_progress * 0.5, 2))

        db.user_achievements.update_one(
            {"user_id": target_user_id, "achievement_code": achievement_id},
            {
                "$set": {
                    "category": achievement_id.split("_", 1)[0],
                    "name": achievement_id,
                    "logic": "seeded_by_test_py",
                    "progress": progress,
                    "max_progress": max_progress,
                    "points": POINTS_PER_ACHIEVEMENT,
                    "unlocked_at": now if completed else None,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )


def main() -> None:
    client, db_name, db = resolve_client_and_db()
    target_user_id = resolve_target_user_id()

    print(f"Seeding DB: {db_name}")
    print(f"Target user: {target_user_id}")

    collection_names = [c for c in db.list_collection_names() if not c.startswith("system.")]

    if not collection_names:
        print("No collections found.")
        return

    total_inserted = 0
    total_errors = 0

    for cname in sorted(collection_names):
        col = db[cname]
        inserted, errors = seed_collection(col, SEED_COUNT_PER_COLLECTION, target_user_id)
        total_inserted += inserted
        total_errors += errors
        print(f"{cname}: inserted {inserted}/{SEED_COUNT_PER_COLLECTION}, errors={errors}")

    ensure_non_empty_measurements_payload(db, target_user_id)
    ensure_non_empty_all_domains(db, target_user_id)
    ensure_achievement_logic_data(db, target_user_id)
    print("Applied non-empty summary seed for measurements endpoint.")

    print(f"Done. Total inserted: {total_inserted}, total_errors: {total_errors}")


if __name__ == "__main__":
    main()
