"""Enterprise WeChat customer-contact integration models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class WeComConnectorType:
    CUSTOMER_CONTACT = "wecom_customer_contact"


class WeComContactWayStatus:
    ACTIVE = "active"
    DISABLED = "disabled"


class WeComExternalContactStatus:
    ACTIVE = "active"
    DELETED = "deleted"


class WeComContactWay(Base):
    """Tenant-scoped Enterprise WeChat contact entry bound to campaign/benefit/scan state."""

    __tablename__ = "wecom_contact_ways"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    connector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connectors.id"), nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True, index=True)
    benefit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("benefits.id"), nullable=True, index=True)
    config_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    qr_code: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    state: Mapped[str] = mapped_column(String(128), nullable=False)
    user_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    scan_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=WeComContactWayStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "state", name="uq_wecom_contact_ways_tenant_state"),
        Index("ix_wecom_contact_ways_lookup", "tenant_id", "campaign_id", "benefit_id", "status"),
    )


class WeComExternalContact(Base):
    """Confirmed customer relationship received from Enterprise WeChat callbacks."""

    __tablename__ = "wecom_external_contacts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    connector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connectors.id"), nullable=False, index=True)
    contact_way_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("wecom_contact_ways.id"),
        nullable=True,
        index=True,
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True, index=True)
    benefit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("benefits.id"), nullable=True, index=True)
    external_userid: Mapped[str] = mapped_column(String(120), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    consumer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scan_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    unionid: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=WeComExternalContactStatus.ACTIVE)
    added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_event: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "connector_id",
            "external_userid",
            "state",
            name="uq_wecom_external_contacts_source",
        ),
        Index("ix_wecom_external_contacts_lookup", "tenant_id", "benefit_id", "scan_token_hash", "status"),
    )
