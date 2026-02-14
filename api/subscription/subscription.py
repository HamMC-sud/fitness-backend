from __future__ import annotations

import csv
import io
import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from starlette.concurrency import run_in_threadpool

from api.auth.config import get_current_user
from models.enums import PromoStatus, SubscriptionSource, SubscriptionStatus
from models.promo import PromoCode, PromoCodeBatch, PromoRedemption
from models.subscription import Subscription, SubscriptionPlan, SubscriptionTransaction
from schemas.subscription import (
    CancelOut,
    PromoActivateIn,
    PromoBatchCreateIn,
    PromoBatchCreateOut,
    PromoBatchOut,
    PromoCodeCreateIn,
    PromoCodeOut,
    PromoCodesOut,
    PromoStatsOut,
    PurchaseIn,
    PurchaseInitOut,
    PurchaseOut,
    PurchaseVerifyIn,
    PurchaseVerifyOut,
    SubscriptionGetOut,
    SubscriptionOut,
    SubscriptionPlanCreateIn,
    SubscriptionPlanOut,
    SubscriptionPlansOut,
    YooKassaWebhookIn,
    YooKassaWebhookOut,
)

router = APIRouter(tags=["subscription"])


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
        duration_days=int(p.duration_days),
        max_uses=int(p.max_uses),
        used_count=int(p.used_count),
        expires_at=p.expires_at,
        status=p.status,
    )


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
        await existing.save()
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
    )
    await sub.insert()
    return sub


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


@router.get("/subscription", response_model=SubscriptionGetOut)
async def get_subscription(current_user=Depends(get_current_user)):
    require_auth(current_user)

    sub = await Subscription.find_one(Subscription.user_id == current_user.id)
    if not sub:
        return SubscriptionGetOut(subscription=None, is_active=False, in_grace=False, expires_at=None)

    status, is_active, in_grace = compute_subscription_status(sub)
    out = sub_to_out(sub)
    out.status = status
    return SubscriptionGetOut(subscription=out, is_active=is_active, in_grace=in_grace, expires_at=sub.expires_at)


@router.get("/subscription/plans", response_model=SubscriptionPlansOut)
async def list_plans(status: str = Query(default="active"), skip: int = 0, limit: int = 20):
    limit = clamp_limit(limit)
    q = SubscriptionPlan.find(SubscriptionPlan.status == status).sort("code")
    total = await q.count()
    items = await q.skip(int(skip)).limit(limit).to_list()
    return SubscriptionPlansOut(items=[plan_to_out(p) for p in items], total=int(total), skip=int(skip), limit=int(limit))


@router.post("/subscription/plans", response_model=SubscriptionPlanOut)
async def create_plan(payload: SubscriptionPlanCreateIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    code = (payload.code or "").strip()
    exists = await SubscriptionPlan.find_one(SubscriptionPlan.code == code)
    if exists:
        raise HTTPException(status_code=409, detail="Plan code already exists")

    doc = SubscriptionPlan(**payload.model_dump())
    await doc.insert()
    return plan_to_out(doc)


@router.post("/subscription/purchase", response_model=PurchaseInitOut)
async def purchase(payload: PurchaseIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    plan = await SubscriptionPlan.find_one(SubscriptionPlan.code == payload.plan_code, SubscriptionPlan.status == "active")
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    store = dict(payload.store or {})
    store["status"] = "pending"
    store["created_at"] = utcnow().isoformat()

    tx = SubscriptionTransaction(
        user_id=current_user.id,
        source=payload.source,
        plan_code=plan.code,
        amount=payload.amount,
        currency=payload.currency,
        store=store,
        promo={},
    )
    await tx.insert()

    sub = await Subscription.find_one(Subscription.user_id == current_user.id)
    if not sub:
        return PurchaseInitOut(
            transaction_id=str(tx.id),
            transaction_status="pending",
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

    receipt = dict(payload.receipt or {})
    if not receipt:
        raise HTTPException(status_code=400, detail="Receipt required")

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


@router.post("/payments/yookassa/webhook", response_model=YooKassaWebhookOut)
async def yookassa_webhook(body: YooKassaWebhookIn, x_webhook_token: Optional[str] = Header(default=None)):
    if not webhook_token_ok(x_webhook_token):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        tx_id = PydanticObjectId(body.transaction_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid transaction_id")

    tx = await SubscriptionTransaction.get(tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    st = (body.status or "").strip().lower()
    store = dict(getattr(tx, "store", {}) or {})
    store["yookassa"] = dict(body.payload or {})
    store["updated_at"] = utcnow().isoformat()

    if st in {"succeeded", "paid", "success"}:
        plan = await SubscriptionPlan.find_one(SubscriptionPlan.code == tx.plan_code, SubscriptionPlan.status == "active")
        store["status"] = "verified"
        store["verified_at"] = utcnow().isoformat()
        tx.store = store
        await tx.save()
        if plan:
            await upsert_subscription(
                user_id=tx.user_id,
                plan_code=plan.code,
                source=tx.source,
                add_days=int(plan.duration_days),
                tx_id=tx.id,
            )
        return YooKassaWebhookOut(ok=True)

    if st in {"failed", "canceled", "cancelled", "refunded"}:
        store["status"] = "failed"
        store["error"] = st
        tx.store = store
        await tx.save()
        return YooKassaWebhookOut(ok=True)

    tx.store = store
    await tx.save()
    return YooKassaWebhookOut(ok=True)


@router.post("/subscription/activate-promo", response_model=PurchaseOut)
async def activate_promo(payload: PromoActivateIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    code = normalize_code(payload.code)
    promo = await PromoCode.find_one(PromoCode.code == code)
    if not promo:
        raise HTTPException(status_code=400, detail="Invalid promo code")

    if promo.status != PromoStatus.active:
        raise HTTPException(status_code=400, detail="Promo code disabled")

    if promo.expires_at is not None:
        exp = promo.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if utcnow() >= exp:
            raise HTTPException(status_code=400, detail="Promo code expired")

    if int(promo.duration_days) <= 0:
        raise HTTPException(status_code=400, detail="Promo code invalid duration")

    tx = SubscriptionTransaction(
        user_id=current_user.id,
        source=SubscriptionSource.promo,
        plan_code="promo",
        amount=None,
        currency=None,
        store={"status": "verified", "verified_at": utcnow().isoformat(), "promo": True},
        promo={"code": code, "promo_id": str(promo.id), "duration_days": int(promo.duration_days)},
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
        return PurchaseOut(subscription=sub_to_out(sub))
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


@router.post("/subscription/cancel", response_model=CancelOut)
async def cancel_subscription(current_user=Depends(get_current_user)):
    require_auth(current_user)

    sub = await Subscription.find_one(Subscription.user_id == current_user.id)
    if not sub:
        return CancelOut(status="ok")

    sub.auto_renew = False
    sub.status = SubscriptionStatus.canceled
    await sub.save()
    return CancelOut(status="ok")


@router.get("/subscription/promos", response_model=PromoCodesOut)
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


@router.post("/subscription/promos", response_model=PromoCodeOut)
async def create_promo(payload: PromoCodeCreateIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    code = normalize_code(payload.code)
    exists = await PromoCode.find_one(PromoCode.code == code)
    if exists:
        raise HTTPException(status_code=409, detail="Promo code already exists")

    doc = PromoCode(
        code=code,
        duration_days=payload.duration_days,
        max_uses=payload.max_uses,
        used_count=0,
        expires_at=payload.expires_at,
        status=payload.status,
    )
    await doc.insert()
    return promo_to_out(doc)


@router.post("/subscription/promo-batches", response_model=PromoBatchCreateOut)
async def create_promo_batch(payload: PromoBatchCreateIn, current_user=Depends(get_current_user)):
    require_auth(current_user)

    batch = PromoCodeBatch(
        name=payload.name,
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
    attempts = 0
    max_attempts = int(payload.codes_count) * 30

    while created < int(payload.codes_count) and attempts < max_attempts:
        attempts += 1
        c = PromoCode(
            batch_id=batch.id,
            code=code_random(int(payload.code_length)),
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

    if created != int(payload.codes_count):
        raise HTTPException(status_code=500, detail="Failed to generate all codes")

    return PromoBatchCreateOut(
        batch=PromoBatchOut(
            id=str(batch.id),
            name=batch.name,
            duration_days=int(batch.duration_days),
            max_uses_per_code=int(batch.max_uses_per_code),
            codes_count=int(batch.codes_count),
            created_at=getattr(batch, "created_at", None),
        ),
        created_codes=int(created),
    )


@router.get("/subscription/promo-batches/{batch_id}/export")
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
        w.writerow(["code", "duration_days", "max_uses", "used_count", "status", "expires_at"])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        for c in codes:
            w.writerow(
                [
                    c.code,
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

@router.get("/subscription/promos/stats", response_model=PromoStatsOut)
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