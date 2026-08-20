"""Authoritative boundary between Yimatong and one independent commerce product."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class CommerceConnection(Base):
    __tablename__ = "commerce_connections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    external_tenant_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    external_shop_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_commerce_connections_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_commerce_connections_tenant_creator",
        ),
        Index("uq_commerce_connections_tenant_id", "tenant_id", "id", unique=True),
        Index(
            "uq_commerce_connections_active_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=(status == "active"),
            sqlite_where=(status == "active"),
        ),
        CheckConstraint("status IN ('active','disconnected')", name="ck_commerce_connections_status"),
        CheckConstraint("version>0", name="ck_commerce_connections_version"),
        CheckConstraint(
            "(status='active' AND disconnected_at IS NULL) OR (status='disconnected' AND disconnected_at IS NOT NULL)",
            name="ck_commerce_connections_lifecycle",
        ),
    )


class CommerceServiceCredential(Base):
    __tablename__ = "commerce_service_credentials"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    direction: Mapped[str] = mapped_column(String(30), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    overlap_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["commerce_connections.tenant_id", "commerce_connections.id"],
            name="fk_commerce_credentials_tenant_connection",
        ),
        Index("uq_commerce_credentials_tenant_id", "tenant_id", "id", unique=True),
        Index("uq_commerce_credentials_prefix", "key_prefix", unique=True),
        Index("uq_commerce_credentials_version", "tenant_id", "connection_id", "direction", "version", unique=True),
        Index("ix_commerce_credentials_active", "tenant_id", "connection_id", "direction", "valid_until"),
        CheckConstraint(
            "direction IN ('yimatong_to_commerce','commerce_to_yimatong')",
            name="ck_commerce_credentials_direction",
        ),
        CheckConstraint("version>0", name="ck_commerce_credentials_version"),
        CheckConstraint("valid_until>valid_from", name="ck_commerce_credentials_validity"),
        CheckConstraint(
            "overlap_until IS NULL OR (overlap_until>valid_from AND overlap_until<=valid_until)",
            name="ck_commerce_credentials_overlap",
        ),
    )


class CommerceMemberReference(Base):
    __tablename__ = "commerce_member_references"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    member_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    external_customer_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["commerce_connections.tenant_id", "commerce_connections.id"],
            name="fk_commerce_member_refs_tenant_connection",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_commerce_member_refs_tenant_membership",
        ),
        Index("uq_commerce_member_refs_tenant_id", "tenant_id", "id", unique=True),
        Index("uq_commerce_member_refs_member", "tenant_id", "connection_id", "membership_id", unique=True),
        Index("uq_commerce_member_refs_ref", "tenant_id", "connection_id", "member_ref", unique=True),
        Index("ix_commerce_member_refs_membership", "tenant_id", "membership_id"),
        Index(
            "uq_commerce_member_refs_external",
            "tenant_id",
            "connection_id",
            "external_customer_ref",
            unique=True,
            postgresql_where=(external_customer_ref.is_not(None)),
            sqlite_where=(external_customer_ref.is_not(None)),
        ),
    )


class CommerceIdentityHandoff(Base):
    __tablename__ = "commerce_identity_handoffs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    member_reference_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["commerce_connections.tenant_id", "commerce_connections.id"],
            name="fk_commerce_handoffs_tenant_connection",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "member_reference_id"],
            ["commerce_member_references.tenant_id", "commerce_member_references.id"],
            name="fk_commerce_handoffs_tenant_member_ref",
        ),
        Index("uq_commerce_handoffs_tenant_id", "tenant_id", "id", unique=True),
        Index("uq_commerce_handoffs_digest", "token_digest", unique=True),
        Index("ix_commerce_handoffs_expiry", "tenant_id", "connection_id", "expires_at"),
        Index("ix_commerce_handoffs_member_ref", "tenant_id", "member_reference_id"),
        CheckConstraint("expires_at>created_at", name="ck_commerce_handoffs_validity"),
    )


class CommerceIntegrationMessage(Base):
    __tablename__ = "commerce_integration_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    message_id: Mapped[str] = mapped_column(String(120), nullable=False)
    message_version: Mapped[int] = mapped_column(nullable=False)
    message_type: Mapped[str] = mapped_column(String(80), nullable=False)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    projected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["commerce_connections.tenant_id", "commerce_connections.id"],
            name="fk_commerce_messages_tenant_connection",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id"],
            ["commerce_service_credentials.tenant_id", "commerce_service_credentials.id"],
            name="fk_commerce_messages_tenant_credential",
        ),
        Index("uq_commerce_messages_tenant_id", "tenant_id", "id", unique=True),
        Index(
            "uq_commerce_messages_business_key",
            "tenant_id",
            "connection_id",
            "direction",
            "message_id",
            "message_version",
            unique=True,
        ),
        Index("ix_commerce_messages_reconcile", "tenant_id", "connection_id", "direction", "status", "created_at"),
        Index("ix_commerce_messages_credential", "tenant_id", "credential_id"),
        CheckConstraint("direction IN ('inbox','outbox')", name="ck_commerce_messages_direction"),
        CheckConstraint("message_version>0", name="ck_commerce_messages_version"),
        CheckConstraint("length(payload_digest)=64", name="ck_commerce_messages_digest"),
        CheckConstraint("status IN ('accepted','pending','delivered','failed')", name="ck_commerce_messages_status"),
        CheckConstraint("attempt_count>=0", name="ck_commerce_messages_attempts"),
        CheckConstraint(
            "(direction='inbox' AND credential_id IS NOT NULL AND status='accepted' AND accepted_at IS NOT NULL) OR "
            "(direction='outbox' AND credential_id IS NULL AND accepted_at IS NULL)",
            name="ck_commerce_messages_direction_state",
        ),
    )


class CommerceConnectionEvent(Base):
    __tablename__ = "commerce_connection_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["commerce_connections.tenant_id", "commerce_connections.id"],
            name="fk_commerce_events_tenant_connection",
        ),
        Index("uq_commerce_events_tenant_id", "tenant_id", "id", unique=True),
        Index("uq_commerce_events_idempotency", "tenant_id", "idempotency_key", unique=True),
        Index("ix_commerce_events_connection_time", "tenant_id", "connection_id", "occurred_at"),
        CheckConstraint(
            "event_type IN ('connected','credential_rotated','credential_revoked','disconnected',"
            "'handoff_issued','handoff_redeemed')",
            name="ck_commerce_events_type",
        ),
        CheckConstraint("length(payload_digest)=64", name="ck_commerce_events_digest"),
    )
