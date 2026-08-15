"""消费者同意记录模型"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
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

from app.models.base import Base


class ConsentType:
    privacy = "privacy"
    marketing = "marketing"
    data_share = "data_share"
    sms = "sms"


class ConsentStatus:
    granted = "granted"
    withdrawn = "withdrawn"


class ConsumerConsentPolicy(Base):
    """Immutable tenant-owned legal material shown before consent."""

    __tablename__ = "consumer_consent_policies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    purpose: Mapped[str] = mapped_column(String(100), nullable=False)
    consent_type: Mapped[str] = mapped_column(String(30), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_title: Mapped[str] = mapped_column(String(200), nullable=False)
    policy_content: Mapped[str] = mapped_column(Text, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_consumer_consent_policies_tenant"),
        UniqueConstraint("tenant_id", "id", name="uq_consumer_consent_policies_tenant_id"),
        UniqueConstraint("tenant_id", "purpose", "id", name="uq_consumer_consent_policies_tenant_purpose_id"),
        UniqueConstraint(
            "tenant_id", "purpose", "policy_version", name="uq_consumer_consent_policies_tenant_purpose_version"
        ),
        CheckConstraint("length(policy_digest)=64", name="ck_consumer_consent_policies_digest_length"),
        CheckConstraint(
            "length(trim(policy_title)) BETWEEN 1 AND 200",
            name="ck_consumer_consent_policies_title_length",
        ),
        CheckConstraint(
            "length(policy_content) BETWEEN 1 AND 16384",
            name="ck_consumer_consent_policies_content_length",
        ),
        Index("ix_consumer_consent_policies_tenant_purpose", "tenant_id", "purpose"),
    )


class ConsumerConsentPolicyCurrent(Base):
    """Server-controlled pointer to the current immutable policy version."""

    __tablename__ = "consumer_consent_policy_current"

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    purpose: Mapped[str] = mapped_column(String(100), primary_key=True)
    policy_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_consumer_consent_policy_current_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "purpose", "policy_id"],
            [
                "consumer_consent_policies.tenant_id",
                "consumer_consent_policies.purpose",
                "consumer_consent_policies.id",
            ],
            name="fk_consumer_consent_policy_current_policy",
        ),
        Index("ix_consumer_consent_policy_current_policy", "tenant_id", "policy_id"),
    )


class ConsentRecord(Base):
    __tablename__ = "consent_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    consumer_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    consent_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ConsentStatus.granted,
    )
    public_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # yimatong-zgb1.5：合规证据链字段（AGENTS.md §7 要求"授权场景、版本、时间、IP/UA、撤回时间"）。
    # scenario：授权场景（如 lead_capture / benefit_phone / privacy_policy）
    # policy_version：政策版本（如 2026-07-27-v1）
    # user_agent：授权时 UA（脱敏截断，与 scan_events.user_agent 一致）
    # 兼容期：旧行允许 NULL（AC4 兼容）
    scenario: Mapped[str | None] = mapped_column(String(100), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(100), nullable=True)
    policy_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    visitor_subject_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scan_event_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    scan_event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    authority_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_consent_records_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "consumer_id"],
            ["consumer_profiles.tenant_id", "consumer_profiles.id"],
            name="fk_consent_records_tenant_consumer",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            ["consumer_consent_policies.tenant_id", "consumer_consent_policies.id"],
            name="fk_consent_records_tenant_policy",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "public_id"],
            ["code_items.tenant_id", "code_items.public_id"],
            name="fk_consent_records_tenant_public_id",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_consent_records_tenant_id"),
        CheckConstraint("status IN ('granted','withdrawn')", name="ck_consent_records_status"),
        CheckConstraint(
            "authority_version=0 OR (authority_version=1 AND policy_id IS NOT NULL AND purpose IS NOT NULL "
            "AND policy_digest IS NOT NULL AND visitor_subject_hash IS NOT NULL AND scan_event_id IS NOT NULL "
            "AND scan_event_time IS NOT NULL AND idempotency_key IS NOT NULL)",
            name="ck_consent_records_authority_provenance",
        ),
        Index("ix_consent_records_tenant_type", "tenant_id", "consent_type"),
        Index("ix_consent_records_tenant_subject", "tenant_id", "purpose", "visitor_subject_hash"),
        Index(
            "uq_consent_records_tenant_subject_purpose_policy_active",
            "tenant_id",
            "purpose",
            "visitor_subject_hash",
            "policy_id",
            unique=True,
            postgresql_where=text("authority_version = 1 AND status = 'granted'"),
            sqlite_where=text("authority_version = 1 AND status = 'granted'"),
        ),
        Index(
            "uq_consent_records_tenant_idempotency",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("authority_version = 1"),
            sqlite_where=text("authority_version = 1"),
        ),
    )


class ConsumerConsentAction(Base):
    """Immutable idempotency receipt and privacy audit without recoverable PII."""

    __tablename__ = "consumer_consent_actions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    consent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    consumer_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    visitor_subject_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_status: Mapped[str] = mapped_column(String(30), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_consumer_consent_actions_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "consent_id"],
            ["consent_records.tenant_id", "consent_records.id"],
            name="fk_consumer_consent_actions_tenant_consent",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            ["consumer_consent_policies.tenant_id", "consumer_consent_policies.id"],
            name="fk_consumer_consent_actions_tenant_policy",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "consumer_id"],
            ["consumer_profiles.tenant_id", "consumer_profiles.id"],
            name="fk_consumer_consent_actions_tenant_consumer",
            use_alter=True,
        ),
        UniqueConstraint("tenant_id", "id", name="uq_consumer_consent_actions_tenant_id"),
        UniqueConstraint(
            "tenant_id", "action", "idempotency_key", name="uq_consumer_consent_actions_tenant_action_idempotency"
        ),
        CheckConstraint(
            "action IN ('grant','lead_capture','withdraw','wechat_bind')",
            name="ck_consumer_consent_actions_action",
        ),
        CheckConstraint("length(payload_hash)=64", name="ck_consumer_consent_actions_payload_hash"),
        CheckConstraint(
            "length(visitor_subject_hash)=64",
            name="ck_consumer_consent_actions_subject_hash",
        ),
        Index("ix_consumer_consent_actions_tenant_consent", "tenant_id", "consent_id", "occurred_at"),
    )
