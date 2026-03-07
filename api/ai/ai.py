from __future__ import annotations

import json
import logging
import os
import random
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import httpx
from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from api.auth.config import get_current_user
from models import (
    AiChatMessage,
    AiChatThread,
    AiDailyRecommendation,
    AiPlan,
    AiRequest,
    AiUsageMonthly,
    Exercise,
    RewardedGrant,
    Subscription,
)
from models.enums import AiRequestStatus, AiRequestType, SubscriptionStatus
from schemas.ai import (
    AiAdjustIn,
    AiAdjustOut,
    AiChatIn,
    AiChatHistoryOut,
    AiChatMessageOut,
    AiChatOut,
    AiDailyRecommendationOut,
    AiDailyRecommendationSaveIn,
    AiGenerateIn,
    AiGenerateOut,
    AiLimitsOut,
    AiPlanDayOut,
    AiPlanDayCardOut,
    AiPlanDayDetailOut,
    AiPlanDayEditIn,
    AiPlanOut,
    AiPlanWeekOut,
    AiPlanWeeksOut,
    AiSwapOptionOut,
    AiSwapOptionsOut,
    AiApplySwapIn,
    AiRerollIn,
    AiRerollOut,
    RewardedGrantIn,
    RewardedGrantOut,
)

router = APIRouter(tags=["ai"])
logger = logging.getLogger(__name__)

YC_API_KEY_SECRET = (os.getenv("YC_API_KEY_SECRET") or "").strip()
YC_FOLDER_ID = (os.getenv("YC_FOLDER_ID") or "").strip()
YC_GPT_MODEL_URI = (os.getenv("YC_GPT_MODEL_URI") or "").strip()
YC_COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def period_yyyy_mm(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _coerce_int(v: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except Exception:
        n = default
    return max(lo, min(hi, n))


def _as_str(v: Any) -> str:
    if v is None:
        return ""
    if hasattr(v, "value"):
        return str(getattr(v, "value"))
    return str(v)


def _as_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [_as_str(x).strip() for x in v if _as_str(x).strip()]
    s = _as_str(v).strip()
    return [s] if s else []


def _pick_i18n_name(name_obj: Any, language: str) -> str:
    lang = (language or "en").lower()
    if not name_obj:
        return "Exercise"

    seq = getattr(name_obj, lang, None)
    if isinstance(seq, list) and seq:
        return str(seq[0])
    if isinstance(seq, str) and seq:
        return seq

    for fallback in ("en", "ru"):
        seq = getattr(name_obj, fallback, None)
        if isinstance(seq, list) and seq:
            return str(seq[0])
        if isinstance(seq, str) and seq:
            return seq
    return "Exercise"


def _weekly_slots(workouts_per_week: int) -> set[int]:
    k = _coerce_int(workouts_per_week, default=4, lo=1, hi=7)
    if k >= 7:
        return set(range(7))
    return {min(6, round(i * (6 / max(1, k - 1)))) for i in range(k)}


async def is_premium_user(user_id: PydanticObjectId) -> bool:
    sub = await Subscription.find_one(Subscription.user_id == user_id)
    if not sub:
        return False

    now = utcnow()
    grace_until = getattr(sub, "grace_until", None)
    if grace_until:
        if grace_until.tzinfo is None:
            grace_until = grace_until.replace(tzinfo=timezone.utc)
        if grace_until > now:
            return True

    expires_at = getattr(sub, "expires_at", None)
    if expires_at:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at > now:
            return True

    return getattr(sub, "status", None) in (
        SubscriptionStatus.active,
        SubscriptionStatus.grace,
    )


async def get_or_create_usage(user_id: PydanticObjectId, period: str) -> AiUsageMonthly:
    rec = await AiUsageMonthly.find_one(
        AiUsageMonthly.user_id == user_id,
        AiUsageMonthly.period == period,
    )
    if rec:
        return rec

    rec = AiUsageMonthly(
        user_id=user_id,
        period=period,
        base_limit=1,
        extra_from_rewarded=0,
        used=0,
    )
    await rec.insert()
    return rec


async def has_child_reroll(parent_plan_id: PydanticObjectId) -> bool:
    return await AiPlan.find_one(AiPlan.reroll_of_plan_id == parent_plan_id) is not None


async def get_active_plan(user_id: PydanticObjectId) -> Optional[AiPlan]:
    return await AiPlan.find(
        AiPlan.user_id == user_id,
        AiPlan.status == "active",
    ).sort("-created_at").first_or_none()


def plan_to_out(plan: AiPlan) -> AiPlanOut:
    return AiPlanOut(
        id=str(plan.id),
        status=plan.status,
        version=plan.version,
        reroll_of_plan_id=str(plan.reroll_of_plan_id) if plan.reroll_of_plan_id else None,
        days=[AiPlanDayOut.model_validate(d.model_dump()) for d in (plan.days or [])],
        created_at=plan.created_at,
    )


def _today_iso_for_user(current_user: Any) -> str:
    tz_name = _as_str(getattr(current_user, "timezone", "UTC")) or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    return utcnow().astimezone(tz).date().isoformat()


def _daily_rec_to_out(rec: AiDailyRecommendation) -> AiDailyRecommendationOut:
    return AiDailyRecommendationOut(
        id=str(rec.id),
        date=rec.date,
        text=rec.text,
        saved=bool(rec.saved),
        opened_at=rec.opened_at,
        saved_at=rec.saved_at,
        meta=rec.meta or {},
    )


async def _build_daily_recommendation(current_user: Any, day_iso: str) -> tuple[str, Dict[str, Any]]:
    plan = await get_active_plan(current_user.id)
    if plan:
        for d in (plan.days or []):
            if str(getattr(d, "date", "")) != day_iso:
                continue
            d_type = str(getattr(d, "type", "recovery"))
            wt = getattr(d, "workout_template", None) or {}
            if d_type == "workout":
                title = str(wt.get("title") or "Workout session")
                duration = wt.get("duration_min")
                focus = wt.get("focus")
                chunks = [f"Today: {title}."]
                if duration:
                    chunks.append(f"Duration: {duration} min.")
                if focus:
                    chunks.append(f"Focus: {focus}.")
                chunks.append("Keep strict form and finish with light stretching.")
                return " ".join(chunks), {
                    "source": "active_plan",
                    "plan_id": str(plan.id),
                    "type": d_type,
                    "date": day_iso,
                }
            recovery_text = str(wt.get("recommendation") or "Mobility + light walk 20-30 min.")
            return recovery_text, {
                "source": "active_plan",
                "plan_id": str(plan.id),
                "type": d_type,
                "date": day_iso,
            }

    return (
        "Daily recommendation: 20-30 min brisk walk, 5 min mobility, and drink enough water.",
        {"source": "fallback", "date": day_iso},
    )


def _is_unsaved_expired(rec: AiDailyRecommendation, now: datetime) -> bool:
    if rec.saved or rec.removed_at is not None or rec.opened_at is None:
        return False
    opened = rec.opened_at
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    return (now - opened) >= timedelta(days=1)


def _goal_to_types(goals: list[str], preferences: list[str]) -> list[str]:
    types: list[str] = []
    mapping = {
        "build_muscle": ["strength"],
        "lose_weight": ["cardio", "hiit"],
        "endurance": ["cardio", "hiit"],
        "flexibility": ["stretching", "yoga"],
        "get_fitter": ["strength", "cardio"],
    }
    pref_mapping = {
        "strength": ["strength"],
        "cardio": ["cardio", "hiit"],
        "stretching": ["stretching", "yoga"],
        "meditation_yoga": ["yoga", "stretching"],
    }
    for g in goals:
        types.extend(mapping.get(g, []))
    for p in preferences:
        types.extend(pref_mapping.get(p, []))
    if not types:
        return ["strength", "cardio", "hiit"]

    seen: set[str] = set()
    out: list[str] = []
    for t in types:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _difficulty_to_intensity(diff: str) -> str:
    d = (diff or "").lower()
    if d == "advanced":
        return "high"
    if d == "intermediate":
        return "moderate"
    return "low"


def _normalize_intensity(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = str(value).strip().lower()
    mapping = {
        "beginner": "low",
        "intermediate": "moderate",
        "advanced": "high",
        "low": "low",
        "moderate": "moderate",
        "high": "high",
    }
    return mapping.get(v)


def _merge_prompt_with_profile(current_user: Any, prompt_meta: Dict[str, Any]) -> Dict[str, Any]:
    profile = getattr(current_user, "profile", None)
    schedule = getattr(profile, "schedule", None) if profile else None

    goals = _as_str_list(prompt_meta.get("goals") or prompt_meta.get("goal"))
    if not goals and profile:
        goals = _as_str_list(getattr(profile, "goals", []))

    preferences = _as_str_list(prompt_meta.get("preferences"))
    if not preferences and profile:
        preferences = _as_str_list(getattr(profile, "preferences", []))

    equipment = _as_str_list(prompt_meta.get("equipment"))
    if not equipment and profile:
        equipment = _as_str_list(getattr(profile, "equipment", []))

    injuries = _as_str_list(prompt_meta.get("injuries"))
    if not injuries and profile:
        injuries = _as_str_list(getattr(profile, "injuries", []))

    level = (_as_str(prompt_meta.get("activity_level"))).lower().strip()
    if not level and profile:
        level = _as_str(getattr(profile, "activity_level", "")).lower().strip()
    if level not in {"beginner", "intermediate", "advanced"}:
        level = "beginner"

    days_per_week = _coerce_int(
        prompt_meta.get("workouts_per_week")
        or prompt_meta.get("days_per_week")
        or (getattr(schedule, "days_per_week", None) if schedule else None),
        default=4,
        lo=1,
        hi=7,
    )
    duration_min = _coerce_int(
        prompt_meta.get("duration_min")
        or prompt_meta.get("session_minutes")
        or (getattr(schedule, "session_minutes", None) if schedule else None),
        default=35,
        lo=10,
        hi=120,
    )
    intensity = _as_str(prompt_meta.get("intensity")).strip().lower()
    if not intensity:
        intensity = _difficulty_to_intensity(level)

    return {
        "goals": goals,
        "preferences": preferences,
        "equipment": equipment,
        "injuries": injuries,
        "level": level,
        "days_per_week": days_per_week,
        "duration_min": duration_min,
        "intensity": intensity,
        "language": _as_str(getattr(current_user, "language", "en")) or "en",
        "country": _as_str(getattr(current_user, "country", "INTL")) or "INTL",
        "timezone": _as_str(getattr(current_user, "timezone", "UTC")) or "UTC",
    }


def _exercise_is_allowed(ex: Exercise, injuries: set[str], equipment: set[str]) -> bool:
    contraindications = {_as_str(x) for x in (ex.contraindications or [])}
    if injuries and contraindications.intersection(injuries):
        return False

    required = {_as_str(x) for x in (ex.equipment or [])}
    if not required:
        return True

    required.discard("bodyweight")
    if not required:
        return True

    return required.issubset(equipment)


def _exercise_match_type(ex: Exercise, target_types: set[str]) -> bool:
    wtypes = {_as_str(x) for x in (ex.workout_type or [])}
    return not target_types or bool(wtypes.intersection(target_types))


def _progression_note(week_idx: int, intensity: str) -> str:
    level = (intensity or "moderate").lower()
    if week_idx == 0:
        return "Week 1: build technique and consistency."
    if week_idx == 1:
        return "Week 2: add 1 set to key exercises if recovery is good."
    if week_idx == 2:
        return "Week 3: increase reps/time by 10-15% with strict form."
    if level == "high":
        return "Week 4+: add load carefully and keep 1-2 reps in reserve."
    return "Week 4+: keep progressive overload with at least one easier day."


def _extract_json(text: str) -> Optional[dict]:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        snippet = raw[start : end + 1]
        try:
            obj = json.loads(snippet)
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


async def yandex_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.4,
    max_tokens: int = 1400,
) -> Optional[str]:
    if not YC_API_KEY_SECRET:
        logger.warning("Yandex completion skipped: YC_API_KEY_SECRET is empty")
        return None

    model_uri = YC_GPT_MODEL_URI or (f"gpt://{YC_FOLDER_ID}/yandexgpt/latest" if YC_FOLDER_ID else "")
    if not model_uri:
        logger.warning("Yandex completion skipped: model URI is empty (YC_GPT_MODEL_URI/YC_FOLDER_ID)")
        return None

    body = {
        "modelUri": model_uri,
        "completionOptions": {
            "stream": False,
            "temperature": temperature,
            "maxTokens": max_tokens,
        },
        "messages": messages,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                YC_COMPLETION_URL,
                headers={"Authorization": f"Api-Key {YC_API_KEY_SECRET}"},
                json=body,
            )
    except httpx.HTTPError as exc:
        logger.error("Yandex completion HTTP error: %s", str(exc))
        return None

    if r.status_code != 200:
        response_preview = (r.text or "")[:1000]
        logger.warning(
            "Yandex completion non-200 response: status=%s model_uri=%s body=%s",
            r.status_code,
            model_uri,
            response_preview,
        )
        return None

    try:
        data = r.json()
        return str(data["result"]["alternatives"][0]["message"]["text"]).strip()
    except Exception as exc:
        response_preview = (r.text or "")[:1000]
        logger.error(
            "Yandex completion parse error: %s; response=%s",
            str(exc),
            response_preview,
        )
        return None


async def _load_exercises_for_planning(
    injuries: set[str],
    equipment: set[str],
    target_types: set[str],
) -> list[Exercise]:
    active = await Exercise.find(Exercise.status == "active").limit(1200).to_list()
    if not active:
        return []

    filtered = [
        ex
        for ex in active
        if _exercise_is_allowed(ex, injuries=injuries, equipment=equipment)
        and _exercise_match_type(ex, target_types=target_types)
    ]
    if filtered:
        return filtered

    safe_only = [ex for ex in active if _exercise_is_allowed(ex, injuries=injuries, equipment=set())]
    return safe_only or active


def _build_workout_template(
    *,
    day_date: date,
    week_idx: int,
    day_idx: int,
    inputs: Dict[str, Any],
    exercises: list[Exercise],
    target_types: list[str],
    rng_seed: str,
) -> Dict[str, Any]:
    duration_min = _coerce_int(inputs.get("duration_min"), default=35, lo=10, hi=120)
    intensity = str(inputs.get("intensity") or "moderate").lower()
    level = str(inputs.get("level") or "beginner").lower()
    language = str(inputs.get("language") or "en").lower()
    goals = _as_str_list(inputs.get("goals"))

    target_type = target_types[(day_idx + week_idx) % len(target_types)] if target_types else "strength"
    type_pool = [
        ex
        for ex in exercises
        if target_type in {_as_str(x) for x in (ex.workout_type or [])}
    ]
    pool = type_pool if len(type_pool) >= 3 else exercises

    rng = random.Random(f"{rng_seed}:{day_date.isoformat()}:{target_type}")
    pool_shuffled = list(pool)
    rng.shuffle(pool_shuffled)

    exercise_count = max(4, min(8, duration_min // 6))
    selected = pool_shuffled[:exercise_count] if len(pool_shuffled) >= exercise_count else pool_shuffled
    if not selected:
        return {
            "title": "AI session",
            "duration_min": duration_min,
            "intensity": intensity,
            "focus": target_type,
            "progression_note": _progression_note(week_idx, intensity),
            "exercises": [],
            "safety": ["Stop if sharp pain appears.", "Keep technique strict before increasing load."],
        }

    base_sets = {"beginner": 2, "intermediate": 3, "advanced": 4}.get(level, 2)
    sets = min(5, base_sets + (1 if week_idx >= 2 else 0))
    rest_seconds = 75 if intensity == "low" else 45 if intensity == "high" else 60

    exercise_items: list[dict[str, Any]] = []
    for ex in selected:
        mode = _as_str(ex.mode)
        default_reps = getattr(ex.defaults, "reps", None) if ex.defaults else None
        default_dur = getattr(ex.defaults, "duration_seconds", None) if ex.defaults else None

        item: dict[str, Any] = {
            "exercise_id": str(ex.id),
            "exercise_code": ex.code,
            "name": _pick_i18n_name(ex.name, language),
            "mode": mode,
            "sets": sets,
            "rest_seconds": rest_seconds,
        }
        if mode == "time":
            item["duration_seconds"] = int(default_dur or 30)
        else:
            item["reps"] = int(default_reps or (10 if intensity == "low" else 12 if intensity == "moderate" else 14))
        exercise_items.append(item)

    goal_label = goals[0] if goals else "get_fitter"
    return {
        "title": f"AI {goal_label} session",
        "duration_min": duration_min,
        "intensity": intensity,
        "focus": target_type,
        "progression_note": _progression_note(week_idx, intensity),
        "exercises": exercise_items,
        "safety": [
            "Stop if sharp pain appears.",
            "Prioritize form over speed or load.",
        ],
    }


async def build_plan_days(
    current_user: Any,
    prompt_meta: Dict[str, Any],
    *,
    total_days: int = 30,
    seed_nonce: Optional[str] = None,
) -> list[Dict[str, Any]]:
    inputs = _merge_prompt_with_profile(current_user, prompt_meta or {})
    target_types = _goal_to_types(_as_str_list(inputs.get("goals")), _as_str_list(inputs.get("preferences")))

    injuries = set(_as_str_list(inputs.get("injuries")))
    equipment = set(_as_str_list(inputs.get("equipment")))
    exercises = await _load_exercises_for_planning(
        injuries=injuries,
        equipment=equipment,
        target_types=set(target_types),
    )

    start = utcnow().date()
    slots = _weekly_slots(_coerce_int(inputs.get("days_per_week"), default=4, lo=1, hi=7))
    nonce = seed_nonce or str(prompt_meta.get("_reroll_nonce") or "")

    days: list[Dict[str, Any]] = []
    workout_day_idx = 0
    for i in range(total_days):
        d = start + timedelta(days=i)
        weekday = i % 7
        if weekday in slots:
            week_idx = i // 7
            workout_template = _build_workout_template(
                day_date=d,
                week_idx=week_idx,
                day_idx=workout_day_idx,
                inputs=inputs,
                exercises=exercises,
                target_types=target_types,
                rng_seed=f"{start.isoformat()}:{nonce}",
            )
            days.append(
                {
                    "date": d.isoformat(),
                    "type": "workout",
                    "workout_template": workout_template,
                }
            )
            workout_day_idx += 1
        else:
            days.append(
                {
                    "date": d.isoformat(),
                    "type": "recovery",
                    "workout_template": {
                        "title": "Recovery day",
                        "recommendation": "Mobility + light walk 20-30 min.",
                    },
                }
            )
    return days


async def _try_generate_plan_with_yandex(
    current_user: Any,
    prompt_meta: Dict[str, Any],
    *,
    total_days: int = 30,
) -> Optional[list[Dict[str, Any]]]:
    inputs = _merge_prompt_with_profile(current_user, prompt_meta or {})
    target_types = _goal_to_types(_as_str_list(inputs.get("goals")), _as_str_list(inputs.get("preferences")))
    injuries = set(_as_str_list(inputs.get("injuries")))
    equipment = set(_as_str_list(inputs.get("equipment")))
    catalog = await _load_exercises_for_planning(
        injuries=injuries,
        equipment=equipment,
        target_types=set(target_types),
    )
    if not catalog:
        return None

    language = str(inputs.get("language") or "en")
    preview = []
    for ex in catalog[:80]:
        preview.append(
            {
                "id": str(ex.id),
                "code": ex.code,
                "name": _pick_i18n_name(ex.name, language),
                "mode": _as_str(ex.mode),
                "workout_type": [_as_str(x) for x in (ex.workout_type or [])],
                "equipment": [_as_str(x) for x in (ex.equipment or [])],
                "contraindications": [_as_str(x) for x in (ex.contraindications or [])],
            }
        )

    start = utcnow().date().isoformat()
    system_prompt = (
        "You are a fitness planner. Return strict JSON only. "
        "Build a safe 30-day plan with workout/recovery distribution, concrete exercises "
        "with sets and reps/duration, and progression notes."
    )
    user_prompt = {
        "start_date": start,
        "days": total_days,
        "user_context": inputs,
        "allowed_exercises": preview,
        "required_output_schema": {
            "days": [
                {
                    "date": "YYYY-MM-DD",
                    "type": "workout|recovery",
                    "workout_template": {
                        "title": "string",
                        "duration_min": 35,
                        "intensity": "low|moderate|high",
                        "focus": "string",
                        "progression_note": "string",
                        "exercises": [
                            {
                                "exercise_id": "must be from allowed_exercises.id",
                                "exercise_code": "string",
                                "name": "string",
                                "mode": "reps|time",
                                "sets": 3,
                                "reps": 12,
                                "duration_seconds": 30,
                                "rest_seconds": 60,
                            }
                        ],
                    },
                }
            ]
        },
        "rules": [
            "Use only allowed exercise ids.",
            "Respect injuries and equipment constraints.",
            "If mode is reps, include reps; if mode is time, include duration_seconds.",
            "Return only JSON object, without markdown.",
        ],
    }

    text = await yandex_completion(
        [
            {"role": "system", "text": system_prompt},
            {"role": "user", "text": json.dumps(user_prompt, ensure_ascii=True)},
        ],
        temperature=0.2,
        max_tokens=3000,
    )
    if not text:
        return None

    obj = _extract_json(text)
    if not obj:
        return None
    raw_days = obj.get("days")
    if not isinstance(raw_days, list) or len(raw_days) != total_days:
        return None

    normalized: list[dict[str, Any]] = []
    for i, d in enumerate(raw_days):
        if not isinstance(d, dict):
            return None
        d_type = str(d.get("type") or "recovery").lower()
        day_date = str(d.get("date") or (utcnow().date() + timedelta(days=i)).isoformat())
        if d_type not in {"workout", "recovery"}:
            d_type = "recovery"
        wt = d.get("workout_template")
        if wt is not None and not isinstance(wt, dict):
            wt = None
        normalized.append(
            {
                "date": day_date,
                "type": d_type,
                "workout_template": wt,
            }
        )
    return normalized


async def create_ai_request(
    user_id: PydanticObjectId,
    req_type: AiRequestType,
    prompt_meta: Dict[str, Any],
) -> AiRequest:
    req = AiRequest(
        user_id=user_id,
        type=req_type,
        status=AiRequestStatus.ok,
        prompt_meta=prompt_meta,
    )
    await req.insert()
    return req


async def archive_active_plans(user_id: PydanticObjectId) -> None:
    await AiPlan.find(
        AiPlan.user_id == user_id,
        AiPlan.status == "active",
    ).update({"$set": {"status": "archived"}})


async def yandex_chat_completion(
    text: str,
    meta: Dict[str, Any],
    history: Optional[list[dict[str, str]]] = None,
) -> str:
    if not YC_API_KEY_SECRET:
        return "AI assistant is configured in stub mode. Add YC_API_KEY_SECRET to enable Yandex GPT."

    model_uri = YC_GPT_MODEL_URI or (f"gpt://{YC_FOLDER_ID}/yandexgpt/latest" if YC_FOLDER_ID else "")
    if not model_uri:
        return "AI assistant is configured in stub mode. Add YC_FOLDER_ID or YC_GPT_MODEL_URI for Yandex GPT."

    sys_text = (
        "You are a fitness assistant. Give safe, concise, actionable advice. "
        "If user asks medical-risk topic, recommend consulting a professional."
    )
    if meta:
        sys_text += f" Context meta: {json.dumps(meta, ensure_ascii=True)}"

    messages: list[dict[str, str]] = [{"role": "system", "text": sys_text}]
    if history:
        messages.extend(history[-12:])
    messages.append({"role": "user", "text": text})

    res = await yandex_completion(messages, temperature=0.6, max_tokens=800)
    if res:
        return res
    return "AI service is temporarily unavailable. Please try again."


async def build_limits(user_id: PydanticObjectId) -> AiLimitsOut:
    now = utcnow()
    period = period_yyyy_mm(now)

    premium = await is_premium_user(user_id)
    active_plan = await get_active_plan(user_id)
    reroll_used = bool(active_plan and await has_child_reroll(active_plan.id))

    if premium:
        return AiLimitsOut(
            period=period,
            is_premium=True,
            base_limit=None,
            extra_from_rewarded=None,
            used=None,
            remaining=None,
            can_generate=True,
            free_reroll_used=reroll_used,
        )

    usage = await get_or_create_usage(user_id, period)
    base_limit = int(usage.base_limit or 1)
    extra = int(usage.extra_from_rewarded or 0)
    used = int(usage.used or 0)

    total = base_limit + extra
    remaining = max(0, total - used)

    return AiLimitsOut(
        period=period,
        is_premium=False,
        base_limit=base_limit,
        extra_from_rewarded=extra,
        used=used,
        remaining=remaining,
        can_generate=remaining > 0,
        free_reroll_used=reroll_used,
    )


@router.get("/ai/limits", response_model=AiLimitsOut)
async def ai_limits(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")
    return await build_limits(current_user.id)


@router.post("/ai/rewarded/grant", response_model=RewardedGrantOut)
async def ai_rewarded_grant(payload: RewardedGrantIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    if await is_premium_user(current_user.id):
        raise HTTPException(403, "Rewarded is available for free users only")

    nonce = (payload.nonce or "").strip()
    provider = (payload.provider or "").strip()

    if not nonce or len(nonce) > 128:
        raise HTTPException(400, "Invalid nonce")
    if not provider or len(provider) > 32:
        raise HTTPException(400, "Invalid provider")

    existing = await RewardedGrant.find_one(RewardedGrant.nonce == nonce)
    if existing:
        return RewardedGrantOut(granted=False, limits=await build_limits(current_user.id))

    now = utcnow()
    await RewardedGrant(
        user_id=current_user.id,
        nonce=nonce,
        provider=provider,
        granted_at=now,
    ).insert()

    period = period_yyyy_mm(now)
    usage = await get_or_create_usage(current_user.id, period)
    usage.extra_from_rewarded += 1
    await usage.save()

    return RewardedGrantOut(granted=True, limits=await build_limits(current_user.id))


@router.post("/ai/generate-plan", response_model=AiGenerateOut)
async def ai_generate_plan(payload: AiGenerateIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    premium = await is_premium_user(current_user.id)
    now = utcnow()

    if not premium:
        period = period_yyyy_mm(now)
        usage = await get_or_create_usage(current_user.id, period)
        if usage.used >= (usage.base_limit + usage.extra_from_rewarded):
            raise HTTPException(403, "AI limit reached")
        usage.used += 1
        await usage.save()

    meta = payload.prompt_meta or {}
    req = await create_ai_request(
        user_id=current_user.id,
        req_type=AiRequestType.generate_plan,
        prompt_meta=meta,
    )

    await archive_active_plans(current_user.id)

    days = await _try_generate_plan_with_yandex(current_user, meta, total_days=30)
    if not days:
        days = await build_plan_days(current_user, meta, total_days=30)

    plan = AiPlan(
        user_id=current_user.id,
        status="active",
        created_from=meta,
        days=days,
        version=1,
        reroll_of_plan_id=None,
    )
    await plan.insert()

    return AiGenerateOut(request_id=str(req.id), plan=plan_to_out(plan))


@router.post("/ai/reroll-plan", response_model=AiRerollOut)
async def ai_reroll_plan(payload: AiRerollIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    try:
        plan_id = PydanticObjectId(payload.plan_id)
    except Exception:
        raise HTTPException(400, "Invalid plan_id")

    plan = await AiPlan.get(plan_id)
    if not plan or plan.user_id != current_user.id:
        raise HTTPException(404, "Plan not found")

    if await has_child_reroll(plan.id):
        raise HTTPException(403, "Free reroll already used for this plan")

    merged_meta = dict(plan.created_from or {})
    merged_meta.update(payload.prompt_meta or {})
    merged_meta["_reroll_nonce"] = utcnow().isoformat()

    req = await create_ai_request(
        user_id=current_user.id,
        req_type=AiRequestType.reroll,
        prompt_meta=merged_meta,
    )

    await archive_active_plans(current_user.id)

    days = await _try_generate_plan_with_yandex(current_user, merged_meta, total_days=30)
    if not days:
        days = await build_plan_days(
            current_user,
            merged_meta,
            total_days=30,
            seed_nonce=merged_meta["_reroll_nonce"],
        )

    new_plan = AiPlan(
        user_id=current_user.id,
        status="active",
        created_from=merged_meta,
        days=days,
        version=int(plan.version or 1) + 1,
        reroll_of_plan_id=plan.id,
    )
    await new_plan.insert()

    return AiRerollOut(request_id=str(req.id), plan=plan_to_out(new_plan))


@router.get("/ai/plan", response_model=AiPlanOut)
async def get_current_plan(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    plan = await get_active_plan(current_user.id)
    if not plan:
        raise HTTPException(404, "No active plan")
    return plan_to_out(plan)


@router.get("/ai/recommendation/daily", response_model=AiDailyRecommendationOut)
async def ai_daily_recommendation(mark_opened: bool = True, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    now = utcnow()
    # Lazy cleanup: remove unsaved opened recommendations older than 1 day.
    stale = await AiDailyRecommendation.find(
        AiDailyRecommendation.user_id == current_user.id,
        AiDailyRecommendation.saved == False,  # noqa: E712
        AiDailyRecommendation.opened_at != None,  # noqa: E711
        AiDailyRecommendation.removed_at == None,  # noqa: E711
    ).to_list()
    for s in stale:
        if _is_unsaved_expired(s, now):
            s.removed_at = now
            await s.save()

    day_iso = _today_iso_for_user(current_user)
    rec = await AiDailyRecommendation.find_one(
        AiDailyRecommendation.user_id == current_user.id,
        AiDailyRecommendation.date == day_iso,
        AiDailyRecommendation.removed_at == None,  # noqa: E711
    )

    if not rec:
        text, meta = await _build_daily_recommendation(current_user, day_iso)
        rec = AiDailyRecommendation(
            user_id=current_user.id,
            date=day_iso,
            text=text,
            meta=meta,
            saved=False,
        )
        await rec.insert()

    if mark_opened:
        if rec.opened_at is None:
            rec.opened_at = now
            await rec.save()

    return _daily_rec_to_out(rec)


@router.post("/ai/recommendation/daily/save", response_model=AiDailyRecommendationOut)
async def ai_daily_recommendation_save(
    payload: AiDailyRecommendationSaveIn,
    current_user=Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    rec = None
    if payload.recommendation_id:
        try:
            rid = PydanticObjectId(payload.recommendation_id)
        except Exception:
            raise HTTPException(400, "Invalid recommendation_id")
        rec = await AiDailyRecommendation.get(rid)
        if not rec or rec.user_id != current_user.id:
            raise HTTPException(404, "Recommendation not found")
    else:
        day_iso = _today_iso_for_user(current_user)
        rec = await AiDailyRecommendation.find_one(
            AiDailyRecommendation.user_id == current_user.id,
            AiDailyRecommendation.date == day_iso,
            AiDailyRecommendation.removed_at == None,  # noqa: E711
        )
        if not rec:
            raise HTTPException(404, "Recommendation not found")

    if rec.removed_at is not None:
        raise HTTPException(409, "Recommendation was removed")

    now = utcnow()
    target_saved = payload.saved if payload.saved is not None else (not rec.saved)
    if target_saved:
        rec.saved = True
        rec.saved_at = now
        if rec.opened_at is None:
            rec.opened_at = now
    else:
        rec.saved = False
        rec.saved_at = None
        # Start unsaved retention window from unsave action.
        rec.opened_at = now
    await rec.save()

    return _daily_rec_to_out(rec)


@router.delete("/ai/recommendation/daily")
async def ai_daily_recommendation_delete(
    recommendation_id: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    rec = None
    if recommendation_id:
        try:
            rid = PydanticObjectId(recommendation_id)
        except Exception:
            raise HTTPException(400, "Invalid recommendation_id")
        rec = await AiDailyRecommendation.get(rid)
        if not rec or rec.user_id != current_user.id:
            raise HTTPException(404, "Recommendation not found")
    else:
        day_iso = _today_iso_for_user(current_user)
        rec = await AiDailyRecommendation.find_one(
            AiDailyRecommendation.user_id == current_user.id,
            AiDailyRecommendation.date == day_iso,
            AiDailyRecommendation.removed_at == None,  # noqa: E711
        )
        if not rec:
            raise HTTPException(404, "Recommendation not found")

    if rec.saved:
        raise HTTPException(409, "Saved recommendation cannot be removed")

    rec.removed_at = utcnow()
    await rec.save()
    return {"status": "ok"}


@router.get("/ai/chat/history", response_model=AiChatHistoryOut)
async def ai_chat_history(
    thread_id: Optional[str] = None,
    limit: int = 50,
    current_user=Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    safe_limit = max(1, min(int(limit), 200))

    thread = None
    if thread_id:
        try:
            tid = PydanticObjectId(thread_id)
        except Exception:
            raise HTTPException(400, "Invalid thread_id")
        thread = await AiChatThread.get(tid)
        if not thread or thread.user_id != current_user.id:
            raise HTTPException(404, "Thread not found")
    else:
        thread = await AiChatThread.find(
            AiChatThread.user_id == current_user.id
        ).sort("-updated_at").first_or_none()
        if not thread:
            raise HTTPException(404, "No chat history")

    rows = await AiChatMessage.find(
        AiChatMessage.thread_id == thread.id
    ).sort("-created_at").limit(safe_limit).to_list()
    rows = list(reversed(rows))

    return AiChatHistoryOut(
        thread_id=str(thread.id),
        items=[
            AiChatMessageOut(
                id=str(m.id),
                role=str(m.role),
                text=str(m.text),
                created_at=m.created_at,
            )
            for m in rows
        ],
    )


@router.post("/ai/chat", response_model=AiChatOut)
async def ai_chat(payload: AiChatIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")
    if not await is_premium_user(current_user.id):
        raise HTTPException(403, "Premium required")

    req = await create_ai_request(
        user_id=current_user.id,
        req_type=AiRequestType.chat,
        prompt_meta=payload.meta or {},
    )

    thread = None
    if payload.thread_id:
        try:
            thread = await AiChatThread.get(PydanticObjectId(payload.thread_id))
        except Exception:
            thread = None
    if not thread or thread.user_id != current_user.id:
        thread = AiChatThread(user_id=current_user.id)
        await thread.insert()

    history_rows = await AiChatMessage.find(AiChatMessage.thread_id == thread.id).sort("-created_at").limit(12).to_list()
    history_rows = list(reversed(history_rows))
    history: list[dict[str, str]] = []
    for m in history_rows:
        role = str(m.role).lower()
        if role not in {"user", "assistant"}:
            continue
        history.append({"role": role, "text": str(m.text)})

    user_message = AiChatMessage(
        thread_id=thread.id,
        user_id=current_user.id,
        role="user",
        text=payload.text,
    )
    await user_message.insert()

    assistant_text = await yandex_chat_completion(payload.text, payload.meta or {}, history=history)

    assistant_message = AiChatMessage(
        thread_id=thread.id,
        user_id=current_user.id,
        role="assistant",
        text=assistant_text,
    )
    await assistant_message.insert()
    await thread.touch()

    return AiChatOut(
        thread_id=str(thread.id),
        user_message_id=str(user_message.id),
        assistant_message_id=str(assistant_message.id),
        assistant_text=assistant_text,
    )


def _apply_adjustments_to_meta(base: Dict[str, Any], adjustments: Dict[str, Any], note: Optional[str]) -> Dict[str, Any]:
    merged = dict(base)
    adj = adjustments or {}

    if "days_per_week" in adj:
        merged["days_per_week"] = _coerce_int(adj.get("days_per_week"), default=4, lo=1, hi=7)
    if "workouts_per_week" in adj:
        merged["days_per_week"] = _coerce_int(adj.get("workouts_per_week"), default=4, lo=1, hi=7)
    if "duration_min" in adj:
        merged["duration_min"] = _coerce_int(adj.get("duration_min"), default=35, lo=10, hi=120)
    if "session_minutes" in adj:
        merged["duration_min"] = _coerce_int(adj.get("session_minutes"), default=35, lo=10, hi=120)
    if "intensity" in adj:
        merged["intensity"] = str(adj.get("intensity") or "moderate").lower()
    if "goals" in adj:
        merged["goals"] = _as_str_list(adj.get("goals"))
    if "preferences" in adj:
        merged["preferences"] = _as_str_list(adj.get("preferences"))
    if "equipment" in adj:
        merged["equipment"] = _as_str_list(adj.get("equipment"))
    if "injuries" in adj:
        merged["injuries"] = _as_str_list(adj.get("injuries"))

    if adj:
        merged["adjustments"] = adj
    if note:
        merged["adjust_note"] = note

    merged["_reroll_nonce"] = utcnow().isoformat()
    return merged


def _find_day_index(plan: AiPlan, day_iso: str) -> int:
    for i, d in enumerate(plan.days or []):
        if str(getattr(d, "date", "")) == day_iso:
            return i
    return -1


def _day_title(day_obj: Any) -> str:
    d_type = str(getattr(day_obj, "type", "recovery"))
    wt = getattr(day_obj, "workout_template", None) or {}
    if d_type == "workout":
        return str(wt.get("title") or "Workout")
    return str(wt.get("title") or "Recovery day")


def _day_duration(day_obj: Any) -> Optional[int]:
    wt = getattr(day_obj, "workout_template", None) or {}
    v = wt.get("duration_min")
    try:
        return int(v) if v is not None else None
    except Exception:
        return None


def _day_intensity(day_obj: Any) -> Optional[str]:
    wt = getattr(day_obj, "workout_template", None) or {}
    v = wt.get("intensity")
    return str(v) if v else None


def _day_focus(day_obj: Any) -> Optional[str]:
    wt = getattr(day_obj, "workout_template", None) or {}
    v = wt.get("focus")
    return str(v) if v else None


@router.post("/ai/adjust-plan", response_model=AiAdjustOut)
async def ai_adjust_plan(payload: AiAdjustIn, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")
    if not await is_premium_user(current_user.id):
        raise HTTPException(403, "Premium required")

    try:
        plan_id = PydanticObjectId(payload.plan_id)
    except Exception:
        raise HTTPException(400, "Invalid plan_id")

    base_plan = await AiPlan.get(plan_id)
    if not base_plan or base_plan.user_id != current_user.id:
        raise HTTPException(404, "Plan not found")

    prompt_meta = dict(base_plan.created_from or {})
    prompt_meta.update(payload.prompt_meta or {})
    prompt_meta = _apply_adjustments_to_meta(prompt_meta, payload.adjustments or {}, payload.note)

    req = await create_ai_request(
        user_id=current_user.id,
        req_type=AiRequestType.adjust,
        prompt_meta=prompt_meta,
    )

    await archive_active_plans(current_user.id)

    days = await _try_generate_plan_with_yandex(current_user, prompt_meta, total_days=30)
    if not days:
        days = await build_plan_days(
            current_user,
            prompt_meta,
            total_days=30,
            seed_nonce=prompt_meta.get("_reroll_nonce"),
        )

    adjusted_plan = AiPlan(
        user_id=current_user.id,
        status="active",
        created_from=prompt_meta,
        days=days,
        version=int(base_plan.version or 1) + 1,
        reroll_of_plan_id=base_plan.id,
    )
    await adjusted_plan.insert()

    return AiAdjustOut(request_id=str(req.id), plan=plan_to_out(adjusted_plan))


@router.get("/ai/plan/weeks", response_model=AiPlanWeeksOut)
async def ai_plan_weeks(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    plan = await get_active_plan(current_user.id)
    if not plan:
        raise HTTPException(404, "No active plan")

    weeks: list[AiPlanWeekOut] = []
    days = plan.days or []
    for w_idx in range(0, len(days), 7):
        chunk = days[w_idx : w_idx + 7]
        cards: list[AiPlanDayCardOut] = []
        for d in chunk:
            day_iso = str(getattr(d, "date", ""))
            try:
                weekday = datetime.strptime(day_iso, "%Y-%m-%d").strftime("%A").upper()
            except Exception:
                weekday = ""
            cards.append(
                AiPlanDayCardOut(
                    date=day_iso,
                    weekday=weekday,
                    type=str(getattr(d, "type", "recovery")),
                    title=_day_title(d),
                    duration_min=_day_duration(d),
                    intensity=_day_intensity(d),
                    focus=_day_focus(d),
                )
            )
        weeks.append(AiPlanWeekOut(week_index=(w_idx // 7) + 1, days=cards))

    return AiPlanWeeksOut(plan_id=str(plan.id), weeks=weeks)


@router.get("/ai/plan/day", response_model=AiPlanDayDetailOut)
async def ai_plan_day(date: str, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    plan = await get_active_plan(current_user.id)
    if not plan:
        raise HTTPException(404, "No active plan")

    idx = _find_day_index(plan, date)
    if idx < 0:
        raise HTTPException(404, "Day not found")

    day_obj = plan.days[idx]
    return AiPlanDayDetailOut(
        plan_id=str(plan.id),
        date=str(getattr(day_obj, "date", date)),
        type=str(getattr(day_obj, "type", "recovery")),
        workout_template=getattr(day_obj, "workout_template", None) or {},
    )


@router.patch("/ai/plan/day", response_model=AiPlanDayDetailOut)
async def ai_plan_day_edit(
    date: str,
    payload: AiPlanDayEditIn,
    current_user=Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    plan = await get_active_plan(current_user.id)
    if not plan:
        raise HTTPException(404, "No active plan")

    idx = _find_day_index(plan, date)
    if idx < 0:
        raise HTTPException(404, "Day not found")

    days = plan.days or []
    day_obj = days[idx]
    day_type = str(getattr(day_obj, "type", "recovery"))
    wt = dict(getattr(day_obj, "workout_template", None) or {})
    patch = payload.model_dump(exclude_unset=True)

    to_rest = bool(patch.get("mark_rest_day")) or bool(patch.get("delete_session"))
    if to_rest:
        day_obj.type = "recovery"
        day_obj.workout_template = {
            "title": "Recovery day",
            "recommendation": "Mobility + light walk 20-30 min.",
        }
    else:
        if day_type != "workout":
            day_obj.type = "workout"
            wt = {
                "title": "Edited workout",
                "duration_min": 35,
                "intensity": "moderate",
                "focus": "strength",
                "exercises": [],
            }

        if "duration_min" in patch and patch["duration_min"] is not None:
            wt["duration_min"] = int(patch["duration_min"])
        if "intensity" in patch and patch["intensity"]:
            norm = _normalize_intensity(patch["intensity"])
            if not norm:
                raise HTTPException(400, "Invalid intensity")
            wt["intensity"] = norm
        if "title" in patch and patch["title"]:
            wt["title"] = patch["title"].strip()
        if "focus" in patch and patch["focus"]:
            wt["focus"] = patch["focus"].strip().lower()

        day_obj.workout_template = wt

    days[idx] = day_obj
    plan.days = days
    await plan.save()

    return AiPlanDayDetailOut(
        plan_id=str(plan.id),
        date=str(getattr(day_obj, "date", date)),
        type=str(getattr(day_obj, "type", "recovery")),
        workout_template=getattr(day_obj, "workout_template", None) or {},
    )


@router.get("/ai/plan/day/swaps", response_model=AiSwapOptionsOut)
async def ai_plan_day_swaps(date: str, limit: int = 3, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    plan = await get_active_plan(current_user.id)
    if not plan:
        raise HTTPException(404, "No active plan")

    idx = _find_day_index(plan, date)
    if idx < 0:
        raise HTTPException(404, "Day not found")

    day_obj = plan.days[idx]
    if str(getattr(day_obj, "type", "")) != "workout":
        raise HTTPException(400, "Swaps available only for workout day")

    safe_limit = max(1, min(int(limit), 6))
    meta = dict(plan.created_from or {})
    inputs = _merge_prompt_with_profile(current_user, meta)
    target_types = _goal_to_types(_as_str_list(inputs.get("goals")), _as_str_list(inputs.get("preferences")))
    injuries = set(_as_str_list(inputs.get("injuries")))
    equipment = set(_as_str_list(inputs.get("equipment")))
    exercises = await _load_exercises_for_planning(injuries=injuries, equipment=equipment, target_types=set(target_types))

    current_focus = _day_focus(day_obj) or ""
    focuses = [t for t in target_types if t != current_focus]
    if not focuses:
        focuses = target_types or ["strength", "cardio", "hiit"]

    items: list[AiSwapOptionOut] = []
    try:
        day_date = datetime.strptime(date, "%Y-%m-%d").date()
    except Exception:
        day_date = utcnow().date()

    for i, focus in enumerate(focuses[:safe_limit]):
        template = _build_workout_template(
            day_date=day_date,
            week_idx=0,
            day_idx=i,
            inputs=inputs,
            exercises=exercises,
            target_types=[focus],
            rng_seed=f"swap:{current_user.id}:{date}:{i}",
        )
        items.append(
            AiSwapOptionOut(
                swap_id=f"swap_{i}",
                title=str(template.get("title") or "Swap workout"),
                duration_min=int(template.get("duration_min") or 35),
                intensity=str(template.get("intensity") or "moderate"),
                focus=str(template.get("focus") or focus),
                workout_template=template,
            )
        )

    return AiSwapOptionsOut(plan_id=str(plan.id), date=date, items=items)


@router.post("/ai/plan/day/swap", response_model=AiPlanDayDetailOut)
async def ai_plan_day_apply_swap(
    date: str,
    payload: AiApplySwapIn,
    current_user=Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    plan = await get_active_plan(current_user.id)
    if not plan:
        raise HTTPException(404, "No active plan")

    idx = _find_day_index(plan, date)
    if idx < 0:
        raise HTTPException(404, "Day not found")

    # Regenerate options and find chosen swap by prefix index from swap_id
    swaps = await ai_plan_day_swaps(date=date, limit=6, current_user=current_user)
    chosen = None
    for s in swaps.items:
        if s.swap_id == payload.swap_id:
            chosen = s
            break
    if not chosen:
        raise HTTPException(404, "Swap option not found")

    day_obj = plan.days[idx]
    day_obj.type = "workout"
    day_obj.workout_template = dict(chosen.workout_template or {})
    plan.days[idx] = day_obj
    await plan.save()

    return AiPlanDayDetailOut(
        plan_id=str(plan.id),
        date=str(getattr(day_obj, "date", date)),
        type=str(getattr(day_obj, "type", "workout")),
        workout_template=getattr(day_obj, "workout_template", None) or {},
    )
