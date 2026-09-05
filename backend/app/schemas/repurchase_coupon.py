"""Repurchase coupon API contracts."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CouponRuleCreate(BaseModel):
    rule_key: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    benefit_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    amount_minor: int = Field(gt=0)
    minimum_spend_minor: int = Field(ge=0)
    product_scope: Literal["all", "specified"] = "all"
    eligible_product_refs: list[str] = Field(default_factory=list, max_length=200)
    channel_scope: Literal["online", "store", "both"]
    validity_mode: Literal["relative", "fixed"]
    valid_days: int | None = Field(default=None, ge=1, le=365)
    fixed_valid_from: datetime | None = None
    fixed_valid_until: datetime | None = None
    issuance_limit: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_commercial_terms(self):
        if self.minimum_spend_minor and self.minimum_spend_minor <= self.amount_minor:
            raise ValueError("minimum_spend_minor must exceed amount_minor")
        if self.product_scope == "specified" and not self.eligible_product_refs:
            raise ValueError("specified product scope requires product refs")
        if self.product_scope == "all" and self.eligible_product_refs:
            raise ValueError("all product scope cannot carry product refs")
        if self.validity_mode == "relative" and (
            self.valid_days is None or self.fixed_valid_from is not None or self.fixed_valid_until is not None
        ):
            raise ValueError("relative validity requires valid_days only")
        if self.validity_mode == "fixed" and (
            self.valid_days is not None
            or self.fixed_valid_from is None
            or self.fixed_valid_until is None
            or self.fixed_valid_until <= self.fixed_valid_from
        ):
            raise ValueError("fixed validity requires an increasing interval")
        return self


class CouponRuleTransition(BaseModel):
    action: Literal["publish", "pause", "resume", "end"]
    idempotency_key: str = Field(min_length=1, max_length=120)


class CouponIssueRequest(BaseModel):
    membership_id: uuid.UUID
    rule_version_id: uuid.UUID
    source_claim_id: uuid.UUID | None = None
    source_scan_event_id: uuid.UUID | None = None
    source_scan_time: datetime | None = None
    source_public_id: str | None = Field(default=None, max_length=20)
    idempotency_key: str = Field(min_length=1, max_length=120)


class CouponReserveRequest(BaseModel):
    membership_id: uuid.UUID
    order_ref: str = Field(min_length=1, max_length=160)
    goods_subtotal_minor: int = Field(gt=0)
    eligible_subtotal_minor: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=120)


class CouponOrderTransition(BaseModel):
    order_ref: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=120)


class CouponReverseRequest(CouponOrderTransition):
    full_refund: bool


class CouponRevokeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=120)


class StoreRedemptionRequest(BaseModel):
    redemption_token: str = Field(min_length=20, max_length=2048)


class CouponItem(BaseModel):
    id: uuid.UUID
    coupon_number: str
    membership_id: uuid.UUID
    rule_version_id: uuid.UUID
    name: str
    amount_minor: int
    minimum_spend_minor: int
    product_scope: str
    eligible_product_refs: list[str]
    channel_scope: str
    status: str
    valid_from: datetime
    valid_until: datetime
    reserved_order_ref: str | None
    reservation_expires_at: datetime | None
    used_order_ref: str | None
    used_store_id: uuid.UUID | None


class StoreRedemptionTokenResponse(BaseModel):
    redemption_token: str
    expires_in: int = 300
