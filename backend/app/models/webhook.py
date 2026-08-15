"""Webhook / Open API 模型"""

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
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    events: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    batch_mode: Mapped[bool] = mapped_column(default=False, nullable=False)
    batch_size: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_webhook_endpoints_tenant_id_id"),
        CheckConstraint("config_version >= 1", name="ck_webhook_endpoints_config_version"),
        Index("ix_webhook_endpoints_tenant", "tenant_id"),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    key_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="data_reader")
    permissions: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    revoked: Mapped[bool] = mapped_column(default=False, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_from_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    idempotency_key_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    permanent_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_api_keys_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_api_keys_tenant_creator",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rotated_from_id"],
            ["api_keys.tenant_id", "api_keys.id"],
            name="fk_api_keys_tenant_rotated_from",
        ),
        CheckConstraint(
            "(revoked = false AND revoked_at IS NULL) OR (revoked = true AND revoked_at IS NOT NULL)",
            name="ck_api_keys_revocation_state",
        ),
        CheckConstraint(
            "created_by IS NULL OR "
            "((expires_at IS NULL AND permanent_reason IS NOT NULL "
            "AND length(trim(permanent_reason)) BETWEEN 10 AND 200) "
            "OR (expires_at IS NOT NULL AND permanent_reason IS NULL))",
            name="ck_api_keys_permanent_reason",
        ),
        CheckConstraint(
            "(idempotency_key_digest IS NULL AND request_fingerprint IS NULL) OR "
            "(created_by IS NOT NULL "
            "AND idempotency_key_digest ~ '^[0-9a-f]{64}$' "
            "AND request_fingerprint ~ '^[0-9a-f]{64}$')",
            name="ck_api_keys_idempotency_contract",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "idempotency_key_digest IS NULL OR expires_at IS NULL OR "
            "(created_at IS NOT NULL AND expires_at <= created_at + INTERVAL '365 days')",
            name="ck_api_keys_post_contract_expiry_max",
        ).ddl_if(dialect="postgresql"),
        Index("ix_api_keys_tenant", "tenant_id"),
        Index("uq_api_keys_key_digest", "key_digest", unique=True),
        Index(
            "uq_api_keys_tenant_creator_idempotency",
            "tenant_id",
            "created_by",
            "idempotency_key_digest",
            unique=True,
            postgresql_where=text("idempotency_key_digest IS NOT NULL"),
            sqlite_where=text("idempotency_key_digest IS NOT NULL"),
        ),
        Index(
            "ix_api_keys_tenant_active_expiry",
            "tenant_id",
            "expires_at",
            postgresql_where=text("revoked = false AND revoked_at IS NULL"),
            sqlite_where=text("revoked = false AND revoked_at IS NULL"),
        ),
        Index(
            "uq_api_keys_tenant_rotated_from",
            "tenant_id",
            "rotated_from_id",
            unique=True,
            postgresql_where=text("rotated_from_id IS NOT NULL"),
            sqlite_where=text("rotated_from_id IS NOT NULL"),
        ),
    )


class WebhookDomainEvent(Base):
    """Immutable event fact written in the same transaction as its domain mutation."""

    __tablename__ = "webhook_domain_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expanded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_webhook_domain_events_tenant_id_id"),
        CheckConstraint("payload_digest ~ '^[0-9a-f]{64}$'", name="ck_webhook_domain_events_payload_digest").ddl_if(
            dialect="postgresql"
        ),
        Index("ix_webhook_domain_events_tenant_created", "tenant_id", "created_at", "id"),
        Index(
            "ix_webhook_domain_events_pending",
            "created_at",
            "id",
            postgresql_where=text("expanded_at IS NULL"),
            sqlite_where=text("expanded_at IS NULL"),
        ),
    )


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    domain_event_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    payload_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    endpoint_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    endpoint_secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    endpoint_secret_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    endpoint_secret_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    endpoint_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    legacy_payload_wrapped: Mapped[bool | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "endpoint_id"],
            ["webhook_endpoints.tenant_id", "webhook_endpoints.id"],
            name="fk_webhook_deliveries_tenant_endpoint",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "domain_event_id"],
            ["webhook_domain_events.tenant_id", "webhook_domain_events.id"],
            name="fk_webhook_deliveries_tenant_domain_event",
        ),
        CheckConstraint(
            "domain_event_id IS NULL OR (payload_digest IS NOT NULL AND endpoint_url IS NOT NULL "
            "AND endpoint_secret_ciphertext IS NOT NULL AND endpoint_secret_nonce IS NOT NULL "
            "AND endpoint_secret_key_id IS NOT NULL AND endpoint_config_version IS NOT NULL)",
            name="ck_webhook_deliveries_snapshot",
        ),
        CheckConstraint(
            "(lease_token IS NULL) = (lease_expires_at IS NULL)",
            name="ck_webhook_deliveries_lease_pair",
        ),
        CheckConstraint("attempt_count >= 0 AND retry_count >= 0", name="ck_webhook_deliveries_attempts"),
        Index(
            "uq_webhook_deliveries_domain_endpoint",
            "tenant_id",
            "domain_event_id",
            "endpoint_id",
            unique=True,
            postgresql_where=text("domain_event_id IS NOT NULL"),
            sqlite_where=text("domain_event_id IS NOT NULL"),
        ),
        Index("ix_webhook_deliveries_tenant", "tenant_id"),
        Index("ix_webhook_deliveries_event_id", "event_id"),
        Index("ix_webhook_deliveries_status", "status"),
        Index("ix_webhook_deliveries_due", "status", "next_retry_at", "lease_expires_at", "id"),
    )
