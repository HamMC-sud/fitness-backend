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
from api.ai.request_understanding import (
    apply_understanding_to_meta,
    detect_plan_intent,
    detect_plan_regeneration_intent,
    has_explicit_rest_day_request,
    parse_plan_request,
    validate_plan_distribution,
)
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
from models.enums import AiRequestStatus, AiRequestType, Equipment, Injury, SubscriptionStatus
from schemas.ai import (
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
    AiExerciseSwapOptionOut,
    AiExerciseSwapOptionsOut,
    AiApplyExerciseSwapIn,
    RewardedGrantIn,
    RewardedGrantOut,
)

router = APIRouter(tags=["ai"])
logger = logging.getLogger("uvicorn.error")

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


def _normalize_equipment_values(v: Any) -> list[str]:
    out: list[str] = []
    for item in _as_str_list(v):
        try:
            normalized = Equipment.normalize(item).value
        except ValueError:
            continue
        if normalized not in out:
            out.append(normalized)
    return out


def _normalize_injury_values(v: Any) -> list[str]:
    out: list[str] = []
    for item in _as_str_list(v):
        raw = item.strip()
        if not raw:
            continue

        # Human-readable values used in exercise contraindications.
        key = raw.lower().replace("-", "_").replace(" ", "_")
        human_to_key = {
            "none": "none",
            "back_pain": "back_pain",
            "knee_issues": "knee_issues",
            "shoulder_issues": "shoulder_issues",
            "no_jumping": "no_jumping",
            "back pain": "back_pain",
            "knee issues": "knee_issues",
            "shoulder issues": "shoulder_issues",
            "no jumping": "no_jumping",
        }
        key = human_to_key.get(raw.lower(), human_to_key.get(key, key))

        try:
            normalized = Injury(key).value
        except ValueError:
            continue
        if normalized not in out:
            out.append(normalized)
    return out


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


async def _ensure_plan_adjustment_access(current_user: Any) -> None:
    """
    Plan day editing/swapping is a Premium-only feature.
    Free users can still use one monthly reroll via chat flow.
    """
    if await is_premium_user(current_user.id):
        return
    raise HTTPException(
        403,
        "Plan adjustment is available on Premium. Free users have one monthly reroll via AI chat.",
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
            return "Rest day. Mobility + light walk 20-30 min.", {
                "source": "active_plan",
                "plan_id": str(plan.id),
                "type": d_type,
                "date": day_iso,
            }

    return (
        "Daily recommendation: 20-30 min brisk walk, 5 min mobility, and drink enough water.",
        {"source": "fallback", "date": day_iso},
    )


def _is_unsaved_expired(rec: AiDailyRecommendation, today_iso: str) -> bool:
    """Remove opened+unsaved recommendations from any previous day."""
    if rec.saved or rec.removed_at is not None or rec.opened_at is None:
        return False
    return rec.date < today_iso


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

    equipment = _normalize_equipment_values(prompt_meta.get("equipment"))
    if not equipment and profile:
        equipment = _normalize_equipment_values(getattr(profile, "equipment", []))

    injuries = _normalize_injury_values(prompt_meta.get("injuries"))
    if not injuries and profile:
        injuries = _normalize_injury_values(getattr(profile, "injuries", []))

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
    contraindications = set(_normalize_injury_values(ex.contraindications or []))
    normalized_injuries = set(_normalize_injury_values(list(injuries)))
    if normalized_injuries and contraindications.intersection(normalized_injuries):
        return False

    required: set[str] = set()
    for item in (ex.equipment or []):
        try:
            required.add(Equipment.normalize(item).value)
        except ValueError:
            continue
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


def _normalize_goal_values(values: Any) -> list[str]:
    raw_values = _as_str_list(values)
    mapping = {
        "lose_weight": "lose_weight",
        "weight_loss": "lose_weight",
        "fat_loss": "lose_weight",
        "burn_calories": "lose_weight",
        "calorie_burn": "lose_weight",
        "build_muscle": "build_muscle",
        "muscle_gain": "build_muscle",
        "hypertrophy": "build_muscle",
        "endurance": "endurance",
        "cardio": "endurance",
        "stamina": "endurance",
        "flexibility": "flexibility",
        "mobility": "flexibility",
        "stretching": "flexibility",
        "general_fitness": "get_fitter",
        "fitness": "get_fitter",
        "get_fitter": "get_fitter",
    }

    out: list[str] = []
    for item in raw_values:
        key = item.strip().lower().replace("-", "_").replace(" ", "_")
        normalized = mapping.get(key, key)
        if normalized in {"lose_weight", "build_muscle", "endurance", "flexibility", "get_fitter"} and normalized not in out:
            out.append(normalized)
    return out


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _meta_without_duration_overrides(meta: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = dict(meta or {})
    for key in ("total_days", "rest_days", "rest_days_strict", "workouts_per_week", "days_per_week"):
        cleaned.pop(key, None)
    return cleaned


def _extract_explicit_schedule_overrides(prompt_text: str) -> Dict[str, Any]:
    text = re.sub(r"\s+", " ", str(prompt_text or "").lower()).strip().replace("ё", "е")
    if not text:
        return {}

    day_units = r"(?:day|days|den|dnya|dney|день|дня|дней)"
    rest_tokens = r"(?:rest|relax|off|recovery|отдых|выходн|otdyh|vihodn)"

    out: Dict[str, Any] = {}

    rest_match = re.search(rf"\b(\d+)\s*{day_units}\s*{rest_tokens}\b", text)
    if not rest_match:
        rest_match = re.search(rf"\b{rest_tokens}\s*(?:for|na|на)?\s*(\d+)\s*{day_units}\b", text)
    if rest_match:
        out["rest_days"] = _coerce_int(rest_match.group(1), default=0, lo=0, hi=364)
        out["rest_days_strict"] = True

    total_patterns = [
        rf"\b(?:in|for|over|within|na|на)\s*(\d+)\s*{day_units}\b",
        rf"\b(\d+)\s*{day_units}\s*(?:plan|program|workout|training|трениров|план)\b",
        rf"\b(\d+)\s*{day_units}\b",
    ]
    for pat in total_patterns:
        m = re.search(pat, text)
        if m:
            out["total_days"] = _coerce_int(m.group(1), default=30, lo=1, hi=365)
            break

    workouts_match = re.search(
        r"\b(\d+)\s*(?:workouts?|sessions?|trainings?|тренировок|тренировки|trenirovok)\s*(?:per week|a week|в неделю|v nedelyu)?\b",
        text,
    )
    if workouts_match:
        out["workouts_per_week"] = _coerce_int(workouts_match.group(1), default=4, lo=1, hi=7)
        out["days_per_week"] = int(out["workouts_per_week"])

    if "total_days" in out and "rest_days" in out:
        active_days = max(1, int(out["total_days"]) - int(out["rest_days"]))
        out["workouts_per_week"] = max(1, min(7, active_days))
        out["days_per_week"] = int(out["workouts_per_week"])

    return out


async def _ai_understand_plan_prompt(prompt_text: str, base_meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    text = str(prompt_text or "").strip()
    if not text:
        return None

    profile_hint = {
        "goals": _as_str_list(base_meta.get("goals")),
        "equipment": _as_str_list(base_meta.get("equipment")),
        "injuries": _as_str_list(base_meta.get("injuries")),
        "days_per_week": base_meta.get("days_per_week") or base_meta.get("workouts_per_week"),
        "duration_min": base_meta.get("duration_min") or base_meta.get("session_minutes"),
        "intensity": base_meta.get("intensity"),
    }
    default_total_days = _coerce_int(base_meta.get("total_days"), default=30, lo=1, hi=365)

    system_prompt = (
        "You extract structured fitness-plan request parameters from free user text. "
        "Return strict JSON only, no markdown. "
        "Interpret colloquial Russian, transliterated Russian (Latin letters), typos, and mixed RU/EN speech. "
        "Infer user intent, not literal wording. "
        "Normalize goals to: lose_weight|build_muscle|endurance|flexibility|get_fitter. "
        "If user asks to burn calories, lose fat, slim down, or similar, map to lose_weight. "
        "If duration/workout frequency is not explicit, choose realistic defaults using profile_hint and defaults. "
        "Never return explanation text outside JSON."
    )
    user_prompt = {
        "user_text": text,
        "profile_hint": profile_hint,
        "defaults": {"total_days": default_total_days},
        "output_schema": {
            "total_days": "int 1..365",
            "workouts_per_week": "int 1..7",
            "rest_days": "int 0..364 or null",
            "rest_days_strict": "bool",
            "goals": ["lose_weight|build_muscle|endurance|flexibility|get_fitter"],
            "intensity": "low|moderate|high",
            "duration_min": "int 10..120",
            "equipment": ["string"],
            "injuries": ["string"],
            "notes": "string",
        },
        "examples": [
            {
                "input": "mne nujen trenirovok shtobi sjigat kalorii po bolshe",
                "output": {
                    "total_days": 30,
                    "workouts_per_week": 5,
                    "rest_days": 2,
                    "rest_days_strict": False,
                    "goals": ["lose_weight"],
                    "intensity": "moderate",
                    "duration_min": 40,
                },
            },
            {
                "input": "сделай план на 14 дней, 5 тренировок в неделю, хочу похудеть",
                "output": {
                    "total_days": 14,
                    "workouts_per_week": 5,
                    "rest_days": 2,
                    "rest_days_strict": True,
                    "goals": ["lose_weight"],
                    "intensity": "moderate",
                    "duration_min": 40,
                },
            },
            {
                "input": "need plan 1 month, more cardio, burn fat, beginner",
                "output": {
                    "total_days": 30,
                    "workouts_per_week": 4,
                    "rest_days": 3,
                    "rest_days_strict": False,
                    "goals": ["lose_weight", "endurance"],
                    "intensity": "low",
                    "duration_min": 30,
                },
            },
        ],
    }

    text_out = await yandex_completion(
        [
            {"role": "system", "text": system_prompt},
            {"role": "user", "text": json.dumps(user_prompt, ensure_ascii=True)},
        ],
        temperature=0.1,
        max_tokens=900,
    )
    if not text_out:
        return None

    obj = _extract_json(text_out)
    if not isinstance(obj, dict):
        return None

    total_days = _coerce_int(obj.get("total_days"), default=default_total_days, lo=1, hi=365)
    workouts_per_week = _coerce_int(
        obj.get("workouts_per_week"),
        default=_coerce_int(base_meta.get("workouts_per_week") or base_meta.get("days_per_week"), default=4, lo=1, hi=7),
        lo=1,
        hi=7,
    )

    rest_days_raw = obj.get("rest_days")
    rest_days: Optional[int]
    try:
        rest_days = int(rest_days_raw) if rest_days_raw is not None else None
    except Exception:
        rest_days = None
    if rest_days is not None:
        rest_days = max(0, min(total_days - 1, rest_days))

    intensity = _normalize_intensity(obj.get("intensity"))
    duration_min = _coerce_int(
        obj.get("duration_min"),
        default=_coerce_int(base_meta.get("duration_min") or base_meta.get("session_minutes"), default=35, lo=10, hi=120),
        lo=10,
        hi=120,
    )
    goals = _normalize_goal_values(obj.get("goals"))
    equipment = _normalize_equipment_values(obj.get("equipment"))
    injuries = _normalize_injury_values(obj.get("injuries"))

    out: Dict[str, Any] = {
        "total_days": int(total_days),
        "workouts_per_week": int(workouts_per_week),
        "days_per_week": int(workouts_per_week),
        "duration_min": int(duration_min),
    }
    if rest_days is not None:
        out["rest_days"] = int(rest_days)
        out["rest_days_strict"] = _coerce_bool(obj.get("rest_days_strict"), default=True)
        out["days_per_week"] = max(1, min(7, int(total_days - rest_days)))
    if intensity:
        out["intensity"] = intensity
    if goals:
        out["goals"] = goals
    if equipment:
        out["equipment"] = equipment
    if injuries:
        out["injuries"] = injuries
    if obj.get("notes"):
        out["notes"] = _as_str(obj.get("notes"))

    return out


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
    # Use raw cursor + per-document validation to avoid one malformed legacy
    # record breaking all AI planning requests.
    collection = Exercise.get_motor_collection()
    raw_active = await collection.find({"status": "active"}).limit(1200).to_list(length=1200)
    active: list[Exercise] = []
    for row in raw_active:
        try:
            active.append(Exercise.model_validate(row))
        except Exception as exc:
            logger.warning("Skipping invalid exercise document id=%s: %s", row.get("_id"), str(exc))
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
        media = getattr(ex, "media", None)

        item: dict[str, Any] = {
            "exercise_id": str(ex.id),
            "exercise_code": ex.code,
            "name": _pick_i18n_name(ex.name, language),
            "mode": mode,
            "sets": sets,
            "rest_seconds": rest_seconds,
            "thumbnail_url": getattr(media, "thumbnail_url", None) if media else None,
            "video_url": getattr(media, "video_url", None) if media else None,
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
                    "workout_template": None,
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
        f"Build a safe {int(total_days)}-day plan with workout/recovery distribution, concrete exercises "
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

    catalog_by_id = {str(ex.id): ex for ex in catalog}
    catalog_by_code = {str(ex.code): ex for ex in catalog if getattr(ex, "code", None)}

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

        # Enrich AI-produced workout template with media and stable ids
        # so frontend can show exercise photo and open a concrete exercise on click.
        if d_type == "workout" and isinstance(wt, dict):
            raw_exercises = wt.get("exercises")
            if isinstance(raw_exercises, list):
                enriched_exercises: list[dict[str, Any]] = []
                for it in raw_exercises:
                    if not isinstance(it, dict):
                        continue
                    row = dict(it)
                    ex_obj = None

                    ex_id = str(row.get("exercise_id") or "").strip()
                    if ex_id:
                        ex_obj = catalog_by_id.get(ex_id)
                    if ex_obj is None:
                        ex_code = str(row.get("exercise_code") or "").strip()
                        if ex_code:
                            ex_obj = catalog_by_code.get(ex_code)

                    if ex_obj is not None:
                        media = getattr(ex_obj, "media", None)
                        row["exercise_id"] = str(ex_obj.id)
                        if not row.get("exercise_code"):
                            row["exercise_code"] = getattr(ex_obj, "code", None)
                        if not row.get("name"):
                            row["name"] = _pick_i18n_name(ex_obj.name, language)
                        if not row.get("thumbnail_url"):
                            row["thumbnail_url"] = getattr(media, "thumbnail_url", None) if media else None
                        if not row.get("video_url"):
                            row["video_url"] = getattr(media, "video_url", None) if media else None

                    enriched_exercises.append(row)
                wt["exercises"] = enriched_exercises

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


async def fail_ai_request(req: AiRequest, message: str, status_code: int = 503) -> None:
    req.status = AiRequestStatus.error
    req.error = message
    await req.save()
    raise HTTPException(status_code, message)


async def archive_active_plans(user_id: PydanticObjectId) -> None:
    await AiPlan.find(
        AiPlan.user_id == user_id,
        AiPlan.status == "active",
    ).update({"$set": {"status": "archived"}})


def _month_bounds_utc(dt: datetime) -> tuple[datetime, datetime]:
    start = datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)
    if dt.month == 12:
        end = datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc)
    return start, end


async def has_monthly_reroll(user_id: PydanticObjectId) -> bool:
    now = utcnow()
    month_start, month_end = _month_bounds_utc(now)
    return await AiRequest.find_one(
        AiRequest.user_id == user_id,
        AiRequest.type == AiRequestType.reroll,
        AiRequest.status == AiRequestStatus.ok,
        AiRequest.created_at >= month_start,
        AiRequest.created_at < month_end,
    ) is not None


def _extract_plan_duration_days(text: str) -> int:
    """Parse requested duration in days from a user message. Defaults to 30."""
    lower = re.sub(r"\s+", " ", text.lower()).strip().replace("ё", "е")

    word_nums = {
        "one": 1, "two": 2, "three": 3, "four": 4,
        "одну": 1, "одна": 1, "две": 2, "три": 3, "четыре": 4,
    }

    week_units = r"(?:week|weeks|nedelyu|nedeli|nedel|неделю|недели|недель|неделя)"
    month_units = r"(?:month|months|mesyac|mesyaca|mesyacev|месяц|месяца|месяцев)"
    day_units = r"(?:day|days|den|dnya|dney|день|дня|дней)"

    m = re.search(rf"(\d+)\s*{week_units}", lower)
    if m:
        return min(int(m.group(1)) * 7, 90)

    m = re.search(rf"(\d+)\s*{month_units}", lower)
    if m:
        return min(int(m.group(1)) * 30, 90)

    m = re.search(rf"(\d+)\s*{day_units}", lower)
    if m:
        return max(7, min(int(m.group(1)), 90))

    for word, num in word_nums.items():
        if re.search(rf"{word}\s*{week_units}", lower):
            return min(num * 7, 90)
        if re.search(rf"{word}\s*{month_units}", lower):
            return min(num * 30, 90)

    if re.search(rf"\b(?:{week_units}|на неделю|na nedelyu)\b", lower):
        return 7
    if re.search(rf"\b(?:{month_units}|на месяц|na mesyac)\b", lower):
        return 30

    return 30


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
    reroll_used = await has_monthly_reroll(user_id)

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


# Removed: not used by frontend
@router.post("/ai/rewarded-grant", response_model=RewardedGrantOut)
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

    usage: Optional[AiUsageMonthly] = None
    if not premium:
        period = period_yyyy_mm(now)
        usage = await get_or_create_usage(current_user.id, period)
        if usage.used >= (usage.base_limit + usage.extra_from_rewarded):
            raise HTTPException(403, "AI limit reached")

    base_meta = dict(payload.prompt_meta or {})
    if payload.total_days is not None:
        base_meta["total_days"] = int(payload.total_days)
    if payload.workouts_per_week is not None:
        base_meta["workouts_per_week"] = int(payload.workouts_per_week)

    prompt_text = str(payload.text or "").strip()
    enforce_rest_distribution = False
    if prompt_text:
        explicit_overrides = _extract_explicit_schedule_overrides(prompt_text)
        ai_meta = await _ai_understand_plan_prompt(prompt_text, base_meta)
        if ai_meta:
            meta = dict(base_meta)
            meta.update(ai_meta)
            meta.update(explicit_overrides)
            total_days = _coerce_int(meta.get("total_days"), default=30, lo=1, hi=365)
            enforce_rest_distribution = bool(meta.get("rest_days") is not None and meta.get("rest_days_strict", False))
        else:
            try:
                parse_meta = _meta_without_duration_overrides(base_meta)
                understanding = parse_plan_request(prompt_text, parse_meta)
            except ValueError as e:
                raise HTTPException(422, f"Plan request is inconsistent: {str(e)}")
            meta = apply_understanding_to_meta(base_meta, understanding)
            meta.update(explicit_overrides)
            total_days = int(understanding.total_days)
            enforce_rest_distribution = has_explicit_rest_day_request(prompt_text, parse_meta)
    else:
        meta = base_meta
        total_days = _coerce_int(meta.get("total_days"), default=30, lo=1, hi=365)

    req = await create_ai_request(
        user_id=current_user.id,
        req_type=AiRequestType.generate_plan,
        prompt_meta=meta,
    )

    days = await _try_generate_plan_with_yandex(current_user, meta, total_days=total_days)
    if not days:
        logger.warning(
            "AI generate-plan fallback to local builder: user_id=%s total_days=%s",
            str(current_user.id),
            int(total_days),
        )
        days = await build_plan_days(current_user, meta, total_days=total_days)
    if enforce_rest_distribution and days and not validate_plan_distribution(
        days,
        total_days=int(meta.get("total_days", total_days)),
        rest_days=meta.get("rest_days"),
        strict=bool(meta.get("rest_days_strict", False)),
    ):
        days = await build_plan_days(current_user, meta, total_days=total_days)
    if enforce_rest_distribution and days and not validate_plan_distribution(
        days,
        total_days=int(meta.get("total_days", total_days)),
        rest_days=meta.get("rest_days"),
        strict=bool(meta.get("rest_days_strict", False)),
    ):
        await fail_ai_request(
            req,
            "Generated plan does not match requested rest-day distribution.",
            status_code=422,
        )
    if not days:
        await fail_ai_request(
            req,
            "AI did not return a valid plan JSON. Plan was not created.",
            status_code=503,
        )

    if usage is not None:
        usage.used += 1
        await usage.save()

    await archive_active_plans(current_user.id)

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


@router.get("/ai/plan", response_model=AiPlanOut)
async def get_current_plan(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    plan = await get_active_plan(current_user.id)
    if not plan:
        raise HTTPException(404, "No active plan")
    return plan_to_out(plan)


@router.delete("/ai/plan/{plan_id}", status_code=200)
async def delete_ai_plan(plan_id: str, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    try:
        pid = PydanticObjectId(plan_id)
    except Exception:
        raise HTTPException(400, "Invalid plan_id")

    plan = await AiPlan.get(pid)
    if not plan or plan.user_id != current_user.id:
        raise HTTPException(404, "Plan not found")

    await plan.delete()
    return {"status": "ok", "plan_id": str(pid)}


@router.delete("/ai/plan/{plan_id}/day", status_code=200)
async def delete_ai_plan_day(plan_id: str, date: str, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    try:
        pid = PydanticObjectId(plan_id)
    except Exception:
        raise HTTPException(400, "Invalid plan_id")

    plan = await AiPlan.get(pid)
    if not plan or plan.user_id != current_user.id:
        raise HTTPException(404, "Plan not found")

    idx = _find_day_index(plan, date)
    if idx < 0:
        raise HTTPException(404, "Day not found")

    day_obj = plan.days[idx]
    day_obj.type = "recovery"
    day_obj.workout_template = None

    plan = await _persist_plan_day(plan=plan, idx=idx, day_obj=day_obj, user_id=current_user.id)
    day_obj = plan.days[idx]

    return {
        "status": "ok",
        "plan_id": str(plan.id),
        "date": str(getattr(day_obj, "date", date)),
        "type": str(getattr(day_obj, "type", "recovery")),
        "workout_template": None,
    }


@router.get("/ai/daily-recommendation", response_model=AiDailyRecommendationOut)
async def ai_daily_recommendation(mark_opened: bool = True, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    now = utcnow()
    day_iso = _today_iso_for_user(current_user)

    # Auto-remove opened+unsaved recommendations from previous days.
    stale = await AiDailyRecommendation.find(
        AiDailyRecommendation.user_id == current_user.id,
        AiDailyRecommendation.saved == False,  # noqa: E712
        AiDailyRecommendation.opened_at != None,  # noqa: E711
        AiDailyRecommendation.removed_at == None,  # noqa: E711
    ).to_list()
    for s in stale:
        if _is_unsaved_expired(s, day_iso):
            s.removed_at = now
            await s.save()
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


@router.post("/ai/daily-recommendation/save", response_model=AiDailyRecommendationOut)
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


@router.delete("/ai/daily-recommendation", status_code=200)
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
    return {
        "status": "ok",
        "recommendation_id": str(rec.id),
        "removed_at": rec.removed_at,
    }


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
            return AiChatHistoryOut(thread_id="", items=[])

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
    premium = await is_premium_user(current_user.id)
    message_text = str(payload.text or "")
    logger.info(
        "AI chat message: user_id=%s thread_id=%s premium=%s text=%s",
        str(current_user.id),
        str(payload.thread_id or ""),
        premium,
        message_text[:2000],
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

    action: Optional[Dict[str, Any]] = None
    regen_intent = detect_plan_regeneration_intent(payload.text)
    plan_intent = detect_plan_intent(payload.text)
    logger.info(
        "AI chat intent: user_id=%s regen_intent=%s plan_intent=%s",
        str(current_user.id),
        bool(regen_intent),
        bool(plan_intent),
    )

    if regen_intent:
        active_plan = await get_active_plan(current_user.id)
        if not active_plan:
            logger.info("AI chat regeneration skipped: user_id=%s reason=no_active_plan", str(current_user.id))
            assistant_text = "No active plan found to regenerate. Generate a plan first."
        else:
            try:
                understanding = parse_plan_request(payload.text, payload.meta or {})
                prompt_meta = apply_understanding_to_meta(dict(active_plan.created_from or {}), understanding)
            except Exception:
                prompt_meta = dict(active_plan.created_from or {})
                prompt_meta.update(payload.meta or {})

            prompt_meta = _apply_adjustments_to_meta(prompt_meta, payload.meta or {}, payload.text)
            total_days = len(active_plan.days or []) or 30

            if premium:
                regen_req = await create_ai_request(
                    user_id=current_user.id,
                    req_type=AiRequestType.adjust,
                    prompt_meta=prompt_meta,
                )
                try:
                    days = await _try_generate_plan_with_yandex(current_user, prompt_meta, total_days=total_days)
                    if not days:
                        await fail_ai_request(
                            regen_req,
                            "AI did not return a valid regenerated plan JSON. Regeneration was not created.",
                            status_code=503,
                        )

                    await archive_active_plans(current_user.id)
                    regenerated_plan = AiPlan(
                        user_id=current_user.id,
                        status="active",
                        created_from=prompt_meta,
                        days=days,
                        version=int(active_plan.version or 1) + 1,
                        reroll_of_plan_id=active_plan.id,
                    )
                    await regenerated_plan.insert()

                    assistant_text = "I regenerated your current plan with updated parameters."
                    action = {
                        "type": "plan_regenerated",
                        "plan_id": str(regenerated_plan.id),
                        "base_plan_id": str(active_plan.id),
                        "total_days": total_days,
                    }
                except HTTPException:
                    raise
                except Exception as e:
                    logger.exception("Plan regeneration from chat failed: %s", e)
                    assistant_text = "Something went wrong regenerating your plan. Please try again."
            else:
                if await has_monthly_reroll(current_user.id):
                    assistant_text = (
                        "Your free reroll for this month is already used. "
                        "Upgrade to Premium for unlimited plan adjustments."
                    )
                else:
                    reroll_req = await create_ai_request(
                        user_id=current_user.id,
                        req_type=AiRequestType.reroll,
                        prompt_meta=prompt_meta,
                    )
                    try:
                        days = await _try_generate_plan_with_yandex(current_user, prompt_meta, total_days=total_days)
                        if not days:
                            await fail_ai_request(
                                reroll_req,
                                "AI did not return a valid reroll plan JSON. Reroll was not created.",
                                status_code=503,
                            )

                        await archive_active_plans(current_user.id)
                        rerolled_plan = AiPlan(
                            user_id=current_user.id,
                            status="active",
                            created_from=prompt_meta,
                            days=days,
                            version=int(active_plan.version or 1) + 1,
                            reroll_of_plan_id=active_plan.id,
                        )
                        await rerolled_plan.insert()

                        assistant_text = "Your plan was rerolled with updated parameters."
                        action = {
                            "type": "plan_rerolled",
                            "plan_id": str(rerolled_plan.id),
                            "base_plan_id": str(active_plan.id),
                            "total_days": total_days,
                        }
                    except HTTPException:
                        raise
                    except Exception as e:
                        logger.exception("Plan reroll from chat failed: %s", e)
                        assistant_text = "Something went wrong rerolling your plan. Please try again."
    elif detect_plan_intent(payload.text):
        # Check generation limits
        usage: Optional[AiUsageMonthly] = None
        can_generate = True

        if not premium:
            period = period_yyyy_mm(utcnow())
            usage = await get_or_create_usage(current_user.id, period)
            can_generate = usage.used < (usage.base_limit + usage.extra_from_rewarded)

        if not can_generate:
            logger.info(
                "AI chat generation blocked: user_id=%s premium=%s used=%s limit=%s",
                str(current_user.id),
                bool(premium),
                int(getattr(usage, "used", 0) or 0) if usage is not None else None,
                (
                    int(getattr(usage, "base_limit", 0) or 0)
                    + int(getattr(usage, "extra_from_rewarded", 0) or 0)
                )
                if usage is not None
                else None,
            )
            assistant_text = (
                "You've used all your plan generation credits for this month. "
                "Watch a rewarded ad to get more, or upgrade to Premium."
            )
        else:
            try:
                base_meta = dict(payload.meta or {})
                explicit_overrides = _extract_explicit_schedule_overrides(payload.text)
                ai_meta = await _ai_understand_plan_prompt(payload.text, base_meta)
                if ai_meta:
                    meta = dict(base_meta)
                    meta.update(ai_meta)
                    meta.update(explicit_overrides)
                    total_days = _coerce_int(meta.get("total_days"), default=30, lo=1, hi=365)
                    enforce_rest_distribution = bool(meta.get("rest_days") is not None and meta.get("rest_days_strict", False))
                else:
                    parse_meta = _meta_without_duration_overrides(base_meta)
                    understanding = parse_plan_request(payload.text, parse_meta)
                    total_days = int(understanding.total_days)
                    meta = apply_understanding_to_meta(base_meta, understanding)
                    meta.update(explicit_overrides)
                    enforce_rest_distribution = has_explicit_rest_day_request(payload.text, parse_meta)
                logger.info(
                    "AI chat generation started: user_id=%s total_days=%s enforce_rest_distribution=%s",
                    str(current_user.id),
                    int(total_days),
                    bool(enforce_rest_distribution),
                )
                gen_req = await create_ai_request(
                    user_id=current_user.id,
                    req_type=AiRequestType.generate_plan,
                    prompt_meta=meta,
                )
                days = await _try_generate_plan_with_yandex(current_user, meta, total_days=total_days)
                if not days:
                    logger.warning(
                        "AI chat generation fallback to local builder: user_id=%s total_days=%s",
                        str(current_user.id),
                        int(total_days),
                    )
                    days = await build_plan_days(current_user, meta, total_days=total_days)
                if enforce_rest_distribution and days and not validate_plan_distribution(
                    days,
                    total_days=int(meta.get("total_days", total_days)),
                    rest_days=meta.get("rest_days"),
                    strict=bool(meta.get("rest_days_strict", False)),
                ):
                    # Keep generation dependent on structured data and force distribution consistency.
                    days = await build_plan_days(current_user, meta, total_days=total_days)
                if enforce_rest_distribution and days and not validate_plan_distribution(
                    days,
                    total_days=int(meta.get("total_days", total_days)),
                    rest_days=meta.get("rest_days"),
                    strict=bool(meta.get("rest_days_strict", False)),
                ):
                    await fail_ai_request(
                        gen_req,
                        "Generated plan does not match requested rest-day distribution.",
                        status_code=422,
                    )
                if days:
                    if usage is not None:
                        usage.used += 1
                        await usage.save()
                    await archive_active_plans(current_user.id)
                    plan = AiPlan(
                        user_id=current_user.id,
                        status="active",
                        created_from=meta,
                        days=days,
                        version=1,
                        reroll_of_plan_id=None,
                    )
                    await plan.insert()
                    weeks = total_days // 7
                    duration_label = f"{weeks} week{'s' if weeks != 1 else ''}" if total_days % 7 == 0 else f"{total_days} days"
                    assistant_text = f"Your {duration_label} training plan is ready! Opening it now."
                    action = {"type": "plan_generated", "plan_id": str(plan.id), "total_days": total_days}
                    logger.info(
                        "AI chat generation success: user_id=%s plan_id=%s total_days=%s",
                        str(current_user.id),
                        str(plan.id),
                        int(total_days),
                    )
                else:
                    logger.info("AI chat generation empty result: user_id=%s", str(current_user.id))
                    assistant_text = "I couldn't generate a plan right now. Please try again in a moment."
            except ValueError as e:
                logger.info("AI chat generation validation failed: user_id=%s error=%s", str(current_user.id), str(e))
                assistant_text = f"Plan request is inconsistent: {str(e)}"
            except Exception as e:
                logger.exception("Plan generation from chat failed: %s", e)
                assistant_text = "Something went wrong generating your plan. Please try again."
    else:
        logger.info("AI chat no-plan intent branch: user_id=%s premium=%s", str(current_user.id), bool(premium))
        if not premium:
            assistant_text = "AI coach chat with questions is available on Premium."
        else:
            await create_ai_request(
                user_id=current_user.id,
                req_type=AiRequestType.chat,
                prompt_meta=payload.meta or {},
            )
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
        action=action,
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


def _day_to_dict(day_obj: Any) -> Dict[str, Any]:
    if hasattr(day_obj, "model_dump"):
        return day_obj.model_dump()
    if isinstance(day_obj, dict):
        return dict(day_obj)
    return {
        "date": str(getattr(day_obj, "date", "")),
        "type": str(getattr(day_obj, "type", "recovery")),
        "workout_template": getattr(day_obj, "workout_template", None),
    }


async def _persist_plan_day(plan: AiPlan, idx: int, day_obj: Any, user_id: PydanticObjectId) -> AiPlan:
    await AiPlan.find_one(
        AiPlan.id == plan.id,
        AiPlan.user_id == user_id,
    ).update(
        {
            "$set": {
                f"days.{idx}": _day_to_dict(day_obj),
                "updated_at": utcnow(),
            }
        }
    )
    refreshed = await AiPlan.get(plan.id)
    if not refreshed or refreshed.user_id != user_id:
        raise HTTPException(404, "Plan not found")
    return refreshed


async def _build_template_for_existing_plan_day(
    *,
    current_user: Any,
    plan: AiPlan,
    day_iso: str,
    day_index: int,
    focus_override: Optional[str] = None,
) -> Dict[str, Any]:
    meta = dict(getattr(plan, "created_from", None) or {})
    inputs = _merge_prompt_with_profile(current_user, meta)
    target_types = _goal_to_types(_as_str_list(inputs.get("goals")), _as_str_list(inputs.get("preferences")))

    if focus_override:
        focus = str(focus_override).strip().lower()
        if focus:
            target_types = [focus] + [t for t in target_types if t != focus]

    injuries = set(_as_str_list(inputs.get("injuries")))
    equipment = set(_as_str_list(inputs.get("equipment")))
    exercises = await _load_exercises_for_planning(
        injuries=injuries,
        equipment=equipment,
        target_types=set(target_types),
    )

    try:
        day_date = datetime.strptime(day_iso, "%Y-%m-%d").date()
    except Exception:
        day_date = utcnow().date()

    workout_day_idx = 0
    for i, d in enumerate(plan.days or []):
        if i >= day_index:
            break
        if str(getattr(d, "type", "recovery")) == "workout":
            workout_day_idx += 1

    return _build_workout_template(
        day_date=day_date,
        week_idx=max(0, int(day_index) // 7),
        day_idx=workout_day_idx,
        inputs=inputs,
        exercises=exercises,
        target_types=target_types or ["strength", "cardio", "hiit"],
        rng_seed=f"edit:{plan.id}:{day_iso}",
    )


def _find_exercise_index_in_template(workout_template: Dict[str, Any], exercise_id: str) -> int:
    exercises = list(workout_template.get("exercises") or [])
    target = str(exercise_id or "").strip()
    for idx, item in enumerate(exercises):
        if str((item or {}).get("exercise_id") or "").strip() == target:
            return idx
    return -1


def _build_replacement_exercise_item(
    *,
    old_item: Dict[str, Any],
    ex_obj: Exercise,
    language: str,
) -> Dict[str, Any]:
    mode = _as_str(getattr(ex_obj, "mode", "")).strip().lower() or "reps"
    media = getattr(ex_obj, "media", None)
    defaults = getattr(ex_obj, "defaults", None)
    default_reps = int(getattr(defaults, "reps", 0) or 0) if defaults else 0
    default_duration = int(getattr(defaults, "duration_seconds", 0) or 0) if defaults else 0

    sets_value = _coerce_int(old_item.get("sets"), default=3, lo=1, hi=10)
    rest_seconds = _coerce_int(old_item.get("rest_seconds"), default=60, lo=15, hi=300)

    item: Dict[str, Any] = {
        "exercise_id": str(ex_obj.id),
        "exercise_code": getattr(ex_obj, "code", None),
        "name": _pick_i18n_name(getattr(ex_obj, "name", None), language),
        "mode": mode,
        "sets": sets_value,
        "rest_seconds": rest_seconds,
        "thumbnail_url": getattr(media, "thumbnail_url", None) if media else None,
        "video_url": getattr(media, "video_url", None) if media else None,
    }

    if mode == "time":
        old_duration = old_item.get("duration_seconds")
        try:
            old_duration = int(old_duration) if old_duration is not None else None
        except Exception:
            old_duration = None
        item["duration_seconds"] = int(old_duration or default_duration or 30)
    else:
        old_reps = old_item.get("reps")
        try:
            old_reps = int(old_reps) if old_reps is not None else None
        except Exception:
            old_reps = None
        item["reps"] = int(old_reps or default_reps or 12)

    return item


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
    wt = getattr(day_obj, "workout_template", None) or None
    return AiPlanDayDetailOut(
        plan_id=str(plan.id),
        date=str(getattr(day_obj, "date", date)),
        type=str(getattr(day_obj, "type", "recovery")),
        workout_template=wt if str(getattr(day_obj, "type", "recovery")) == "workout" else None,
    )


@router.patch("/ai/plan/day", response_model=AiPlanDayDetailOut)
async def ai_plan_day_edit(
    date: str,
    payload: AiPlanDayEditIn,
    current_user=Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(401, "Unauthorized")
    await _ensure_plan_adjustment_access(current_user)

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
        day_obj.workout_template = None
    else:
        requested_focus = None
        if "focus" in patch and patch["focus"]:
            requested_focus = patch["focus"].strip().lower()

        if day_type != "workout":
            day_obj.type = "workout"
            wt = await _build_template_for_existing_plan_day(
                current_user=current_user,
                plan=plan,
                day_iso=date,
                day_index=idx,
                focus_override=requested_focus,
            )

        if "duration_min" in patch and patch["duration_min"] is not None:
            wt["duration_min"] = int(patch["duration_min"])
        if "intensity" in patch and patch["intensity"]:
            norm = _normalize_intensity(patch["intensity"])
            if not norm:
                raise HTTPException(400, "Invalid intensity")
            wt["intensity"] = norm
        if "title" in patch and patch["title"]:
            wt["title"] = patch["title"].strip()
        if requested_focus:
            wt["focus"] = requested_focus

        day_obj.workout_template = wt

    plan = await _persist_plan_day(plan=plan, idx=idx, day_obj=day_obj, user_id=current_user.id)
    day_obj = plan.days[idx]

    final_type = str(getattr(day_obj, "type", "recovery"))
    return AiPlanDayDetailOut(
        plan_id=str(plan.id),
        date=str(getattr(day_obj, "date", date)),
        type=final_type,
        workout_template=getattr(day_obj, "workout_template", None) if final_type == "workout" else None,
    )


@router.get("/ai/plan/day/swaps", response_model=AiSwapOptionsOut)
async def ai_plan_day_swaps(date: str, limit: int = 3, current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")
    await _ensure_plan_adjustment_access(current_user)

    plan = await get_active_plan(current_user.id)
    if not plan:
        raise HTTPException(404, "No active plan")

    idx = _find_day_index(plan, date)
    if idx < 0:
        raise HTTPException(404, "Day not found")

    day_obj = plan.days[idx]
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


@router.get("/ai/plan/day/exercise-swaps", response_model=AiExerciseSwapOptionsOut)
async def ai_plan_day_exercise_swaps(
    date: str,
    exercise_id: str,
    limit: int = 5,
    current_user=Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(401, "Unauthorized")
    await _ensure_plan_adjustment_access(current_user)

    plan = await get_active_plan(current_user.id)
    if not plan:
        raise HTTPException(404, "No active plan")

    idx = _find_day_index(plan, date)
    if idx < 0:
        raise HTTPException(404, "Day not found")

    day_obj = plan.days[idx]
    if str(getattr(day_obj, "type", "recovery")) != "workout":
        raise HTTPException(400, "Day is not a workout")

    wt = dict(getattr(day_obj, "workout_template", None) or {})
    ex_idx = _find_exercise_index_in_template(wt, exercise_id)
    if ex_idx < 0:
        raise HTTPException(404, "Exercise not found in this day")

    existing_exercises = list(wt.get("exercises") or [])
    old_item = dict(existing_exercises[ex_idx] or {})
    old_exercise_id = str(old_item.get("exercise_id") or "").strip()
    day_focus = str(wt.get("focus") or "").strip().lower()

    meta = dict(plan.created_from or {})
    inputs = _merge_prompt_with_profile(current_user, meta)
    target_types = _goal_to_types(_as_str_list(inputs.get("goals")), _as_str_list(inputs.get("preferences")))
    if day_focus:
        target_types = [day_focus] + [t for t in target_types if t != day_focus]

    injuries = set(_as_str_list(inputs.get("injuries")))
    equipment = set(_as_str_list(inputs.get("equipment")))
    catalog = await _load_exercises_for_planning(
        injuries=injuries,
        equipment=equipment,
        target_types=set(target_types),
    )

    safe_limit = max(1, min(int(limit), 10))
    language = _as_str(inputs.get("language") or getattr(current_user, "language", "en")) or "en"

    def _collect_options(source: list[Exercise], reason: str, max_items: int, used_ids: set[str]) -> list[AiExerciseSwapOptionOut]:
        rng = random.Random(f"exswap:{current_user.id}:{date}:{exercise_id}:{reason}")
        shuffled = list(source)
        rng.shuffle(shuffled)
        out_items: list[AiExerciseSwapOptionOut] = []
        for ex in shuffled:
            ex_id = str(ex.id)
            if ex_id == old_exercise_id or ex_id in used_ids:
                continue
            replacement = _build_replacement_exercise_item(old_item=old_item, ex_obj=ex, language=language)
            out_items.append(
                AiExerciseSwapOptionOut(
                    swap_id=f"exswap_{len(used_ids) + len(out_items)}",
                    exercise=replacement,
                    reason=reason,
                )
            )
            if len(out_items) >= max_items:
                break
        return out_items

    items: list[AiExerciseSwapOptionOut] = []
    used_ids: set[str] = set()
    focused = _collect_options(
        source=catalog,
        reason="Similar day focus and available for your profile constraints.",
        max_items=safe_limit,
        used_ids=used_ids,
    )
    items.extend(focused)
    used_ids.update(str((i.exercise or {}).get("exercise_id") or "") for i in focused)

    # Fallback: widen search if focused pool is too narrow.
    if len(items) < safe_limit:
        broad_catalog = await _load_exercises_for_planning(
            injuries=injuries,
            equipment=equipment,
            target_types=set(),
        )
        extra = _collect_options(
            source=broad_catalog,
            reason="Closest available alternative from your allowed exercise pool.",
            max_items=safe_limit - len(items),
            used_ids=used_ids,
        )
        items.extend(extra)

    if not items:
        raise HTTPException(404, "No replacement exercise found")

    return AiExerciseSwapOptionsOut(
        plan_id=str(plan.id),
        date=date,
        exercise_id=str(exercise_id),
        items=items,
    )


@router.post("/ai/plan/day/exercise-swap", response_model=AiPlanDayDetailOut)
async def ai_plan_day_apply_exercise_swap(
    date: str,
    payload: AiApplyExerciseSwapIn,
    current_user=Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(401, "Unauthorized")
    await _ensure_plan_adjustment_access(current_user)

    if not payload.swap_id and not payload.new_exercise_id:
        raise HTTPException(400, "Either swap_id or new_exercise_id is required")

    plan = await get_active_plan(current_user.id)
    if not plan:
        raise HTTPException(404, "No active plan")

    idx = _find_day_index(plan, date)
    if idx < 0:
        raise HTTPException(404, "Day not found")

    day_obj = plan.days[idx]
    if str(getattr(day_obj, "type", "recovery")) != "workout":
        raise HTTPException(400, "Day is not a workout")

    wt = dict(getattr(day_obj, "workout_template", None) or {})
    exercises = list(wt.get("exercises") or [])
    ex_idx = _find_exercise_index_in_template(wt, payload.exercise_id)
    if ex_idx < 0:
        raise HTTPException(404, "Exercise not found in this day")

    replacement_item: Optional[Dict[str, Any]] = None
    if payload.swap_id:
        swaps = await ai_plan_day_exercise_swaps(
            date=date,
            exercise_id=payload.exercise_id,
            limit=10,
            current_user=current_user,
        )
        chosen = next((s for s in swaps.items if s.swap_id == payload.swap_id), None)
        if not chosen:
            raise HTTPException(404, "Swap option not found")
        replacement_item = dict(chosen.exercise or {})
    else:
        try:
            new_id = PydanticObjectId(payload.new_exercise_id)
        except Exception:
            raise HTTPException(400, "Invalid new_exercise_id")
        ex_obj = await Exercise.get(new_id)
        if not ex_obj or str(getattr(ex_obj, "status", "")) != "active":
            raise HTTPException(404, "Replacement exercise not found")

        meta = dict(plan.created_from or {})
        inputs = _merge_prompt_with_profile(current_user, meta)
        language = _as_str(inputs.get("language") or getattr(current_user, "language", "en")) or "en"
        replacement_item = _build_replacement_exercise_item(
            old_item=dict(exercises[ex_idx] or {}),
            ex_obj=ex_obj,
            language=language,
        )

    exercises[ex_idx] = replacement_item
    wt["exercises"] = exercises
    day_obj.workout_template = wt

    plan = await _persist_plan_day(plan=plan, idx=idx, day_obj=day_obj, user_id=current_user.id)
    day_obj = plan.days[idx]
    return AiPlanDayDetailOut(
        plan_id=str(plan.id),
        date=str(getattr(day_obj, "date", date)),
        type=str(getattr(day_obj, "type", "workout")),
        workout_template=getattr(day_obj, "workout_template", None),
    )


@router.post("/ai/plan/day/swap", response_model=AiPlanDayDetailOut)
@router.post("/ai/plan/day/workout-swap", response_model=AiPlanDayDetailOut)
async def ai_plan_day_apply_swap(
    date: str,
    payload: AiApplySwapIn,
    current_user=Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(401, "Unauthorized")
    await _ensure_plan_adjustment_access(current_user)

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
    plan = await _persist_plan_day(plan=plan, idx=idx, day_obj=day_obj, user_id=current_user.id)
    day_obj = plan.days[idx]

    return AiPlanDayDetailOut(
        plan_id=str(plan.id),
        date=str(getattr(day_obj, "date", date)),
        type="workout",
        workout_template=getattr(day_obj, "workout_template", None),
    )
