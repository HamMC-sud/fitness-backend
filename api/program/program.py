from __future__ import annotations

import logging
import re
from typing import Optional, Any

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import AliasChoices, BaseModel, Field

from api.auth.config import decode_token, oauth2_scheme
from models import User
from models.enums import WorkoutType, Difficulty, Equipment, ExerciseMode
from models.content import Exercise
from utils.fitness_metrics import build_metrics_block, seconds_to_minutes
from utils.exercise_video_parser import ensure_existing_media_url, parse_exercise_video_from_url, resolve_local_media_path
from utils.workout_contract import apply_uniform_rest_seconds, summarize_sets_payload

router = APIRouter(tags=["content"])
logger = logging.getLogger("uvicorn.error")


async def _get_optional_user(token: Optional[str] = Depends(oauth2_scheme)) -> Optional[User]:
    if not token:
        return None
    decoded = decode_token(token)
    if not decoded or decoded.get("type") != "access":
        return None
    sub = decoded.get("sub")
    if isinstance(sub, dict):
        sub = sub.get("sub")
    try:
        return await User.get(PydanticObjectId(str(sub)))
    except Exception:
        return None

_DISCOVER_WORKTYPE_ALIASES: dict[str, tuple[WorkoutType, Optional[Equipment]]] = {
    # Direct labels
    "strength": (WorkoutType.strength, None),
    "cardio": (WorkoutType.cardio, None),
    "hiit": (WorkoutType.hiit, None),
    "yoga": (WorkoutType.yoga, None),
    "stretching": (WorkoutType.stretching, None),
    # Common grouped labels from client UI
    "cardio_hiit": (WorkoutType.cardio, None),
    "core": (WorkoutType.strength, None),
    "core_abs": (WorkoutType.strength, None),
    "core_and_abs": (WorkoutType.strength, None),
    "abs": (WorkoutType.strength, None),
    "bodyweight": (WorkoutType.strength, Equipment.home),
    "dumbbells": (WorkoutType.strength, Equipment.gym),
    "mobility": (WorkoutType.stretching, None),
    "relaxation": (WorkoutType.yoga, None),
    "legs_glutes": (WorkoutType.strength, None),
    "upper_body": (WorkoutType.strength, None),
    "arms": (WorkoutType.strength, None),
}

_DISCOVER_CATEGORY_CANONICAL: dict[str, str] = {
    "core_abs": "core",
    "core_and_abs": "core",
    "abs": "core",
}

_DISCOVER_CATEGORY_MUSCLE_GROUPS: dict[str, list[str]] = {
    "legs_glutes": [
        "quads",
        "quadriceps",
        "glutes",
        "hamstrings",
        "calves",
        "hips",
        "adductors",
        "abductors",
    ],
    "upper_body": [
        "chest",
        "back",
        "shoulders",
        "biceps",
        "triceps",
        "forearms",
        "upper_back",
        "lower_back",
        "lats",
        "traps",
        "rear_delts",
        "front_delts",
        "side_delts",
    ],
    "arms": [
        "biceps",
        "triceps",
        "forearms",
    ],
    "core": [
        "core",
        "abs",
        "abs_core",
        "obliques",
    ],
}

_DISCOVER_CATEGORY_EQUIPMENT_ALIASES: dict[str, list[str]] = {
    "bodyweight": [
        "home",
        "bodyweight",
        "no_equipment",
        "No equipment",
        "no equipment",
    ],
    "dumbbells": [
        "dumbbells",
        "Dumbbells",
    ],
}


def _normalize_discover_worktype(value: str) -> tuple[WorkoutType, Optional[Equipment]]:
    raw_input = str(value or "").strip()
    if not raw_input:
        raise ValueError("empty worktype")

    # Accept camelCase/PascalCase inputs from clients as well.
    raw = re.sub(r"(?<!^)(?=[A-Z])", "_", raw_input).lower()

    try:
        return WorkoutType.normalize(raw), None
    except Exception:
        pass

    token = (
        raw.replace("-", "_")
        .replace(" ", "_")
        .replace("&", "_")
        .replace("/", "_")
    )
    token = "_".join(part for part in token.split("_") if part)
    mapped = _DISCOVER_WORKTYPE_ALIASES.get(token)
    if mapped is None:
        raise ValueError(f"Unsupported worktype: {value}")
    return mapped


def _normalize_discover_category(value: str) -> tuple[str, WorkoutType, Optional[Equipment]]:
    raw_input = str(value or "").strip()
    if not raw_input:
        raise ValueError("empty worktype")

    raw = re.sub(r"(?<!^)(?=[A-Z])", "_", raw_input).lower()
    token = (
        raw.replace("-", "_")
        .replace(" ", "_")
        .replace("&", "_")
        .replace("/", "_")
    )
    token = "_".join(part for part in token.split("_") if part)

    workout_type, implied_equipment = _normalize_discover_worktype(value)
    canonical = _DISCOVER_CATEGORY_CANONICAL.get(token, token or workout_type.value)
    return canonical, workout_type, implied_equipment


def _workout_type_filter(worktype: Optional[Any]) -> Optional[dict[str, Any]]:
    if worktype is None:
        return None

    raw = getattr(worktype, "value", worktype)
    token = str(raw or "").strip().lower()
    if not token:
        return None

    if token in {"cardio", "hiit", "cardio_hiit"}:
        return {"workout_type": {"$in": ["cardio", "hiit"]}}

    return {"workout_type": token}


def _dedupe_keep_order(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def _build_discovery_filter_parts(
    requested_category: str,
    equipment: Optional[Equipment] = None,
) -> dict[str, Any]:
    category_token, workout_type, implied_equipment = _normalize_discover_category(requested_category)
    worktype_token = (
        category_token
        if category_token in {"cardio", "hiit", "cardio_hiit"}
        else workout_type.value
    )
    worktype_filter = _workout_type_filter(worktype_token)

    extra_filters: list[dict[str, Any]] = []

    muscle_group_values = _dedupe_keep_order(
        list(_DISCOVER_CATEGORY_MUSCLE_GROUPS.get(category_token, []))
    )
    if muscle_group_values:
        extra_filters.append({"muscle_groups": {"$in": muscle_group_values}})

    equipment_values: list[str] = []
    if equipment is not None:
        equipment_values = equipment_db_aliases(equipment)
    elif category_token in _DISCOVER_CATEGORY_EQUIPMENT_ALIASES:
        equipment_values = list(_DISCOVER_CATEGORY_EQUIPMENT_ALIASES[category_token])
    elif implied_equipment is not None:
        equipment_values = equipment_db_aliases(implied_equipment)

    equipment_values = _dedupe_keep_order(equipment_values)
    if equipment_values:
        extra_filters.append({"equipment": {"$in": equipment_values}})

    return {
        "requested_category": requested_category,
        "canonical_category": category_token,
        "resolved_workout_type": workout_type,
        "implied_equipment": implied_equipment,
        "worktype_filter": worktype_filter,
        "extra_filters": extra_filters,
    }


def _source_muscle_similarity_filter(source: Exercise) -> Optional[dict[str, Any]]:
    raw_groups = list(getattr(source, "muscle_groups", None) or [])

    groups: list[str] = []
    for group in raw_groups:
        value = str(group or "").strip()
        if not value:
            continue
        groups.append(value)

    ignored_generic_groups = {
        "core",
        "abs",
        "abs_core",
    }

    groups = [group for group in groups if group not in ignored_generic_groups]
    groups = _dedupe_keep_order(groups)
    if not groups:
        return None

    return {
        "muscle_groups": {
            "$in": groups,
        }
    }


def build_discovery_filters(
    requested_category: str,
    level: Optional[Difficulty] = None,
    equipment: Optional[Equipment] = None,
    status: str = "active",
) -> dict[str, Any]:
    parts = _build_discovery_filter_parts(requested_category, equipment=equipment)
    filters: list[Any] = [Exercise.status == status]
    if parts["worktype_filter"] is not None:
        filters.append(parts["worktype_filter"])
    if level is not None:
        filters.append(Exercise.difficulty == level)
    filters.extend(parts["extra_filters"])
    return {
        **parts,
        "filters": filters,
    }


class SimilarExerciseIn(BaseModel):
    exercise_id: PydanticObjectId = Field(
        validation_alias=AliasChoices("exercise_id", "id", "exerciseId"),
    )

    level: Optional[Difficulty] = None

    workouttype: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("workouttype", "workoutType", "workout_type"),
    )

    reps: int = Field(
        default=1,
        validation_alias=AliasChoices("reps", "targetReps", "target_reps", "repetitions"),
        ge=1,
        le=500,
    )

    target_duration_seconds: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("targetDurationSeconds", "target_duration_seconds", "duration_seconds"),
    )

    @classmethod
    def from_raw_payload(cls, payload: Any) -> "SimilarExerciseIn":
        try:
            normalized = cls.model_validate(payload or {})
            logger.info(
                "Similar workouts payload normalized: raw_payload=%s normalized_payload=%s exercise_id=%s level=%s workoutType=%s reps=%s targetDurationSeconds=%s",
                payload,
                normalized.model_dump(mode="json"),
                str(normalized.exercise_id),
                str(getattr(normalized.level, "value", normalized.level) if normalized.level is not None else ""),
                str(normalized.workouttype or ""),
                int(normalized.reps),
                normalized.target_duration_seconds,
            )
            return normalized
        except Exception as exc:
            logger.info(
                "Similar workouts payload rejected: raw_payload=%s rejection_reason=%s",
                payload,
                str(exc),
            )
            raise HTTPException(status_code=400, detail=f"Invalid similar workout payload: {str(exc)}") from exc


def equipment_db_aliases(equipment: Equipment) -> list[str]:
    if equipment == Equipment.home:
        return [
            Equipment.home.value,
            "Home",
            "No equipment",
            "no equipment",
            "bodyweight",
            "resistance_bands",
            "Resistance bands",
            "bands",
        ]
    return [
        Equipment.gym.value,
        "Gym",
        "Dumbbells",
        "dumbbells",
        "Pull-up bar",
        "pullup_bar",
        "pull_up_bar",
        "Barbell & Bench",
        "barbell_bench",
        "barbell_and_bench",
    ]


def _pick_i18n_text(i18n_obj: Any, lang: str = "en") -> str:
    data = jsonable_encoder(i18n_obj or {})
    value = data.get(lang)
    if isinstance(value, list):
        return str(value[0]) if value else ""
    if isinstance(value, str):
        return value
    fallback = data.get("en") or data.get("ru")
    if isinstance(fallback, list):
        return str(fallback[0]) if fallback else ""
    if isinstance(fallback, str):
        return fallback
    return ""


def _enum_value_str(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip()


def _worktype_label(value: Optional[str], lang: str) -> str:
    token = str(value or "").strip().lower()
    if str(lang or "en").lower().startswith("ru"):
        return {
            "strength": "Силовая тренировка",
            "cardio": "Кардио",
            "hiit": "HIIT",
            "stretching": "Растяжка",
            "yoga": "Йога",
            "mobility": "Мобильность",
        }.get(token, token)
    return {
        "strength": "Strength",
        "cardio": "Cardio",
        "hiit": "HIIT",
        "stretching": "Stretching",
        "yoga": "Yoga",
        "mobility": "Mobility",
    }.get(token, token)


def _resolve_set_plan(ex: Exercise, step_duration_default: int) -> list[dict[str, Any]]:
    defaults = getattr(ex, "defaults", None)
    mode_value = _enum_value_str(getattr(ex, "mode", ""))
    default_sets = int(getattr(defaults, "sets", 4) or 4) if defaults else 4
    default_reps = getattr(defaults, "reps", None) if defaults else None
    default_rest = int(getattr(defaults, "rest_seconds_after", 60) or 60) if defaults else 60
    default_duration = int(getattr(defaults, "duration_seconds", 0) or 0) if defaults else 0
    media = getattr(ex, "media", None)
    video_url = ensure_existing_media_url(getattr(media, "video_url", None) if media else None, kind="video")
    video_meta = parse_exercise_video_from_url(video_url)
    parsed_repetitions = video_meta.get("repetitions")
    parsed_duration_seconds = video_meta.get("duration_seconds")
    local_video_path = resolve_local_media_path(video_url) if video_url else None
    logger.info(
        "Local exercise video metadata: exercise_code=%s video_url=%s local_path=%s file_exists=%s parsed_video_mode=%s parsed_repetitions=%s parsed_duration_seconds=%s reason=%s",
        str(getattr(ex, "code", None) or ""),
        video_url,
        str(local_video_path) if local_video_path else None,
        bool(local_video_path and local_video_path.exists()),
        video_meta.get("video_mode"),
        parsed_repetitions,
        parsed_duration_seconds,
        "parsed" if video_meta.get("video_mode") else "metadata_null",
    )
    if mode_value == ExerciseMode.reps.value and default_reps is None and parsed_repetitions is not None:
        default_reps = int(parsed_repetitions)
    if default_duration <= 0 and parsed_duration_seconds is not None:
        default_duration = int(round(float(parsed_duration_seconds)))
    if default_duration <= 0:
        default_duration = int(step_duration_default or 0)

    raw_plan = list(getattr(defaults, "set_plan", []) or []) if defaults else []
    if raw_plan:
        normalized: list[dict[str, Any]] = []
        for item in raw_plan:
            set_no = int(getattr(item, "set_no", 0) or 0)
            if set_no <= 0:
                continue
            target_reps = getattr(item, "target_reps", None)
            target_seconds = getattr(item, "target_duration_seconds", None)
            rest_after = int(getattr(item, "rest_seconds_after", default_rest) or default_rest)
            normalized.append(
                {
                    "set_no": set_no,
                    "target_reps": int(target_reps) if target_reps is not None else None,
                    "target_duration_seconds": int(target_seconds) if target_seconds is not None else None,
                    "rest_seconds_after": rest_after,
                }
            )
        if normalized:
            return sorted(normalized, key=lambda x: x["set_no"])

    fallback: list[dict[str, Any]] = []
    for i in range(1, default_sets + 1):
        fallback.append(
            {
                "set_no": i,
                "target_reps": int(default_reps) if (mode_value == "reps" and default_reps is not None) else None,
                "target_duration_seconds": int(default_duration) if mode_value == "time" else None,
                "rest_seconds_after": default_rest,
            }
        )
    return fallback


def _build_sets_payload(ex: Exercise, set_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    media = getattr(ex, "media", None)
    mode_value = _enum_value_str(getattr(ex, "mode", ""))
    media_duration = int(getattr(media, "duration_seconds", 0) or 0) if media else 0
    video_url = ensure_existing_media_url(getattr(media, "video_url", None) if media else None, kind="video")
    thumbnail_url = ensure_existing_media_url(getattr(media, "thumbnail_url", None) if media else None, kind="thumbnail")
    video_meta = parse_exercise_video_from_url(video_url)
    defaults = getattr(ex, "defaults", None)

    # New column: defaults.sets_reps allows several reps objects per set.
    raw_sets_reps = list(getattr(defaults, "sets_reps", []) or []) if defaults else []
    if raw_sets_reps:
        out_new: list[dict[str, Any]] = []
        for set_item in sorted(raw_sets_reps, key=lambda x: int(getattr(x, "set_no", 0) or 0)):
            set_no = int(getattr(set_item, "set_no", 0) or 0)
            if set_no <= 0:
                continue
            rest_after = int(getattr(set_item, "rest_seconds_after", 60) or 60)
            reps_items = list(getattr(set_item, "reps", []) or [])
            reps_payload: list[dict[str, Any]] = []
            for rep_item in sorted(reps_items, key=lambda x: int(getattr(x, "rep_no", 0) or 0)):
                rep_no = int(getattr(rep_item, "rep_no", 0) or 0)
                if rep_no <= 0:
                    continue
                target_reps = getattr(rep_item, "target_reps", None)
                target_seconds = getattr(rep_item, "target_duration_seconds", None)
                if mode_value == ExerciseMode.time.value and target_seconds is None and media_duration > 0:
                    target_seconds = media_duration
                reps_payload.append(
                    {
                        "rep_no": rep_no,
                        "mode": mode_value,
                        "target": int(target_reps) if target_reps is not None else None,
                        "target_reps": int(target_reps) if target_reps is not None else None,
                        "duration_seconds": (
                            int(target_seconds)
                            if mode_value == ExerciseMode.time.value and target_seconds is not None
                            else None
                        ),
                        "target_duration_seconds": (
                            int(target_seconds)
                            if mode_value == ExerciseMode.time.value and target_seconds is not None
                            else None
                        ),
                        "video_url": video_url,
                        "thumbnail_url": thumbnail_url,
                        **video_meta,
                    }
                )
            if reps_payload:
                out_new.append(
                    {
                        "set_no": set_no,
                        "rest_seconds_after": rest_after,
                        "reps": reps_payload,
                    }
                )
        if out_new:
            return out_new

    out: list[dict[str, Any]] = []
    for p in set_plan:
        target_duration = p.get("target_duration_seconds")
        if mode_value == ExerciseMode.time.value and target_duration is None and media_duration > 0:
            target_duration = media_duration
        target_reps = p.get("target_reps")

        reps_payload: list[dict[str, Any]] = []
        if mode_value == "reps" and target_reps is not None:
            total_reps = int(target_reps)
            reps_payload = [
                {
                    "rep_no": 1,
                    "mode": mode_value,
                    "target": total_reps,
                    "target_reps": total_reps,
                    "duration_seconds": None,
                    "target_duration_seconds": None,
                    "video_url": video_url,
                    "thumbnail_url": thumbnail_url,
                    **video_meta,
                }
            ]
        else:
            total_seconds = int(target_duration) if target_duration is not None else None
            reps_payload = [
                {
                    "rep_no": 1,
                    "mode": mode_value,
                    "target": target_reps,
                    "target_reps": int(target_reps) if target_reps is not None else None,
                    "duration_seconds": total_seconds,
                    "target_duration_seconds": total_seconds,
                    "video_url": video_url,
                    "thumbnail_url": thumbnail_url,
                    **video_meta,
                }
            ]

        out.append(
            {
                "set_no": int(p["set_no"]),
                "rest_seconds_after": int(p["rest_seconds_after"]),
                "reps": reps_payload,
            }
        )
    return out


def _aggregate_sets_metrics(sets_payload: list[dict[str, Any]]) -> dict[str, int]:
    total_sets = len(sets_payload)
    total_intervals = 0
    total_reps_target = 0
    timed_intervals = 0
    timed_intervals_seconds = 0

    for s in sets_payload:
        reps = s.get("reps", []) or []
        total_intervals += len(reps)
        for rep in reps:
            target = rep.get("target")
            duration_seconds = rep.get("duration_seconds")

            if target is not None:
                total_reps_target += int(target)
            if duration_seconds is not None:
                timed_intervals += 1
                timed_intervals_seconds += int(duration_seconds)

    return {
        "total_sets": total_sets,
        "total_intervals": total_intervals,
        "total_reps_target": total_reps_target,
        "timed_intervals": timed_intervals,
        "timed_intervals_seconds": timed_intervals_seconds,
    }


def _build_set_summaries(sets_payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in sets_payload:
        reps_rows = list(s.get("reps", []) or [])
        total_target_reps = 0
        total_target_duration_seconds = 0
        mode_value = None
        for rep in reps_rows:
            if mode_value is None:
                mode_value = str(rep.get("mode") or "")
            target_reps = rep.get("target_reps", rep.get("target"))
            target_duration_seconds = (
                rep.get("target_duration_seconds", rep.get("duration_seconds"))
                if mode_value == ExerciseMode.time.value
                else None
            )
            if target_reps is not None:
                total_target_reps += int(target_reps)
            if target_duration_seconds is not None:
                total_target_duration_seconds += int(round(float(target_duration_seconds)))

        out.append(
            {
                "set_id": int(s.get("set_no", 0)),
                "reps_count": total_target_reps if total_target_reps > 0 else len(reps_rows),
                "target_reps": total_target_reps if total_target_reps > 0 else None,
                "duration_seconds": (
                    total_target_duration_seconds
                    if mode_value == ExerciseMode.time.value and total_target_duration_seconds > 0
                    else None
                ),
                "target_duration_seconds": (
                    total_target_duration_seconds
                    if mode_value == ExerciseMode.time.value and total_target_duration_seconds > 0
                    else None
                ),
                "rep_variations": len(reps_rows),
                "mode": mode_value,
                "rest_seconds_after": int(s.get("rest_seconds_after", 0) or 0),
            }
        )
    return out


def _exercise_base_duration_seconds(ex: Exercise) -> int:
    media = getattr(ex, "media", None)
    duration_seconds = int(getattr(media, "duration_seconds", 0) or 0) if media else 0
    if duration_seconds <= 0 and getattr(ex, "defaults", None):
        duration_seconds = int(getattr(ex.defaults, "duration_seconds", 0) or 0)
    return max(0, duration_seconds)


def _derive_exercise_workout_metrics(ex: Exercise) -> dict[str, Any]:
    mode_value = _enum_value_str(getattr(ex, "mode", ""))
    base_duration_seconds = _exercise_base_duration_seconds(ex)
    step_duration = int(base_duration_seconds) if mode_value == ExerciseMode.time.value else None

    set_plan = _resolve_set_plan(ex, step_duration or base_duration_seconds)
    sets_payload = _build_sets_payload(ex, set_plan)
    normalized = summarize_sets_payload(sets_payload, fallback_mode=mode_value)
    total_seconds = int(normalized["planned_total_seconds"] or base_duration_seconds or 0)

    return {
        "mode": mode_value,
        "sets_payload": normalized["sets_payload"],
        "set_summaries": normalized["set_summaries"],
        "total_sets": int(normalized["total_sets"]),
        "total_intervals": int(normalized["total_intervals"]),
        "total_reps": int(normalized["total_reps"]),
        "timed_intervals": int(normalized["timed_intervals"]),
        "timed_intervals_seconds": int(normalized["timed_intervals_seconds"]),
        "rest_between_sets_seconds": int(normalized["rest_between_sets_seconds"]),
        "rest_seconds_after_exercise": int(normalized["rest_seconds_after_exercise"]),
        "planned_total_seconds": int(total_seconds),
    }


def _build_round_robin_workout_set_plan(
    source_exercise: Exercise,
    serialized_exercises: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    exercise_sequence = list(serialized_exercises or [])
    if not exercise_sequence:
        return []

    sequence_codes = [str(item.get("exercise_code") or item.get("code") or "") for item in exercise_sequence]
    logger.info(
        "Workout details exercise sequence: source=%s sequence=%s",
        str(getattr(source_exercise, "code", None) or ""),
        sequence_codes,
    )

    set_plan: list[dict[str, Any]] = []
    max_sets = max(int(len(item.get("set_plan", []) or [])) for item in exercise_sequence)
    global_set_no = 1

    for round_idx in range(max_sets):
        for serialized_selected in exercise_sequence:
            exercise_set_plan = list(serialized_selected.get("set_plan", []) or [])
            if round_idx >= len(exercise_set_plan):
                continue

            set_row = dict(exercise_set_plan[round_idx])
            selected_video_url = serialized_selected.get("video_url")
            logger.info(
                "Workout details set exercise selected: set_no=%s exercise_code=%s video_url=%s",
                global_set_no,
                str(serialized_selected.get("exercise_code") or ""),
                selected_video_url,
            )

            set_payload = dict(set_row)
            set_payload.update(
                {
                    "set_no": global_set_no,
                    "exercise_id": serialized_selected.get("exercise_id"),
                    "exercise_code": serialized_selected.get("exercise_code"),
                    "code": serialized_selected.get("exercise_code"),
                    "name": serialized_selected.get("name"),
                    "title": serialized_selected.get("title"),
                    "name_i18n": serialized_selected.get("name_i18n"),
                    "description_i18n": serialized_selected.get("description_i18n"),
                    "localization": serialized_selected.get("localization"),
                    "video_url": selected_video_url,
                    "thumbnail_url": serialized_selected.get("thumbnail_url"),
                    "video_mode": serialized_selected.get("mode"),
                    "repetitions": sum(
                        int(rep.get("target_reps", rep.get("target")) or 0)
                        for rep in list(set_row.get("reps", []) or [])
                    ) or None,
                    "estimated_duration_seconds": sum(
                        int(rep.get("target_duration_seconds", rep.get("duration_seconds")) or 0)
                        for rep in list(set_row.get("reps", []) or [])
                    ) or None,
                }
            )

            reps_payload: list[dict[str, Any]] = []
            for rep in list(set_row.get("reps", []) or []):
                rep_payload = dict(rep)
                rep_payload.update(
                    {
                        "exercise_id": serialized_selected.get("exercise_id"),
                        "exercise_code": serialized_selected.get("exercise_code"),
                        "code": serialized_selected.get("exercise_code"),
                        "name": serialized_selected.get("name"),
                        "title": serialized_selected.get("title"),
                        "name_i18n": serialized_selected.get("name_i18n"),
                        "description_i18n": serialized_selected.get("description_i18n"),
                        "localization": serialized_selected.get("localization"),
                        "video_url": selected_video_url,
                        "thumbnail_url": serialized_selected.get("thumbnail_url"),
                        "video_mode": serialized_selected.get("mode"),
                        "repetitions": rep.get("target_reps", rep.get("target")),
                        "estimated_duration_seconds": rep.get(
                            "target_duration_seconds",
                            rep.get("duration_seconds"),
                        ),
                    }
                )
                reps_payload.append(rep_payload)

            set_payload["reps"] = reps_payload
            set_plan.append(set_payload)
            global_set_no += 1

    return set_plan


def _serialize_workout_exercise(ex: Exercise, rest_seconds_override: Optional[int] = None) -> dict[str, Any]:
    wm = _derive_exercise_workout_metrics(ex)
    sets_payload = apply_uniform_rest_seconds(wm["sets_payload"], rest_seconds_override)
    normalized = summarize_sets_payload(sets_payload, fallback_mode=_enum_value_str(getattr(ex, "mode", "")))
    media = getattr(ex, "media", None)
    mode_value = _enum_value_str(getattr(ex, "mode", ""))
    worktype_value = (
        str(ex.workout_type[0].value if hasattr(ex.workout_type[0], "value") else ex.workout_type[0])
        if getattr(ex, "workout_type", None)
        else None
    )
    title_i18n = {
        "ru": _pick_i18n_text(getattr(ex, "name", None), "ru"),
        "en": _pick_i18n_text(getattr(ex, "name", None), "en"),
    }
    description_i18n = {
        "ru": _pick_i18n_text(getattr(ex, "description", None), "ru"),
        "en": _pick_i18n_text(getattr(ex, "description", None), "en"),
    }
    instructions_payload = [
        {
            "step": int(getattr(item, "step", 0) or 0),
            "title": {
                "ru": _pick_i18n_text(getattr(item, "title", None), "ru"),
                "en": _pick_i18n_text(getattr(item, "title", None), "en"),
            },
            "description": {
                "ru": _pick_i18n_text(getattr(item, "description", None), "ru"),
                "en": _pick_i18n_text(getattr(item, "description", None), "en"),
            },
        }
        for item in list(getattr(ex, "instructions", None) or [])
    ]
    common_mistakes_payload = [
        {
            "title": {
                "ru": _pick_i18n_text(getattr(item, "title", None), "ru"),
                "en": _pick_i18n_text(getattr(item, "title", None), "en"),
            },
            "description": {
                "ru": _pick_i18n_text(getattr(item, "description", None), "ru"),
                "en": _pick_i18n_text(getattr(item, "description", None), "en"),
            },
        }
        for item in list(getattr(ex, "common_mistakes", None) or [])
    ]
    return {
        "exercise_id": str(ex.id),
        "exercise_code": getattr(ex, "code", None),
        "name": title_i18n["en"],
        "title": title_i18n,
        "name_i18n": title_i18n,
        "description": description_i18n["en"],
        "description_i18n": description_i18n,
        "localization": {
            "title": title_i18n,
            "description": description_i18n,
        },
        "mode": mode_value,
        "workout_type": worktype_value,
        "worktype_label": {
            "ru": _worktype_label(worktype_value, "ru"),
            "en": _worktype_label(worktype_value, "en"),
        },
        "instructions": instructions_payload,
        "common_mistakes": common_mistakes_payload,
        "level": str(ex.difficulty.value if hasattr(ex.difficulty, "value") else ex.difficulty),
        "thumbnail_url": ensure_existing_media_url(
            getattr(media, "thumbnail_url", None) if media else None,
            kind="thumbnail",
        ),
        "video_url": ensure_existing_media_url(
            getattr(media, "video_url", None) if media else None,
            kind="video",
        ),
        "set_plan": normalized["sets_payload"],
        "steps": normalized["sets_payload"],
        "sets": normalized["set_summaries"],
        "total_sets": int(normalized["total_sets"]),
        "total_reps": int(normalized["total_reps"]),
        "total_seconds": int(normalized["planned_total_seconds"]),
        "total_minutes": int(normalized["total_minutes"]),
        "rest_between_sets_seconds": int(normalized["rest_between_sets_seconds"]),
        "rest_seconds_after_exercise": int(normalized["rest_seconds_after_exercise"]),
        "rest_seconds": int(rest_seconds_override if rest_seconds_override is not None else normalized["rest_seconds_after_exercise"]),
    }


@router.get("/discover/worktypes/{worktype}")
async def discover_worktype_details(
    worktype: str,
    level: Optional[Difficulty] = None,
    equipment: Optional[Equipment] = None,
    status: str = "active",
    current_user: Optional[User] = Depends(_get_optional_user),
):
    try:
        discovery = build_discovery_filters(
            requested_category=worktype,
            level=level,
            equipment=equipment,
            status=status,
        )
    except ValueError:
        supported = sorted(_DISCOVER_WORKTYPE_ALIASES.keys())
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Invalid worktype",
                "received": worktype,
                "supported_labels": supported,
            },
        )
    wtype = discovery["resolved_workout_type"]
    worktype_filter = discovery["worktype_filter"]
    extra_filters = discovery["extra_filters"]

    exercises = await Exercise.find(*discovery["filters"]).sort("-created_at").to_list()
    logger.info(
        "Workout discovery query: requested_workout_type=%s effective_workout_type_filter=%s extra_filters=%s result_count=%s",
        worktype,
        worktype_filter,
        extra_filters,
        len(exercises),
    )
    if not exercises:
        return {
            "worktype": wtype.value,
            "worktype_label": {
                "ru": _worktype_label(wtype.value, "ru"),
                "en": _worktype_label(wtype.value, "en"),
            },
            "category_image": None,
            "totals": {
                "workouts": 0,
                "total_minutes": 0,
                "total_calories": 0.0,
            },
            "items": [],
        }

    items: list[dict[str, Any]] = []
    sum_seconds = 0
    sum_calories = 0.0
    category_image = None

    rest_override = int(getattr(current_user, "training_rest_seconds", 0) or 0) or None
    for ex in exercises:
        serialized = _serialize_workout_exercise(ex, rest_seconds_override=rest_override)
        media = getattr(ex, "media", None)
        total_seconds = int(serialized["total_seconds"])
        total_calories = 0.0
        if getattr(ex, "calories_per_minute", None):
            total_calories = float(ex.calories_per_minute or 0) * (total_seconds / 60.0)

        cover_image = getattr(media, "thumbnail_url", None) if media else None
        sum_seconds += total_seconds
        sum_calories += total_calories
        if not category_image and cover_image:
            category_image = cover_image

        items.append(
            {
                "id": str(ex.id),
                "title": {
                    "ru": _pick_i18n_text(getattr(ex, "name", None), "ru"),
                    "en": _pick_i18n_text(getattr(ex, "name", None), "en"),
                },
                "description": {
                    "ru": _pick_i18n_text(getattr(ex, "description", None), "ru"),
                    "en": _pick_i18n_text(getattr(ex, "description", None), "en"),
                },
                "level": str(ex.difficulty.value if hasattr(ex.difficulty, "value") else ex.difficulty),
                "worktype": wtype.value,
                "worktype_label": {
                    "ru": _worktype_label(wtype.value, "ru"),
                    "en": _worktype_label(wtype.value, "en"),
                },
                "cover_image": cover_image,
                "exercise_count": 1,
                "total_seconds": total_seconds,
                "total_minutes": int(serialized["total_minutes"]),
                "sets": int(serialized["total_sets"]),
                "intervals": int(sum(len(set_row.get("reps") or []) for set_row in serialized["set_plan"])),
                "reps": int(serialized["total_reps"]),
                "timed_intervals_seconds": int(sum(
                    int(rep.get("duration_seconds") or 0)
                    for set_row in serialized["set_plan"]
                    for rep in list(set_row.get("reps") or [])
                    if str(rep.get("mode") or "") == ExerciseMode.time.value
                )),
                "total_calories": round(total_calories, 1),
                "metrics": build_metrics_block(
                    total_seconds=total_seconds,
                    total_calories=round(total_calories, 1),
                    total_sets=int(serialized["total_sets"]),
                    total_reps=int(serialized["total_reps"]),
                    total_intervals=int(sum(len(set_row.get("reps") or []) for set_row in serialized["set_plan"])),
                    timed_intervals=int(sum(
                        1
                        for set_row in serialized["set_plan"]
                        for rep in list(set_row.get("reps") or [])
                        if str(rep.get("mode") or "") == ExerciseMode.time.value
                    )),
                    timed_intervals_seconds=int(sum(
                        int(rep.get("duration_seconds") or 0)
                        for set_row in serialized["set_plan"]
                        for rep in list(set_row.get("reps") or [])
                        if str(rep.get("mode") or "") == ExerciseMode.time.value
                    )),
                    rest_between_sets_seconds=int(serialized["rest_between_sets_seconds"]),
                ),
                "exercise_id": str(ex.id),
                "exercise_code": getattr(ex, "code", None),
                "set_plan": serialized["set_plan"],
            }
        )

    return {
        "worktype": wtype.value,
        "worktype_label": {
            "ru": _worktype_label(wtype.value, "ru"),
            "en": _worktype_label(wtype.value, "en"),
        },
        "category_image": category_image,
        "totals": {
            "workouts": len(items),
            "total_minutes": seconds_to_minutes(sum_seconds),
            "total_calories": round(sum_calories, 1),
        },
        "items": items,
    }


@router.get("/discover/workouts/{template_id}")
async def discover_workout_details(template_id: PydanticObjectId, current_user: Optional[User] = Depends(_get_optional_user)):
    ex = await Exercise.get(template_id)
    if not ex:
        raise HTTPException(status_code=404, detail="Workout template not found")
    if ex.status != "active":
        raise HTTPException(status_code=404, detail="Workout template is inactive")

    media = getattr(ex, "media", None)
    rest_override = int(getattr(current_user, "training_rest_seconds", 0) or 0) or None
    serialized_exercises = [_serialize_workout_exercise(ex, rest_seconds_override=rest_override)]
    workout_set_plan = _build_round_robin_workout_set_plan(ex, serialized_exercises)

    total_sets = sum(int(item.get("total_sets", 0) or 0) for item in serialized_exercises)
    total_reps = sum(int(item.get("total_reps", 0) or 0) for item in serialized_exercises)
    duration_seconds = sum(int(item.get("total_seconds", 0) or 0) for item in serialized_exercises)

    calories = 0.0
    if getattr(ex, "calories_per_minute", None):
        calories = float(ex.calories_per_minute or 0) * (duration_seconds / 60.0)

    set_summaries = _build_set_summaries(workout_set_plan)
    ai_tip = getattr(ex, "ai_technique", None)
    ai_mistakes = getattr(ex, "ai_mistakes", None)
    instructions_payload = [
        {
            "step": int(getattr(item, "step", 0) or 0),
            "title": {
                "ru": _pick_i18n_text(getattr(item, "title", None), "ru"),
                "en": _pick_i18n_text(getattr(item, "title", None), "en"),
            },
            "description": {
                "ru": _pick_i18n_text(getattr(item, "description", None), "ru"),
                "en": _pick_i18n_text(getattr(item, "description", None), "en"),
            },
        }
        for item in list(getattr(ex, "instructions", None) or [])
    ]
    common_mistakes_payload = [
        {
            "title": {
                "ru": _pick_i18n_text(getattr(item, "title", None), "ru"),
                "en": _pick_i18n_text(getattr(item, "title", None), "en"),
            },
            "description": {
                "ru": _pick_i18n_text(getattr(item, "description", None), "ru"),
                "en": _pick_i18n_text(getattr(item, "description", None), "en"),
            },
        }
        for item in list(getattr(ex, "common_mistakes", None) or [])
    ]
    logger.info(
        "Workout details payload: exercise_id=%s code=%s mode=%s total_sets=%s total_reps=%s total_seconds=%s exercises_count=%s set_plan=%s",
        str(ex.id),
        str(getattr(ex, "code", None) or ""),
        _enum_value_str(getattr(ex, "mode", "")),
        int(total_sets),
        int(total_reps),
        int(duration_seconds),
        len(serialized_exercises),
        workout_set_plan,
    )

    return {
        "id": str(ex.id),
        "localization": {
            "title": {
                "ru": _pick_i18n_text(getattr(ex, "name", None), "ru"),
                "en": _pick_i18n_text(getattr(ex, "name", None), "en"),
            },
            "description": {
                "ru": _pick_i18n_text(getattr(ex, "description", None), "ru"),
                "en": _pick_i18n_text(getattr(ex, "description", None), "en"),
            },
        },
        "title": {
            "ru": _pick_i18n_text(getattr(ex, "name", None), "ru"),
            "en": _pick_i18n_text(getattr(ex, "name", None), "en"),
        },
        "image": getattr(media, "thumbnail_url", None) if media else None,
        "ai_coach_tip": {
            "ru": _pick_i18n_text(ai_tip, "ru"),
            "en": _pick_i18n_text(ai_tip, "en"),
        },
        "ai_common_mistakes": {
            "ru": _pick_i18n_text(ai_mistakes, "ru"),
            "en": _pick_i18n_text(ai_mistakes, "en"),
        },
        "instructions": instructions_payload,
        "common_mistakes": common_mistakes_payload,
        "worktype": (
            str(ex.workout_type[0].value if hasattr(ex.workout_type[0], "value") else ex.workout_type[0])
            if getattr(ex, "workout_type", None)
            else None
        ),
        "worktype_label": {
            "ru": _worktype_label(
                str(ex.workout_type[0].value if hasattr(ex.workout_type[0], "value") else ex.workout_type[0])
                if getattr(ex, "workout_type", None)
                else None,
                "ru",
            ),
            "en": _worktype_label(
                str(ex.workout_type[0].value if hasattr(ex.workout_type[0], "value") else ex.workout_type[0])
                if getattr(ex, "workout_type", None)
                else None,
                "en",
            ),
        },
        "level": str(ex.difficulty.value if hasattr(ex.difficulty, "value") else ex.difficulty),
        "exercise_count": len(serialized_exercises),
        "exercises": serialized_exercises,
        "items": serialized_exercises,
        "totals": {
            "sets": int(total_sets),
            "reps": int(total_reps),
            "intervals": sum(int(sum(len(set_row.get("reps") or []) for set_row in item.get("set_plan", []))) for item in serialized_exercises),
            "timed_intervals": sum(
                int(sum(
                    1
                    for set_row in item.get("set_plan", [])
                    for rep in list(set_row.get("reps") or [])
                    if str(rep.get("mode") or "") == ExerciseMode.time.value
                ))
                for item in serialized_exercises
            ),
            "timed_intervals_seconds": sum(
                int(sum(
                    int(rep.get("duration_seconds") or 0)
                    for set_row in item.get("set_plan", [])
                    for rep in list(set_row.get("reps") or [])
                    if str(rep.get("mode") or "") == ExerciseMode.time.value
                ))
                for item in serialized_exercises
            ),
            "rest_between_sets_seconds": sum(int(item.get("rest_between_sets_seconds", 0) or 0) for item in serialized_exercises),
            "rest_seconds_after_exercise": sum(int(item.get("rest_seconds_after_exercise", 0) or 0) for item in serialized_exercises),
            "total_seconds": duration_seconds,
            "total_minutes": max(1, (duration_seconds + 59) // 60) if duration_seconds > 0 else 0,
            "total_calories": round(calories, 1),
        },
        "metrics": build_metrics_block(
            total_seconds=duration_seconds,
            total_calories=round(calories, 1),
            total_sets=int(total_sets),
            total_reps=int(total_reps),
            total_intervals=sum(int(sum(len(set_row.get("reps") or []) for set_row in item.get("set_plan", []))) for item in serialized_exercises),
            timed_intervals=sum(
                int(sum(
                    1
                    for set_row in item.get("set_plan", [])
                    for rep in list(set_row.get("reps") or [])
                    if str(rep.get("mode") or "") == ExerciseMode.time.value
                ))
                for item in serialized_exercises
            ),
            timed_intervals_seconds=sum(
                int(sum(
                    int(rep.get("duration_seconds") or 0)
                    for set_row in item.get("set_plan", [])
                    for rep in list(set_row.get("reps") or [])
                    if str(rep.get("mode") or "") == ExerciseMode.time.value
                ))
                for item in serialized_exercises
            ),
            rest_between_sets_seconds=sum(int(item.get("rest_between_sets_seconds", 0) or 0) for item in serialized_exercises),
        ),
        "set_plan": workout_set_plan,
        "steps": workout_set_plan,
        "sets": set_summaries,
    }


async def _discover_similar_workouts(
    exercise_id: PydanticObjectId,
    payload: SimilarExerciseIn,
):
    source = await Exercise.get(exercise_id)

    if not source or source.status != "active":
        logger.info(
            "Similar workouts rejected: exercise_id=%s reason=source_not_found",
            str(exercise_id),
        )
        raise HTTPException(status_code=404, detail="Source exercise not found")

    source_mode = getattr(source, "mode", None)
    source_muscle_filter = _source_muscle_similarity_filter(source)

    source_worktypes = WorkoutType.normalize_many(
        getattr(source, "workout_type", None) or []
    )
    source_worktype = source_worktypes[0].value if source_worktypes else None

    requested_worktype_raw = str(
        getattr(payload, "workouttype", None) or ""
    ).strip()

    generic_worktype_tokens = {
        "",
        "workout",
        "training",
        "session",
        "exercise",
    }

    extra_filters: list[dict[str, Any]] = []
    if requested_worktype_raw and requested_worktype_raw.lower() not in generic_worktype_tokens:
        try:
            discovery_parts = _build_discovery_filter_parts(requested_worktype_raw)
            source_worktype = discovery_parts["resolved_workout_type"].value
            extra_filters = list(discovery_parts["extra_filters"])
            worktype_filter = discovery_parts["worktype_filter"]
        except ValueError as exc:
            logger.info(
                "Similar workouts rejected: exercise_id=%s reason=unsupported_worktype raw_workouttype=%s",
                str(exercise_id),
                requested_worktype_raw,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        worktype_filter = _workout_type_filter(source_worktype)

    source_level = payload.level or getattr(source, "difficulty", None)

    if source_level is None:
        raise HTTPException(
            status_code=400,
            detail="Unable to resolve source exercise difficulty",
        )
    limit = max(1, min(int(getattr(payload, "reps", 3) or 3), 12))

    normalized_duration = getattr(payload, "target_duration_seconds", None)

    logger.info(
        "Similar workouts normalized: exercise_id=%s level=%s workoutType=%s requestedWorkoutType=%s limit=%s targetDurationSeconds=%s source_mode=%s",
        str(exercise_id),
        str(getattr(source_level, "value", source_level)),
        source_worktype,
        requested_worktype_raw or None,
        limit,
        normalized_duration,
        str(getattr(source_mode, "value", source_mode) or ""),
    )
    logger.info(
        "Similar workouts source filters: exercise_id=%s source_code=%s source_muscle_filter=%s category_extra_filters=%s",
        str(exercise_id),
        str(getattr(source, "code", None) or ""),
        source_muscle_filter,
        extra_filters,
    )

    base_filters: list[Any] = [
        Exercise.status == "active",
        {"_id": {"$ne": source.id}},
    ]

    query_steps: list[tuple[str, list[Any]]] = []

    strict_filters = [*base_filters, Exercise.difficulty == source_level]

    if worktype_filter:
        strict_filters.append(worktype_filter)
    strict_filters.extend(extra_filters)
    if source_muscle_filter:
        strict_filters.append(source_muscle_filter)

    if source_mode:
        strict_filters.append(Exercise.mode == source_mode)

    query_steps.append(("strict_level_worktype_mode", strict_filters))

    same_worktype_mode = [*base_filters]

    if worktype_filter:
        same_worktype_mode.append(worktype_filter)
    same_worktype_mode.extend(extra_filters)
    if source_muscle_filter:
        same_worktype_mode.append(source_muscle_filter)

    if source_mode:
        same_worktype_mode.append(Exercise.mode == source_mode)

    query_steps.append(("same_worktype_mode", same_worktype_mode))

    if worktype_filter:
        same_worktype = [*base_filters, worktype_filter, *extra_filters]
        if source_muscle_filter:
            same_worktype.append(source_muscle_filter)
        query_steps.append(("same_worktype", same_worktype))

    if source_muscle_filter:
        same_source_muscles = [*base_filters, source_muscle_filter]
        query_steps.append(("same_source_muscles", same_source_muscles))

    if source_mode:
        same_mode = [*base_filters, Exercise.mode == source_mode]
        query_steps.append(("same_mode", same_mode))

    query_steps.append(("any_active", base_filters))

    items: list[Exercise] = []
    used_step = ""

    for step_name, step_filters in query_steps:
        items = (
            await Exercise.find(*step_filters)
            .sort("-created_at")
            .limit(limit)
            .to_list()
        )

        logger.info(
            "Similar workouts query: exercise_id=%s step=%s requested_workout_type=%s effective_workout_type_filter=%s extra_filters=%s result_count=%s",
            str(exercise_id),
            step_name,
            requested_worktype_raw or source_worktype,
            worktype_filter,
            extra_filters,
            len(items),
        )

        if items:
            used_step = step_name
            break

    out_items: list[dict[str, Any]] = []

    for ex in items:
        media = getattr(ex, "media", None)

        duration_seconds = (
            int(getattr(media, "duration_seconds", 0) or 0)
            if media
            else 0
        )

        if duration_seconds <= 0:
            duration_seconds = int(
                getattr(getattr(ex, "defaults", None), "duration_seconds", 0) or 0
            )

        out_items.append(
            {
                "exercise_id": str(ex.id),
                "exercise_code": getattr(ex, "code", None),
                "video_url": ensure_existing_media_url(
                    getattr(media, "video_url", None) if media else None,
                    kind="video",
                ),
                "thumbnail_url": ensure_existing_media_url(
                    getattr(media, "thumbnail_url", None) if media else None,
                    kind="thumbnail",
                ),
                "duration_seconds": duration_seconds,
                "workout_type": (
                    str(ex.workout_type[0].value if hasattr(ex.workout_type[0], "value") else ex.workout_type[0])
                    if getattr(ex, "workout_type", None)
                    else None
                ),
                "level": str(
                    ex.difficulty.value
                    if hasattr(ex.difficulty, "value")
                    else ex.difficulty
                ),
                "mode": str(
                    ex.mode.value
                    if hasattr(ex.mode, "value")
                    else ex.mode
                ),
            }
        )

    logger.info(
        "Similar workouts response: exercise_id=%s used_step=%s returned_count=%s",
        str(exercise_id),
        used_step,
        len(out_items),
    )

    return out_items

@router.post("/discover/workouts/similar")
async def discover_similar_workouts_by_body(payload: dict[str, Any]):
    logger.info("Similar workouts raw payload: %s", payload)
    normalized = SimilarExerciseIn.from_raw_payload(payload)
    return await _discover_similar_workouts(normalized.exercise_id, normalized)
