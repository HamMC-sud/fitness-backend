from __future__ import annotations

import csv
import io
import logging
import os
import secrets
import string
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import httpx
from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from starlette.concurrency import run_in_threadpool

from api.auth.config import get_current_user
from models.enums import PromoStatus, SubscriptionSource, SubscriptionStatus
from models.landing_payment import LandingYooKassaOrder
from models.promo import PromoCode, PromoCodeBatch, PromoRedemption
from models.subscription import Subscription, SubscriptionPlan, SubscriptionTransaction
from models.users import User
from schemas.subscription import (
    CancelOut,
    LandingYooKassaInitIn,
    LandingYooKassaInitOut,
    LandingYooKassaOrderStatusOut,
    PremiumActivateByProductIn,
    PremiumActivateByProductOut,
    PromoActivateIn,
    PromoBatchCreateIn,
    PromoBatchCreateOut,
    PromoBatchOut,
    PromoCodeCreateIn,
    PromoCodeOut,
    PromoCodesOut,
    PromoPreviewOut,
    PromoStatsOut,
    PurchaseIn,
    PurchaseInitOut,
    PurchaseOut,
    PurchaseVerifyIn,
    PurchaseVerifyOut,
    SubscriptionActivateIn,
    SubscriptionActivateOut,
    SubscriptionGetOut,
    SubscriptionOut,
    SubscriptionPlanCreateIn,
    SubscriptionPlanOut,
    SubscriptionPlansOut,
    YooKassaWebhookIn,
    YooKassaWebhookOut,
)

router = APIRouter(tags=["subscription"])
logger = logging.getLogger("uvicorn.error")

SUBSCRIPTION_DURATIONS = {
    "0760188330": 30,
    "0760188340": 90,
    "0760188350": 365,
}


def _mask_email(email: Optional[str]) -> Optional[str]:
    raw = str(email or "").strip().lower()
    if not raw or "@" not in raw:
        return None
    local, domain = raw.split("@", 1)
    if len(local) <= 2:
        masked_local = local[:1] + "*"
    else:
        masked_local = local[:2] + "*" * max(1, len(local) - 2)
    return f"{masked_local}@{domain}"


def _tail(value: Optional[str], size: int = 6) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    return raw[-size:]


def _order_log_context(order: Optional[LandingYooKassaOrder]) -> Dict[str, Any]:
    if not order:
        return {}
    return {
        "order_uid": getattr(order, "order_uid", None),
        "payment_id_tail": _tail(getattr(order, "yookassa_payment_id", None)),
        "payment_status": getattr(order, "yookassa_status", None),
        "plan_code": getattr(order, "plan_code", None),
        "tariff": getattr(order, "tariff", None),
        "email_masked": _mask_email(getattr(order, "email", None)),
        "promo": getattr(order, "promocode", None),
        "linked_user_id": str(getattr(order, "linked_user_id", None)) if getattr(order, "linked_user_id", None) else None,
        "activated_at": getattr(order, "activated_at", None),
        "activation_error": getattr(order, "activation_error", None),
    }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def clamp_limit(limit: int) -> int:
    return min(max(int(limit or 20), 1), 100)


def require_auth(user):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")


def normalize_code(code: str) -> str:
    return (code or "").strip().upper()


def plan_to_out(p: SubscriptionPlan) -> SubscriptionPlanOut:
    return SubscriptionPlanOut(
        id=str(p.id),
        code=p.code,
        duration_days=int(p.duration_days),
        prices=p.prices or {},
        status=p.status,
    )


def promo_to_out(p: PromoCode) -> PromoCodeOut:
    return PromoCodeOut(
        id=str(p.id),
        code=p.code,
        discount_percent=int(getattr(p, "discount_percent", 0) or 0),
        duration_days=int(p.duration_days),
        max_uses=int(p.max_uses),
        used_count=int(p.used_count),
        expires_at=p.expires_at,
        status=p.status,
    )


def _promo_is_expired(promo: PromoCode) -> bool:
    if promo.expires_at is None:
        return False
    exp = promo.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return utcnow() >= exp


def _promo_duration_is_allowed(duration_days: int) -> bool:
    return int(duration_days) in PromoCodeBatch.ALLOWED_DURATION_DAYS


def compute_subscription_status(sub: Subscription) -> Tuple[SubscriptionStatus, bool, bool]:
    now = utcnow()

    exp = getattr(sub, "expires_at", None)
    if not exp:
        return SubscriptionStatus.expired, False, False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)

    gu = getattr(sub, "grace_until", None)
    if gu and gu.tzinfo is None:
        gu = gu.replace(tzinfo=timezone.utc)

    if now >= exp:
        if gu and now < gu:
            return SubscriptionStatus.grace, False, True
        return SubscriptionStatus.expired, False, False

    if not getattr(sub, "auto_renew", True):
        return SubscriptionStatus.canceled, True, False

    return SubscriptionStatus.active, True, False


def sub_to_out(sub: Subscription) -> SubscriptionOut:
    status, _, _ = compute_subscription_status(sub)
    return SubscriptionOut(
        id=str(sub.id),
        status=status,
        plan_code=sub.plan_code,
        source=sub.source,
        started_at=sub.started_at,
        expires_at=sub.expires_at,
        grace_until=sub.grace_until,
        auto_renew=sub.auto_renew,
        last_transaction_id=str(sub.last_transaction_id) if sub.last_transaction_id else None,
        amount=getattr(sub, "amount", None),
        currency=getattr(sub, "currency", None),
    )


async def upsert_subscription(
    user_id: PydanticObjectId,
    plan_code: str,
    source: SubscriptionSource,
    add_days: int,
    tx_id: Optional[PydanticObjectId],
) -> Subscription:
    now = utcnow()
    existing = await Subscription.find_one(Subscription.user_id == user_id)
    logger.info(
        "Subscription upsert started: user_id=%s plan_code=%s source=%s add_days=%s tx_id=%s has_existing=%s",
        str(user_id),
        plan_code,
        source.value if hasattr(source, "value") else str(source),
        int(add_days),
        str(tx_id) if tx_id else None,
        bool(existing),
    )

    amount = None
    currency = None
    if tx_id:
        tx = await SubscriptionTransaction.get(tx_id)
        if tx:
            amount = tx.amount
            currency = tx.currency

    base = now
    started_at = now

    if existing:
        exp = getattr(existing, "expires_at", None)
        if exp:
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp > now:
                base = exp
                started_at = existing.started_at

    expires_at = base + timedelta(days=int(add_days))
    grace_until = expires_at + timedelta(days=30)

    if existing:
        existing.plan_code = plan_code
        existing.source = source
        existing.started_at = started_at
        existing.expires_at = expires_at
        existing.grace_until = grace_until
        existing.auto_renew = True
        existing.last_transaction_id = tx_id
        existing.status = SubscriptionStatus.active
        existing.amount = amount
        existing.currency = currency
        await existing.save()
        logger.info(
            "Subscription upsert updated existing: subscription_id=%s user_id=%s expires_at=%s grace_until=%s",
            str(existing.id),
            str(user_id),
            existing.expires_at,
            existing.grace_until,
        )

        user = await User.get(user_id)
        if user:
            user.flags.is_premium = True
            user.flags.premium_until = expires_at
            await user.save()
        return existing

    sub = Subscription(
        user_id=user_id,
        status=SubscriptionStatus.active,
        plan_code=plan_code,
        source=source,
        started_at=started_at,
        expires_at=expires_at,
        grace_until=grace_until,
        auto_renew=True,
        last_transaction_id=tx_id,
        amount=amount,
        currency=currency,
    )
    await sub.insert()
    logger.info(
        "Subscription upsert created new: subscription_id=%s user_id=%s expires_at=%s grace_until=%s",
        str(sub.id),
        str(user_id),
        sub.expires_at,
        sub.grace_until,
    )

    user = await User.get(user_id)
    if user:
        user.flags.is_premium = True
        user.flags.premium_until = expires_at
        await user.save()
    return sub


async def activate_google_play_premium(user_id: str, product_id: str) -> User:
    product = (product_id or "").strip()
    if not product:
        raise HTTPException(status_code=400, detail="productId is required")

    duration_days = SUBSCRIPTION_DURATIONS.get(product)
    if duration_days is None:
        raise HTTPException(status_code=400, detail="Invalid productId")

    try:
        uid = PydanticObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid userId")

    user = await User.get(uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = utcnow()
    current_until = getattr(user.flags, "premium_until", None)
    if current_until and current_until.tzinfo is None:
        current_until = current_until.replace(tzinfo=timezone.utc)

    base_date = current_until if current_until and current_until > now else now
    new_premium_until = base_date + timedelta(days=duration_days)

    user.flags.is_premium = True
    user.flags.premium_until = new_premium_until

    await user.save()
    return user


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_motor_collection(col) -> bool:
    mod = (getattr(col.__class__, "__module__", "") or "").lower()
    name = (getattr(col.__class__, "__name__", "") or "").lower()
    return ("motor" in mod) or ("motor" in name)


def _get_promo_collection():
    settings = PromoCode.get_settings()

    col = getattr(settings, "motor_collection", None)
    if col is not None:
        return col

    col = getattr(settings, "pymongo_collection", None)
    if col is not None:
        return col

    fn = getattr(PromoCode, "get_motor_collection", None)
    if callable(fn):
        return fn()

    fn = getattr(PromoCode, "get_pymongo_collection", None)
    if callable(fn):
        return fn()

    fn = getattr(PromoCode, "get_collection", None)
    if callable(fn):
        return fn()

    raise RuntimeError("Cannot get PromoCode collection")


async def _find_one_and_update(col, q: dict, upd: dict) -> Optional[Dict[str, Any]]:
    if _is_motor_collection(col):
        return await col.find_one_and_update(q, upd, return_document=ReturnDocument.AFTER)
    return await run_in_threadpool(col.find_one_and_update, q, upd, return_document=ReturnDocument.AFTER)


async def promo_atomic_claim(code: str) -> Optional[Dict[str, Any]]:
    now = _utcnow_naive()
    col = _get_promo_collection()

    st = PromoStatus.active.value if hasattr(PromoStatus.active, "value") else PromoStatus.active

    q = {
        "code": code,
        "status": st,
        "$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}],
        "$expr": {
            "$lt": [
                {"$ifNull": ["$used_count", 0]},
                {"$ifNull": ["$max_uses", 1]},
            ]
        },
    }
    upd = {"$inc": {"used_count": 1}}
    return await _find_one_and_update(col, q, upd)


async def promo_atomic_rollback(code: str) -> None:
    col = _get_promo_collection()
    q = {"code": code, "used_count": {"$gt": 0}}
    upd = {"$inc": {"used_count": -1}}
    await _find_one_and_update(col, q, upd)


def code_random(length: int) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def webhook_token_ok(x_webhook_token: Optional[str]) -> bool:
    expected = (os.getenv("YOOKASSA_WEBHOOK_TOKEN") or "").strip()
    if not expected:
        return False
    return (x_webhook_token or "").strip() == expected


def expected_provider_for_source(source: SubscriptionSource) -> Optional[str]:
    mapping = {
        SubscriptionSource.appstore: "apple",
        SubscriptionSource.googleplay: "google",
        SubscriptionSource.rustore: "rustore",
        SubscriptionSource.web: "yookassa",
        SubscriptionSource.promo: None,
    }
    return mapping.get(source)


def build_web_checkout_url(transaction_id: str) -> Optional[str]:
    base = (os.getenv("PAYMENT_WEB_CHECKOUT_URL") or "").strip()
    if not base:
        return None

    sep = "&" if "?" in base else "?"
    return f"{base}{sep}transaction_id={transaction_id}"


def _money_to_decimal(value: Any) -> Decimal:
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid amount in plan web price")

    if dec <= Decimal("0"):
        raise HTTPException(status_code=400, detail="Plan web price amount must be greater than 0")

    return dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _format_money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def _resolve_web_plan_price(plan: SubscriptionPlan) -> Tuple[Decimal, str]:
    prices = dict(getattr(plan, "prices", {}) or {})
    web_price = prices.get(SubscriptionSource.web.value)

    if not isinstance(web_price, dict):
        raise HTTPException(status_code=400, detail="Plan has no web price configured")

    amount = _money_to_decimal(web_price.get("amount"))
    currency = str(web_price.get("currency") or "RUB").strip().upper()
    if len(currency) != 3:
        raise HTTPException(status_code=400, detail="Invalid currency in plan web price")
    return amount, currency


def _resolve_yookassa_credentials() -> Tuple[str, str]:
    shop_id = (os.getenv("YOOKASSA_SHOP_ID") or "").strip()
    secret_key = (os.getenv("YOOKASSA_SECRET_KEY") or "").strip()

    if not shop_id:
        raise HTTPException(status_code=500, detail="YOOKASSA_SHOP_ID is not configured")
    if not secret_key:
        raise HTTPException(status_code=500, detail="YOOKASSA_SECRET_KEY is not configured")

    return shop_id, secret_key


def _resolve_return_url(payload_return_url: Optional[str]) -> str:
    return_url = (
        (payload_return_url or "").strip()
        or (os.getenv("YOOKASSA_RETURN_URL") or "").strip()
        or (os.getenv("PAYMENT_WEB_CHECKOUT_URL") or "").strip()
    )
    if not return_url:
        raise HTTPException(status_code=500, detail="Return URL is not configured")
    return return_url


async def _get_yookassa_payment(
    *,
    shop_id: str,
    secret_key: str,
    payment_id: str,
) -> Dict[str, Any]:
    logger.info("YooKassa get payment request: payment_id_tail=%s", _tail(payment_id))
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"https://api.yookassa.ru/v3/payments/{payment_id}",
                auth=(shop_id, secret_key),
            )
    except httpx.HTTPError:
        logger.exception("YooKassa get payment transport error: payment_id_tail=%s", _tail(payment_id))
        raise HTTPException(status_code=502, detail="Cannot reach YooKassa")

    if response.status_code >= 400:
        logger.warning(
            "YooKassa get payment rejected: status_code=%s payment_id_tail=%s body=%s",
            response.status_code,
            _tail(payment_id),
            response.text[:1000],
        )
        raise HTTPException(status_code=502, detail="YooKassa rejected payment status request")

    try:
        data = response.json()
    except ValueError:
        logger.warning("YooKassa get payment invalid JSON response: payment_id_tail=%s", _tail(payment_id))
        raise HTTPException(status_code=502, detail="Invalid YooKassa response")

    logger.info(
        "YooKassa get payment success: payment_id_tail=%s status=%s paid=%s",
        _tail(payment_id),
        str(data.get("status") or "").strip() or None,
        bool(data.get("paid")),
    )
    return data


async def _resolve_promocode_discount(promocode: Optional[str]) -> Tuple[Optional[str], int]:
    code = normalize_code(promocode or "")
    if not code:
        return None, 0

    promo = await PromoCode.find_one(PromoCode.code == code)
    if not promo:
        raise HTTPException(status_code=400, detail="Invalid promo code")
    if promo.status != PromoStatus.active:
        raise HTTPException(status_code=400, detail="Promo code disabled")
    if _promo_is_expired(promo):
        raise HTTPException(status_code=400, detail="Promo code expired")

    remaining = max(0, int(promo.max_uses) - int(promo.used_count))
    if remaining <= 0:
        raise HTTPException(status_code=400, detail="Promo code limit reached or expired")

    return code, int(getattr(promo, "discount_percent", 0) or 0)


async def _create_yookassa_payment(
    *,
    shop_id: str,
    secret_key: str,
    amount: Decimal,
    currency: str,
    return_url: str,
    description: str,
    metadata: Dict[str, str],
) -> Dict[str, Any]:
    logger.info(
        "YooKassa create payment request: amount=%s currency=%s return_url=%s description=%s order_uid=%s tariff=%s email_masked=%s promo=%s",
        _format_money(amount),
        currency,
        return_url,
        description,
        metadata.get("order_uid"),
        metadata.get("tariff"),
        _mask_email(metadata.get("email")),
        metadata.get("promo") or None,
    )
    payload = {
        "amount": {
            "value": _format_money(amount),
            "currency": currency,
        },
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": return_url,
        },
        "description": description,
        "metadata": metadata,
    }

    headers = {"Idempotence-Key": str(uuid.uuid4())}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                "https://api.yookassa.ru/v3/payments",
                json=payload,
                headers=headers,
                auth=(shop_id, secret_key),
            )
    except httpx.HTTPError:
        logger.exception(
            "YooKassa create payment transport error: order_uid=%s tariff=%s email_masked=%s",
            metadata.get("order_uid"),
            metadata.get("tariff"),
            _mask_email(metadata.get("email")),
        )
        raise HTTPException(status_code=502, detail="Cannot reach YooKassa")

    if response.status_code >= 400:
        detail = "YooKassa rejected payment creation"
        try:
            err = response.json()
            message = err.get("description") or err.get("type")
            if message:
                detail = f"{detail}: {message}"
        except Exception:
            pass
        logger.warning(
            "YooKassa create payment rejected: status_code=%s detail=%s order_uid=%s tariff=%s email_masked=%s body=%s",
            response.status_code,
            detail,
            metadata.get("order_uid"),
            metadata.get("tariff"),
            _mask_email(metadata.get("email")),
            response.text[:1000],
        )
        raise HTTPException(status_code=502, detail=detail)

    try:
        data = response.json()
    except ValueError:
        logger.warning(
            "YooKassa create payment invalid JSON response: order_uid=%s tariff=%s email_masked=%s",
            metadata.get("order_uid"),
            metadata.get("tariff"),
            _mask_email(metadata.get("email")),
        )
        raise HTTPException(status_code=502, detail="Invalid YooKassa response")

    logger.info(
        "YooKassa create payment success: order_uid=%s payment_id_tail=%s status=%s confirmation_url_present=%s",
        metadata.get("order_uid"),
        _tail(str(data.get("id") or "")),
        str(data.get("status") or "").strip() or None,
        bool((data.get("confirmation") or {}).get("confirmation_url")),
    )
    return data


async def _activate_yookassa_succeeded_order(
    *,
    order: LandingYooKassaOrder,
    payment_status: str,
    payment_obj: Dict[str, Any],
    event: str,
) -> None:
    if order.activated_at is not None and order.linked_user_id is not None:
        logger.info("YooKassa activation skipped: already activated %s", _order_log_context(order))
        return

    user = await User.find_one(User.email == str(order.email).lower().strip())
    if not user:
        order.activation_error = "User with this email is not found"
        await order.save()
        logger.warning("YooKassa activation failed: user_not_found %s", _order_log_context(order))
        return

    plan = await SubscriptionPlan.find_one(SubscriptionPlan.code == order.plan_code, SubscriptionPlan.status == "active")
    if not plan:
        order.activation_error = "Plan not found or inactive"
        await order.save()
        logger.warning("YooKassa activation failed: plan_not_found %s", _order_log_context(order))
        return

    provider_tx_id = str(order.yookassa_payment_id).strip()
    tx = await SubscriptionTransaction.find_one(
        {"source": SubscriptionSource.web.value, "store.provider_tx_id": provider_tx_id}
    )
    if not tx:
        logger.info(
            "YooKassa activation creating transaction: order_uid=%s payment_id_tail=%s user_id=%s plan_code=%s amount=%s currency=%s",
            order.order_uid,
            _tail(provider_tx_id),
            str(user.id),
            plan.code,
            order.amount,
            order.currency,
        )
        tx = SubscriptionTransaction(
            user_id=user.id,
            source=SubscriptionSource.web,
            plan_code=plan.code,
            amount=order.amount,
            currency=order.currency,
            store={
                "status": "verified",
                "verified_at": utcnow().isoformat(),
                "provider": "yookassa",
                "provider_tx_id": provider_tx_id,
                "event": event,
                "payload": payment_obj,
            },
            promo={
                "code": order.promocode,
                "discount_percent": order.discount_percent,
            },
        )
        await tx.insert()
    else:
        logger.info(
            "YooKassa activation reusing existing transaction: order_uid=%s payment_id_tail=%s tx_id=%s",
            order.order_uid,
            _tail(provider_tx_id),
            str(tx.id),
        )

    sub = await upsert_subscription(
        user_id=user.id,
        plan_code=plan.code,
        source=SubscriptionSource.web,
        add_days=int(plan.duration_days),
        tx_id=tx.id,
    )
    logger.info(
        "YooKassa activation subscription upserted: order_uid=%s payment_id_tail=%s subscription_id=%s user_id=%s expires_at=%s",
        order.order_uid,
        _tail(provider_tx_id),
        str(sub.id),
        str(user.id),
        sub.expires_at,
    )

    order.linked_user_id = user.id
    order.activated_at = utcnow()
    order.activation_error = None
    order.yookassa_status = payment_status or "succeeded"
    order.metadata = {
        **dict(order.metadata or {}),
        "subscription_id": str(sub.id),
        "user_id": str(user.id),
    }
    await order.save()
    logger.info("YooKassa activation completed: %s", _order_log_context(order))


async def create_plan(payload: SubscriptionPlanCreateIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    code = (payload.code or "").strip()
    exists = await SubscriptionPlan.find_one(SubscriptionPlan.code == code)
    if exists:
        raise HTTPException(status_code=409, detail="Plan code already exists")

    doc = SubscriptionPlan(**payload.model_dump())
    await doc.insert()
    return plan_to_out(doc)


@router.post("/subscription/activate", response_model=SubscriptionActivateOut)
async def activate_subscription(payload: SubscriptionActivateIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    user = await activate_google_play_premium(payload.user_id, payload.product_id)
    return SubscriptionActivateOut(
        userId=str(user.id),
        isPremium=bool(getattr(user.flags, "is_premium", False)),
        premiumUntil=getattr(user.flags, "premium_until", None),
    )


@router.post("/subscription/activate-premium", response_model=PremiumActivateByProductOut)
async def activate_premium_by_product(payload: PremiumActivateByProductIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    product_id = (payload.product_id or "").strip()
    duration_days = SUBSCRIPTION_DURATIONS.get(product_id)
    if duration_days is None:
        raise HTTPException(status_code=400, detail="Invalid product_id")

    platform = (payload.platform or "").strip().lower()
    if platform == "google_play":
        source = SubscriptionSource.googleplay
    elif platform == "apple":
        source = SubscriptionSource.appstore
    else:
        raise HTTPException(status_code=400, detail="Invalid platform")

    plan_code_by_days = {
        30: "plan_30d",
        90: "plan_90d",
        365: "plan_365d",
    }
    plan_code = plan_code_by_days.get(int(duration_days), f"plan_{int(duration_days)}d")

    sub = await upsert_subscription(
        user_id=current_user.id,
        plan_code=plan_code,
        source=source,
        add_days=int(duration_days),
        tx_id=None,
    )

    status, is_active, in_grace = compute_subscription_status(sub)
    out = sub_to_out(sub)
    out.status = status

    return PremiumActivateByProductOut(
        platform=platform,
        product_name=(payload.product_name or "").strip(),
        product_id=product_id,
        duration_days=int(duration_days),
        subscription=out,
        is_active=is_active,
        in_grace=in_grace,
        expires_at=sub.expires_at,
    )


@router.post("/subscription/web/yookassa/init", response_model=LandingYooKassaInitOut)
async def init_web_yookassa_payment(payload: LandingYooKassaInitIn):
    tariff = (payload.tariff or "").strip()
    logger.info(
        "YooKassa init requested: tariff=%s email_masked=%s promo=%s return_url_present=%s fio_present=%s",
        tariff,
        _mask_email(payload.email),
        normalize_code(payload.promocode or "") or None,
        bool((payload.return_url or "").strip()),
        bool((payload.fio or "").strip()),
    )
    plan = await SubscriptionPlan.find_one(SubscriptionPlan.code == tariff, SubscriptionPlan.status == "active")
    if not plan:
        available = await SubscriptionPlan.find(SubscriptionPlan.status == "active").to_list()
        available_codes = [p.code for p in available]
        logger.warning(
            "YooKassa init failed: tariff_not_found requested_tariff=%s available_tariffs=%s email_masked=%s",
            tariff,
            available_codes,
            _mask_email(payload.email),
        )
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Tariff not found",
                "requested_tariff": tariff,
                "available_tariffs": available_codes,
            },
        )

    base_amount, currency = _resolve_web_plan_price(plan)
    promo_code, discount_percent = await _resolve_promocode_discount(payload.promocode)

    amount = base_amount
    if discount_percent > 0:
        amount = (base_amount * Decimal(100 - discount_percent) / Decimal(100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if amount <= Decimal("0"):
            amount = Decimal("0.01")
    logger.info(
        "YooKassa init pricing resolved: tariff=%s plan_code=%s base_amount=%s final_amount=%s currency=%s discount_percent=%s promo=%s",
        tariff,
        plan.code,
        _format_money(base_amount),
        _format_money(amount),
        currency,
        discount_percent,
        promo_code,
    )

    email_lc = str(payload.email).lower().strip()
    order_uid = uuid.uuid4().hex
    return_url = _resolve_return_url(payload.return_url)
    shop_id, secret_key = _resolve_yookassa_credentials()

    yookassa_data = await _create_yookassa_payment(
        shop_id=shop_id,
        secret_key=secret_key,
        amount=amount,
        currency=currency,
        return_url=return_url,
        description=f"Subscription {plan.code}",
        metadata={
            "order_uid": order_uid,
            "tariff": tariff,
            "email": email_lc,
            "promo": promo_code or "",
        },
    )

    payment_id = str(yookassa_data.get("id") or "").strip()
    payment_status = str(yookassa_data.get("status") or "").strip()
    confirmation_url = str((yookassa_data.get("confirmation") or {}).get("confirmation_url") or "").strip()
    if not payment_id or not confirmation_url:
        logger.warning(
            "YooKassa init failed: missing payment data order_uid=%s payment_id_tail=%s status=%s confirmation_url_present=%s",
            order_uid,
            _tail(payment_id),
            payment_status or None,
            bool(confirmation_url),
        )
        raise HTTPException(status_code=502, detail="YooKassa response missing payment data")

    order = LandingYooKassaOrder(
        order_uid=order_uid,
        fio=(payload.fio or "").strip(),
        email=email_lc,
        tariff=tariff,
        plan_code=plan.code,
        promocode=promo_code,
        discount_percent=discount_percent,
        amount=float(amount),
        currency=currency,
        return_url=return_url,
        yookassa_payment_id=payment_id,
        yookassa_status=payment_status or "pending",
        confirmation_url=confirmation_url,
        metadata={
            "tariff": tariff,
            "email": email_lc,
            "promo": promo_code,
        },
        payload=yookassa_data,
    )
    await order.insert()
    logger.info(
        "YooKassa init order stored: order_uid=%s payment_id_tail=%s status=%s plan_code=%s tariff=%s email_masked=%s promo=%s amount=%s currency=%s",
        order_uid,
        _tail(payment_id),
        payment_status or "pending",
        plan.code,
        tariff,
        _mask_email(email_lc),
        promo_code,
        float(amount),
        currency,
    )

    return LandingYooKassaInitOut(
        order_id=order_uid,
        payment_id=payment_id,
        payment_status=payment_status or "pending",
        confirmation_url=confirmation_url,
        plan_code=plan.code,
        tariff=tariff,
        amount=float(amount),
        currency=currency,
        discount_percent=discount_percent,
        promocode=promo_code,
    )


@router.post("/subscription/web/yookassa/webhook", response_model=YooKassaWebhookOut)
async def yookassa_webhook(payload: Dict[str, Any], x_webhook_token: Optional[str] = Header(default=None)):
    if not webhook_token_ok(x_webhook_token):
        logger.warning(
            "YooKassa webhook rejected: invalid_token token_tail=%s payload_keys=%s",
            _tail(x_webhook_token, size=4),
            sorted(payload.keys()) if isinstance(payload, dict) else None,
        )
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    event = str(payload.get("event") or "").strip()
    payment_obj = payload.get("object") if isinstance(payload.get("object"), dict) else {}
    payment_id = str(payment_obj.get("id") or "").strip()
    payment_status = str(payment_obj.get("status") or "").strip().lower()
    logger.info(
        "YooKassa webhook received: event=%s payment_id_tail=%s payment_status=%s",
        event or None,
        _tail(payment_id),
        payment_status or None,
    )

    if not payment_id:
        logger.warning("YooKassa webhook rejected: missing payment id event=%s", event or None)
        raise HTTPException(status_code=400, detail="Missing payment id")

    order = await LandingYooKassaOrder.find_one(LandingYooKassaOrder.yookassa_payment_id == payment_id)
    if not order:
        logger.warning(
            "YooKassa webhook ignored: order_not_found payment_id_tail=%s event=%s status=%s",
            _tail(payment_id),
            event or None,
            payment_status or None,
        )
        return YooKassaWebhookOut(ok=True)

    logger.info("YooKassa webhook order matched: %s", _order_log_context(order))
    order.yookassa_status = payment_status or order.yookassa_status
    order.payload = payload

    if payment_status != "succeeded":
        await order.save()
        logger.info(
            "YooKassa webhook stored non-terminal/non-success status: %s",
            _order_log_context(order),
        )
        return YooKassaWebhookOut(ok=True)

    await _activate_yookassa_succeeded_order(
        order=order,
        payment_status=payment_status,
        payment_obj=payment_obj,
        event=event or "webhook",
    )
    return YooKassaWebhookOut(ok=True)


@router.get("/subscription/web/yookassa/order/{order_id}", response_model=LandingYooKassaOrderStatusOut)
async def yookassa_order_status(order_id: str):
    oid = (order_id or "").strip()
    if not oid:
        logger.warning("YooKassa order status rejected: empty order_id")
        raise HTTPException(status_code=400, detail="order_id is required")

    order = await LandingYooKassaOrder.find_one(LandingYooKassaOrder.order_uid == oid)
    if not order:
        logger.warning("YooKassa order status not found: order_uid=%s", oid)
        raise HTTPException(status_code=404, detail="Order not found")

    if order.yookassa_payment_id and (order.yookassa_status or "").lower() in {"pending", "waiting_for_capture"}:
        try:
            shop_id, secret_key = _resolve_yookassa_credentials()
            remote_payment = await _get_yookassa_payment(
                shop_id=shop_id,
                secret_key=secret_key,
                payment_id=order.yookassa_payment_id,
            )
            remote_status = str(remote_payment.get("status") or "").strip().lower()
            if remote_status and remote_status != (order.yookassa_status or "").lower():
                logger.info(
                    "YooKassa order status sync updated local status: order_uid=%s payment_id_tail=%s old_status=%s new_status=%s",
                    order.order_uid,
                    _tail(order.yookassa_payment_id),
                    order.yookassa_status,
                    remote_status,
                )
                order.yookassa_status = remote_status
                order.payload = remote_payment
                await order.save()

            if remote_status == "succeeded":
                await _activate_yookassa_succeeded_order(
                    order=order,
                    payment_status=remote_status,
                    payment_obj=remote_payment,
                    event="status_poll_sync",
                )
        except HTTPException as exc:
            logger.warning(
                "YooKassa order status sync failed: order_uid=%s payment_id_tail=%s status_code=%s detail=%s",
                order.order_uid,
                _tail(order.yookassa_payment_id),
                exc.status_code,
                exc.detail,
            )

    logger.info(
        "YooKassa order status polled: order_uid=%s payment_id_tail=%s payment_status=%s activated=%s user_id=%s activation_error=%s",
        order.order_uid,
        _tail(order.yookassa_payment_id),
        order.yookassa_status,
        bool(order.activated_at and order.linked_user_id),
        str(order.linked_user_id) if order.linked_user_id else None,
        order.activation_error,
    )

    return LandingYooKassaOrderStatusOut(
        order_id=order.order_uid,
        payment_status=order.yookassa_status,
        activated=bool(order.activated_at and order.linked_user_id),
        user_id=str(order.linked_user_id) if order.linked_user_id else None,
        activated_at=order.activated_at,
        activation_error=order.activation_error,
    )


@router.post("/subscription/purchase", response_model=PurchaseInitOut)
async def purchase(payload: PurchaseIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    plan = await SubscriptionPlan.find_one(SubscriptionPlan.code == payload.plan_code, SubscriptionPlan.status == "active")
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    store = dict(payload.store or {})
    store["status"] = "pending"
    store["created_at"] = utcnow().isoformat()

    price = {}
    if isinstance(plan.prices, dict):
        price = dict(plan.prices.get(str(payload.source), {}) or {})
    resolved_amount = price.get("amount", payload.amount)
    resolved_currency = price.get("currency", payload.currency)

    tx = SubscriptionTransaction(
        user_id=current_user.id,
        source=payload.source,
        plan_code=plan.code,
        amount=resolved_amount,
        currency=resolved_currency,
        store=store,
        promo={},
    )
    await tx.insert()

    payment_url = None
    payment_provider = None
    if payload.source == SubscriptionSource.web:
        payment_provider = "yookassa"
        payment_url = build_web_checkout_url(str(tx.id))
        store["payment_provider"] = payment_provider
        if payment_url:
            store["payment_url"] = payment_url
        tx.store = store
        await tx.save()

    sub = await Subscription.find_one(Subscription.user_id == current_user.id)
    if not sub:
        return PurchaseInitOut(
            transaction_id=str(tx.id),
            transaction_status="pending",
            payment_url=payment_url,
            payment_provider=payment_provider,
            subscription=None,
            is_active=False,
            in_grace=False,
            expires_at=None,
        )

    status, is_active, in_grace = compute_subscription_status(sub)
    out = sub_to_out(sub)
    out.status = status
    return PurchaseInitOut(
        transaction_id=str(tx.id),
        transaction_status="pending",
        payment_url=payment_url,
        payment_provider=payment_provider,
        subscription=out,
        is_active=is_active,
        in_grace=in_grace,
        expires_at=sub.expires_at,
    )


@router.post("/subscription/verify", response_model=PurchaseVerifyOut)
async def verify_purchase(payload: PurchaseVerifyIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    try:
        tx_id = PydanticObjectId(payload.transaction_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid transaction_id")

    tx = await SubscriptionTransaction.get(tx_id)
    if not tx or tx.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Transaction not found")

    store = dict(getattr(tx, "store", {}) or {})
    status_str = str(store.get("status", "pending"))

    if status_str == "verified":
        sub = await Subscription.find_one(Subscription.user_id == current_user.id)
        if not sub:
            return PurchaseVerifyOut(
                transaction_id=str(tx.id),
                transaction_status="verified",
                subscription=None,
                is_active=False,
                in_grace=False,
                expires_at=None,
            )
        s, is_active, in_grace = compute_subscription_status(sub)
        out = sub_to_out(sub)
        out.status = s
        return PurchaseVerifyOut(
            transaction_id=str(tx.id),
            transaction_status="verified",
            subscription=out,
            is_active=is_active,
            in_grace=in_grace,
            expires_at=sub.expires_at,
        )

    provider = (payload.provider or "").strip().lower()
    if not provider or len(provider) > 32:
        raise HTTPException(status_code=400, detail="Invalid provider")

    expected_provider = expected_provider_for_source(tx.source)
    if expected_provider and provider != expected_provider:
        raise HTTPException(
            status_code=400,
            detail=f"Provider/source mismatch. Expected '{expected_provider}' for source '{tx.source.value}'",
        )

    receipt = dict(payload.receipt or {})
    if not receipt:
        raise HTTPException(status_code=400, detail="Receipt required")

    if payload.provider_tx_id:
        dup = await SubscriptionTransaction.find_one(
            {
                "store.provider": provider,
                "store.provider_tx_id": payload.provider_tx_id,
                "_id": {"$ne": tx.id},
            }
        )
        if dup:
            raise HTTPException(status_code=409, detail="provider_tx_id already used")

    plan = await SubscriptionPlan.find_one(SubscriptionPlan.code == tx.plan_code, SubscriptionPlan.status == "active")
    if not plan:
        store["status"] = "failed"
        store["error"] = "plan_not_found"
        store["updated_at"] = utcnow().isoformat()
        tx.store = store
        await tx.save()
        raise HTTPException(status_code=400, detail="Plan not found")

    store["status"] = "verified"
    store["verified_at"] = utcnow().isoformat()
    store["provider"] = provider
    store["receipt"] = receipt
    if payload.provider_tx_id:
        store["provider_tx_id"] = payload.provider_tx_id

    tx.store = store
    await tx.save()

    sub = await upsert_subscription(
        user_id=current_user.id,
        plan_code=plan.code,
        source=tx.source,
        add_days=int(plan.duration_days),
        tx_id=tx.id,
    )

    s, is_active, in_grace = compute_subscription_status(sub)
    out = sub_to_out(sub)
    out.status = s
    return PurchaseVerifyOut(
        transaction_id=str(tx.id),
        transaction_status="verified",
        subscription=out,
        is_active=is_active,
        in_grace=in_grace,
        expires_at=sub.expires_at,
    )


@router.post("/subscription/promo/preview", response_model=PromoPreviewOut)
async def preview_promo(payload: PromoActivateIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    code = normalize_code(payload.code)
    promo = await PromoCode.find_one(PromoCode.code == code)
    if not promo:
        raise HTTPException(status_code=400, detail="Invalid promo code")

    if promo.status != PromoStatus.active:
        raise HTTPException(status_code=400, detail="Promo code disabled")

    if _promo_is_expired(promo):
        raise HTTPException(status_code=400, detail="Promo code expired")

    if not _promo_duration_is_allowed(int(promo.duration_days)):
        raise HTTPException(status_code=400, detail="Promo code has unsupported duration")

    remaining = max(0, int(promo.max_uses) - int(promo.used_count))
    if remaining <= 0:
        raise HTTPException(status_code=400, detail="Promo code limit reached or expired")

    return PromoPreviewOut(
        valid=True,
        code=promo.code,
        discount_percent=int(getattr(promo, "discount_percent", 0) or 0),
        duration_days=int(promo.duration_days),
        max_uses=int(promo.max_uses),
        used_count=int(promo.used_count),
        remaining_uses=remaining,
        expires_at=promo.expires_at,
        status=promo.status,
    )


@router.post("/subscription/activate-promo", response_model=PurchaseOut)
async def activate_promo(payload: PromoActivateIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    code = normalize_code(payload.code)
    promo = await PromoCode.find_one(PromoCode.code == code)
    if not promo:
        raise HTTPException(status_code=400, detail="Invalid promo code")

    if promo.status != PromoStatus.active:
        raise HTTPException(status_code=400, detail="Promo code disabled")

    if _promo_is_expired(promo):
        raise HTTPException(status_code=400, detail="Promo code expired")

    if int(promo.duration_days) <= 0:
        raise HTTPException(status_code=400, detail="Promo code invalid duration")
    if not _promo_duration_is_allowed(int(promo.duration_days)):
        raise HTTPException(status_code=400, detail="Promo code has unsupported duration")

    tx = SubscriptionTransaction(
        user_id=current_user.id,
        source=SubscriptionSource.promo,
        plan_code="promo",
        amount=None,
        currency=None,
        store={"status": "verified", "verified_at": utcnow().isoformat(), "promo": True},
        promo={
            "code": code,
            "promo_id": str(promo.id),
            "duration_days": int(promo.duration_days),
            "discount_percent": int(getattr(promo, "discount_percent", 0) or 0),
        },
    )
    await tx.insert()

    redemption = PromoRedemption(
        code=code,
        promo_code_id=promo.id,
        user_id=current_user.id,
        subscription_transaction_id=tx.id,
    )
    try:
        await redemption.insert()
    except DuplicateKeyError:
        try:
            await tx.delete()
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="Promo code already used")

    claimed = await promo_atomic_claim(code)
    if not claimed:
        try:
            await redemption.delete()
        except Exception:
            pass
        try:
            await tx.delete()
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="Promo code limit reached or expired")

    duration_days = int(claimed.get("duration_days", promo.duration_days) or 0)
    if duration_days <= 0:
        await promo_atomic_rollback(code)
        try:
            await redemption.delete()
        except Exception:
            pass
        try:
            await tx.delete()
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="Promo code invalid duration")
    if not _promo_duration_is_allowed(duration_days):
        await promo_atomic_rollback(code)
        try:
            await redemption.delete()
        except Exception:
            pass
        try:
            await tx.delete()
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="Promo code has unsupported duration")

    existing = await Subscription.find_one(Subscription.user_id == current_user.id)
    plan_code = (existing.plan_code if existing else None) or "promo"

    try:
        sub = await upsert_subscription(
            user_id=current_user.id,
            plan_code=plan_code,
            source=SubscriptionSource.promo,
            add_days=duration_days,
            tx_id=tx.id,
        )
        promo_discount_percent = int(claimed.get("discount_percent", getattr(promo, "discount_percent", 0)) or 0)
        return PurchaseOut(
            subscription=sub_to_out(sub),
            promo_code=code,
            promo_duration_days=duration_days,
            promo_discount_percent=promo_discount_percent,
        )
    except Exception:
        await promo_atomic_rollback(code)
        try:
            await redemption.delete()
        except Exception:
            pass
        try:
            await tx.delete()
        except Exception:
            pass
        raise


async def list_promos(
    status: Optional[PromoStatus] = None,
    skip: int = 0,
    limit: int = 20,
    q: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    require_auth(current_user)

    limit = clamp_limit(limit)
    query = PromoCode.find()

    if status is not None:
        query = query.find(PromoCode.status == status)

    if q:
        query = query.find({"code": {"$regex": normalize_code(q), "$options": "i"}})

    total = await query.count()
    items = await query.sort("-created_at").skip(int(skip)).limit(limit).to_list()
    return PromoCodesOut(items=[promo_to_out(p) for p in items], total=int(total), skip=int(skip), limit=int(limit))


async def create_promo(payload: PromoCodeCreateIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    code = normalize_code(payload.code)
    exists = await PromoCode.find_one(PromoCode.code == code)
    if exists:
        raise HTTPException(status_code=409, detail="Promo code already exists")

    doc = PromoCode(
        code=code,
        discount_percent=int(payload.discount_percent),
        duration_days=payload.duration_days,
        max_uses=payload.max_uses,
        used_count=0,
        expires_at=payload.expires_at,
        status=payload.status,
    )
    await doc.insert()
    return promo_to_out(doc)


async def create_promo_batch(payload: PromoBatchCreateIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    batch = PromoCodeBatch(
        name=payload.name,
        discount_percent=int(payload.discount_percent),
        duration_days=payload.duration_days,
        max_uses_per_code=payload.max_uses_per_code,
        codes_count=payload.codes_count,
        created_by_admin_id=current_user.id,
    )
    try:
        await batch.insert()
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Batch name already exists")

    created = 0

    while created < int(payload.codes_count):
        c = PromoCode(
            batch_id=batch.id,
            code=code_random(int(payload.code_length)),
            discount_percent=int(payload.discount_percent),
            duration_days=payload.duration_days,
            max_uses=payload.max_uses_per_code,
            used_count=0,
            expires_at=None,
            status=PromoStatus.active,
        )
        try:
            await c.insert()
            created += 1
        except DuplicateKeyError:
            continue

    return PromoBatchCreateOut(
        batch=PromoBatchOut(
            id=str(batch.id),
            name=batch.name,
            discount_percent=int(getattr(batch, "discount_percent", 0) or 0),
            duration_days=int(batch.duration_days),
            max_uses_per_code=int(batch.max_uses_per_code),
            codes_count=int(batch.codes_count),
            created_at=getattr(batch, "created_at", None),
        ),
        created_codes=int(created),
    )


async def export_promo_batch_csv(batch_id: str, current_user=Depends(get_current_user)):
    require_auth(current_user)

    try:
        bid = PydanticObjectId(batch_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid batch_id")

    batch = await PromoCodeBatch.get(bid)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    codes = await PromoCode.find(PromoCode.batch_id == bid).sort("code").to_list()

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["code", "discount_percent", "duration_days", "max_uses", "used_count", "status", "expires_at"])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        for c in codes:
            w.writerow(
                [
                    c.code,
                    int(getattr(c, "discount_percent", 0) or 0),
                    int(c.duration_days),
                    int(c.max_uses),
                    int(c.used_count),
                    str(c.status),
                    c.expires_at.isoformat() if c.expires_at else "",
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    filename = f"promo_batch_{batch.name}.csv".replace(" ", "_")
    return StreamingResponse(gen(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

async def promo_stats(
    batch_id: Optional[str] = None,
    promo_code_id: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    require_auth(current_user)

    if batch_id:
        try:
            bid = PydanticObjectId(batch_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid batch_id")

        promo_docs = await PromoCode.find(PromoCode.batch_id == bid).to_list()
        promo_ids = [p.id for p in promo_docs]

        promo_codes_total = len(promo_ids)
        redemptions_total = 0
        if promo_ids:
            redemptions_total = int(await PromoRedemption.find({"promo_code_id": {"$in": promo_ids}}).count())

        return PromoStatsOut(
            promo_codes_total=int(promo_codes_total),
            redemptions_total=int(redemptions_total),
        )

    if promo_code_id:
        try:
            pid = PydanticObjectId(promo_code_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid promo_code_id")

        promo_codes_total = int(await PromoCode.find(PromoCode.id == pid).count())
        redemptions_total = int(await PromoRedemption.find(PromoRedemption.promo_code_id == pid).count())

        return PromoStatsOut(
            promo_codes_total=int(promo_codes_total),
            redemptions_total=int(redemptions_total),
        )

    promo_codes_total = int(await PromoCode.find().count())
    redemptions_total = int(await PromoRedemption.find().count())
    return PromoStatsOut(promo_codes_total=promo_codes_total, redemptions_total=redemptions_total)


@router.get("/subscription", response_model=SubscriptionGetOut)
async def get_subscription(current_user=Depends(get_current_user)):
    require_auth(current_user)
    sub = await Subscription.find_one(Subscription.user_id == current_user.id)
    if not sub:
        return SubscriptionGetOut(
            subscription=None,
            is_active=False,
            in_grace=False,
            expires_at=None,
        )
    status, is_active, in_grace = compute_subscription_status(sub)
    return SubscriptionGetOut(
        subscription=sub_to_out(sub),
        is_active=is_active,
        in_grace=in_grace,
        expires_at=sub.expires_at,
    )
