"""外部成交与 GMV 归因模型"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class ExternalOrder(Base):
    __tablename__ = "external_orders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[float] = mapped_column(nullable=False)
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    product_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    order_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    matched: Mapped[bool] = mapped_column(default=False, nullable=False)
    channel: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    source_system: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # yimatong-zgb1.13：订单状态 + 退款金额 + 币种（Decision 30 净 GMV）
    # status：paid / refunded / partially_refunded / cancelled
    # refund_amount：累计退款金额（含部分退款）；net_amount = amount - refund_amount
    # currency：币种（ISO 4217，默认 CNY）
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="paid")
    refund_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    ledger_original_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    ledger_refunded_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    ledger_cancelled_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    ledger_net_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    ledger_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_ext_orders_tenant_ext_id", "tenant_id", "external_id"),
        Index("uq_external_orders_tenant_id_id_u8a", "tenant_id", "id", unique=True),
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "external_id",
            name="uq_ext_orders_source_external",
        ),
    )


class ExternalOrderValueReceipt(Base):
    __tablename__ = "external_order_value_receipts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    external_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    result_original_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    result_refunded_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    result_cancelled_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    result_net_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    result_status: Mapped[str] = mapped_column(String(30), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.statement_timestamp())

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["external_orders.tenant_id", "external_orders.id"],
            name="fk_external_order_receipts_tenant_order_u8a",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            ["external_order_value_events.tenant_id", "external_order_value_events.id"],
            name="fk_external_order_receipts_tenant_event_u8a",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        Index(
            "uq_external_order_receipts_idem_u8a",
            "tenant_id",
            "source_system",
            "idempotency_key",
            unique=True,
        ),
        Index("uq_external_order_receipts_tenant_id_u8a", "tenant_id", "id", unique=True),
    )


class ExternalOrderValueEvent(Base):
    __tablename__ = "external_order_value_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("external_order_value_receipts.id"), nullable=False)
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    event_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    external_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    provenance_type: Mapped[str] = mapped_column(String(30), nullable=False)
    provenance_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.statement_timestamp())

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["external_orders.tenant_id", "external_orders.id"],
            name="fk_external_order_events_tenant_order_u8a",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "receipt_id"],
            ["external_order_value_receipts.tenant_id", "external_order_value_receipts.id"],
            name="fk_external_order_events_tenant_receipt_u8a",
        ),
        Index("uq_external_order_events_sequence_u8a", "tenant_id", "order_id", "sequence_no", unique=True),
        Index("uq_external_order_events_tenant_id_u8a", "tenant_id", "id", unique=True),
        Index(
            "ix_external_order_events_identity_u8a",
            "tenant_id",
            "source_system",
            "external_order_id",
            "sequence_no",
        ),
    )


class GmvAttribution(Base):
    __tablename__ = "gmv_attributions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    external_order_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    public_id: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    code_item_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    consumer_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    amount: Mapped[float] = mapped_column(nullable=False)
    match_type: Mapped[str] = mapped_column(String(30), nullable=False)
    scan_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attribution_window_hours: Mapped[int] = mapped_column(default=168, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    # yimatong-zgb1.14 Decision 33：归因快照（不可漂移）。
    # 这些字段在归因写入时快照，后续配置变更不改写历史。
    product_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code_batch_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    channel_snapshot: Mapped[str | None] = mapped_column(String(50), nullable=True)
    page_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    original_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    authority_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="legacy_quarantined", server_default="legacy_quarantined"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "authority_status IN ('legacy_quarantined','confirmed')",
            name="ck_gmv_attributions_authority_status_u8b",
        ),
        Index("uq_gmv_attributions_tenant_id_u8b", "tenant_id", "id", unique=True),
        Index(
            "uq_gmv_attributions_confirmed_order_u8b",
            "tenant_id",
            "external_order_id",
            unique=True,
            postgresql_where=sa_text("authority_status='confirmed'"),
            sqlite_where=sa_text("authority_status='confirmed'"),
        ),
        Index("ix_gmv_attr_tenant_order", "tenant_id", "external_order_id"),
        Index("ix_gmv_attr_tenant_campaign", "tenant_id", "campaign_id"),
    )


class GmvAttributionConfirmation(Base):
    """Immutable proof that a GMV row came from an exact consumer-bound scan."""

    __tablename__ = "gmv_attribution_confirmations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    attribution_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    external_order_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    auth_session_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    actor_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    consumer_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    scan_event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    scan_event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scan_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    visitor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    public_id: Mapped[str] = mapped_column(String(20), nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attribution_window_hours: Mapped[int] = mapped_column(nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.statement_timestamp()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "attribution_id"],
            ["gmv_attributions.tenant_id", "gmv_attributions.id"],
            name="fk_gmv_attr_confirmations_tenant_attribution_u8b",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "external_order_id"],
            ["external_orders.tenant_id", "external_orders.id"],
            name="fk_gmv_attr_confirmations_tenant_order_u8b",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "auth_session_id"],
            ["auth_sessions.tenant_id", "auth_sessions.id"],
            name="fk_gmv_attr_confirmations_tenant_auth_session_u8b",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_account_id"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_gmv_attr_confirmations_tenant_actor_u8b",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "consumer_id"],
            ["consumer_profiles.tenant_id", "consumer_profiles.id"],
            name="fk_gmv_attr_confirmations_tenant_consumer_u8b",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "public_id"],
            ["code_items.tenant_id", "code_items.public_id"],
            name="fk_gmv_attr_confirmations_tenant_public_id_u8b",
        ),
        UniqueConstraint("tenant_id", "attribution_id", name="uq_gmv_attr_confirmations_tenant_attr_u8b"),
        UniqueConstraint("tenant_id", "external_order_id", name="uq_gmv_attr_confirmations_tenant_order_u8b"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_gmv_attr_confirmations_tenant_idem_u8b"),
        Index(
            "ix_gmv_attr_confirmations_tenant_auth_session_u8c",
            "tenant_id",
            "auth_session_id",
        ),
        CheckConstraint("attribution_window_hours IN (168,720)", name="ck_gmv_attr_confirmations_window_u8b"),
        CheckConstraint(
            "window_started_at=scan_received_at",
            name="ck_gmv_attr_confirmations_window_start_u8b",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "window_ends_at=window_started_at + make_interval(hours=>attribution_window_hours)",
            name="ck_gmv_attr_confirmations_window_end_u8b",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'",
            name="ck_gmv_attr_confirmations_payload_u8b",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "provenance_digest ~ '^[0-9a-f]{64}$'",
            name="ck_gmv_attr_confirmations_provenance_u8b",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "NULLIF(btrim(visitor_id),'') IS NOT NULL",
            name="ck_gmv_attr_confirmations_visitor_u8b",
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_gmv_attr_confirmations_cohort_u8b",
            "tenant_id",
            "window_started_at",
            "window_ends_at",
        ),
    )


class GmvDailyStats(Base):
    __tablename__ = "gmv_daily_stats"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    stat_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    channel: Mapped[str | None] = mapped_column(String(50), nullable=True)
    attributed_gmv: Mapped[float] = mapped_column(nullable=False, default=0)
    attributed_orders: Mapped[int] = mapped_column(nullable=False, default=0)
    scan_count: Mapped[int] = mapped_column(nullable=False, default=0)
    scan_uv: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_gmv_daily_tenant_date", "tenant_id", "stat_date"),
        Index("uq_gmv_daily_stats", "tenant_id", "stat_date", "campaign_id", "channel", unique=True),
    )
