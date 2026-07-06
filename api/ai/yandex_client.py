from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger("uvicorn.error")

YC_API_KEY_SECRET = (os.getenv("YC_API_KEY_SECRET") or os.getenv("YANDEX_API_KEY") or "").strip()
YC_FOLDER_ID = (os.getenv("YC_FOLDER_ID") or os.getenv("YANDEX_FOLDER_ID") or "").strip()
YC_GPT_MODEL_URI = (os.getenv("YC_GPT_MODEL_URI") or os.getenv("YANDEX_GPT_MODEL_URI") or "").strip()
YC_COMPLETION_URL = (
    os.getenv("YC_COMPLETION_URL")
    or os.getenv("YANDEX_COMPLETION_URL")
    or "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
).strip()


def _model_uri() -> str:
    if YC_GPT_MODEL_URI:
        return YC_GPT_MODEL_URI
    if YC_FOLDER_ID:
        return f"gpt://{YC_FOLDER_ID}/yandexgpt/latest"
    return ""


async def yandex_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.4,
    max_tokens: int = 1400,
) -> Optional[str]:
    if not YC_API_KEY_SECRET:
        logger.warning(
            "Yandex completion skipped: missing YC_API_KEY_SECRET; endpoint=%s folder_id=%s model_uri=%s",
            YC_COMPLETION_URL,
            bool(YC_FOLDER_ID),
            bool(YC_GPT_MODEL_URI),
        )
        return None

    model_uri = _model_uri()
    if not model_uri:
        logger.warning(
            "Yandex completion skipped: model URI is empty; endpoint=%s YC_FOLDER_ID=%s YC_GPT_MODEL_URI=%s",
            YC_COMPLETION_URL,
            bool(YC_FOLDER_ID),
            bool(YC_GPT_MODEL_URI),
        )
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
            response = await client.post(
                YC_COMPLETION_URL,
                headers={"Authorization": f"Api-Key {YC_API_KEY_SECRET}"},
                json=body,
            )
    except httpx.TimeoutException as exc:
        logger.error(
            "Yandex completion timeout: endpoint=%s model_uri=%s error=%s",
            YC_COMPLETION_URL,
            model_uri,
            str(exc),
        )
        return None
    except httpx.ConnectError as exc:
        logger.error(
            "Yandex completion connection error: endpoint=%s model_uri=%s error=%s",
            YC_COMPLETION_URL,
            model_uri,
            str(exc),
        )
        return None
    except httpx.HTTPError as exc:
        logger.error(
            "Yandex completion HTTP error: endpoint=%s model_uri=%s error=%s",
            YC_COMPLETION_URL,
            model_uri,
            str(exc),
        )
        return None

    if response.status_code != 200:
        response_preview = (response.text or "")[:500]
        request_id = (
            response.headers.get("x-request-id")
            or response.headers.get("X-Request-Id")
            or response.headers.get("x-trace-id")
        )
        logger.warning(
            "Yandex completion non-200 response: status=%s endpoint=%s model_uri=%s request_id=%s body=%s",
            response.status_code,
            YC_COMPLETION_URL,
            model_uri,
            request_id,
            response_preview,
        )
        return None

    try:
        data = response.json()
        return str(data["result"]["alternatives"][0]["message"]["text"]).strip()
    except Exception as exc:
        response_preview = (response.text or "")[:500]
        request_id = (
            response.headers.get("x-request-id")
            or response.headers.get("X-Request-Id")
            or response.headers.get("x-trace-id")
        )
        logger.error(
            "Yandex completion parse error: endpoint=%s model_uri=%s request_id=%s error=%s response=%s",
            YC_COMPLETION_URL,
            model_uri,
            request_id,
            str(exc),
            response_preview,
        )
        return None


async def yandex_chat_completion(
    text: str,
    meta: dict[str, Any],
    history: Optional[list[dict[str, str]]] = None,
) -> str:
    if not YC_API_KEY_SECRET:
        return "AI assistant is configured in stub mode. Add YC_API_KEY_SECRET or YANDEX_API_KEY to enable Yandex GPT."

    if not _model_uri():
        return "AI assistant is configured in stub mode. Add YC_FOLDER_ID/YANDEX_FOLDER_ID or YC_GPT_MODEL_URI/YANDEX_GPT_MODEL_URI for Yandex GPT."

    system_text = (
        "You are a fitness assistant. Give safe, concise, actionable advice. "
        "If the topic has medical risk, recommend consulting a professional. "
        "Use plain text, no markdown."
    )
    if meta:
        system_text += f" Context meta: {json.dumps(meta, ensure_ascii=True)}"

    messages: list[dict[str, str]] = [{"role": "system", "text": system_text}]
    if history:
        messages.extend(history[-12:])
    messages.append({"role": "user", "text": text})

    result = await yandex_completion(messages, temperature=0.6, max_tokens=800)
    if result:
        return result
    return "AI service is temporarily unavailable. Please try again."
