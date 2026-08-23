"""Cross-product commerce integration API contracts."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

CredentialDirection = Literal["yimatong_to_commerce", "commerce_to_yimatong"]


class CommerceConnectionCreate(BaseModel):
    external_tenant_ref: str = Field(min_length=1, max_length=120)
    external_shop_ref: str = Field(min_length=1, max_length=120)
    base_url: AnyHttpUrl
    capabilities: list[Literal["identity_handoff", "coupon_lifecycle", "order_events", "reconciliation"]] = Field(
        default_factory=lambda: ["identity_handoff", "coupon_lifecycle", "order_events", "reconciliation"],
        min_length=1,
    )
    idempotency_key: str = Field(min_length=1, max_length=120)


class CredentialSecret(BaseModel):
    id: uuid.UUID
    direction: CredentialDirection
    version: int
    key_prefix: str
    secret: str
    valid_from: datetime
    valid_until: datetime


class CommerceConnectionCreated(BaseModel):
    id: uuid.UUID
    external_tenant_ref: str
    external_shop_ref: str
    base_url: str
    capabilities: list[str]
    status: str
    credentials: list[CredentialSecret]


class CommerceConnectionItem(BaseModel):
    id: uuid.UUID
    external_tenant_ref: str
    external_shop_ref: str
    base_url: str
    capabilities: list[str]
    status: str
    version: int
    disconnected_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CommerceProductMappingCreate(BaseModel):
    connection_id: uuid.UUID
    external_product_ref: str = Field(min_length=1, max_length=160)
    product_id: uuid.UUID


class CommerceProductMappingItem(BaseModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    external_product_ref: str
    product_id: uuid.UUID
    created_at: datetime


class CredentialRotateRequest(BaseModel):
    direction: CredentialDirection
    idempotency_key: str = Field(min_length=1, max_length=120)


class CommerceHandoffRequest(BaseModel):
    connection_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=120)


class CommerceHandoffIssued(BaseModel):
    handoff_token: str
    expires_in: int = 300
    connection_id: uuid.UUID
    external_shop_ref: str


class CommerceHandoffRedeemRequest(BaseModel):
    handoff_token: str = Field(min_length=40, max_length=2048)


class CommerceHandoffRedeemed(BaseModel):
    connection_id: uuid.UUID
    external_tenant_ref: str
    external_shop_ref: str
    member_ref: str


class CommerceLineItemSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_ref: str = Field(min_length=1, max_length=160)
    product_ref: str = Field(min_length=1, max_length=160)
    sku_ref: str | None = Field(default=None, max_length=160)
    quantity: int = Field(gt=0)
    original_amount_fen: int = Field(ge=0)
    refunded_amount_fen: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_amounts(self):
        if self.refunded_amount_fen > self.original_amount_fen:
            raise ValueError("line_refunded_amount_exceeds_original")
        return self


class CommerceLineRefundSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_ref: str = Field(min_length=1, max_length=160)
    amount_fen: int = Field(ge=0)


class CommerceRefundSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refund_ref: str = Field(min_length=1, max_length=160)
    order_amount_fen: int = Field(gt=0)
    product_amount_fen: int = Field(ge=0)
    occurred_at: datetime
    line_refunds: list[CommerceLineRefundSnapshot] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_amounts(self):
        if self.product_amount_fen > self.order_amount_fen:
            raise ValueError("refund_product_amount_exceeds_order_amount")
        if self.line_refunds and sum(item.amount_fen for item in self.line_refunds) != self.product_amount_fen:
            raise ValueError("refund_line_amounts_do_not_match_product_amount")
        return self


class CommerceOrderSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_ref: str = Field(min_length=1, max_length=160)
    source_system: Literal["medusa_v2"]
    status: Literal["placed", "paid", "fulfilled", "completed", "cancelled", "partially_refunded", "refunded"]
    currency: Literal["CNY"]
    order_original_amount_fen: int = Field(ge=0)
    order_refunded_amount_fen: int = Field(ge=0)
    product_original_amount_fen: int = Field(ge=0)
    product_refunded_amount_fen: int = Field(ge=0)
    coverage_status: Literal["complete", "partial", "missing"]
    paid_at: datetime | None = None
    fulfilled_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    coupon_ref: uuid.UUID | None = None
    coupon_order_ref: str | None = Field(default=None, min_length=1, max_length=160)
    line_items: list[CommerceLineItemSnapshot]
    refunds: list[CommerceRefundSnapshot] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_snapshot(self):
        if self.order_refunded_amount_fen > self.order_original_amount_fen:
            raise ValueError("order_refunded_amount_exceeds_original")
        if self.product_refunded_amount_fen > self.product_original_amount_fen:
            raise ValueError("product_refunded_amount_exceeds_original")
        if self.status not in {"placed", "cancelled"} and self.paid_at is None:
            raise ValueError("paid_at_required_for_paid_order_state")
        if self.status == "fulfilled" and self.fulfilled_at is None:
            raise ValueError("fulfilled_at_required")
        if self.status == "completed" and self.completed_at is None:
            raise ValueError("completed_at_required")
        if self.status == "cancelled" and self.cancelled_at is None:
            raise ValueError("cancelled_at_required")
        if self.coverage_status == "complete":
            if sum(item.original_amount_fen for item in self.line_items) != self.product_original_amount_fen:
                raise ValueError("line_original_amounts_do_not_match_product_amount")
            if sum(item.refunded_amount_fen for item in self.line_items) != self.product_refunded_amount_fen:
                raise ValueError("line_refunded_amounts_do_not_match_product_amount")
        if self.coupon_order_ref is not None and self.coupon_ref is None:
            raise ValueError("coupon_order_ref_requires_coupon_ref")
        if sum(refund.order_amount_fen for refund in self.refunds) != self.order_refunded_amount_fen:
            raise ValueError("refund_order_amounts_do_not_match_order_snapshot")
        if sum(refund.product_amount_fen for refund in self.refunds) != self.product_refunded_amount_fen:
            raise ValueError("refund_product_amounts_do_not_match_order_snapshot")
        return self


class CommerceIncomingEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=120)
    event_version: int = Field(ge=1)
    event_type: Literal[
        "commerce.order.placed",
        "commerce.order.paid",
        "commerce.order.fulfilled",
        "commerce.order.completed",
        "commerce.order.cancelled",
        "commerce.order.refunded",
    ]
    occurred_at: datetime
    member_ref: str | None = Field(default=None, max_length=64)
    data: CommerceOrderSnapshot

    @model_validator(mode="after")
    def validate_event(self):
        expected_status = {
            "commerce.order.placed": "placed",
            "commerce.order.paid": "paid",
            "commerce.order.fulfilled": "fulfilled",
            "commerce.order.completed": "completed",
            "commerce.order.cancelled": "cancelled",
            "commerce.order.refunded": {"partially_refunded", "refunded"},
        }[self.event_type]
        if isinstance(expected_status, set):
            if self.data.status not in expected_status:
                raise ValueError("event_type_status_mismatch")
        elif self.data.status != expected_status:
            raise ValueError("event_type_status_mismatch")
        if self.data.coupon_ref is not None and self.member_ref is None:
            raise ValueError("coupon_attribution_requires_member_ref")
        return self


class CommerceEventReceipt(BaseModel):
    message_id: str
    message_version: int
    status: Literal["accepted"]
    replayed: bool


class CommerceCouponCartLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_ref: str = Field(min_length=1, max_length=160)
    sku_ref: str | None = Field(default=None, max_length=160)
    quantity: int = Field(gt=0)
    amount_fen: int = Field(ge=0)


class CommerceCouponEligibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_ref: str = Field(min_length=1, max_length=64)
    cart_ref: str = Field(min_length=1, max_length=160)
    goods_subtotal_fen: int = Field(gt=0)
    line_items: list[CommerceCouponCartLine] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_subtotal(self):
        if sum(item.amount_fen for item in self.line_items) != self.goods_subtotal_fen:
            raise ValueError("commerce_coupon_line_amounts_do_not_match_goods_subtotal")
        return self


class CommerceEligibleCoupon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coupon_ref: uuid.UUID
    display_name: str = Field(min_length=1, max_length=80)
    amount_fen: int = Field(gt=0)
    minimum_spend_fen: int = Field(ge=0)
    valid_until: datetime


class CommerceCouponEligibilityResponse(BaseModel):
    coupons: list[CommerceEligibleCoupon]


class CommerceCouponTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coupon_ref: uuid.UUID
    member_ref: str = Field(min_length=1, max_length=64)
    order_id: str = Field(min_length=1, max_length=160)
    amount_fen: int = Field(gt=0)
    action: Literal["reserve", "commit", "release", "reverse"]
    idempotency_key: str = Field(min_length=1, max_length=120)
    goods_subtotal_fen: int | None = Field(default=None, gt=0)
    line_items: list[CommerceCouponCartLine] = Field(default_factory=list, max_length=200)
    full_refund: bool | None = None

    @model_validator(mode="after")
    def validate_action_payload(self):
        if self.action == "reserve":
            if self.goods_subtotal_fen is None or not self.line_items:
                raise ValueError("commerce_coupon_reserve_cart_required")
            if sum(item.amount_fen for item in self.line_items) != self.goods_subtotal_fen:
                raise ValueError("commerce_coupon_line_amounts_do_not_match_goods_subtotal")
        elif self.goods_subtotal_fen is not None or self.line_items:
            raise ValueError("commerce_coupon_cart_only_allowed_for_reserve")
        if self.action == "reverse":
            if self.full_refund is None:
                raise ValueError("commerce_coupon_refund_scope_required")
        elif self.full_refund is not None:
            raise ValueError("commerce_coupon_refund_scope_only_allowed_for_reverse")
        return self


class CommerceCouponTransitionResponse(BaseModel):
    coupon_ref: uuid.UUID
    status: Literal["available", "reserved", "used", "expired", "revoked"]
    discount_amount_fen: int = Field(gt=0)
    reservation_expires_at: datetime | None


class CommerceReconciliation(BaseModel):
    connection_id: uuid.UUID
    status: str
    inbox_accepted: int
    outbox_pending: int
    outbox_delivered: int
    outbox_failed: int
    last_inbox_at: datetime | None
    last_outbox_at: datetime | None


class CommerceOrderFactItem(BaseModel):
    id: uuid.UUID
    external_order_ref: str
    status: str
    currency: str
    is_member_order: bool
    is_packaging_repurchase: bool
    entry_attributed: bool
    coupon_attributed: bool
    product_original_amount_fen: int
    product_refunded_amount_fen: int
    net_product_sales_fen: int
    coverage_status: str
    trust_level: str
    occurrence_at: datetime | None
    member_cohort_at: datetime | None


class CommerceOrderFactList(BaseModel):
    items: list[CommerceOrderFactItem]
    total: int


class CommerceRepurchaseMetrics(BaseModel):
    member_orders: int
    packaging_repurchase_orders: int
    repurchase_members: int
    net_product_sales_fen: int
    entry_attributed_orders: int
    coupon_attributed_orders: int
    complete_coverage_orders: int
    partial_or_missing_orders: int
