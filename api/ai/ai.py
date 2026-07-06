from __future__ import annotations

import json
import logging
import os
import random
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from api.auth.config import get_current_user
from api.ai.ai_chat_decision import get_ai_chat_decision, sanitize_decision_meta
from api.ai.ai_request_validator import get_plan_generation_access
from api.ai.yandex_client import yandex_chat_completion, yandex_completion
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
from utils.exercise_video_parser import ensure_existing_media_url, parse_exercise_video_from_url, resolve_local_media_path
from utils.workout_contract import apply_uniform_rest_seconds, summarize_sets_payload
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


def _short_text_preview(text: str, limit: int = 160) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())[:limit]


def _contains_any(text: str, markers: list[str]) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in markers)


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


def _is_russian_language(language: str) -> bool:
    return str(language or "").strip().lower().startswith("ru")


def _localized_goal_label(goal: str, language: str) -> str:
    if not _is_russian_language(language):
        return goal
    return {
        "lose_weight": "снижение веса",
        "build_muscle": "набор мышц",
        "get_fitter": "общая форма",
        "endurance": "выносливость",
        "flexibility": "гибкость",
        "strength": "сила",
        "cardio": "кардио",
        "hiit": "HIIT",
        "stretching": "растяжка",
        "yoga": "йога",
    }.get(str(goal or "").strip().lower(), "общая форма")


def _localized_type_label(value: str, language: str) -> str:
    token = str(value or "").strip().lower()
    if _is_russian_language(language):
        return {
            "workout": "Тренировка",
            "recovery": "Восстановление",
            "rest": "Отдых",
            "strength": "Силовая тренировка",
            "cardio": "Кардио",
            "hiit": "HIIT",
            "mobility": "Мобильность",
            "flexibility": "Растяжка",
            "stretching": "Растяжка",
            "endurance": "Выносливость",
            "yoga": "Йога",
        }.get(token, token or "Тренировка")
    return {
        "workout": "Workout",
        "recovery": "Recovery",
        "rest": "Rest",
        "strength": "Strength",
        "cardio": "Cardio",
        "hiit": "HIIT",
        "mobility": "Mobility",
        "flexibility": "Flexibility",
        "stretching": "Stretching",
        "endurance": "Endurance",
        "yoga": "Yoga",
    }.get(token, token or "Workout")


def _localized_recovery_day_title(language: str) -> str:
    return "День восстановления" if _is_russian_language(language) else "Recovery day"


def _localized_weekday(day_iso: str, language: str) -> str:
    try:
        weekday_idx = datetime.strptime(day_iso, "%Y-%m-%d").weekday()
    except Exception:
        return ""
    if _is_russian_language(language):
        return ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"][weekday_idx]
    return ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][weekday_idx]


def _localized_progression_note(week_idx: int, intensity: str, language: str) -> str:
    if not _is_russian_language(language):
        return _progression_note(week_idx, intensity)
    level = (intensity or "moderate").lower()
    if week_idx == 0:
        return "Неделя 1: сосредоточьтесь на технике и регулярности."
    if week_idx == 1:
        return "Неделя 2: добавьте 1 подход к ключевым упражнениям, если восстановление хорошее."
    if week_idx == 2:
        return "Неделя 3: увеличьте повторения или время на 10-15%, сохраняя технику."
    if level == "high":
        return "Неделя 4+: повышайте нагрузку постепенно и оставляйте 1-2 повтора в запасе."
    return "Неделя 4+: продолжайте прогрессию нагрузки и сохраняйте хотя бы один лёгкий день."


def _localized_swap_title(language: str) -> str:
    return "Замена тренировки" if _is_russian_language(language) else "Swap workout"


def _localized_swap_reason(kind: str, language: str) -> str:
    if not _is_russian_language(language):
        if kind == "focused":
            return "Similar day focus and available for your profile constraints."
        if kind == "fallback":
            return "Closest available alternative from your allowed exercise pool."
        return kind
    if kind == "focused":
        return "Похоже по фокусу дня и подходит под ограничения вашего профиля."
    if kind == "fallback":
        return "Ближайшая доступная альтернатива из разрешённого вам пула упражнений."
    return kind


def _localized_ai_chat_text(key: str, language: str) -> str:
    if not _is_russian_language(language):
        mapping = {
            "no_active_plan_to_regenerate": "No active plan found to regenerate. Generate a plan first.",
            "premium_chat_only": "AI coach chat with questions is available on Premium.",
        }
        return mapping.get(key, key)
    mapping = {
        "no_active_plan_to_regenerate": "Активный план для пересборки не найден. Сначала сгенерируйте план.",
        "premium_chat_only": "Чат с AI-тренером и вопросами доступен на Premium.",
    }
    return mapping.get(key, key)


def _localized_safety_messages(language: str) -> list[str]:
    if _is_russian_language(language):
        return [
            "Остановитесь, если появляется резкая боль.",
            "Ставьте технику выше скорости и веса.",
        ]
    return [
        "Stop if sharp pain appears.",
        "Prioritize form over speed or load.",
    ]


def _localized_workout_title(goal_label: str, language: str) -> str:
    if _is_russian_language(language):
        return f"Тренировка: {goal_label}"
    return f"{goal_label} workout"


def _pick_i18n_value(i18n_obj: Any, language: str) -> str:
    data = i18n_obj if isinstance(i18n_obj, dict) else {}
    lang = "ru" if _is_russian_language(language) else "en"
    value = data.get(lang) or data.get("en") or data.get("ru")
    return str(value or "").strip()


def _display_goal_label(goal: str, language: str) -> str:
    token = str(goal or "").strip().lower()
    if _is_russian_language(language):
        return _localized_goal_label(token, language)
    return {
        "lose_weight": "Weight loss",
        "build_muscle": "Muscle gain",
        "get_fitter": "General fitness",
        "endurance": "Endurance",
        "flexibility": "Flexibility",
        "strength": "Strength",
        "cardio": "Cardio",
        "hiit": "HIIT",
        "stretching": "Stretching",
        "yoga": "Yoga",
    }.get(token, "General fitness")


def _humanize_ai_label(value: Any, *, language: str, fallback: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback

    text = raw.replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(?:ai\s+)+", "", text, flags=re.IGNORECASE).strip(" :_-")
    lowered = text.lower()
    if lowered in {"session", "workout", "workout session", "training"}:
        return fallback

    if not _has_cyrillic(text):
        preserve = {"HIIT", "EMOM", "AMRAP", "TABATA"}
        text = " ".join(
            part.upper() if part.upper() in preserve else part.capitalize()
            for part in text.split()
        ).strip()

    return text or fallback


def _localized_workout_title_i18n(focus_value: str) -> Dict[str, str]:
    focus = str(focus_value or "get_fitter").strip().lower() or "get_fitter"
    return {
        "en": _localized_workout_title(_display_goal_label(focus, "en"), "en"),
        "ru": _localized_workout_title(_display_goal_label(focus, "ru"), "ru"),
    }


def _has_cyrillic(text: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", str(text or "")))


def _has_latin(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", str(text or "")))


def _should_replace_with_localized_title(existing_title: str, language: str) -> bool:
    title = str(existing_title or "").strip()
    if not title:
        return True
    lowered = title.lower()
    if lowered.startswith("ai ") or lowered.startswith("тренировка:"):
        return True
    if lowered in {"recovery day", "день восстановления", "workout", "тренировка", "workout session"}:
        return True
    if _is_russian_language(language):
        return _has_latin(title) and not _has_cyrillic(title)
    return _has_cyrillic(title)


def _localized_display_workout_title(workout_template: Any, language: str) -> str:
    wt = dict(workout_template or {})
    focus_value = str(wt.get("focus") or "get_fitter").strip().lower() or "get_fitter"
    title_i18n = wt.get("title_i18n")
    localized_from_map = _pick_i18n_value(title_i18n, language)
    if localized_from_map:
        return localized_from_map
    fallback = _localized_workout_title(
        _localized_goal_label(focus_value, language),
        language,
    )
    existing_title = str(wt.get("title") or "").strip()
    if _should_replace_with_localized_title(existing_title, language):
        return fallback
    return _humanize_ai_label(existing_title, language=language, fallback=fallback)


def _normalize_plan_day_language(day_obj: dict[str, Any], language: str) -> dict[str, Any]:
    normalized = dict(day_obj)
    day_type = str(normalized.get("type") or "").lower()
    normalized["type_label"] = _localized_type_label(day_type, language)
    if day_type != "workout":
        normalized["title"] = _localized_recovery_day_title(language)
        normalized["focus"] = None
        normalized["focus_label"] = None
        return normalized

    workout_template = dict(normalized.get("workout_template") or {})
    focus_value = str(workout_template.get("focus") or "").strip().lower()
    focus_label = _localized_type_label(focus_value, language) if focus_value else ""
    if focus_value:
        workout_template["focus_label"] = focus_label
        workout_template["category_label"] = focus_label
    workout_template["type_label"] = normalized["type_label"]
    workout_template["title_i18n"] = _localized_workout_title_i18n(focus_value or "get_fitter")
    workout_template["title"] = _localized_display_workout_title(workout_template, language)
    workout_template["progression_note"] = _localized_progression_note(
        0,
        str(workout_template.get("intensity") or "moderate"),
        language,
    )
    workout_template["safety"] = _localized_safety_messages(language)
    normalized["workout_template"] = workout_template
    normalized["title"] = str(workout_template.get("title") or _localized_display_workout_title(workout_template, language))
    normalized["focus"] = focus_value or None
    normalized["focus_label"] = focus_label if focus_value else None
    return normalized


def _media_public_prefix_from_urls(*urls: Any) -> str:
    marker = "/upload_exercises/"
    for url in urls:
        raw = str(url or "").strip()
        idx = raw.find(marker)
        if idx > 0:
            return raw[:idx].rstrip("/")
    return (
        os.getenv("MEDIA_PUBLIC_BASE_URL")
        or os.getenv("PUBLIC_BASE_URL")
        or os.getenv("API_PUBLIC_BASE_URL")
        or ""
    ).strip().rstrip("/")


def _basename_from_media_url(url: Any) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    raw = raw.split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
    return raw.rsplit("/", 1)[-1].strip()


def _resolve_existing_exercise_mp4_url(
    exercise_code: Any,
    *,
    preferred_url: Any = None,
    thumbnail_url: Any = None,
) -> Optional[str]:
    code = str(exercise_code or "").strip().strip("/")
    if not code or "/" in code or "\\" in code:
        return None

    media_dir = os.path.join(os.getcwd(), "upload_exercises", code)
    if not os.path.isdir(media_dir):
        logger.warning(
            "AI plan video enrichment skipped: exercise_code=%s media_dir=%s reason=dir_missing",
            code,
            media_dir,
        )
        return None

    candidates: list[str] = []
    preferred_name = _basename_from_media_url(preferred_url)
    if preferred_name.lower().endswith(".mp4"):
        candidates.append(preferred_name)

    try:
        mp4_names = sorted(
            name
            for name in os.listdir(media_dir)
            if name.lower().endswith(".mp4") and os.path.isfile(os.path.join(media_dir, name))
        )
    except OSError as exc:
        logger.warning(
            "AI plan video enrichment skipped: exercise_code=%s media_dir=%s reason=list_failed error=%s",
            code,
            media_dir,
            str(exc),
        )
        return None

    candidates.extend(mp4_names)

    seen: set[str] = set()
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        local_path = os.path.join(media_dir, name)
        if os.path.isfile(local_path):
            public_path = f"/upload_exercises/{code}/{name}"
            prefix = _media_public_prefix_from_urls(preferred_url, thumbnail_url)
            resolved = f"{prefix}{public_path}" if prefix else public_path
            if not preferred_name or name != preferred_name:
                logger.info(
                    "AI plan video enrichment fallback: exercise_code=%s preferred=%s resolved=%s local_path=%s",
                    code,
                    preferred_name or None,
                    resolved,
                    local_path,
                )
            return resolved

    logger.warning(
        "AI plan video enrichment skipped: exercise_code=%s media_dir=%s reason=no_mp4",
        code,
        media_dir,
    )
    return None


def _enrich_saved_plan_exercise_media(item: Any, language: str = "en") -> Dict[str, Any]:
    row = dict(item or {})
    name_i18n = row.get("name_i18n")
    if name_i18n:
        localized_name = _pick_i18n_value(name_i18n, language)
        if localized_name:
            row["name"] = localized_name
    elif isinstance(row.get("title"), dict):
        localized_name = _pick_i18n_value(row.get("title"), language)
        if localized_name:
            row["name"] = localized_name
    exercise_code = row.get("exercise_code")
    thumbnail_url = row.get("thumbnail_url")
    original_video_url = row.get("video_url")

    video_url = ensure_existing_media_url(original_video_url, kind="video") if original_video_url else None
    if not video_url:
        video_url = _resolve_existing_exercise_mp4_url(
            exercise_code,
            preferred_url=original_video_url,
            thumbnail_url=thumbnail_url,
        )

    if video_url:
        row["video_url"] = video_url
        row.update(parse_exercise_video_from_url(video_url))
    resolved_video_path = resolve_local_media_path(row.get("video_url")) if row.get("video_url") else None
    logger.info(
        "AI plan video metadata: exercise_code=%s video_url=%s local_path=%s file_exists=%s parsed_video_mode=%s parsed_repetitions=%s parsed_duration_seconds=%s reason=%s",
        str(exercise_code or ""),
        row.get("video_url"),
        str(resolved_video_path) if resolved_video_path else None,
        bool(resolved_video_path and resolved_video_path.exists()),
        row.get("video_mode"),
        row.get("repetitions"),
        row.get("duration_seconds"),
        "parsed" if row.get("video_mode") else "metadata_null",
    )

    set_plan = row.get("set_plan")
    if isinstance(set_plan, list):
        fixed_sets: list[dict[str, Any]] = []
        for set_row in set_plan:
            fixed_set = dict(set_row or {})
            reps = fixed_set.get("reps")
            if isinstance(reps, list):
                fixed_reps: list[dict[str, Any]] = []
                for rep_row in reps:
                    fixed_rep = dict(rep_row or {})
                    if video_url and not fixed_rep.get("video_url"):
                        fixed_rep["video_url"] = video_url
                        fixed_rep.update(parse_exercise_video_from_url(video_url))
                    if thumbnail_url and not fixed_rep.get("thumbnail_url"):
                        fixed_rep["thumbnail_url"] = thumbnail_url
                    fixed_reps.append(fixed_rep)
                fixed_set["reps"] = fixed_reps
            fixed_sets.append(fixed_set)
        row["set_plan"] = fixed_sets

    return row


def _normalize_workout_template_exercise_counts(
    workout_template: Dict[str, Any],
    *,
    plan_id: Optional[str] = None,
    day_iso: Optional[str] = None,
) -> Dict[str, Any]:
    wt = dict(workout_template or {})
    exercises = wt.get("exercises")
    if not isinstance(exercises, list):
        return wt

    actual_count = len(exercises)
    for field_name in ("exercise_count", "exercises_count", "total_exercises", "workout_count"):
        previous = wt.get(field_name)
        if previous is not None:
            try:
                previous_int = int(previous)
            except Exception:
                previous_int = None
            if previous_int is not None and previous_int != actual_count:
                logger.warning(
                    "AI plan exercise count mismatch: plan_id=%s day=%s returned_exercises_length=%s returned_count_field=%s count_field=%s",
                    plan_id or "",
                    day_iso or "",
                    actual_count,
                    previous_int,
                    field_name,
                )
        wt[field_name] = actual_count
    logger.info(
        "AI plan exercise count normalized: plan_id=%s day=%s returned_exercises_length=%s returned_count_field=%s",
        plan_id or "",
        day_iso or "",
        actual_count,
        actual_count,
    )
    return wt


def _normalize_ai_exercise_contract(
    row: Dict[str, Any],
    *,
    rest_seconds_override: Optional[int] = None,
) -> Dict[str, Any]:
    normalized = dict(row or {})
    mode_value = str(normalized.get("mode") or "reps")
    rest_seconds = _coerce_int(
        rest_seconds_override if rest_seconds_override is not None else normalized.get("rest_seconds"),
        default=60,
        lo=0,
        hi=600,
    )
    set_plan = normalized.get("set_plan")
    if not isinstance(set_plan, list) or not set_plan:
        sets_count = _coerce_int(normalized.get("sets"), default=1, lo=1, hi=20)
        if mode_value == "time":
            target_seconds = _coerce_int(normalized.get("duration_seconds"), default=30, lo=5, hi=3600)
            set_plan = [
                {
                    "set_no": set_no,
                    "rest_seconds_after": rest_seconds,
                    "reps": [
                        {
                            "rep_no": 1,
                            "mode": mode_value,
                            "target_reps": None,
                            "target_duration_seconds": target_seconds,
                            "video_url": normalized.get("video_url"),
                            "thumbnail_url": normalized.get("thumbnail_url"),
                        }
                    ],
                }
                for set_no in range(1, sets_count + 1)
            ]
        else:
            target_reps = _coerce_int(normalized.get("reps"), default=12, lo=1, hi=500)
            set_plan = [
                {
                    "set_no": set_no,
                    "rest_seconds_after": rest_seconds,
                    "reps": [
                        {
                            "rep_no": 1,
                            "mode": mode_value,
                            "target_reps": target_reps,
                            "target_duration_seconds": None,
                            "video_url": normalized.get("video_url"),
                            "thumbnail_url": normalized.get("thumbnail_url"),
                        }
                    ],
                }
                for set_no in range(1, sets_count + 1)
            ]

    set_plan = apply_uniform_rest_seconds(set_plan, rest_seconds)
    metrics = summarize_sets_payload(set_plan, fallback_mode=mode_value)
    normalized["set_plan"] = metrics["sets_payload"]
    normalized["steps"] = metrics["sets_payload"]
    normalized["sets"] = metrics["set_summaries"]
    normalized["sets_count"] = int(metrics["total_sets"])
    normalized["total_sets"] = int(metrics["total_sets"])
    normalized["total_reps"] = int(metrics["total_reps"])
    normalized["total_seconds"] = int(metrics["planned_total_seconds"])
    normalized["total_minutes"] = int(metrics["total_minutes"])
    normalized["rest_seconds"] = int(rest_seconds)
    normalized["rest_between_sets_seconds"] = int(metrics["rest_between_sets_seconds"])
    normalized["rest_seconds_after_exercise"] = int(metrics["rest_seconds_after_exercise"])
    normalized["duration_min"] = int(metrics["total_minutes"])
    fallback_name = "Упражнение" if _is_russian_language(str(normalized.get("language") or "")) else "Exercise"
    normalized["name"] = _humanize_ai_label(normalized.get("name"), language=str(normalized.get("language") or ""), fallback=fallback_name)
    normalized["title_text"] = normalized["name"]
    return normalized


def _log_ai_localization(
    *,
    user_id: str,
    language: str,
    plan_id: str,
    day_iso: str,
    day_title: str,
    workout_title: str,
    type_label: str,
    focus_label: str,
    raw_exercises: list[Any],
    localized_exercises: list[Any],
) -> None:
    for raw_item, localized_item in zip(raw_exercises, localized_exercises):
        raw_row = dict(raw_item or {})
        localized_row = dict(localized_item or {})
        logger.info(
            "AI plan localization: user_id=%s language=%s plan_id=%s date=%s day_title=%s workout_title=%s exercise_code=%s localized_exercise_name=%s old_saved_name=%s focus_label=%s type_label=%s",
            user_id,
            language,
            plan_id,
            day_iso,
            day_title,
            workout_title,
            str(localized_row.get("exercise_code") or raw_row.get("exercise_code") or ""),
            str(localized_row.get("name") or ""),
            str(raw_row.get("name") or ""),
            focus_label,
            type_label,
        )


def _pick_i18n_text(i18n_obj: Any, language: str, default: str = "") -> str:
    if not i18n_obj:
        return default
    lang = "ru" if _is_russian_language(language) else "en"
    value = getattr(i18n_obj, lang, None)
    if isinstance(value, list):
        return str(value[0]) if value else default
    if isinstance(value, str) and value.strip():
        return value.strip()
    for fallback in ("en", "ru"):
        value = getattr(i18n_obj, fallback, None)
        if isinstance(value, list) and value:
            return str(value[0])
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(i18n_obj, dict):
        return _pick_i18n_value(i18n_obj, language) or default
    return default


def _localized_equipment_label(value: Any, language: str) -> str:
    token = str(getattr(value, "value", value) or "").strip().lower()
    if _is_russian_language(language):
        return {
            "home": "Дом",
            "gym": "Зал",
        }.get(token, token)
    return {
        "home": "Home",
        "gym": "Gym",
    }.get(token, token)


async def _load_exercise_lookup(
    exercise_ids: set[str],
    exercise_codes: set[str],
) -> tuple[dict[str, Exercise], dict[str, Exercise]]:
    object_ids: list[PydanticObjectId] = []
    for raw_id in exercise_ids:
        try:
            object_ids.append(PydanticObjectId(raw_id))
        except Exception:
            continue

    filters: list[dict[str, Any]] = []
    if object_ids:
        filters.append({"_id": {"$in": object_ids}})
    if exercise_codes:
        filters.append({"code": {"$in": sorted(exercise_codes)}})
    if not filters:
        return {}, {}

    collection = Exercise.get_motor_collection()
    raw_rows = await collection.find({"$or": filters}).to_list(length=max(len(object_ids) + len(exercise_codes), 1))
    by_id: dict[str, Exercise] = {}
    by_code: dict[str, Exercise] = {}
    for row in raw_rows:
        try:
            ex_obj = Exercise.model_validate(row)
        except Exception as exc:
            logger.warning("Skipping invalid exercise during AI localization id=%s error=%s", row.get("_id"), str(exc))
            continue
        by_id[str(ex_obj.id)] = ex_obj
        if getattr(ex_obj, "code", None):
            by_code[str(ex_obj.code)] = ex_obj
    return by_id, by_code


def _extract_exercise_refs_from_template(workout_template: Any) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    codes: set[str] = set()
    wt = dict(workout_template or {})
    for item in list(wt.get("exercises") or []):
        row = dict(item or {})
        exercise_id = str(row.get("exercise_id") or "").strip()
        exercise_code = str(row.get("exercise_code") or "").strip()
        if exercise_id:
            ids.add(exercise_id)
        if exercise_code:
            codes.add(exercise_code)
    return ids, codes


def _localize_exercise_payload(
    item: Any,
    *,
    language: str,
    exercise_by_id: dict[str, Exercise],
    exercise_by_code: dict[str, Exercise],
) -> Dict[str, Any]:
    row = _enrich_saved_plan_exercise_media(item, language)
    row["language"] = language
    exercise_id = str(row.get("exercise_id") or "").strip()
    exercise_code = str(row.get("exercise_code") or "").strip()
    ex_obj = exercise_by_id.get(exercise_id) or exercise_by_code.get(exercise_code)
    if not ex_obj:
        saved_name = str(row.get("name") or "").strip()
        title_map = row.get("title") if isinstance(row.get("title"), dict) else {}
        fallback_name = "Упражнение" if _is_russian_language(language) else "Exercise"
        row["name"] = _humanize_ai_label(
            _pick_i18n_value(title_map, language) or saved_name,
            language=language,
            fallback=fallback_name,
        )
        row["title_text"] = row["name"]
        if isinstance(row.get("subtitle"), dict):
            row["subtitle_text"] = _pick_i18n_value(row.get("subtitle"), language)
        return row

    row["exercise_id"] = str(ex_obj.id)
    row["exercise_code"] = getattr(ex_obj, "code", None)
    row["title"] = {
        "ru": _pick_i18n_text(getattr(ex_obj, "name", None), "ru", default=str(row.get("name") or "")),
        "en": _pick_i18n_text(getattr(ex_obj, "name", None), "en", default=str(row.get("name") or "")),
    }
    row["name_i18n"] = {
        "ru": _pick_i18n_text(getattr(ex_obj, "name", None), "ru", default=str(row.get("name") or "")),
        "en": _pick_i18n_text(getattr(ex_obj, "name", None), "en", default=str(row.get("name") or "")),
    }
    row["name"] = _humanize_ai_label(
        _pick_i18n_text(getattr(ex_obj, "name", None), language, default=str(row.get("name") or "Exercise")),
        language=language,
        fallback="Упражнение" if _is_russian_language(language) else "Exercise",
    )
    row["title_text"] = row["name"]
    row["description_i18n"] = {
        "ru": _pick_i18n_text(getattr(ex_obj, "description", None), "ru"),
        "en": _pick_i18n_text(getattr(ex_obj, "description", None), "en"),
    }
    row["description"] = _pick_i18n_text(getattr(ex_obj, "description", None), language)
    if isinstance(row.get("subtitle"), dict):
        row["subtitle_text"] = _pick_i18n_value(row.get("subtitle"), language)
    row["beginner_tip_i18n"] = {
        "ru": _pick_i18n_text(getattr(ex_obj, "beginner_tip", None), "ru"),
        "en": _pick_i18n_text(getattr(ex_obj, "beginner_tip", None), "en"),
    }
    row["beginner_tip"] = _pick_i18n_text(getattr(ex_obj, "beginner_tip", None), language)
    row["ai_technique_i18n"] = {
        "ru": _pick_i18n_text(getattr(ex_obj, "ai_technique", None), "ru"),
        "en": _pick_i18n_text(getattr(ex_obj, "ai_technique", None), "en"),
    }
    row["ai_technique"] = _pick_i18n_text(getattr(ex_obj, "ai_technique", None), language)
    row["ai_mistakes_i18n"] = {
        "ru": _pick_i18n_text(getattr(ex_obj, "ai_mistakes", None), "ru"),
        "en": _pick_i18n_text(getattr(ex_obj, "ai_mistakes", None), "en"),
    }
    row["ai_mistakes"] = _pick_i18n_text(getattr(ex_obj, "ai_mistakes", None), language)
    row["instructions"] = [
        {
            "step": int(getattr(step_obj, "step", 0) or 0),
            "title": _pick_i18n_text(getattr(step_obj, "title", None), language),
            "description": _pick_i18n_text(getattr(step_obj, "description", None), language),
            "title_i18n": {
                "ru": _pick_i18n_text(getattr(step_obj, "title", None), "ru"),
                "en": _pick_i18n_text(getattr(step_obj, "title", None), "en"),
            },
            "description_i18n": {
                "ru": _pick_i18n_text(getattr(step_obj, "description", None), "ru"),
                "en": _pick_i18n_text(getattr(step_obj, "description", None), "en"),
            },
        }
        for step_obj in list(getattr(ex_obj, "instructions", None) or [])
    ]
    row["common_mistakes"] = [
        {
            "title": _pick_i18n_text(getattr(mistake_obj, "title", None), language),
            "description": _pick_i18n_text(getattr(mistake_obj, "description", None), language),
            "title_i18n": {
                "ru": _pick_i18n_text(getattr(mistake_obj, "title", None), "ru"),
                "en": _pick_i18n_text(getattr(mistake_obj, "title", None), "en"),
            },
            "description_i18n": {
                "ru": _pick_i18n_text(getattr(mistake_obj, "description", None), "ru"),
                "en": _pick_i18n_text(getattr(mistake_obj, "description", None), "en"),
            },
        }
        for mistake_obj in list(getattr(ex_obj, "common_mistakes", None) or [])
    ]
    row["workout_type"] = [
        str(getattr(workout_type, "value", workout_type) or "")
        for workout_type in list(getattr(ex_obj, "workout_type", None) or [])
    ]
    row["worktype_label"] = [
        _localized_type_label(str(getattr(workout_type, "value", workout_type) or ""), language)
        for workout_type in list(getattr(ex_obj, "workout_type", None) or [])
    ]
    row["equipment"] = [
        str(getattr(equipment_item, "value", equipment_item) or "")
        for equipment_item in list(getattr(ex_obj, "equipment", None) or [])
    ]
    row["equipment_labels"] = [
        _localized_equipment_label(equipment_item, language)
        for equipment_item in list(getattr(ex_obj, "equipment", None) or [])
    ]
    row["muscle_groups"] = list(getattr(ex_obj, "muscle_groups", None) or [])
    row["movement_type"] = getattr(ex_obj, "movement_type", None)
    return row


async def _workout_template_for_output(
    workout_template: Any,
    language: str,
    day_type: str = "workout",
    *,
    week_idx: int = 0,
    exercise_by_id: Optional[dict[str, Exercise]] = None,
    exercise_by_code: Optional[dict[str, Exercise]] = None,
    plan_id: Optional[str] = None,
    day_iso: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not workout_template or str(day_type or "").lower() != "workout":
        return None
    wt = dict(workout_template)
    focus_value = str(wt.get("focus") or "").strip().lower()
    if focus_value:
        focus_label = _localized_type_label(focus_value, language)
        wt["focus_label"] = focus_label
        wt["category_label"] = focus_label
    wt["type_label"] = _localized_type_label(day_type, language)
    wt["title_i18n"] = _localized_workout_title_i18n(focus_value or "get_fitter")
    wt["title"] = _localized_display_workout_title(wt, language)
    wt["progression_note"] = _localized_progression_note(
        int(week_idx),
        str(wt.get("intensity") or "moderate"),
        language,
    )
    wt["safety"] = _localized_safety_messages(language)

    exercises = wt.get("exercises")
    if isinstance(exercises, list):
        by_id = exercise_by_id or {}
        by_code = exercise_by_code or {}
        rest_override = _coerce_int(wt.get("rest_seconds"), default=60, lo=0, hi=600)
        wt["exercises"] = [
            _normalize_ai_exercise_contract(
                _localize_exercise_payload(
                    item,
                    language=language,
                    exercise_by_id=by_id,
                    exercise_by_code=by_code,
                ),
                rest_seconds_override=rest_override,
            )
            for item in exercises
        ]
        wt = _normalize_workout_template_exercise_counts(wt, plan_id=plan_id, day_iso=day_iso)
        wt["exercise_count"] = len(wt["exercises"])
        wt["total_sets"] = sum(int(item.get("total_sets", 0) or 0) for item in wt["exercises"])
        wt["total_reps"] = sum(int(item.get("total_reps", 0) or 0) for item in wt["exercises"])
        wt["total_seconds"] = sum(int(item.get("total_seconds", 0) or 0) for item in wt["exercises"])
        wt["duration_min"] = max(1, (int(wt["total_seconds"]) + 59) // 60) if int(wt["total_seconds"]) > 0 else _coerce_int(wt.get("duration_min"), default=0, lo=0, hi=600)

    return wt


def _weekly_slots(workouts_per_week: int) -> set[int]:
    k = _coerce_int(workouts_per_week, default=4, lo=1, hi=7)
    if k >= 7:
        return set(range(7))
    return {min(6, round(i * (6 / max(1, k - 1)))) for i in range(k)}


def _distributed_slots(total_days: int, workout_days: int) -> set[int]:
    total = max(1, int(total_days))
    workouts = max(0, min(int(workout_days), total))
    if workouts <= 0:
        return set()
    if workouts >= total:
        return set(range(total))

    # Evenly spread exact workout count across a finite horizon.
    slots: set[int] = set()
    placed = 0
    for day_idx in range(total):
        expected = round(((day_idx + 1) * workouts) / total)
        if expected > placed:
            slots.add(day_idx)
            placed = expected
        if placed >= workouts:
            break
    return slots


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


async def plan_to_out(plan: AiPlan, language: str = "en") -> AiPlanOut:
    exercise_ids: set[str] = set()
    exercise_codes: set[str] = set()
    for day_obj in (plan.days or []):
        ids, codes = _extract_exercise_refs_from_template(getattr(day_obj, "workout_template", None))
        exercise_ids.update(ids)
        exercise_codes.update(codes)
    exercise_by_id, exercise_by_code = await _load_exercise_lookup(exercise_ids, exercise_codes)
    days_out: list[AiPlanDayOut] = []
    for idx, day_obj in enumerate(plan.days or []):
        raw_workout_template = getattr(day_obj, "workout_template", None)
        workout_template = await _workout_template_for_output(
            raw_workout_template,
            language,
            str(getattr(day_obj, "type", "recovery")),
            week_idx=(idx // 7),
            exercise_by_id=exercise_by_id,
            exercise_by_code=exercise_by_code,
            plan_id=str(plan.id),
            day_iso=str(getattr(day_obj, "date", "")),
        )
        day_out = AiPlanDayOut(
            date=str(getattr(day_obj, "date", "")),
            type=str(getattr(day_obj, "type", "recovery")),
            type_label=_day_type_label(day_obj, language),
            title=_day_title(day_obj, language),
            focus=_day_focus(day_obj),
            focus_label=_day_focus_label(day_obj, language),
            workout_template=workout_template,
        )
        days_out.append(day_out)
        if workout_template and isinstance(workout_template.get("exercises"), list):
            _log_ai_localization(
                user_id=str(plan.user_id),
                language=language,
                plan_id=str(plan.id),
                day_iso=str(day_out.date),
                day_title=str(day_out.title or ""),
                workout_title=str((workout_template or {}).get("title") or day_out.title or ""),
                type_label=str(day_out.type_label or ""),
                focus_label=str(day_out.focus_label or ""),
                raw_exercises=list((raw_workout_template or {}).get("exercises") or []) if isinstance(raw_workout_template, dict) else [],
                localized_exercises=list(workout_template.get("exercises") or []),
            )
    return AiPlanOut(
        id=str(plan.id),
        status=plan.status,
        version=plan.version,
        reroll_of_plan_id=str(plan.reroll_of_plan_id) if plan.reroll_of_plan_id else None,
        days=days_out,
        created_at=plan.created_at,
    )


def _today_iso_for_user(current_user: Any) -> str:
    tz_name = _as_str(getattr(current_user, "timezone", "UTC")) or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    return utcnow().astimezone(tz).date().isoformat()


async def _daily_rec_to_out(rec: AiDailyRecommendation, current_user: Any | None = None) -> AiDailyRecommendationOut:
    text = rec.text
    meta = rec.meta or {}
    if current_user and str((rec.meta or {}).get("source") or "") == "active_plan":
        text, meta = await _build_daily_recommendation(current_user, rec.date)
    return AiDailyRecommendationOut(
        id=str(rec.id),
        date=rec.date,
        text=text,
        saved=bool(rec.saved),
        opened_at=rec.opened_at,
        saved_at=rec.saved_at,
        meta=meta,
    )


async def _build_daily_recommendation(current_user: Any, day_iso: str) -> tuple[str, Dict[str, Any]]:
    plan = await get_active_plan(current_user.id)
    language = _as_str(getattr(current_user, "language", "en")) or "en"
    if plan:
        for d in (plan.days or []):
            if str(getattr(d, "date", "")) != day_iso:
                continue
            d_type = str(getattr(d, "type", "recovery"))
            wt = getattr(d, "workout_template", None) or {}
            if d_type == "workout":
                title = _day_title(d, language)
                duration = wt.get("duration_min")
                focus = wt.get("focus")
                chunks = [f"Сегодня: {title}." if _is_russian_language(language) else f"Today: {title}."]
                if duration:
                    chunks.append(f"Длительность: {duration} мин." if _is_russian_language(language) else f"Duration: {duration} min.")
                if focus:
                    focus_label = _localized_type_label(focus, language)
                    chunks.append(
                        f"Фокус: {focus_label}."
                        if _is_russian_language(language)
                        else f"Focus: {focus_label}."
                    )
                chunks.append(
                    "Соблюдайте технику и завершите тренировку лёгкой растяжкой."
                    if _is_russian_language(language)
                    else "Keep strict form and finish with light stretching."
                )
                return " ".join(chunks), {
                    "source": "active_plan",
                    "plan_id": str(plan.id),
                    "type": d_type,
                    "date": day_iso,
                }
            return (_localized_recovery_day_title(language) + ". Сделайте мобилити и лёгкую прогулку на 20-30 минут."
                if _is_russian_language(language)
                else "Rest day. Mobility + light walk 20-30 min."
            ), {
                "source": "active_plan",
                "plan_id": str(plan.id),
                "type": d_type,
                "date": day_iso,
            }

    return (
        "Рекомендация на день: 20-30 минут быстрой ходьбы, 5 минут мобилити и достаточное количество воды."
        if _is_russian_language(language)
        else "Daily recommendation: 20-30 min brisk walk, 5 min mobility, and drink enough water.",
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
        "training_rest_seconds": int(getattr(current_user, "training_rest_seconds", 60) or 60),
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
    recent_exercise_ids: Optional[set[str]] = None,
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
    recent_ids = {str(x) for x in (recent_exercise_ids or set()) if str(x)}
    fresh_candidates = [ex for ex in pool_shuffled if str(ex.id) not in recent_ids]
    selected_pool = list(fresh_candidates)
    if len(selected_pool) < exercise_count:
        selected_pool.extend(ex for ex in pool_shuffled if str(ex.id) in recent_ids)
    selected = selected_pool[:exercise_count] if len(selected_pool) >= exercise_count else selected_pool
    if not selected:
        focus_label = _localized_type_label(target_type, language)
        return {
            "title": _localized_workout_title(_display_goal_label(target_type, language), language),
            "title_i18n": _localized_workout_title_i18n(target_type),
            "duration_min": duration_min,
            "intensity": intensity,
            "focus": target_type,
            "focus_label": focus_label,
            "category_label": focus_label,
            "type_label": _localized_type_label("workout", language),
            "progression_note": _localized_progression_note(week_idx, intensity, language),
            "exercises": [],
            "safety": _localized_safety_messages(language),
        }

    base_sets = {"beginner": 2, "intermediate": 3, "advanced": 4}.get(level, 2)
    sets = min(5, base_sets + (1 if week_idx >= 2 else 0))
    rest_seconds = _coerce_int(inputs.get("training_rest_seconds"), default=60, lo=10, hi=600)

    def _build_set_plan_payload(
        *,
        mode: str,
        sets_count: int,
        rest_seconds_after: int,
        base_reps: Optional[int],
        base_duration_seconds: Optional[int],
        video_url: Optional[str],
        thumbnail_url: Optional[str],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for set_no in range(1, int(sets_count) + 1):
            rep_rows: list[dict[str, Any]] = []
            if mode == "time":
                base_sec = int(base_duration_seconds or 30)
                for idx, seconds_value in enumerate([base_sec], start=1):
                    rep_rows.append(
                        {
                            "rep_no": idx,
                            "mode": mode,
                            "target_reps": None,
                            "target_duration_seconds": int(seconds_value),
                            "video_url": video_url,
                            "thumbnail_url": thumbnail_url,
                        }
                    )
            else:
                base_rep_value = int(base_reps or 12)
                for idx, reps_value in enumerate([base_rep_value], start=1):
                    rep_rows.append(
                        {
                            "rep_no": idx,
                            "mode": mode,
                            "target_reps": int(reps_value),
                            "target_duration_seconds": None,
                            "video_url": video_url,
                            "thumbnail_url": thumbnail_url,
                        }
                    )
            out.append(
                {
                    "set_no": set_no,
                    "rest_seconds_after": int(rest_seconds_after),
                    "reps": rep_rows,
                }
            )
        return out

    exercise_items: list[dict[str, Any]] = []
    for ex in selected:
        mode = _as_str(ex.mode)
        default_reps = getattr(ex.defaults, "reps", None) if ex.defaults else None
        default_dur = getattr(ex.defaults, "duration_seconds", None) if ex.defaults else None
        media = getattr(ex, "media", None)
        thumbnail_url = ensure_existing_media_url(getattr(media, "thumbnail_url", None) if media else None, kind="thumbnail")
        video_url = ensure_existing_media_url(getattr(media, "video_url", None) if media else None, kind="video")
        video_meta = parse_exercise_video_from_url(video_url)

        item: dict[str, Any] = {
            "exercise_id": str(ex.id),
            "exercise_code": ex.code,
            "name": _pick_i18n_name(ex.name, language),
            "name_i18n": {
                "ru": _pick_i18n_name(ex.name, "ru"),
                "en": _pick_i18n_name(ex.name, "en"),
            },
            "mode": mode,
            "sets": sets,
            "rest_seconds": rest_seconds,
            "thumbnail_url": thumbnail_url,
            "video_url": video_url,
            **video_meta,
        }
        set_plan = _build_set_plan_payload(
            mode=mode,
            sets_count=sets,
            rest_seconds_after=rest_seconds,
            base_reps=int(default_reps) if default_reps is not None else None,
            base_duration_seconds=int(default_dur) if default_dur is not None else None,
            video_url=video_url,
            thumbnail_url=thumbnail_url,
        )
        if mode == "time":
            item["duration_seconds"] = int(default_dur or 30)
        else:
            item["reps"] = int(default_reps or (10 if intensity == "low" else 12 if intensity == "moderate" else 14))
        item["set_plan"] = set_plan
        exercise_items.append(item)

    focus_label = _localized_type_label(target_type, language)
    return {
        "title": _localized_workout_title(_display_goal_label(target_type, language), language),
        "title_i18n": _localized_workout_title_i18n(target_type),
        "duration_min": duration_min,
        "intensity": intensity,
        "focus": target_type,
        "focus_label": focus_label,
        "category_label": focus_label,
        "type_label": _localized_type_label("workout", language),
        "progression_note": _localized_progression_note(week_idx, intensity, language),
        "exercises": exercise_items,
        "safety": _localized_safety_messages(language),
    }


def _workout_template_signature(day_obj: Any) -> tuple[str, ...]:
    wt = getattr(day_obj, "workout_template", None) if not isinstance(day_obj, dict) else day_obj.get("workout_template")
    exercises = list((wt or {}).get("exercises") or [])
    tokens: list[str] = []
    for item in exercises:
        row = dict(item or {})
        token = str(row.get("exercise_id") or row.get("exercise_code") or row.get("name") or "").strip()
        if token:
            tokens.append(token)
    return tuple(tokens)


def _plan_has_low_variety(days: list[Dict[str, Any]]) -> bool:
    workout_days = [day for day in days if str((day or {}).get("type") or "").lower() == "workout"]
    if len(workout_days) < 4:
        return False

    unique_exercises: set[str] = set()
    signatures: list[tuple[str, ...]] = []
    for day in workout_days:
        signature = _workout_template_signature(day)
        if signature:
            signatures.append(signature)
            unique_exercises.update(signature)

    if not signatures:
        return False

    signature_counts: dict[tuple[str, ...], int] = {}
    for signature in signatures:
        signature_counts[signature] = signature_counts.get(signature, 0) + 1

    most_common_signature_count = max(signature_counts.values())
    same_signature_ratio = most_common_signature_count / max(1, len(signatures))
    unique_exercise_count = len(unique_exercises)

    return (
        unique_exercise_count <= 4 and len(signatures) >= 6
    ) or same_signature_ratio >= 0.6


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
    strict_rest = bool(prompt_meta.get("rest_days_strict", False))
    rest_days_raw = prompt_meta.get("rest_days")
    if strict_rest and rest_days_raw is not None:
        try:
            rest_days = int(rest_days_raw)
        except Exception:
            rest_days = 0
        rest_days = max(0, min(int(total_days) - 1, rest_days))
        workout_days = max(1, int(total_days) - rest_days)
        absolute_slots = _distributed_slots(int(total_days), workout_days)
        slots = None
    else:
        slots = _weekly_slots(_coerce_int(inputs.get("days_per_week"), default=4, lo=1, hi=7))
        absolute_slots = None
    nonce = seed_nonce or str(prompt_meta.get("_reroll_nonce") or "")

    days: list[Dict[str, Any]] = []
    workout_day_idx = 0
    recent_exercise_ids: list[set[str]] = []
    for i in range(total_days):
        d = start + timedelta(days=i)
        weekday = i % 7
        is_workout_day = (i in absolute_slots) if absolute_slots is not None else (weekday in slots)
        if is_workout_day:
            week_idx = i // 7
            workout_template = _build_workout_template(
                day_date=d,
                week_idx=week_idx,
                day_idx=workout_day_idx,
                inputs=inputs,
                exercises=exercises,
                target_types=target_types,
                rng_seed=f"{start.isoformat()}:{nonce}",
                recent_exercise_ids=set().union(*recent_exercise_ids) if recent_exercise_ids else set(),
            )
            current_ids = {
                str((item or {}).get("exercise_id") or "").strip()
                for item in list((workout_template or {}).get("exercises") or [])
                if str((item or {}).get("exercise_id") or "").strip()
            }
            if current_ids:
                recent_exercise_ids.append(current_ids)
                if len(recent_exercise_ids) > 2:
                    recent_exercise_ids.pop(0)
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
    if _is_russian_language(language):
        system_prompt = (
            "Ты фитнес-планировщик. Верни только строгий JSON без markdown. "
            f"Собери безопасный план на {int(total_days)} дней с распределением тренировок и восстановления, "
            "конкретными упражнениями, подходами, повторениями или временем, а также рекомендациями по прогрессии. "
            "Все пользовательские тексты должны быть полностью на русском языке без смешения с английским."
        )
    else:
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
    if _is_russian_language(language):
        user_prompt["rules"].append("All titles, exercise names, notes, tips and recommendations must be in Russian only.")

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
                        row["name"] = _pick_i18n_name(ex_obj.name, language)
                        row["name_i18n"] = {
                            "ru": _pick_i18n_name(ex_obj.name, "ru"),
                            "en": _pick_i18n_name(ex_obj.name, "en"),
                        }
                        if not row.get("thumbnail_url"):
                            row["thumbnail_url"] = ensure_existing_media_url(
                                getattr(media, "thumbnail_url", None) if media else None,
                                kind="thumbnail",
                            )
                        if not row.get("video_url"):
                            row["video_url"] = ensure_existing_media_url(
                                getattr(media, "video_url", None) if media else None,
                                kind="video",
                            )
                        else:
                            row["video_url"] = ensure_existing_media_url(row.get("video_url"), kind="video")
                        row.update(parse_exercise_video_from_url(row.get("video_url")))

                    mode_value = str(row.get("mode") or "reps").strip().lower()
                    sets_value = _coerce_int(row.get("sets"), default=3, lo=1, hi=10)
                    rest_seconds = _coerce_int(row.get("rest_seconds"), default=60, lo=0, hi=600)
                    existing_set_plan = row.get("set_plan")
                    if not isinstance(existing_set_plan, list) or not existing_set_plan:
                        if mode_value == "time":
                            base_seconds = _coerce_int(row.get("duration_seconds"), default=30, lo=5, hi=3600)
                            row["set_plan"] = [
                                {
                                    "set_no": set_no,
                                    "rest_seconds_after": rest_seconds,
                                    "reps": [
                                        {
                                            "rep_no": rep_no,
                                            "mode": mode_value,
                                            "target_reps": None,
                                            "target_duration_seconds": max(10, base_seconds + (rep_no - 2) * 5),
                                            "video_url": row.get("video_url"),
                                            "thumbnail_url": row.get("thumbnail_url"),
                                            **parse_exercise_video_from_url(row.get("video_url")),
                                        }
                                        for rep_no in range(1, 4)
                                    ],
                                }
                                for set_no in range(1, sets_value + 1)
                            ]
                        else:
                            base_reps = _coerce_int(row.get("reps"), default=12, lo=1, hi=500)
                            row["set_plan"] = [
                                {
                                    "set_no": set_no,
                                    "rest_seconds_after": rest_seconds,
                                    "reps": [
                                        {
                                            "rep_no": rep_no,
                                            "mode": mode_value,
                                            "target_reps": max(1, base_reps + (rep_no - 2) * 2),
                                            "target_duration_seconds": None,
                                            "video_url": row.get("video_url"),
                                            "thumbnail_url": row.get("thumbnail_url"),
                                            **parse_exercise_video_from_url(row.get("video_url")),
                                        }
                                        for rep_no in range(1, 4)
                                    ],
                                }
                                for set_no in range(1, sets_value + 1)
                            ]

                    enriched_exercises.append(row)
                wt["exercises"] = enriched_exercises
            if _is_russian_language(language):
                wt["title"] = _localized_workout_title(
                    _localized_goal_label(str(wt.get("focus") or "get_fitter"), language),
                    language,
                )
                wt["progression_note"] = _localized_progression_note(
                    i // 7,
                    str(wt.get("intensity") or "moderate"),
                    language,
                )
                wt["safety"] = _localized_safety_messages(language)
            wt["title_i18n"] = _localized_workout_title_i18n(str(wt.get("focus") or "get_fitter"))

        normalized.append(
            _normalize_plan_day_language({
                "date": day_date,
                "type": d_type,
                "workout_template": wt,
            }, language)
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


def _chat_generate_plan_label(language: str) -> str:
    return "Сгенерировать план" if _is_russian_language(language) else "Generate plan"


def _chat_plan_ready_text(language: str, *, regenerated: bool = False) -> str:
    if _is_russian_language(language):
        return "План обновлен и готов." if regenerated else "План готов."
    return "Your plan has been updated." if regenerated else "Your plan is ready."


def _chat_generate_button_text(language: str) -> str:
    if _is_russian_language(language):
        return "Могу собрать план по этому запросу. Нажми кнопку, и я его сгенерирую."
    return "I can build a plan from this request. Tap the button and I'll generate it."


def _combine_history_for_plan_prompt(history: list[dict[str, str]], text: str, limit: int = 6) -> str:
    parts = [str((item or {}).get("text") or "").strip() for item in (history[-limit:] if history else [])]
    parts.append(str(text or "").strip())
    return "\n".join(part for part in parts if part).strip()


def _combine_user_history_for_plan_prompt(history: list[dict[str, str]], text: str, limit: int = 6) -> str:
    parts = [
        str((item or {}).get("text") or "").strip()
        for item in (history[-limit:] if history else [])
        if str((item or {}).get("role") or "").lower() == "user"
    ]
    parts.append(str(text or "").strip())
    return "\n".join(part for part in parts if part).strip()


def _has_lose_weight_context(text: str) -> bool:
    lowered = str(text or "").lower()
    markers = [
        "жир",
        "живот",
        "похуд",
        "сжечь жир",
        "сбросить вес",
        "снижение веса",
        "lose weight",
        "weight loss",
        "fat loss",
        "burn fat",
        "belly fat",
        "slim down",
        "pohud",
        "zhir",
    ]
    return any(marker in lowered for marker in markers)


def _apply_safe_decision_meta_hints(meta: Dict[str, Any], decision_meta: Dict[str, Any], combined_user_text: str) -> Dict[str, Any]:
    out = dict(meta or {})
    safe_meta = sanitize_decision_meta(decision_meta or {})

    if _has_lose_weight_context(combined_user_text):
        goals = _normalize_goal_values(out.get("goals"))
        if "lose_weight" not in goals:
            goals.insert(0, "lose_weight")
        out["goals"] = goals or ["lose_weight"]

        body_focus = _as_str_list(out.get("body_focus"))
        if "core" not in body_focus and _contains_any(combined_user_text, ["живот", "belly", "core", "пресс", "press"]):
            body_focus.append("core")
        if body_focus:
            out["body_focus"] = body_focus

    for key in ("equipment", "injuries", "location", "style", "level", "intensity", "body_focus"):
        if safe_meta.get(key) and not out.get(key):
            out[key] = safe_meta[key]

    if safe_meta.get("goals"):
        existing_goals = _normalize_goal_values(out.get("goals"))
        hinted_goals = _normalize_goal_values(safe_meta.get("goals"))
        merged_goals = list(existing_goals)
        for goal in hinted_goals:
            if goal not in merged_goals:
                merged_goals.append(goal)
        if merged_goals:
            out["goals"] = merged_goals

    return out


async def _prepare_plan_generation_inputs(
    *,
    prompt_text: str,
    base_meta: Dict[str, Any],
) -> tuple[Dict[str, Any], int, bool]:
    meta = dict(base_meta or {})
    total_days = _coerce_int(meta.get("total_days"), default=30, lo=1, hi=365)
    enforce_rest_distribution = False

    text = str(prompt_text or "").strip()
    if not text:
        return meta, total_days, enforce_rest_distribution

    explicit_overrides = _extract_explicit_schedule_overrides(text)
    ai_meta = await _ai_understand_plan_prompt(text, meta)
    if ai_meta:
        meta.update(ai_meta)
        meta.update(explicit_overrides)
        total_days = _coerce_int(meta.get("total_days"), default=30, lo=1, hi=365)
        enforce_rest_distribution = bool(meta.get("rest_days") is not None and meta.get("rest_days_strict", False))
        return meta, total_days, enforce_rest_distribution

    parse_meta = _meta_without_duration_overrides(meta)
    understanding = parse_plan_request(text, parse_meta)
    total_days = int(understanding.total_days)
    meta = apply_understanding_to_meta(meta, understanding)
    meta.update(explicit_overrides)
    enforce_rest_distribution = has_explicit_rest_day_request(text, parse_meta)
    return meta, total_days, enforce_rest_distribution


async def _generate_plan_for_user(
    current_user: Any,
    *,
    prompt_text: str,
    base_meta: Dict[str, Any],
    usage: Optional[AiUsageMonthly],
    req_type: AiRequestType = AiRequestType.generate_plan,
    version: int = 1,
    reroll_of_plan_id: Optional[PydanticObjectId] = None,
) -> tuple[AiPlan, AiRequest, Dict[str, Any], int]:
    meta, total_days, enforce_rest_distribution = await _prepare_plan_generation_inputs(
        prompt_text=prompt_text,
        base_meta=base_meta,
    )
    req = await create_ai_request(
        user_id=current_user.id,
        req_type=req_type,
        prompt_meta=meta,
    )

    days = await _try_generate_plan_with_yandex(current_user, meta, total_days=total_days)
    if not days:
        logger.warning(
            "AI plan generation fallback to local builder: user_id=%s total_days=%s req_type=%s",
            str(current_user.id),
            int(total_days),
            str(req_type.value if hasattr(req_type, "value") else req_type),
        )
        days = await build_plan_days(current_user, meta, total_days=total_days)
    elif _plan_has_low_variety(days):
        logger.warning(
            "AI plan generation low-variety fallback: user_id=%s total_days=%s req_type=%s",
            str(current_user.id),
            int(total_days),
            str(req_type.value if hasattr(req_type, "value") else req_type),
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
        version=version,
        reroll_of_plan_id=reroll_of_plan_id,
    )
    await plan.insert()
    return plan, req, meta, total_days


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
    try:
        plan, req, meta, _ = await _generate_plan_for_user(
            current_user,
            prompt_text=prompt_text,
            base_meta=base_meta,
            usage=usage,
            req_type=AiRequestType.generate_plan,
            version=1,
            reroll_of_plan_id=None,
        )
    except ValueError as e:
        raise HTTPException(422, f"Plan request is inconsistent: {str(e)}")

    return AiGenerateOut(
        request_id=str(req.id),
        plan=await plan_to_out(plan, _as_str(getattr(current_user, "language", "en")) or "en"),
    )


@router.get("/ai/plan", response_model=AiPlanOut)
async def get_current_plan(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    plan = await get_active_plan(current_user.id)
    if not plan:
        raise HTTPException(404, "No active plan")
    return await plan_to_out(plan, _as_str(getattr(current_user, "language", "en")) or "en")


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

    return await _daily_rec_to_out(rec, current_user)


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

    return await _daily_rec_to_out(rec, current_user)


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
    language = _as_str(getattr(current_user, "language", "en")) or "en"
    message_text = str(payload.text or "")
    logger.info(
        "AI chat message: user_id=%s thread_id=%s premium=%s text=%s",
        str(current_user.id),
        str(payload.thread_id or ""),
        premium,
        _short_text_preview(message_text),
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
    assistant_text = ""
    decision = await get_ai_chat_decision(
        text=payload.text,
        history=history,
        meta=payload.meta or {},
        language=language,
    )
    combined_text = _combine_history_for_plan_prompt(history, payload.text)
    combined_user_text = _combine_user_history_for_plan_prompt(history, payload.text)
    regen_intent = detect_plan_regeneration_intent(payload.text)
    plan_intent = detect_plan_intent(payload.text)

    if decision.fallback_used and regen_intent:
        decision.type = "generate_plan_now"

    if regen_intent:
        active_plan = await get_active_plan(current_user.id)
        if not active_plan:
            logger.info("AI chat regeneration skipped: user_id=%s reason=no_active_plan", str(current_user.id))
            assistant_text = _localized_ai_chat_text("no_active_plan_to_regenerate", language)
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
                try:
                    regenerated_plan, _, _, _ = await _generate_plan_for_user(
                        current_user,
                        prompt_text=combined_user_text,
                        base_meta=prompt_meta,
                        usage=None,
                        req_type=AiRequestType.adjust,
                        version=int(active_plan.version or 1) + 1,
                        reroll_of_plan_id=active_plan.id,
                    )
                    assistant_text = _chat_plan_ready_text(language, regenerated=True)
                    action = {
                        "type": "plan_generated",
                        "plan_id": str(regenerated_plan.id),
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
                    try:
                        rerolled_plan, _, _, _ = await _generate_plan_for_user(
                            current_user,
                            prompt_text=combined_user_text,
                            base_meta=prompt_meta,
                            usage=None,
                            req_type=AiRequestType.reroll,
                            version=int(active_plan.version or 1) + 1,
                            reroll_of_plan_id=active_plan.id,
                        )
                        assistant_text = _chat_plan_ready_text(language, regenerated=True)
                        action = {
                            "type": "plan_generated",
                            "plan_id": str(rerolled_plan.id),
                            "total_days": total_days,
                        }
                    except HTTPException:
                        raise
                    except Exception as e:
                        logger.exception("Plan reroll from chat failed: %s", e)
                        assistant_text = "Something went wrong rerolling your plan. Please try again."
    elif decision.type == "show_generate_button":
        try:
            normalized_meta, total_days, _ = await _prepare_plan_generation_inputs(
                prompt_text=combined_user_text,
                base_meta=dict(payload.meta or {}),
            )
        except ValueError as e:
            assistant_text = f"Plan request is inconsistent: {str(e)}"
        else:
            assistant_text = decision.assistant_text or _chat_generate_button_text(language)
            normalized_meta = _apply_safe_decision_meta_hints(
                normalized_meta,
                decision.meta,
                combined_user_text,
            )
            action = {
                "type": "suggest_generate_plan",
                "label": decision.label or _chat_generate_plan_label(language),
                "meta": normalized_meta,
                "total_days": int(total_days),
            }
    elif decision.type == "generate_plan_now" or (decision.fallback_used and plan_intent):
        access = await get_plan_generation_access(
            user_id=current_user.id,
            is_premium=bool(premium),
        )
        usage = access.usage

        if not access.can_generate:
            logger.info(
                "AI chat generation blocked: user_id=%s premium=%s used=%s limit=%s",
                str(current_user.id),
                bool(premium),
                access.used,
                access.limit,
            )
            assistant_text = (
                "You've used all your plan generation credits for this month. "
                "Watch a rewarded ad to get more, or upgrade to Premium."
            )
        else:
            try:
                generation_meta = dict(payload.meta or {})
                generation_meta = _apply_safe_decision_meta_hints(
                    generation_meta,
                    decision.meta,
                    combined_user_text,
                )
                plan, _, _, total_days = await _generate_plan_for_user(
                    current_user,
                    prompt_text=combined_user_text,
                    base_meta=generation_meta,
                    usage=usage,
                    req_type=AiRequestType.generate_plan,
                    version=1,
                    reroll_of_plan_id=None,
                )
                assistant_text = _chat_plan_ready_text(language)
                action = {"type": "plan_generated", "plan_id": str(plan.id), "total_days": int(total_days)}
                logger.info(
                    "AI chat generation success: user_id=%s plan_id=%s total_days=%s",
                    str(current_user.id),
                    str(plan.id),
                    int(total_days),
                )
            except HTTPException:
                raise
            except ValueError as e:
                logger.info("AI chat generation validation failed: user_id=%s error=%s", str(current_user.id), str(e))
                assistant_text = f"Plan request is inconsistent: {str(e)}"
            except Exception as e:
                logger.exception("Plan generation from chat failed: %s", e)
                assistant_text = "Something went wrong generating your plan. Please try again."
    else:
        logger.info("AI chat no-plan intent branch: user_id=%s premium=%s", str(current_user.id), bool(premium))
        if not premium:
            assistant_text = _localized_ai_chat_text("premium_chat_only", language)
        else:
            await create_ai_request(
                user_id=current_user.id,
                req_type=AiRequestType.chat,
                prompt_meta=payload.meta or {},
            )
            assistant_text = decision.assistant_text or await yandex_chat_completion(payload.text, payload.meta or {}, history=history)

    logger.info(
        "AI chat decision: user_id=%s thread_id=%s decision_type=%s has_action=%s action_type=%s fallback_used=%s text=%s",
        str(current_user.id),
        str(thread.id),
        decision.type,
        bool(action),
        str((action or {}).get("type") or ""),
        bool(decision.fallback_used),
        _short_text_preview(payload.text),
    )

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


def _day_title(day_obj: Any, language: str = "en") -> str:
    d_type = str(getattr(day_obj, "type", "recovery"))
    wt = getattr(day_obj, "workout_template", None) or {}
    if d_type == "workout":
        return _localized_display_workout_title(wt, language)
    return _localized_recovery_day_title(language)


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


def _day_type_label(day_obj: Any, language: str = "en") -> str:
    return _localized_type_label(str(getattr(day_obj, "type", "recovery")), language)


def _day_focus_label(day_obj: Any, language: str = "en") -> Optional[str]:
    focus = _day_focus(day_obj)
    if not focus:
        return None
    return _localized_type_label(focus, language)


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
    thumbnail_url = ensure_existing_media_url(getattr(media, "thumbnail_url", None) if media else None, kind="thumbnail")
    video_url = ensure_existing_media_url(getattr(media, "video_url", None) if media else None, kind="video")
    video_meta = parse_exercise_video_from_url(video_url)
    defaults = getattr(ex_obj, "defaults", None)
    default_reps = int(getattr(defaults, "reps", 0) or 0) if defaults else 0
    default_duration = int(getattr(defaults, "duration_seconds", 0) or 0) if defaults else 0

    sets_value = _coerce_int(old_item.get("sets"), default=3, lo=1, hi=10)
    rest_seconds = _coerce_int(old_item.get("rest_seconds"), default=60, lo=15, hi=300)
    ru_name = _pick_i18n_name(getattr(ex_obj, "name", None), "ru")
    en_name = _pick_i18n_name(getattr(ex_obj, "name", None), "en")
    chosen_name = _pick_i18n_name(getattr(ex_obj, "name", None), language)
    media_duration_seconds = int(getattr(media, "duration_seconds", 0) or 0) if media else 0
    if mode == "reps":
        duration_min = 20
    else:
        duration_source_seconds = default_duration or media_duration_seconds
        duration_min = max(1, int(duration_source_seconds / 60)) if duration_source_seconds > 0 else 20
    difficulty = str(getattr(getattr(ex_obj, "difficulty", None), "value", getattr(ex_obj, "difficulty", "")) or "").lower()
    level_en = {"beginner": "Beginner", "intermediate": "Intermediate", "advanced": "Advanced"}.get(difficulty, "Beginner")
    level_ru = {"beginner": "Начальный", "intermediate": "Средний", "advanced": "Продвинутый"}.get(difficulty, "Начальный")

    item: Dict[str, Any] = {
        "exercise_id": str(ex_obj.id),
        "exercise_code": getattr(ex_obj, "code", None),
        "name": chosen_name,
        "title": {
            "ru": ru_name,
            "en": en_name,
        },
        "subtitle": {
            "ru": f"{level_ru} - {duration_min} мин",
            "en": f"{level_en} - {duration_min} min",
        },
        "level": difficulty or "beginner",
        "duration_min": duration_min,
        "mode": mode,
        "sets": sets_value,
        "rest_seconds": rest_seconds,
        "thumbnail_url": thumbnail_url,
        "video_url": video_url,
        **video_meta,
    }

    set_count = _coerce_int(old_item.get("sets"), default=sets_value, lo=1, hi=10)
    rep_variations = _coerce_int(old_item.get("rep_variations"), default=1, lo=1, hi=6)

    if mode == "time":
        old_duration = old_item.get("duration_seconds")
        try:
            old_duration = int(old_duration) if old_duration is not None else None
        except Exception:
            old_duration = None
        resolved_duration = int(old_duration or default_duration or 30)
        item["duration_seconds"] = resolved_duration
        item["set_plan"] = [
            {
                "set_no": set_no,
                "rest_seconds_after": rest_seconds,
                "reps": [
                    {
                        "rep_no": rep_no,
                        "mode": mode,
                        "target_reps": None,
                        "target_duration_seconds": max(10, resolved_duration + (rep_no - 2) * 5),
                        "video_url": video_url,
                        "thumbnail_url": thumbnail_url,
                        **video_meta,
                    }
                    for rep_no in range(1, rep_variations + 1)
                ],
            }
            for set_no in range(1, set_count + 1)
        ]
    else:
        old_reps = old_item.get("reps")
        try:
            old_reps = int(old_reps) if old_reps is not None else None
        except Exception:
            old_reps = None
        resolved_reps = int(old_reps or default_reps or 12)
        item["reps"] = resolved_reps
        item["set_plan"] = [
            {
                "set_no": set_no,
                "rest_seconds_after": rest_seconds,
                "reps": [
                    {
                        "rep_no": rep_no,
                        "mode": mode,
                        "target_reps": max(1, resolved_reps + (rep_no - 2) * 2),
                        "target_duration_seconds": None,
                        "video_url": video_url,
                        "thumbnail_url": thumbnail_url,
                        **video_meta,
                    }
                    for rep_no in range(1, rep_variations + 1)
                ],
            }
            for set_no in range(1, set_count + 1)
        ]

    return item


def _exercise_difficulty_value(ex_obj: Exercise) -> str:
    return str(getattr(getattr(ex_obj, "difficulty", None), "value", getattr(ex_obj, "difficulty", "")) or "").lower()


async def _expand_day_exercises_for_duration(
    *,
    current_user: Any,
    plan: AiPlan,
    day_iso: str,
    workout_template: Dict[str, Any],
    target_duration_min: int,
) -> Dict[str, Any]:
    wt = dict(workout_template or {})
    exercises = list(wt.get("exercises") or [])
    if not exercises:
        wt["duration_min"] = int(target_duration_min)
        return wt

    current_duration = _coerce_int(wt.get("duration_min"), default=0, lo=0, hi=600)
    wt["duration_min"] = int(target_duration_min)
    # Scale exercise count by total day duration.
    # 6 min per exercise heuristic with bounds [2..8].
    target_count = max(2, min(8, int((int(target_duration_min) + 5) // 6)))
    if int(target_duration_min) < int(current_duration) and len(exercises) > target_count:
        wt["exercises"] = exercises[:target_count]
        return wt
    if len(exercises) >= target_count:
        wt["exercises"] = exercises
        return wt

    meta = dict(plan.created_from or {})
    inputs = _merge_prompt_with_profile(current_user, meta)
    language = _as_str(inputs.get("language") or getattr(current_user, "language", "en")) or "en"
    focus = str(wt.get("focus") or "").strip().lower()
    target_types = [focus] if focus else _goal_to_types(_as_str_list(inputs.get("goals")), _as_str_list(inputs.get("preferences")))

    injuries = set(_as_str_list(inputs.get("injuries")))
    equipment = set(_as_str_list(inputs.get("equipment")))
    focused_catalog = await _load_exercises_for_planning(
        injuries=injuries,
        equipment=equipment,
        target_types=set(target_types),
    )

    # Keep complexity consistent with user's level when possible.
    preferred_level = str(inputs.get("level") or "").strip().lower()
    catalog = list(focused_catalog)
    if preferred_level in {"beginner", "intermediate", "advanced"}:
        same_level = [ex for ex in focused_catalog if _exercise_difficulty_value(ex) == preferred_level]
        if same_level:
            catalog = same_level

    existing_ids = {str((item or {}).get("exercise_id") or "").strip() for item in exercises}
    existing_ids.discard("")
    if not catalog:
        return wt

    # Preserve style (sets/rest/reps) from existing day.
    style_item = dict(exercises[0] or {})
    rng = random.Random(f"expand:{current_user.id}:{day_iso}:{target_duration_min}:{len(exercises)}")
    shuffled = list(catalog)
    rng.shuffle(shuffled)

    for ex in shuffled:
        ex_id = str(ex.id)
        if ex_id in existing_ids:
            continue
        exercises.append(
            _build_replacement_exercise_item(
                old_item=style_item,
                ex_obj=ex,
                language=language,
            )
        )
        existing_ids.add(ex_id)
        if len(exercises) >= target_count:
            break

    # Fallback: widen pool to any workout type (still respecting injuries/equipment),
    # then relax level preference if still not enough unique exercises.
    if len(exercises) < target_count:
        broad_catalog = await _load_exercises_for_planning(
            injuries=injuries,
            equipment=equipment,
            target_types=set(),
        )

        fallback_pool = list(broad_catalog)
        if preferred_level in {"beginner", "intermediate", "advanced"}:
            same_level_broad = [ex for ex in broad_catalog if _exercise_difficulty_value(ex) == preferred_level]
            if same_level_broad:
                fallback_pool = same_level_broad

        rng2 = random.Random(f"expand:broad:{current_user.id}:{day_iso}:{target_duration_min}:{len(exercises)}")
        shuffled2 = list(fallback_pool)
        rng2.shuffle(shuffled2)
        for ex in shuffled2:
            ex_id = str(ex.id)
            if ex_id in existing_ids:
                continue
            exercises.append(
                _build_replacement_exercise_item(
                    old_item=style_item,
                    ex_obj=ex,
                    language=language,
                )
            )
            existing_ids.add(ex_id)
            if len(exercises) >= target_count:
                break

    if len(exercises) < target_count and preferred_level in {"beginner", "intermediate", "advanced"}:
        broad_catalog = await _load_exercises_for_planning(
            injuries=injuries,
            equipment=equipment,
            target_types=set(),
        )
        rng3 = random.Random(f"expand:relaxed-level:{current_user.id}:{day_iso}:{target_duration_min}:{len(exercises)}")
        shuffled3 = list(broad_catalog)
        rng3.shuffle(shuffled3)
        for ex in shuffled3:
            ex_id = str(ex.id)
            if ex_id in existing_ids:
                continue
            exercises.append(
                _build_replacement_exercise_item(
                    old_item=style_item,
                    ex_obj=ex,
                    language=language,
                )
            )
            existing_ids.add(ex_id)
            if len(exercises) >= target_count:
                break

    wt["exercises"] = exercises
    return wt


@router.get("/ai/plan/weeks", response_model=AiPlanWeeksOut)
async def ai_plan_weeks(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Unauthorized")

    plan = await get_active_plan(current_user.id)
    if not plan:
        raise HTTPException(404, "No active plan")
    language = _as_str(getattr(current_user, "language", "en")) or "en"

    weeks: list[AiPlanWeekOut] = []
    days = plan.days or []
    for w_idx in range(0, len(days), 7):
        chunk = days[w_idx : w_idx + 7]
        cards: list[AiPlanDayCardOut] = []
        for d in chunk:
            day_iso = str(getattr(d, "date", ""))
            weekday = _localized_weekday(day_iso, language)
            cards.append(
                AiPlanDayCardOut(
                    date=day_iso,
                    weekday=weekday,
                    type=str(getattr(d, "type", "recovery")),
                    type_label=_day_type_label(d, language),
                    title=_day_title(d, language),
                    duration_min=_day_duration(d),
                    intensity=_day_intensity(d),
                    focus=_day_focus(d),
                    focus_label=_day_focus_label(d, language),
                )
            )
        weeks.append(AiPlanWeekOut(week_index=(w_idx // 7) + 1, days=cards))

    logger.info(
        "AI plan weeks localized: user_id=%s language=%s first_title=%s",
        str(current_user.id),
        language,
        str(weeks[0].days[0].title) if weeks and weeks[0].days else "",
    )

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
    language = _as_str(getattr(current_user, "language", "en")) or "en"
    exercise_ids, exercise_codes = _extract_exercise_refs_from_template(getattr(day_obj, "workout_template", None))
    exercise_by_id, exercise_by_code = await _load_exercise_lookup(exercise_ids, exercise_codes)
    raw_workout_template = getattr(day_obj, "workout_template", None)
    wt = await _workout_template_for_output(
        raw_workout_template,
        language,
        str(getattr(day_obj, "type", "recovery")),
        week_idx=(idx // 7),
        exercise_by_id=exercise_by_id,
        exercise_by_code=exercise_by_code,
        plan_id=str(plan.id),
        day_iso=str(getattr(day_obj, "date", date)),
    )
    result = AiPlanDayDetailOut(
        plan_id=str(plan.id),
        date=str(getattr(day_obj, "date", date)),
        type=str(getattr(day_obj, "type", "recovery")),
        type_label=_day_type_label(day_obj, language),
        title=_day_title(day_obj, language),
        focus=_day_focus(day_obj),
        focus_label=_day_focus_label(day_obj, language),
        workout_template=wt,
    )
    logger.info(
        "AI plan day localized: user_id=%s language=%s plan_id=%s date=%s title=%s type_label=%s focus_label=%s",
        str(current_user.id),
        language,
        str(plan.id),
        date,
        str(result.title),
        str(result.type_label),
        str(result.focus_label or ""),
    )
    if wt and isinstance(wt.get("exercises"), list):
        _log_ai_localization(
            user_id=str(current_user.id),
            language=language,
            plan_id=str(plan.id),
            day_iso=str(result.date),
            day_title=str(result.title or ""),
            workout_title=str((wt or {}).get("title") or result.title or ""),
            type_label=str(result.type_label or ""),
            focus_label=str(result.focus_label or ""),
            raw_exercises=list((raw_workout_template or {}).get("exercises") or []) if isinstance(raw_workout_template, dict) else [],
            localized_exercises=list(wt.get("exercises") or []),
        )
    return result


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
            requested_duration = int(patch["duration_min"])
            wt = await _expand_day_exercises_for_duration(
                current_user=current_user,
                plan=plan,
                day_iso=date,
                workout_template=wt,
                target_duration_min=requested_duration,
            )
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
    language = _as_str(getattr(current_user, "language", "en")) or "en"
    exercise_ids, exercise_codes = _extract_exercise_refs_from_template(getattr(day_obj, "workout_template", None))
    exercise_by_id, exercise_by_code = await _load_exercise_lookup(exercise_ids, exercise_codes)
    return AiPlanDayDetailOut(
        plan_id=str(plan.id),
        date=str(getattr(day_obj, "date", date)),
        type=final_type,
        type_label=_day_type_label(day_obj, language),
        title=_day_title(day_obj, language),
        focus=_day_focus(day_obj),
        focus_label=_day_focus_label(day_obj, language),
        workout_template=await _workout_template_for_output(
            getattr(day_obj, "workout_template", None),
            language,
            final_type,
            week_idx=(idx // 7),
            exercise_by_id=exercise_by_id,
            exercise_by_code=exercise_by_code,
            plan_id=str(plan.id),
            day_iso=str(getattr(day_obj, "date", date)),
        ),
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
    language = _as_str(inputs.get("language") or getattr(current_user, "language", "en")) or "en"
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
                title=_humanize_ai_label(
                    template.get("title"),
                    language=language,
                    fallback=_localized_swap_title(language),
                ),
                duration_min=int(template.get("duration_min") or 35),
                intensity=str(template.get("intensity") or "moderate"),
                focus=str(template.get("focus") or focus),
                focus_label=str(template.get("focus_label") or _localized_type_label(str(template.get("focus") or focus), language)),
                type_label=str(template.get("type_label") or _localized_type_label("workout", language)),
                workout_template={
                    **template,
                    "title": _humanize_ai_label(
                        template.get("title"),
                        language=language,
                        fallback=_localized_swap_title(language),
                    ),
                },
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
        reason=_localized_swap_reason("focused", language),
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
            reason=_localized_swap_reason("fallback", language),
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
    language = _as_str(getattr(current_user, "language", "en")) or "en"
    exercise_ids, exercise_codes = _extract_exercise_refs_from_template(getattr(day_obj, "workout_template", None))
    exercise_by_id, exercise_by_code = await _load_exercise_lookup(exercise_ids, exercise_codes)
    return AiPlanDayDetailOut(
        plan_id=str(plan.id),
        date=str(getattr(day_obj, "date", date)),
        type=str(getattr(day_obj, "type", "workout")),
        type_label=_day_type_label(day_obj, language),
        title=_day_title(day_obj, language),
        focus=_day_focus(day_obj),
        focus_label=_day_focus_label(day_obj, language),
        workout_template=await _workout_template_for_output(
            getattr(day_obj, "workout_template", None),
            language,
            str(getattr(day_obj, "type", "workout")),
            week_idx=(idx // 7),
            exercise_by_id=exercise_by_id,
            exercise_by_code=exercise_by_code,
            plan_id=str(plan.id),
            day_iso=str(getattr(day_obj, "date", date)),
        ),
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
    language = _as_str(getattr(current_user, "language", "en")) or "en"
    exercise_ids, exercise_codes = _extract_exercise_refs_from_template(getattr(day_obj, "workout_template", None))
    exercise_by_id, exercise_by_code = await _load_exercise_lookup(exercise_ids, exercise_codes)

    return AiPlanDayDetailOut(
        plan_id=str(plan.id),
        date=str(getattr(day_obj, "date", date)),
        type="workout",
        type_label=_day_type_label(day_obj, language),
        title=_day_title(day_obj, language),
        focus=_day_focus(day_obj),
        focus_label=_day_focus_label(day_obj, language),
        workout_template=await _workout_template_for_output(
            getattr(day_obj, "workout_template", None),
            language,
            "workout",
            week_idx=(idx // 7),
            exercise_by_id=exercise_by_id,
            exercise_by_code=exercise_by_code,
            plan_id=str(plan.id),
            day_iso=str(getattr(day_obj, "date", date)),
        ),
    )
