"""防窜调查证据模型（yimatong-zgb1.17）。

AC2：证据保留来源、上传人、时间和与扫码观察的关联。
每条证据关联一个 DiversionClue，记录证据类型（transfer/order/logistics/explanation）、
文件 URL、上传人、上传时间。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
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


class DiversionEvidence(Base):
    __tablename__ = "diversion_evidence"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    clue_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    # 证据类型：transfer（调货单）/ order（订单）/ logistics（物流）/ explanation（说明）/ other
    evidence_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # 证据来源：distributor（经销商）/ brand_ops（品牌运营）/ system（系统）/ other
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="distributor")
    # 文件 URL 或文本内容
    file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 上传人 + 时间（AC2）
    uploaded_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uploaded_by_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_diversion_evidence_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "clue_id"],
            ["diversion_clues.tenant_id", "diversion_clues.id"],
            name="fk_diversion_evidence_tenant_clue",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "uploaded_by_account_id"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_diversion_evidence_tenant_actor",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_diversion_evidence_tenant_id_id"),
        Index("ix_diversion_evidence_tenant_clue_time", "tenant_id", "clue_id", "uploaded_at"),
    )


class DiversionObservation(Base):
    """Append-only classification bound to one authoritative scan event."""

    __tablename__ = "diversion_observations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    scan_event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    clue_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    public_id: Mapped[str] = mapped_column(String(20), nullable=False)
    code_item_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detected_city: Mapped[str] = mapped_column(String(200), nullable=False)
    expected_region: Mapped[str] = mapped_column(String(200), nullable=False)
    location_source: Mapped[str] = mapped_column(String(30), nullable=False)
    location_accuracy: Mapped[str] = mapped_column(String(20), nullable=False)
    location_authorized: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    region_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    rule_name: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    clue_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    investigation_status: Mapped[str] = mapped_column(String(30), nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observation_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_diversion_observations_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "clue_id"],
            ["diversion_clues.tenant_id", "diversion_clues.id"],
            name="fk_diversion_observations_tenant_clue",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "code_item_id"],
            ["code_items.tenant_id", "code_items.id"],
            name="fk_diversion_observations_tenant_code_item",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_diversion_observations_tenant_id_id"),
        UniqueConstraint("tenant_id", "scan_event_id", "rule_name", name="uq_diversion_observation_scan_rule"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_diversion_observation_idem"),
        CheckConstraint("clue_version > 0", name="ck_diversion_observations_version"),
        CheckConstraint("observation_count > 0", name="ck_diversion_observations_count"),
        Index("ix_diversion_observations_tenant_clue_time", "tenant_id", "clue_id", "observed_at"),
    )


class DiversionActionReceipt(Base):
    """Append-only actor and idempotency receipt for investigation mutations."""

    __tablename__ = "diversion_action_receipts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    clue_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    clue_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    investigation_status: Mapped[str] = mapped_column(String(30), nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observation_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    audit_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_diversion_receipts_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "clue_id"],
            ["diversion_clues.tenant_id", "diversion_clues.id"],
            name="fk_diversion_receipts_tenant_clue",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_diversion_receipts_tenant_actor",
        ),
        UniqueConstraint("tenant_id", "action", "idempotency_key", name="uq_diversion_receipts_idem"),
        UniqueConstraint("tenant_id", "audit_id", name="uq_diversion_receipts_audit"),
        Index("ix_diversion_receipts_tenant_clue", "tenant_id", "clue_id"),
    )
