"""活动与权益模型"""

import uuid

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.constants.campaign import BenefitType, CampaignStatus
from app.models.base import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    campaign_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CampaignStatus.DRAFT)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    start_at: Mapped[str] = mapped_column(String(30), nullable=False)
    end_at: Mapped[str] = mapped_column(String(30), nullable=False)
    rules_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_campaign_tenant_name"),
        Index("ix_campaigns_tenant_status", "tenant_id", "status"),
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
        CheckConstraint("stock_used <= stock_total", name="check_benefit_stock_not_exceeded"),
        CheckConstraint("stock_total >= 0", name="check_benefit_stock_total_non_negative"),
        CheckConstraint("per_person_limit >= 1", name="check_benefit_per_person_limit_min"),
        Index("ix_benefits_stock", "stock_total", "stock_used"),
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
    claim_type: Mapped[str] = mapped_column(String(20), nullable=False, default="claim")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    delivery_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_required"
    )
    created_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("benefit_id", "consumer_id", "idempotency_key", name="uq_claim_idempotent"),
        Index("ix_benefit_claims_campaign_consumer", "campaign_id", "consumer_id"),
    )
