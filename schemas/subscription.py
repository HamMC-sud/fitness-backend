from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from models.enums import SubscriptionSource, SubscriptionStatus, PromoStatus


class SubscriptionPlanOut(BaseModel):
    id: str
    code: str
    duration_days: int
    prices: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    status: str


class SubscriptionOut(BaseModel):
    id: str
    status: SubscriptionStatus
    plan_code: str
    source: SubscriptionSource
    started_at: datetime
    expires_at: datetime
    grace_until: Optional[datetime] = None
    auto_renew: bool = True
    last_transaction_id: Optional[str] = None


class SubscriptionGetOut(BaseModel):
    subscription: Optional[SubscriptionOut] = None
    is_active: bool
    in_grace: bool
    expires_at: Optional[datetime] = None


class SubscriptionPlansOut(BaseModel):
    items: List[SubscriptionPlanOut] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 20


class SubscriptionPlanCreateIn(BaseModel):
    code: str
    duration_days: int = Field(ge=1, le=3650)
    prices: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    status: str = "active"


class PurchaseIn(BaseModel):
    plan_code: str
    source: SubscriptionSource
    amount: Optional[float] = None
    currency: Optional[str] = None
    store: Dict[str, Any] = Field(default_factory=dict)


class PurchaseOut(BaseModel):
    subscription: SubscriptionOut


class PurchaseInitOut(BaseModel):
    transaction_id: str
    transaction_status: str
    subscription: Optional[SubscriptionOut] = None
    is_active: bool
    in_grace: bool
    expires_at: Optional[datetime] = None


class PurchaseVerifyIn(BaseModel):
    transaction_id: str
    provider: str = Field(min_length=1, max_length=32)
    receipt: Dict[str, Any] = Field(default_factory=dict)
    provider_tx_id: Optional[str] = Field(default=None, max_length=128)


class PurchaseVerifyOut(BaseModel):
    transaction_id: str
    transaction_status: str
    subscription: Optional[SubscriptionOut] = None
    is_active: bool
    in_grace: bool
    expires_at: Optional[datetime] = None


class PromoActivateIn(BaseModel):
    code: str


class CancelOut(BaseModel):
    status: str


class PromoCodeOut(BaseModel):
    id: str
    code: str
    duration_days: int
    max_uses: int
    used_count: int
    expires_at: Optional[datetime] = None
    status: PromoStatus


class PromoCodesOut(BaseModel):
    items: List[PromoCodeOut] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 20


class PromoCodeCreateIn(BaseModel):
    code: str
    duration_days: int = Field(ge=1, le=3650)
    max_uses: int = Field(default=1, ge=1, le=10_000_000)
    expires_at: Optional[datetime] = None
    status: PromoStatus = PromoStatus.active


class PromoBatchCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    duration_days: int = Field(ge=1, le=3650)
    max_uses_per_code: int = Field(ge=1, le=1_000_000)
    codes_count: int = Field(ge=1, le=5000)
    code_length: int = Field(default=10, ge=6, le=24)


class PromoBatchOut(BaseModel):
    id: str
    name: str
    duration_days: int
    max_uses_per_code: int
    codes_count: int
    created_at: Optional[datetime] = None


class PromoBatchCreateOut(BaseModel):
    batch: PromoBatchOut
    created_codes: int


class PromoStatsOut(BaseModel):
    promo_codes_total: int
    redemptions_total: int


class YooKassaWebhookIn(BaseModel):
    transaction_id: str
    status: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class YooKassaWebhookOut(BaseModel):
    ok: bool
