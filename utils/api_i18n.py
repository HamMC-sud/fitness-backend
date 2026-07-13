from __future__ import annotations

import re
from http import HTTPStatus
from typing import Any, Callable, Match


EXACT_TRANSLATIONS: dict[str, str] = {
    "Success": "Успешно",
    "Created": "Создано",
    "Bad request": "Некорректный запрос",
    "Unauthorized": "Не авторизован",
    "Forbidden": "Доступ запрещён",
    "Not Found": "Не найдено",
    "User not found": "Пользователь не найден",
    "Admin not found": "Администратор не найден",
    "Notification not found": "Уведомление не найдено",
    "Achievement not found": "Достижение не найдено",
    "Exercise not found": "Упражнение не найдено",
    "Workout not found": "Тренировка не найдена",
    "Workout template not found": "Шаблон тренировки не найден",
    "Workout template is inactive": "Шаблон тренировки неактивен",
    "Content asset not found": "Контент не найден",
    "Order not found": "Заказ не найден",
    "Plan not found": "План не найден",
    "No active plan": "Нет активного плана",
    "Day not found": "День не найден",
    "Recommendation not found": "Рекомендация не найдена",
    "Swap option not found": "Вариант замены не найден",
    "Source exercise not found": "Исходное упражнение не найдено",
    "Replacement exercise not found": "Упражнение для замены не найдено",
    "Thread not found": "Тред не найден",
    "Transaction not found": "Транзакция не найдена",
    "Tariff not found": "Тариф не найден",
    "Plan has no web price configured": "Для плана не настроена веб-цена",
    "Plan code already exists": "Код плана уже существует",
    "Batch name already exists": "Имя батча уже существует",
    "Promo code already exists": "Промокод уже существует",
    "Promo code already used": "Промокод уже использован",
    "Promo code disabled": "Промокод отключён",
    "Promo code expired": "Срок действия промокода истёк",
    "Promo code invalid duration": "Некорректная длительность промокода",
    "Promo code has unsupported duration": "Длительность промокода не поддерживается",
    "Promo code limit reached or expired": "Лимит промокода исчерпан или срок действия истёк",
    "Invalid promo code": "Некорректный промокод",
    "Invalid token": "Некорректный токен",
    "Invalid credentials": "Неверные учётные данные",
    "Invalid refresh token": "Некорректный refresh token",
    "Refresh token expired": "Срок действия refresh token истёк",
    "Failed to create refresh token": "Не удалось создать refresh token",
    "Failed to create user": "Не удалось создать пользователя",
    "Failed to prepare verification code": "Не удалось подготовить код подтверждения",
    "Failed to prepare social profile completion": "Не удалось подготовить завершение социального профиля",
    "Failed to save steps": "Не удалось сохранить шаги",
    "Password must be at least 8 characters long": "Пароль должен содержать минимум 8 символов",
    "Phone must be E.164 format": "Телефон должен быть в формате E.164",
    "Password login is not available for this account": "Вход по паролю недоступен для этого аккаунта",
    "New password and confirm password do not match": "Новый пароль и подтверждение не совпадают",
    "Current password is incorrect": "Текущий пароль неверный",
    "New password must be different from current password": "Новый пароль должен отличаться от текущего",
    "Invalid reset request": "Некорректный запрос на сброс",
    "Reset already used": "Сброс уже использован",
    "Reset expired": "Срок действия сброса истёк",
    "Too many attempts": "Слишком много попыток",
    "User already exists": "Пользователь уже существует",
    "Email required to complete profile": "Для завершения профиля требуется email",
    "Email verified successfully. Please complete registration with profile information.": "Email успешно подтверждён. Пожалуйста, завершите регистрацию, заполнив данные профиля.",
    "No pending verification found. Start registration again.": "Ожидающая верификация не найдена. Начните регистрацию заново.",
    "Code expired. Start registration again.": "Срок действия кода истёк. Начните регистрацию заново.",
    "Too many attempts. Start registration again.": "Слишком много попыток. Начните регистрацию заново.",
    "Email verification not found. Start registration again.": "Подтверждение email не найдено. Начните регистрацию заново.",
    "Email verification is pending. Complete verification first.": "Подтверждение email ещё ожидается. Сначала завершите верификацию.",
    "Registration session expired. Start registration again.": "Сессия регистрации истекла. Начните регистрацию заново.",
    "Invalid image data URI format": "Некорректный формат image data URI",
    "Image must be base64 encoded": "Изображение должно быть в формате base64",
    "Invalid base64 image": "Некорректное изображение base64",
    "Empty image data": "Пустые данные изображения",
    "Unsupported image format. Use jpg, png, or webp": "Неподдерживаемый формат изображения. Используйте jpg, png или webp",
    "Image MIME type does not match file content": "MIME-тип изображения не совпадает с содержимым файла",
    "Invalid cursor": "Некорректный курсор",
    "Invalid amount in plan web price": "Некорректная сумма в веб-цене плана",
    "Plan web price amount must be greater than 0": "Сумма веб-цены плана должна быть больше 0",
    "Invalid currency in plan web price": "Некорректная валюта в веб-цене плана",
    "YOOKASSA_SHOP_ID is not configured": "YOOKASSA_SHOP_ID не настроен",
    "YOOKASSA_SECRET_KEY is not configured": "YOOKASSA_SECRET_KEY не настроен",
    "Return URL is not configured": "Return URL не настроен",
    "Cannot reach YooKassa": "Не удаётся связаться с YooKassa",
    "Invalid YooKassa response": "Некорректный ответ YooKassa",
    "YooKassa response missing payment data": "В ответе YooKassa отсутствуют данные платежа",
    "Invalid webhook token": "Некорректный webhook token",
    "Missing payment id": "Отсутствует payment id",
    "Invalid provider": "Некорректный provider",
    "Receipt required": "Требуется чек",
    "provider_tx_id already used": "provider_tx_id уже использован",
    "productId is required": "Требуется productId",
    "Invalid productId": "Некорректный productId",
    "Invalid userId": "Некорректный userId",
    "Invalid product_id": "Некорректный product_id",
    "Invalid platform": "Некорректная платформа",
    "order_id is required": "Требуется order_id",
    "Invalid transaction_id": "Некорректный transaction_id",
    "Invalid batch_id": "Некорректный batch_id",
    "Invalid promo_code_id": "Некорректный promo_code_id",
    "mapping_file is empty": "Файл mapping_file пуст",
    "mapping_file exceeds 5MB": "Файл mapping_file превышает 5 МБ",
    "At least one file is required": "Требуется хотя бы один файл",
    "JSON must be an array of rows": "JSON должен быть массивом строк",
    "CSV has no header": "CSV не содержит заголовка",
    "duration_mmss must be MM:SS format": "duration_mmss должен быть в формате MM:SS",
    "Support role required": "Требуется роль support",
    "Identifier and password are required": "Требуются идентификатор и пароль",
    "Exercise code is required": "Требуется код упражнения",
    "Exercise code already exists": "Код упражнения уже существует",
    "thumbnail_file or video_file is required": "Требуется thumbnail_file или video_file",
    "campaign_name is required": "Требуется campaign_name",
    "campaign_name already exists": "campaign_name уже существует",
    "Connected to WebSocket": "Подключено к WebSocket",
    "A client disconnected": "Клиент отключился",
    "Rewarded is available for free users only": "Rewarded доступен только бесплатным пользователям",
    "Invalid nonce": "Некорректный nonce",
    "AI limit reached": "Лимит AI достигнут",
    "Day is not a workout": "Этот день не является тренировочным",
    "No replacement exercise found": "Не найдено упражнение для замены",
    "Either swap_id or new_exercise_id is required": "Требуется либо swap_id, либо new_exercise_id",
    "Invalid intensity": "Некорректная интенсивность",
    "Forbidden": "Доступ запрещён",
    "Run already completed": "Проход уже завершён",
    "difficulty is required": "Требуется difficulty",
    "difficulty must be easy|normal|hard": "difficulty должен быть easy|normal|hard",
}


PatternTranslator = tuple[re.Pattern[str], Callable[[Match[str]], str]]

PATTERN_TRANSLATIONS: list[PatternTranslator] = [
    (
        re.compile(r"^No suitable exercises available for workout type: (.+)$"),
        lambda m: {
            "strength": "ÐÐµÑ‚ Ð¿Ð¾Ð´Ñ…Ð¾Ð´ÑÑ‰Ð¸Ñ… ÑÐ¸Ð»Ð¾Ð²Ñ‹Ñ… ÑƒÐ¿Ñ€Ð°Ð¶Ð½ÐµÐ½Ð¸Ð¹ Ð´Ð»Ñ ÑÑ‚Ð¾Ð³Ð¾ Ñ‚Ð¸Ð¿Ð° Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÐºÐ¸",
            "cardio": "ÐÐµÑ‚ Ð¿Ð¾Ð´Ñ…Ð¾Ð´ÑÑ‰Ð¸Ñ… ÐºÐ°Ñ€Ð´Ð¸Ð¾-ÑƒÐ¿Ñ€Ð°Ð¶Ð½ÐµÐ½Ð¸Ð¹ Ð´Ð»Ñ ÑÑ‚Ð¾Ð³Ð¾ Ñ‚Ð¸Ð¿Ð° Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÐºÐ¸",
            "yoga": "ÐÐµÑ‚ Ð¿Ð¾Ð´Ñ…Ð¾Ð´ÑÑ‰Ð¸Ñ… ÑƒÐ¿Ñ€Ð°Ð¶Ð½ÐµÐ½Ð¸Ð¹ Ð´Ð»Ñ Ð¹Ð¾Ð³Ð¸",
            "stretching": "ÐÐµÑ‚ Ð¿Ð¾Ð´Ñ…Ð¾Ð´ÑÑ‰Ð¸Ñ… ÑƒÐ¿Ñ€Ð°Ð¶Ð½ÐµÐ½Ð¸Ð¹ Ð´Ð»Ñ Ñ€Ð°ÑÑ‚ÑÐ¶ÐºÐ¸/mobility",
            "recovery": "ÐÐµÑ‚ Ð¿Ð¾Ð´Ñ…Ð¾Ð´ÑÑ‰Ð¸Ñ… Ð²Ð¾ÑÑÑ‚Ð°Ð½Ð¾Ð²Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ñ‹Ñ… ÑƒÐ¿Ñ€Ð°Ð¶Ð½ÐµÐ½Ð¸Ð¹",
            "hiit": "ÐÐµÑ‚ Ð¿Ð¾Ð´Ñ…Ð¾Ð´ÑÑ‰Ð¸Ñ… HIIT-ÑƒÐ¿Ñ€Ð°Ð¶Ð½ÐµÐ½Ð¸Ð¹ Ð´Ð»Ñ ÑÑ‚Ð¾Ð³Ð¾ Ñ‚Ð¸Ð¿Ð° Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÐºÐ¸",
            "rest": "Ð”Ð»Ñ Ð´Ð½Ñ Ð¾Ñ‚Ð´Ñ‹Ñ…Ð° ÑƒÐ¿Ñ€Ð°Ð¶Ð½ÐµÐ½Ð¸Ñ Ð½Ðµ Ð½Ð°Ð·Ð½Ð°Ñ‡Ð°ÑŽÑ‚ÑÑ",
        }.get(m.group(1).strip().lower(), f"ÐÐµÑ‚ Ð¿Ð¾Ð´Ñ…Ð¾Ð´ÑÑ‰Ð¸Ñ… ÑƒÐ¿Ñ€Ð°Ð¶Ð½ÐµÐ½Ð¸Ð¹ Ð´Ð»Ñ Ñ‚Ð¸Ð¿Ð° Ñ‚Ñ€ÐµÐ½Ð¸Ñ€Ð¾Ð²ÐºÐ¸: {m.group(1)}"),
    ),
    (re.compile(r"^Code must be (\d+) digits$"), lambda m: f"Код должен состоять из {m.group(1)} цифр"),
    (
        re.compile(r"^Image too large\. Max size is (\d+) MB$"),
        lambda m: f"Изображение слишком большое. Максимальный размер — {m.group(1)} МБ",
    ),
    (
        re.compile(r"^File '(.+)' already exists\. Set overwrite_existing=true to replace it\.$"),
        lambda m: f"Файл '{m.group(1)}' уже существует. Установите overwrite_existing=true, чтобы заменить его.",
    ),
    (
        re.compile(r"^(.+?) file is empty$"),
        lambda m: f"Файл '{m.group(1)}' пуст",
    ),
    (
        re.compile(r"^(.+?) file exceeds size limit$"),
        lambda m: f"Файл '{m.group(1)}' превышает лимит размера",
    ),
    (
        re.compile(r"^Invalid file name\. Use only letters, numbers, dot, dash, underscore\.$"),
        lambda m: "Некорректное имя файла. Используйте только буквы, цифры, точку, дефис и подчёркивание.",
    ),
    (
        re.compile(r"^JSON row (\d+) must be an object$"),
        lambda m: f"Строка JSON {m.group(1)} должна быть объектом",
    ),
    (
        re.compile(r"^(.+?) must be a (video|audio|image)/\* file$"),
        lambda m: f"Файл '{m.group(1)}' должен быть типа {m.group(2)}/*",
    ),
    (
        re.compile(r"^(.+?) is required for (video|audio|image) content$"),
        lambda m: f"Для контента типа {m.group(2)} требуется '{m.group(1)}'",
    ),
    (
        re.compile(r"^exercise_id not found in workout: (.+)$"),
        lambda m: f"exercise_id не найден в тренировке: {m.group(1)}",
    ),
    (
        re.compile(r"^mode mismatch for exercise_id: (.+)$"),
        lambda m: f"Несовпадение режима для exercise_id: {m.group(1)}",
    ),
    (
        re.compile(r"^set_no exceeds configured sets for exercise: (.+)$"),
        lambda m: f"set_no превышает настроенное число подходов для упражнения: {m.group(1)}",
    ),
    (
        re.compile(r"^Provider/source mismatch\. Expected '(.+)' for source '(.+)'$"),
        lambda m: f"Несовпадение provider/source. Ожидался '{m.group(1)}' для source '{m.group(2)}'",
    ),
    (
        re.compile(r"^Invalid similar workout payload: (.+)$"),
        lambda m: f"Некорректный payload похожей тренировки: {m.group(1)}",
    ),
    (
        re.compile(r"^Plan request is inconsistent: (.+)$"),
        lambda m: f"Запрос плана противоречив: {m.group(1)}",
    ),
    (
        re.compile(r"^You sent: (.+)$"),
        lambda m: f"Вы отправили: {m.group(1)}",
    ),
    (
        re.compile(r"^Broadcast: (.+)$"),
        lambda m: f"Рассылка: {m.group(1)}",
    ),
]


def translate_text(text: str) -> str:
    normalized = str(text or "")
    if not normalized:
        return normalized
    translated = EXACT_TRANSLATIONS.get(normalized)
    if translated:
        return translated
    for pattern, resolver in PATTERN_TRANSLATIONS:
        match = pattern.match(normalized)
        if match:
            return resolver(match)
    return normalized


def to_bilingual_text(text: str) -> dict[str, str]:
    normalized = str(text or "")
    if not normalized:
        return {"en": "", "ru": ""}
    has_cyrillic = bool(re.search(r"[А-Яа-яЁё]", normalized))
    has_latin = bool(re.search(r"[A-Za-z]", normalized))
    if has_cyrillic and not has_latin:
        return {"en": normalized, "ru": normalized}
    return {"en": normalized, "ru": translate_text(normalized)}


LOCALIZABLE_FIELD_NAMES = {
    "name",
    "title",
    "description",
    "subtitle",
    "body",
    "beginner_tip",
    "ai_technique",
    "ai_mistakes",
}


def is_bilingual_map(value: Any) -> bool:
    return isinstance(value, dict) and "ru" in value and "en" in value


def normalize_i18n_value(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if is_bilingual_map(value):
        return {
            "en": str(value.get("en") or ""),
            "ru": str(value.get("ru") or ""),
        }
    if isinstance(value, str):
        return to_bilingual_text(value)
    return None


def expand_i18n_payload(value: Any) -> Any:
    if isinstance(value, list):
        return [expand_i18n_payload(item) for item in value]

    if not isinstance(value, dict):
        return value

    expanded = {key: expand_i18n_payload(item) for key, item in value.items()}

    for key in tuple(expanded.keys()):
        if not key.endswith("_i18n"):
            continue
        base_key = key[:-5]
        if base_key not in LOCALIZABLE_FIELD_NAMES:
            continue
        normalized = normalize_i18n_value(expanded.get(key))
        if normalized is None:
            continue
        localized_value = expanded.get(base_key)
        if isinstance(localized_value, str):
            expanded[f"{base_key}_localized"] = localized_value
        elif localized_value is not None and not is_bilingual_map(localized_value):
            expanded[f"{base_key}_localized"] = localized_value
        expanded[base_key] = normalized

    for key in LOCALIZABLE_FIELD_NAMES:
        normalized = normalize_i18n_value(expanded.get(key))
        if normalized is not None and is_bilingual_map(expanded.get(key)):
            expanded[key] = normalized

    return expanded


def localize_detail(value: Any) -> Any:
    if isinstance(value, str):
        return to_bilingual_text(value)
    if isinstance(value, list):
        return [localize_detail(item) for item in value]
    if isinstance(value, dict):
        localized = dict(value)
        if isinstance(localized.get("message"), str) and "message_i18n" not in localized:
            localized["message_i18n"] = to_bilingual_text(localized["message"])
        if isinstance(localized.get("detail"), str) and "detail_i18n" not in localized:
            localized["detail_i18n"] = to_bilingual_text(localized["detail"])
        if isinstance(localized.get("msg"), str) and "msg_i18n" not in localized:
            localized["msg_i18n"] = to_bilingual_text(localized["msg"])
        return localized
    return value


def default_message_for_status(status_code: int) -> str:
    try:
        status = HTTPStatus(status_code)
    except ValueError:
        return "Success" if 200 <= status_code < 400 else "Request failed"
    if 200 <= status_code < 300:
        if status_code == 201:
            return "Created"
        return "Success"
    if status == HTTPStatus.BAD_REQUEST:
        return "Bad request"
    if status == HTTPStatus.UNAUTHORIZED:
        return "Unauthorized"
    if status == HTTPStatus.FORBIDDEN:
        return "Forbidden"
    if status == HTTPStatus.NOT_FOUND:
        return "Not Found"
    return status.phrase


def augment_payload(payload: Any, status_code: int) -> Any:
    if not isinstance(payload, dict):
        return payload

    enriched = expand_i18n_payload(dict(payload))

    if isinstance(enriched.get("message"), str) and "message_i18n" not in enriched:
        enriched["message_i18n"] = to_bilingual_text(enriched["message"])

    if isinstance(enriched.get("detail"), str) and "detail_i18n" not in enriched:
        enriched["detail_i18n"] = to_bilingual_text(enriched["detail"])
    elif "detail" in enriched and "detail_i18n" not in enriched:
        enriched["detail_i18n"] = localize_detail(enriched["detail"])

    if "_i18n" not in enriched:
        enriched["_i18n"] = {"message": to_bilingual_text(default_message_for_status(status_code))}

    return enriched
