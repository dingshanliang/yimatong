"""Authoritative commerce order, refund, and repurchase attribution facts."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class CommerceProductMapping(Base):
    __tablename__ = "commerce_product_mappings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    external_product_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["commerce_connections.tenant_id", "commerce_connections.id"],
            name="fk_commerce_product_mappings_connection",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_commerce_product_mappings_product",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_commerce_product_mappings_tenant_id"),
        UniqueConstraint(
            "tenant_id", "connection_id", "external_product_ref", name="uq_commerce_product_mappings_external"
        ),
    )


class CommerceOrderFact(Base):
    __tablename__ = "commerce_order_facts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    external_order_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    membership_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    member_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_system: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    order_original_amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_refunded_amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    order_net_amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_original_amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_refunded_amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    product_net_amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    coverage_status: Mapped[str] = mapped_column(String(20), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_event_version: Mapped[int] = mapped_column(nullable=False)
    last_message_row_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["commerce_connections.tenant_id", "commerce_connections.id"],
            name="fk_commerce_order_facts_connection",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_commerce_order_facts_membership",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "last_message_row_id"],
            ["commerce_integration_messages.tenant_id", "commerce_integration_messages.id"],
            name="fk_commerce_order_facts_message",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_commerce_order_facts_tenant_id"),
        UniqueConstraint("tenant_id", "connection_id", "external_order_ref", name="uq_commerce_order_facts_external"),
        Index("ix_commerce_order_facts_membership_paid", "tenant_id", "membership_id", "paid_at"),
        CheckConstraint(
            "status IN ('placed','paid','fulfilled','completed','cancelled','partially_refunded','refunded')",
            name="ck_commerce_order_facts_status",
        ),
        CheckConstraint("currency='CNY'", name="ck_commerce_order_facts_currency"),
        CheckConstraint(
            "coverage_status IN ('complete','partial','missing')",
            name="ck_commerce_order_facts_coverage",
        ),
        CheckConstraint(
            "order_original_amount_fen>=0 AND order_refunded_amount_fen>=0 "
            "AND order_net_amount_fen=order_original_amount_fen-order_refunded_amount_fen "
            "AND order_net_amount_fen>=0",
            name="ck_commerce_order_facts_order_amounts",
        ),
        CheckConstraint(
            "product_original_amount_fen>=0 AND product_refunded_amount_fen>=0 "
            "AND product_net_amount_fen=product_original_amount_fen-product_refunded_amount_fen "
            "AND product_net_amount_fen>=0",
            name="ck_commerce_order_facts_product_amounts",
        ),
        CheckConstraint(
            "(membership_id IS NULL AND member_ref IS NULL) OR (membership_id IS NOT NULL AND member_ref IS NOT NULL)",
            name="ck_commerce_order_facts_member_pair",
        ),
    )


class CommerceOrderLineFact(Base):
    __tablename__ = "commerce_order_line_facts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    order_fact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    external_line_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    external_product_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    sku_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    quantity: Mapped[int] = mapped_column(nullable=False)
    original_amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    refunded_amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    net_amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mapping_status: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "order_fact_id"],
            ["commerce_order_facts.tenant_id", "commerce_order_facts.id"],
            name="fk_commerce_order_lines_order",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_commerce_order_lines_product",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_commerce_order_lines_tenant_id"),
        UniqueConstraint("tenant_id", "order_fact_id", "external_line_ref", name="uq_commerce_order_lines_external"),
        CheckConstraint("quantity>0", name="ck_commerce_order_lines_quantity"),
        CheckConstraint(
            "original_amount_fen>=0 AND refunded_amount_fen>=0 "
            "AND net_amount_fen=original_amount_fen-refunded_amount_fen AND net_amount_fen>=0",
            name="ck_commerce_order_lines_amounts",
        ),
        CheckConstraint("mapping_status IN ('mapped','unmapped')", name="ck_commerce_order_lines_mapping"),
        CheckConstraint(
            "(mapping_status='mapped' AND product_id IS NOT NULL) OR "
            "(mapping_status='unmapped' AND product_id IS NULL)",
            name="ck_commerce_order_lines_mapping_pair",
        ),
    )


class CommerceRefundFact(Base):
    __tablename__ = "commerce_refund_facts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    order_fact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    external_refund_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    order_amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_refunds: Mapped[list[dict]] = mapped_column(
        JSON().with_variant(postgresql.JSONB, "postgresql"), nullable=False, default=list
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message_row_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "order_fact_id"],
            ["commerce_order_facts.tenant_id", "commerce_order_facts.id"],
            name="fk_commerce_refund_facts_order",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "message_row_id"],
            ["commerce_integration_messages.tenant_id", "commerce_integration_messages.id"],
            name="fk_commerce_refund_facts_message",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_commerce_refund_facts_tenant_id"),
        UniqueConstraint("tenant_id", "order_fact_id", "external_refund_ref", name="uq_commerce_refund_facts_external"),
        CheckConstraint(
            "order_amount_fen>=0 AND product_amount_fen>=0 AND product_amount_fen<=order_amount_fen",
            name="ck_commerce_refund_facts_amounts",
        ),
    )


class CommerceRepurchaseAttribution(Base):
    __tablename__ = "commerce_repurchase_attributions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    order_fact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    membership_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    scan_event_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    public_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    coupon_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    is_member_order: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_packaging_repurchase: Mapped[bool] = mapped_column(Boolean, nullable=False)
    entry_attributed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    coupon_attributed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    occurrence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    member_cohort_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    product_original_amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_refunded_amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    net_product_sales_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    coverage_status: Mapped[str] = mapped_column(String(20), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON().with_variant(postgresql.JSONB, "postgresql"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "order_fact_id"],
            ["commerce_order_facts.tenant_id", "commerce_order_facts.id"],
            name="fk_commerce_repurchase_attr_order",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_commerce_repurchase_attr_membership",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "coupon_id"],
            ["member_coupons.tenant_id", "member_coupons.id"],
            name="fk_commerce_repurchase_attr_coupon",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "public_id"],
            ["code_items.tenant_id", "code_items.public_id"],
            name="fk_commerce_repurchase_attr_public_id",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_commerce_repurchase_attr_tenant_id"),
        UniqueConstraint("tenant_id", "order_fact_id", name="uq_commerce_repurchase_attr_order"),
        Index("ix_commerce_repurchase_attr_occurrence", "tenant_id", "occurrence_at"),
        Index("ix_commerce_repurchase_attr_cohort", "tenant_id", "member_cohort_at"),
        CheckConstraint(
            "coverage_status IN ('complete','partial','missing')",
            name="ck_commerce_repurchase_attr_coverage",
        ),
        CheckConstraint(
            "trust_level IN ('authoritative','partial','unattributed')",
            name="ck_commerce_repurchase_attr_trust",
        ),
        CheckConstraint(
            "net_product_sales_fen=product_original_amount_fen-product_refunded_amount_fen "
            "AND net_product_sales_fen>=0",
            name="ck_commerce_repurchase_attr_amounts",
        ),
        CheckConstraint(
            "NOT is_packaging_repurchase OR (is_member_order AND entry_attributed AND net_product_sales_fen>0)",
            name="ck_commerce_repurchase_attr_qualification",
        ),
    )
