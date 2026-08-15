"""Enterprise WeChat customer-contact integration models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
    """Confirmed customer relationship received from Enterprise WeChat callbacks.

    yimatong-zgb1.12 Decision 26：只有验签后的 add_external_contact 才计为确认转化。
    - verification_source：confirmed_callback（验签回调）/ mock_added（本地演示，非确认）
    - change_type：事件类型（add_external_contact / add_half_external_contact / del_*）
    - event_fingerprint：事件指纹（change_type + CreateTime + external_userid 的 sha256），用于幂等
    - welcome_code_pending：add_half_external_contact 时为 True（待客户确认，不计为 ACTIVE）
    """

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
    # yimatong-zgb1.12：验签来源 + 事件类型 + 指纹 + 待验证标记
    verification_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    change_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    event_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    event_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    welcome_code_pending: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_wecom_external_contacts_tenant_id_id_u6l"),
        UniqueConstraint(
            "tenant_id",
            "connector_id",
            "user_id",
            "external_userid",
            name="uq_wecom_external_contacts_member_source_u6l",
        ),
        Index("ix_wecom_external_contacts_lookup", "tenant_id", "benefit_id", "scan_token_hash", "status"),
        # yimatong-zgb1.12：事件指纹幂等（同 change_type + CreateTime 只处理一次）
        Index("ix_wecom_external_contacts_fingerprint", "tenant_id", "event_fingerprint"),
        Index(
            "ix_wecom_external_contacts_member_order_u6l",
            "tenant_id",
            "connector_id",
            "user_id",
            "external_userid",
            "event_time",
            "event_sequence",
        ),
    )


class WeComCallbackReceipt(Base):
    """Immutable official callback history applied to the contact projection."""

    __tablename__ = "wecom_callback_receipts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    connector_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    event_identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    change_type: Mapped[str] = mapped_column(String(50), nullable=False)
    external_userid: Mapped[str] = mapped_column(String(120), nullable=False)
    user_id: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str | None] = mapped_column(String(128), nullable=True)
    unionid: Mapped[str | None] = mapped_column(String(120), nullable=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_wecom_callback_receipts_tenant_id"),
        UniqueConstraint("tenant_id", "connector_id", "event_identity_digest", name="uq_wecom_callback_receipts_event"),
        ForeignKeyConstraint(
            ["tenant_id", "connector_id"],
            ["connectors.tenant_id", "connectors.id"],
            name="fk_wecom_callback_receipts_tenant_connector",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            ["wecom_external_contacts.tenant_id", "wecom_external_contacts.id"],
            name="fk_wecom_callback_receipts_tenant_contact",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(event_identity_digest) = 64", name="ck_wecom_callback_receipts_event_digest"),
        CheckConstraint("length(payload_digest) = 64", name="ck_wecom_callback_receipts_payload_digest"),
        CheckConstraint("event_sequence >= 0", name="ck_wecom_callback_receipts_event_sequence"),
        Index(
            "ix_wecom_callback_receipts_contact_order",
            "tenant_id",
            "connector_id",
            "external_userid",
            "event_time",
            "event_sequence",
        ),
        Index(
            "ix_wecom_callback_receipts_member_order_u6l",
            "tenant_id",
            "connector_id",
            "user_id",
            "external_userid",
            "event_time",
            "event_sequence",
        ),
        Index("ix_wecom_callback_receipts_tenant_contact", "tenant_id", "contact_id"),
    )


class WeComMemberRecoveryMarker(Base):
    """Owner-only staged reconciliation for historical official contact member identity."""

    __tablename__ = "wecom_member_recovery_markers"

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    observed_user_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    acknowledged_user_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolution_state: Mapped[str] = mapped_column(String(30), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            ["wecom_external_contacts.tenant_id", "wecom_external_contacts.id"],
            name="fk_wecom_member_recovery_marker_contact",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "resolution_state IN ('exact_bound','operator_acknowledged','unresolved')",
            name="ck_wecom_member_recovery_resolution",
        ),
    )
