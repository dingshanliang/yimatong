"""活动与权益模型"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.constants.campaign import CampaignStatus
from app.models.base import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    campaign_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CampaignStatus.DRAFT)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rules_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 首次发布时间戳：切到 ACTIVE 时写入一次，不复写（试点里程碑 5 事实源，
    # beads: yimatong-bgag.7，PRD pilot-learning-retrospective §4.1）。
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_campaigns_tenant_id_id"),
        UniqueConstraint("tenant_id", "name", name="uq_campaign_tenant_name"),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_campaigns_tenant_product",
        ),
        CheckConstraint("status IN ('draft','active','paused','ended')", name="ck_campaigns_status"),
        CheckConstraint("end_at > start_at", name="ck_campaigns_time_window"),
        Index("ix_campaigns_tenant_status", "tenant_id", "status"),
        Index("ix_campaigns_tenant_product", "tenant_id", "product_id"),
        Index("ix_campaigns_published_at", "tenant_id", "published_at"),
    )


class Benefit(Base):
    __tablename__ = "benefits"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    benefit_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    connector_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connectors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    stock_total: Mapped[int] = mapped_column(nullable=False, default=0)
    stock_used: Mapped[int] = mapped_column(nullable=False, default=0)
    per_person_limit: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_benefits_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["campaigns.tenant_id", "campaigns.id"],
            name="fk_benefits_tenant_campaign",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "connector_id"],
            ["connectors.tenant_id", "connectors.id"],
            name="fk_benefits_tenant_connector",
        ),
        CheckConstraint("stock_used <= stock_total", name="check_benefit_stock_not_exceeded"),
        CheckConstraint("stock_total >= 0", name="check_benefit_stock_total_non_negative"),
        CheckConstraint("per_person_limit >= 1", name="check_benefit_per_person_limit_min"),
        CheckConstraint("status IN ('active','inactive')", name="ck_benefits_status"),
        CheckConstraint(
            "benefit_type IN ('platform_coupon','external_link','private_domain','form_benefit','cash_red_packet')",
            name="ck_benefits_type",
        ),
        Index("ix_benefits_stock", "stock_total", "stock_used"),
        Index("ix_benefits_tenant_campaign", "tenant_id", "campaign_id"),
    )


class BenefitClaim(Base):
    __tablename__ = "benefit_claims"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    benefit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("benefits.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    consumer_id: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claim_type: Mapped[str] = mapped_column(String(20), nullable=False, default="claim")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    delivery_status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_required")
    reserved_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reservation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_required")
    created_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_benefit_claims_tenant_id_id"),
        UniqueConstraint("benefit_id", "consumer_id", "idempotency_key", name="uq_claim_idempotent"),
        UniqueConstraint(
            "tenant_id",
            "benefit_id",
            "consumer_id",
            "idempotency_key",
            name="uq_benefit_claims_tenant_idempotent",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "benefit_id"],
            ["benefits.tenant_id", "benefits.id"],
            name="fk_benefit_claims_tenant_benefit",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["campaigns.tenant_id", "campaigns.id"],
            name="fk_benefit_claims_tenant_campaign",
        ),
        CheckConstraint("claim_type = 'claim'", name="ck_benefit_claims_type"),
        CheckConstraint(
            "request_digest IS NULL OR length(request_digest) = 64",
            name="ck_benefit_claims_request_digest",
        ),
        CheckConstraint(
            "status IN ('success','claimed','delivered','used','failed')",
            name="ck_benefit_claims_status",
        ),
        CheckConstraint(
            "delivery_status IN ('not_required','pending','processing','success','delivered','failed','dead_letter')",
            name="ck_benefit_claims_delivery_status",
        ),
        CheckConstraint(
            "(reservation_status='not_required' AND reserved_amount IS NULL) OR "
            "(reservation_status IN ('reserved','settled','refunded') AND reserved_amount>0)",
            name="ck_benefit_claims_reservation",
        ),
        Index("ix_benefit_claims_campaign_consumer", "campaign_id", "consumer_id"),
        Index("ix_benefit_claims_tenant_benefit", "tenant_id", "benefit_id"),
        Index("ix_benefit_claims_tenant_campaign", "tenant_id", "campaign_id"),
        Index(
            "uq_benefit_claims_bound_idempotency",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("request_digest IS NOT NULL"),
        ),
    )


class CampaignClaimOutbox(Base):
    """Durable, tenant-bound delivery intent committed with a benefit claim."""

    __tablename__ = "campaign_claim_outbox"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    claim_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, default="claim_committed")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_token: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    last_lease_token: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "claim_id"],
            ["benefit_claims.tenant_id", "benefit_claims.id"],
            name="fk_campaign_claim_outbox_tenant_claim",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_campaign_claim_outbox_tenant_id_id"),
        UniqueConstraint("tenant_id", "claim_id", "event_type", name="uq_campaign_claim_outbox_claim_event"),
        CheckConstraint("event_type = 'claim_committed'", name="ck_campaign_claim_outbox_event_type"),
        CheckConstraint(
            "status IN ('pending','processing','awaiting_callback','delivered','dead_letter')",
            name="ck_campaign_claim_outbox_status",
        ),
        CheckConstraint("attempt_count >= 0 AND max_attempts >= 1", name="ck_campaign_claim_outbox_attempts"),
        CheckConstraint(
            "(status='processing' AND lease_token IS NOT NULL AND leased_until IS NOT NULL "
            "AND NULLIF(trim(worker_id),'') IS NOT NULL) OR "
            "(status<>'processing' AND lease_token IS NULL AND leased_until IS NULL AND worker_id IS NULL)",
            name="ck_campaign_claim_outbox_lease",
        ),
        CheckConstraint(
            "(status='delivered' AND delivered_at IS NOT NULL) OR (status<>'delivered' AND delivered_at IS NULL)",
            name="ck_campaign_claim_outbox_delivery",
        ),
        Index("ix_campaign_claim_outbox_tenant_status", "tenant_id", "status"),
        Index(
            "ix_campaign_claim_outbox_ready",
            "tenant_id",
            "next_attempt_at",
            "id",
            postgresql_where=text("status IN ('pending','awaiting_callback')"),
            sqlite_where=text("status IN ('pending','awaiting_callback')"),
        ),
        Index(
            "ix_campaign_claim_outbox_expired_lease",
            "tenant_id",
            "leased_until",
            "id",
            postgresql_where=text("status = 'processing'"),
            sqlite_where=text("status = 'processing'"),
        ),
    )
