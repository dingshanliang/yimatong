"""Authoritative consumer-rights, PII access, and sensitive-export records."""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base

_JSON = JSON().with_variant(JSONB(), "postgresql")


class PrivacyRightsRequest(Base):
    __tablename__ = "privacy_rights_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    request_number: Mapped[str] = mapped_column(String(40), nullable=False)
    consumer_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    membership_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    request_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="submitted")
    owner_account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    restricted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    evidence: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        ForeignKeyConstraint(["tenant_id", "consumer_id"], ["consumer_profiles.tenant_id", "consumer_profiles.id"]),
        ForeignKeyConstraint(["tenant_id", "membership_id"], ["brand_memberships.tenant_id", "brand_memberships.id"]),
        ForeignKeyConstraint(["tenant_id", "owner_account_id"], ["accounts.tenant_id", "accounts.id"]),
        UniqueConstraint("tenant_id", "id", name="uq_privacy_rights_requests_tenant_id"),
        UniqueConstraint("tenant_id", "request_number", name="uq_privacy_rights_requests_number"),
        Index("ix_privacy_rights_requests_queue", "tenant_id", "status", "due_at"),
    )


class PrivacyRightsEvent(Base):
    __tablename__ = "privacy_rights_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    evidence: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    previous_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    event_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            ["privacy_rights_requests.tenant_id", "privacy_rights_requests.id"],
        ),
        UniqueConstraint("tenant_id", "id", name="uq_privacy_rights_events_tenant_id"),
        Index("ix_privacy_rights_events_request", "tenant_id", "request_id", "occurred_at"),
    )


class MemberPiiAccessEvent(Base):
    __tablename__ = "member_pii_access_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    consumer_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    actor_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    fields: Mapped[list] = mapped_column(_JSON, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    ticket_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_trace_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "consumer_id"], ["consumer_profiles.tenant_id", "consumer_profiles.id"]),
        ForeignKeyConstraint(["tenant_id", "actor_account_id"], ["accounts.tenant_id", "accounts.id"]),
        UniqueConstraint("tenant_id", "id", name="uq_member_pii_access_events_tenant_id"),
        Index("ix_member_pii_access_events_subject", "tenant_id", "consumer_id", "occurred_at"),
    )


class SensitiveMemberExport(Base):
    __tablename__ = "sensitive_member_exports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    requester_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    approver_account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending_approval")
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    recipient_purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    requested_fields: Mapped[list] = mapped_column(_JSON, nullable=False)
    filters: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    includes_full_pii: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    row_count: Mapped[int | None] = mapped_column(nullable=True)
    artifact_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    artifact_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    artifact_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    download_token_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        ForeignKeyConstraint(["tenant_id", "requester_account_id"], ["accounts.tenant_id", "accounts.id"]),
        ForeignKeyConstraint(["tenant_id", "approver_account_id"], ["accounts.tenant_id", "accounts.id"]),
        UniqueConstraint("tenant_id", "id", name="uq_sensitive_member_exports_tenant_id"),
        Index("ix_sensitive_member_exports_queue", "tenant_id", "status", "expires_at"),
    )


class SensitiveMemberExportEvent(Base):
    __tablename__ = "sensitive_member_export_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    export_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    metadata_snapshot: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    previous_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    event_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "export_id"],
            ["sensitive_member_exports.tenant_id", "sensitive_member_exports.id"],
        ),
        ForeignKeyConstraint(["tenant_id", "actor_account_id"], ["accounts.tenant_id", "accounts.id"]),
        UniqueConstraint("tenant_id", "id", name="uq_sensitive_member_export_events_tenant_id"),
        Index("ix_sensitive_member_export_events_export", "tenant_id", "export_id", "occurred_at"),
    )
