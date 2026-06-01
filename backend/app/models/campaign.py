"""活动与权益模型"""

import uuid

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CampaignStatus:
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


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

    __table_args__ = (Index("ix_campaigns_tenant_status", "tenant_id", "status"),)


class BenefitType:
    PLATFORM_COUPON = "platform_coupon"
    EXTERNAL_LINK = "external_link"
    PRIVATE_DOMAIN = "private_domain"
    FORM_BENEFIT = "form_benefit"
    CASH_RED_PACKET = "cash_red_packet"


class Benefit(Base):
    __tablename__ = "benefits"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    benefit_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    connector_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connectors.id"),
        nullable=True,
        index=True,
    )
    stock_total: Mapped[int] = mapped_column(nullable=False, default=0)
    stock_used: Mapped[int] = mapped_column(nullable=False, default=0)
    per_person_limit: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    __table_args__ = (Index("ix_benefits_stock", "stock_total", "stock_used"),)


class BenefitClaim(Base):
    __tablename__ = "benefit_claims"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    benefit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("benefits.id"),
        nullable=False,
        index=True,
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id"),
        nullable=False,
        index=True,
    )
    consumer_id: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(20), nullable=False, default="claim")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    delivery_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_required"
    )

    __table_args__ = (
        UniqueConstraint("benefit_id", "consumer_id", "idempotency_key", name="uq_claim_idempotent"),
        Index("ix_benefit_claims_campaign_consumer", "campaign_id", "consumer_id"),
    )
