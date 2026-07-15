from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from api.ai.yandex_client import yandex_completion

logger = logging.getLogger("uvicorn.error")

AiChatDecisionType = Literal["normal", "show_generate_button", "generate_plan_now"]

ALLOWED_META_KEYS = {
    "total_days",
    "workouts_per_week",
    "days_per_week",
    "duration_min",
    "rest_days",
    "rest_days_strict",
    "goals",
    "body_focus",
    "workout_types",
    "equipment",
    "injuries",
    "level",
    "intensity",
    "location",
    "style",
    "notes",
}

LIST_META_KEYS = {
    "goals",
    "body_focus",
    "workout_types",
    "equipment",
    "injuries",
}

INT_META_LIMITS = {
    "total_days": (1, 365),
    "workouts_per_week": (1, 7),
    "days_per_week": (1, 7),
    "duration_min": (10, 120),
    "rest_days": (0, 364),
}

PERMISSION_MARKERS = [
    "можешь",
    "можно",
    "можешь ли",
    "получится ли",
    "сделаешь ли",
    "can you",
    "could you",
    "would you",
    "is it possible",
    "can we",
    "mozhesh",
    "mojno",
    "mojesh",
    "mojno li",
    "smojesh",
]

PLAN_MARKERS = [
    "план",
    "программа",
    "тренировка",
    "тренировочный план",
    "plan",
    "program",
    "workout",
    "training plan",
    "programma",
    "trenirovka",
    "trenirovochniy plan",
]

GENERATION_MARKERS = [
    "сгенерируй",
    "составь",
    "создай",
    "сделай",
    "тогда сгенерируй",
    "давай сгенерируй",
    "generate",
    "generate it",
    "create plan",
    "build plan",
    "make plan",
    "do it",
    "sgeneriruy",
    "sgenerirovat",
    "sostav",
    "sozday",
    "sdelay",
    "togda sgeneriruy",
]

NON_EXECUTION_PLAN_MARKERS = [
    "пример",
    "example",
    "sample",
    "покажи",
    "show",
    "обсуд",
    "discuss",
    "идея",
    "idea",
    "вариант",
    "option",
    "какой план",
    "what plan",
]


class AiChatDecision(BaseModel):
    assistant_text: str = ""
    type: AiChatDecisionType = "normal"
    label: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)
    fallback_used: bool = False


def _is_russian(language: str) -> bool:
    return str(language or "").strip().lower().startswith("ru")


def _clean_text(text: Any, max_len: int = 500) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())[:max_len]


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None

    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None

    try:
        obj = json.loads(raw[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _normalize_decision_type(value: Any) -> AiChatDecisionType:
    token = str(value or "").strip().lower()
    if token == "show_generate_button":
        return "show_generate_button"
    if token == "generate_plan_now":
        return "generate_plan_now"
    return "normal"


def _recent_context_text(history: list[dict[str, str]], limit: int = 4) -> str:
    rows = history[-limit:] if history else []
    return " ".join(_clean_text((item or {}).get("text"), 500) for item in rows).strip()


def _contains_any(text: str, markers: list[str]) -> bool:
    lowered = str(text or "").lower().replace("ё", "е")
    return any(marker in lowered for marker in markers)


def _fitness_context_present(text: str) -> bool:
    lowered = str(text or "").lower()
    tokens = [
        "plan",
        "workout",
        "training",
        "fitness",
        "fat",
        "weight",
        "muscle",
        "belly",
        "lose",
        "pohud",
        "press",
        "zhir",
        "tren",
        "план",
        "трен",
        "жир",
        "живот",
        "похуд",
        "вес",
        "пресс",
        "упраж",
    ]
    return any(token in lowered for token in tokens)


def _has_plan_or_generation_marker(text: str) -> bool:
    lowered = str(text or "").lower()
    return _contains_any(lowered, PLAN_MARKERS + GENERATION_MARKERS)


def _has_explicit_plan_generation_command(text: str) -> bool:
    lowered = str(text or "").lower().replace("ё", "е")
    if _contains_any(lowered, NON_EXECUTION_PLAN_MARKERS):
        return False
    if _contains_any(lowered, PERMISSION_MARKERS):
        return False
    direct_command_patterns = [
        r"\b(?:сгенерируй|составь|создай|сделай)\b.{0,40}\b(?:план|программ|трениров)\b",
        r"\b(?:sgeneriruy|sgenerirovat|sostav|sozday|sdelay)\b.{0,40}\b(?:plan|programma|trenirov)\b",
        r"\b(?:generate|create|build|make)\b.{0,40}\b(?:plan|program|workout|training plan)\b",
    ]
    return any(re.search(pattern, lowered) for pattern in direct_command_patterns)


def _has_contextual_generation_command(text: str) -> bool:
    lowered = str(text or "").lower().replace("ё", "е")
    if _contains_any(lowered, NON_EXECUTION_PLAN_MARKERS):
        return False
    return _contains_any(
        lowered,
        [
            "тогда сгенерируй",
            "давай сгенерируй",
            "generate it",
            "do it",
            "then generate",
            "togda sgeneriruy",
        ],
    )


def _has_plan_button_request(text: str) -> bool:
    lowered = str(text or "").lower().replace("ё", "е")
    return _contains_any(lowered, PERMISSION_MARKERS) and _has_plan_or_generation_marker(lowered)


def _history_has_fitness_goal_context(history: list[dict[str, str]]) -> bool:
    user_rows = [
        str((item or {}).get("text") or "")
        for item in history
        if str((item or {}).get("role") or "").lower() == "user"
    ]
    joined = " ".join(user_rows[-4:]).lower()
    return _fitness_context_present(joined)


def _button_text(language: str) -> str:
    if _is_russian(language):
        return "Могу подготовить план. Нажми кнопку, и я его сгенерирую."
    return "I can prepare a plan. Tap the button and I'll generate it."


def _button_label(language: str) -> str:
    return "Сгенерировать план" if _is_russian(language) else "Generate plan"


def _generating_text(language: str) -> str:
    return "Генерирую план." if _is_russian(language) else "Generating your plan."


def _fallback_decision(text: str, history: list[dict[str, str]], language: str) -> Optional[AiChatDecision]:
    lowered = re.sub(r"\s+", " ", str(text or "").strip().lower()).replace("ё", "е")
    history_has_context = _history_has_fitness_goal_context(history)

    if _has_plan_button_request(lowered) and history_has_context:
        return AiChatDecision(
            assistant_text=_button_text(language),
            type="show_generate_button",
            label=_button_label(language),
            meta={},
            fallback_used=True,
        )

    if _has_explicit_plan_generation_command(lowered):
        return AiChatDecision(
            assistant_text=_generating_text(language),
            type="generate_plan_now",
            label=None,
            meta={},
            fallback_used=True,
        )

    if _has_contextual_generation_command(lowered) and history_has_context:
        return AiChatDecision(
            assistant_text=_generating_text(language),
            type="generate_plan_now",
            label=None,
            meta={},
            fallback_used=True,
        )

    return None


def _postprocess_decision(
    decision: AiChatDecision,
    *,
    text: str,
    history: list[dict[str, str]],
    language: str,
) -> AiChatDecision:
    current_text = _clean_text(text, 500).lower().replace("ё", "е")
    has_explicit = _has_explicit_plan_generation_command(current_text)
    has_contextual = _has_contextual_generation_command(current_text)
    has_button_request = _has_plan_button_request(current_text)
    has_markers = _has_plan_or_generation_marker(current_text)
    history_has_context = _history_has_fitness_goal_context(history)
    permission_markers_present = _contains_any(current_text, PERMISSION_MARKERS)

    if permission_markers_present and (has_markers or history_has_context):
        decision.type = "show_generate_button"
        decision.assistant_text = decision.assistant_text or _button_text(language)
        decision.label = decision.label or _button_label(language)
        return decision

    if has_explicit:
        decision.type = "generate_plan_now"
        decision.assistant_text = decision.assistant_text or _generating_text(language)
        decision.label = None
        return decision

    if has_contextual and history_has_context:
        decision.type = "generate_plan_now"
        decision.assistant_text = decision.assistant_text or _generating_text(language)
        decision.label = None
        return decision

    if has_button_request and (has_markers or history_has_context):
        decision.type = "show_generate_button"
        decision.assistant_text = decision.assistant_text or _button_text(language)
        decision.label = decision.label or _button_label(language)
        return decision

    if decision.type == "show_generate_button" and not (has_button_request or (permission_markers_present and history_has_context)):
        decision.type = "normal"
        decision.label = None
        decision.meta = {}
        decision.assistant_text = ""

    if decision.type == "generate_plan_now" and not (has_explicit or (has_contextual and history_has_context)):
        decision.type = "normal"
        decision.label = None
        decision.meta = {}
        decision.assistant_text = ""

    return decision


def sanitize_decision_meta(meta: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    if not isinstance(meta, dict):
        return clean

    for key, value in meta.items():
        if key not in ALLOWED_META_KEYS:
            continue

        if key in INT_META_LIMITS:
            try:
                num = int(value)
            except Exception:
                continue
            lo, hi = INT_META_LIMITS[key]
            clean[key] = max(lo, min(hi, num))
            continue

        if key == "rest_days_strict":
            clean[key] = bool(value)
            continue

        if key in LIST_META_KEYS:
            if isinstance(value, (list, tuple)):
                items = [_clean_text(item, 64) for item in value]
            else:
                items = [_clean_text(value, 64)]
            clean[key] = [item for item in items if item]
            continue

        text_value = _clean_text(value, 500 if key == "notes" else 64)
        if text_value:
            clean[key] = text_value

    return clean


def _normal_fallback() -> AiChatDecision:
    return AiChatDecision(assistant_text="", type="normal", fallback_used=True)


async def get_ai_chat_decision(
    *,
    text: str,
    history: list[dict[str, str]],
    meta: dict[str, Any],
    language: str,
) -> AiChatDecision:
    fallback = _fallback_decision(text, history, language)

    history_preview = [
        {
            "role": _clean_text((item or {}).get("role"), 16),
            "text": _clean_text((item or {}).get("text"), 500),
        }
        for item in (history[-6:] if history else [])
    ]

    system_prompt = (
        "Return strict JSON only without markdown. "
        'Schema: {"assistant_text":"...","type":"normal|show_generate_button|generate_plan_now","label":null,"meta":{}}. '
        "Rules: normal means standard advice answer. "
        "show_generate_button means the user asks permission or possibility to generate a plan. "
        "generate_plan_now means the user explicitly tells you to generate or create the plan now. "
        "Use chat history for references like for this, based on that, then generate, для этого, по этому, тогда. "
        "For belly fat requests, meta may include goals ['lose_weight'] and body_focus ['core'], but never promise spot fat reduction. "
        "assistant_text must be plain text. meta must contain only useful plan-generation fields."
    )
    user_payload = {
        "language": language,
        "meta": meta or {},
        "history": history_preview,
        "text": str(text or ""),
    }

    try:
        raw = await yandex_completion(
            [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": json.dumps(user_payload, ensure_ascii=False)},
            ],
            temperature=0.2,
            max_tokens=600,
        )
    except Exception as exc:
        logger.warning("AI chat decision fallback used: reason=model_call_error error=%s", str(exc))
        return _postprocess_decision(fallback or _normal_fallback(), text=text, history=history, language=language)

    obj = _extract_json_object(raw or "")
    if not obj:
        logger.warning("AI chat decision fallback used: reason=invalid_json")
        return _postprocess_decision(fallback or _normal_fallback(), text=text, history=history, language=language)

    try:
        decision_type = _normalize_decision_type(obj.get("type"))
        assistant_text = _clean_text(obj.get("assistant_text"), 1200)
        label = _clean_text(obj.get("label"), 80) if obj.get("label") is not None else None
        meta_clean = sanitize_decision_meta(obj.get("meta") if isinstance(obj.get("meta"), dict) else {})

        if decision_type == "show_generate_button":
            assistant_text = assistant_text or _button_text(language)
            label = label or _button_label(language)
        elif decision_type == "generate_plan_now":
            assistant_text = assistant_text or _generating_text(language)

        return _postprocess_decision(
            AiChatDecision(
                assistant_text=assistant_text,
                type=decision_type,
                label=label,
                meta=meta_clean,
                fallback_used=False,
            ),
            text=text,
            history=history,
            language=language,
        )
    except Exception as exc:
        logger.warning("AI chat decision fallback used: reason=parse_error error=%s", str(exc))
        return _postprocess_decision(fallback or _normal_fallback(), text=text, history=history, language=language)
