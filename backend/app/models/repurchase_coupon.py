"""Authoritative fixed-amount repurchase coupon assets."""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class RepurchaseCouponRuleVersion(Base):
    """An immutable commercial snapshot once it has been published."""

    __tablename__ = "repurchase_coupon_rule_versions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    rule_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    benefit_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_spend_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    product_scope: Mapped[str] = mapped_column(String(20), nullable=False, default="all")
    eligible_product_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    channel_scope: Mapped[str] = mapped_column(String(20), nullable=False)
    validity_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    valid_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fixed_valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fixed_valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issuance_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    issued_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_coupon_rules_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "benefit_id"],
            ["benefits.tenant_id", "benefits.id"],
            name="fk_coupon_rules_tenant_benefit",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_coupon_rules_tenant_creator",
        ),
        CheckConstraint("length(trim(rule_key)) BETWEEN 1 AND 64", name="ck_coupon_rules_key"),
        CheckConstraint("version > 0", name="ck_coupon_rules_version"),
        CheckConstraint("amount_minor > 0", name="ck_coupon_rules_amount"),
        CheckConstraint(
            "minimum_spend_minor = 0 OR minimum_spend_minor > amount_minor",
            name="ck_coupon_rules_minimum_spend",
        ),
        CheckConstraint("currency = 'CNY'", name="ck_coupon_rules_currency"),
        CheckConstraint("product_scope IN ('all','specified')", name="ck_coupon_rules_product_scope"),
        CheckConstraint("channel_scope IN ('online','store','both')", name="ck_coupon_rules_channel_scope"),
        CheckConstraint(
            "(validity_mode='relative' AND valid_days BETWEEN 1 AND 365 "
            "AND fixed_valid_from IS NULL AND fixed_valid_until IS NULL) OR "
            "(validity_mode='fixed' AND valid_days IS NULL AND fixed_valid_from IS NOT NULL "
            "AND fixed_valid_until>fixed_valid_from)",
            name="ck_coupon_rules_validity",
        ),
        CheckConstraint("issuance_limit > 0", name="ck_coupon_rules_issuance_limit"),
        CheckConstraint(
            "issued_count >= 0 AND issued_count <= issuance_limit",
            name="ck_coupon_rules_issued_count",
        ),
        CheckConstraint("status IN ('draft','published','paused','ended')", name="ck_coupon_rules_status"),
        CheckConstraint(
            "(status='draft' AND published_at IS NULL AND ended_at IS NULL) OR "
            "(status IN ('published','paused') AND published_at IS NOT NULL AND ended_at IS NULL) OR "
            "(status='ended' AND published_at IS NOT NULL AND ended_at IS NOT NULL)",
            name="ck_coupon_rules_lifecycle_times",
        ),
        Index("uq_coupon_rules_tenant_id", "tenant_id", "id", unique=True),
        Index("uq_coupon_rules_tenant_key_version", "tenant_id", "rule_key", "version", unique=True),
        Index(
            "uq_coupon_rules_tenant_benefit",
            "tenant_id",
            "benefit_id",
            unique=True,
            postgresql_where=text("benefit_id IS NOT NULL"),
            sqlite_where=text("benefit_id IS NOT NULL"),
        ),
        Index("ix_coupon_rules_tenant_status", "tenant_id", "status"),
    )


class MemberCoupon(Base):
    """One coupon asset belonging to exactly one active brand membership."""

    __tablename__ = "member_coupons"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    rule_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_claim_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    source_scan_event_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    source_scan_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_public_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    coupon_number: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="available")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reserved_order_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reservation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reserved_discount_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_order_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    used_store_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    authority_type: Mapped[str] = mapped_column(String(20), nullable=False, default="yimatong")
    external_connector_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    external_coupon_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_required")
    sync_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_member_coupons_tenant_membership",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rule_version_id"],
            ["repurchase_coupon_rule_versions.tenant_id", "repurchase_coupon_rule_versions.id"],
            name="fk_member_coupons_tenant_rule",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_claim_id"],
            ["benefit_claims.tenant_id", "benefit_claims.id"],
            name="fk_member_coupons_tenant_claim",
        ),
        ForeignKeyConstraint(
            ["source_scan_event_id", "source_scan_time"],
            ["scan_events.id", "scan_events.scan_time"],
            name="fk_member_coupons_scan",
        ),
        CheckConstraint(
            "(source_scan_event_id IS NULL) = (source_scan_time IS NULL)",
            name="ck_member_coupons_scan_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "used_store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_member_coupons_tenant_store",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "external_connector_id"],
            ["connectors.tenant_id", "connectors.id"],
            name="fk_member_coupons_tenant_connector",
        ),
        CheckConstraint(
            "coupon_number ~ '^RCP-[0-9A-F]{16}$'",
            name="ck_member_coupons_number",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "status IN ('available','reserved','used','expired','revoked')", name="ck_member_coupons_status"
        ),
        CheckConstraint("valid_until > valid_from", name="ck_member_coupons_validity"),
        CheckConstraint(
            "(status='reserved' AND reserved_order_ref IS NOT NULL AND reservation_expires_at IS NOT NULL "
            "AND reserved_discount_minor>0) OR "
            "(status<>'reserved' AND reserved_order_ref IS NULL AND reservation_expires_at IS NULL "
            "AND reserved_discount_minor IS NULL)",
            name="ck_member_coupons_reservation",
        ),
        CheckConstraint(
            "(status='used' AND used_at IS NOT NULL AND (used_order_ref IS NOT NULL OR used_store_id IS NOT NULL)) OR "
            "(status<>'used' AND used_at IS NULL AND used_order_ref IS NULL AND used_store_id IS NULL)",
            name="ck_member_coupons_usage",
        ),
        CheckConstraint(
            "(status='revoked' AND revoked_at IS NOT NULL AND NULLIF(trim(revoke_reason),'') IS NOT NULL) OR "
            "(status<>'revoked' AND revoked_at IS NULL AND revoke_reason IS NULL)",
            name="ck_member_coupons_revocation",
        ),
        CheckConstraint("authority_type IN ('yimatong','external')", name="ck_member_coupons_authority"),
        CheckConstraint(
            "(authority_type='yimatong' AND external_connector_id IS NULL AND external_coupon_ref IS NULL "
            "AND sync_status='not_required' AND sync_error IS NULL) OR "
            "(authority_type='external' AND external_connector_id IS NOT NULL "
            "AND NULLIF(trim(external_coupon_ref),'') IS NOT NULL "
            "AND sync_status IN ('pending','synchronized','error'))",
            name="ck_member_coupons_external_sync",
        ),
        CheckConstraint("version > 0", name="ck_member_coupons_version"),
        Index("uq_member_coupons_tenant_id", "tenant_id", "id", unique=True),
        Index("uq_member_coupons_tenant_number", "tenant_id", "coupon_number", unique=True),
        Index(
            "uq_member_coupons_tenant_claim",
            "tenant_id",
            "source_claim_id",
            unique=True,
            postgresql_where=text("source_claim_id IS NOT NULL"),
            sqlite_where=text("source_claim_id IS NOT NULL"),
        ),
        Index("ix_member_coupons_wallet", "tenant_id", "membership_id", "status", "valid_until"),
    )


class RepurchaseCouponEvent(Base):
    """PII-free append-only receipt for rule and coupon authority changes."""

    __tablename__ = "repurchase_coupon_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    rule_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    coupon_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    order_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    store_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "rule_version_id"],
            ["repurchase_coupon_rule_versions.tenant_id", "repurchase_coupon_rule_versions.id"],
            name="fk_coupon_events_tenant_rule",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "coupon_id"],
            ["member_coupons.tenant_id", "member_coupons.id"],
            name="fk_coupon_events_tenant_coupon",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_coupon_events_tenant_store",
        ),
        CheckConstraint(
            "event_type IN ('rule_created','rule_published','rule_paused','rule_resumed','rule_ended',"
            "'issued','reserved','committed','released','reversed','refund_recorded','expired','revoked',"
            "'store_redeemed','external_sync_pending','external_sync_confirmed','external_sync_error')",
            name="ck_coupon_events_type",
        ),
        CheckConstraint("length(idempotency_key) BETWEEN 1 AND 120", name="ck_coupon_events_idempotency"),
        CheckConstraint("length(payload_digest)=64", name="ck_coupon_events_digest"),
        CheckConstraint("actor_type IN ('consumer','store','brand','service','system')", name="ck_coupon_events_actor"),
        Index("uq_coupon_events_tenant_id", "tenant_id", "id", unique=True),
        Index("uq_coupon_events_tenant_idempotency", "tenant_id", "idempotency_key", unique=True),
        Index("ix_coupon_events_coupon_time", "tenant_id", "coupon_id", "occurred_at"),
        Index("ix_coupon_events_rule_time", "tenant_id", "rule_version_id", "occurred_at"),
    )
