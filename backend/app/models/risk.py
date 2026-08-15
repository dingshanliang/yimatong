"""风险预警与风控规则模型"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
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


class RiskAlertType(StrEnum):
    multi_location = "multi_location"
    suspected_copy = "suspected_copy"
    risk_frozen = "risk_frozen"


class RiskAlert(Base):
    __tablename__ = "risk_alerts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    alert_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    public_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    code_item_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    risk_rule_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    risk_action_receipt_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reason_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prior_code_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    current_code_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    detail: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved: Mapped[bool] = mapped_column(default=False, nullable=False)
    # yimatong-zgb1.7 Decision 17：风险记录存储命中事实、证据质量、规则版本、风险等级。
    # risk_level：low/medium/high（决定是否阻断权益领取）
    # rule_version：命中时的规则版本快照（便于审计追溯）
    # evidence_quality：strong/medium/weak（证据可信度）
    # rule_name：命中的规则名（便于运营查看，无需 join risk_rules）
    risk_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    evidence_quality: Mapped[str | None] = mapped_column(String(10), nullable=True)
    rule_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "code_item_id"],
            ["code_items.tenant_id", "code_items.id"],
            name="fk_risk_alerts_tenant_code_item",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "risk_rule_id"],
            ["risk_rules.tenant_id", "risk_rules.id"],
            name="fk_risk_alerts_tenant_rule",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "risk_action_receipt_id"],
            ["risk_action_receipts.tenant_id", "risk_action_receipts.id"],
            name="fk_risk_alerts_tenant_receipt",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_risk_alerts_tenant_actor",
        ),
        UniqueConstraint("tenant_id", "risk_action_receipt_id", name="uq_risk_alerts_tenant_receipt"),
        Index("ix_risk_alerts_tenant_type", "tenant_id", "alert_type"),
        Index("ix_risk_alerts_tenant_resolved", "tenant_id", "resolved"),
        # yimatong-zgb1.7：按 public_id 查 active risk（claim_benefit 门禁用）
        Index("ix_risk_alerts_tenant_pid_resolved", "tenant_id", "public_id", "resolved"),
    )


class RiskRuleAction(StrEnum):
    block = "block"
    warn = "warn"


class RiskRule(Base):
    __tablename__ = "risk_rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False, default=RiskRuleAction.block)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_risk_rules_tenant_id_id"),
        CheckConstraint("version >= 1", name="ck_risk_rules_version_positive"),
        Index("ix_risk_rules_tenant_type", "tenant_id", "rule_type"),
    )


class CampaignRiskRule(Base):
    __tablename__ = "campaign_risk_rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    risk_rule_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_campaign_risk_rules_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "campaign_id", "risk_rule_id", name="uq_campaign_risk_rules_tenant_campaign_rule"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["campaigns.tenant_id", "campaigns.id"],
            name="fk_campaign_risk_rules_tenant_campaign",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "risk_rule_id"],
            ["risk_rules.tenant_id", "risk_rules.id"],
            name="fk_campaign_risk_rules_tenant_rule",
            ondelete="CASCADE",
        ),
    )


class InterceptionRecord(Base):
    __tablename__ = "interception_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    risk_rule_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    code_item_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    consumer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    auto_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    action_taken: Mapped[str | None] = mapped_column(String(50), nullable=True)
    action_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "code_item_id"],
            ["code_items.tenant_id", "code_items.id"],
            name="fk_interception_records_tenant_code_item",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "risk_rule_id"],
            ["risk_rules.tenant_id", "risk_rules.id"],
            name="fk_interception_records_tenant_rule",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["campaigns.tenant_id", "campaigns.id"],
            name="fk_interception_records_tenant_campaign",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_interception_records_tenant_id_id"),
        Index("ix_interceptions_tenant_rule", "tenant_id", "risk_rule_id"),
        Index("ix_interception_records_tenant_code_item", "tenant_id", "code_item_id"),
    )


class RiskNotification(Base):
    __tablename__ = "risk_notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    notification_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    risk_rule_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code_item_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "risk_rule_id"],
            ["risk_rules.tenant_id", "risk_rules.id"],
            name="fk_risk_notifications_tenant_rule",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["campaigns.tenant_id", "campaigns.id"],
            name="fk_risk_notifications_tenant_campaign",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "code_item_id"],
            ["code_items.tenant_id", "code_items.id"],
            name="fk_risk_notifications_tenant_code_item",
        ),
        Index("ix_risk_notif_tenant_read", "tenant_id", "read"),
    )


class RiskActionReceipt(Base):
    __tablename__ = "risk_action_receipts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    scan_event_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    risk_rule_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_risk_action_receipts_tenant_id_id"),
        UniqueConstraint("tenant_id", "action", "idempotency_key", name="uq_risk_receipt_tenant_action_idem"),
        UniqueConstraint("tenant_id", "scan_event_id", "risk_rule_id", name="uq_risk_receipt_tenant_scan_rule"),
        ForeignKeyConstraint(
            ["tenant_id", "risk_rule_id"],
            ["risk_rules.tenant_id", "risk_rules.id"],
            name="fk_risk_receipts_tenant_rule",
        ),
    )


class RiskCampaignPause(Base):
    __tablename__ = "risk_campaign_pauses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    receipt_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    risk_rule_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    prior_status: Mapped[str] = mapped_column(String(20), nullable=False)
    authority_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    paused_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resumed_by_account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    resume_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_risk_campaign_pauses_tenant_id_id"),
        UniqueConstraint("tenant_id", "receipt_id", "campaign_id", name="uq_risk_pause_receipt_campaign"),
        ForeignKeyConstraint(
            ["tenant_id", "receipt_id"],
            ["risk_action_receipts.tenant_id", "risk_action_receipts.id"],
            name="fk_risk_pauses_tenant_receipt",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["campaigns.tenant_id", "campaigns.id"],
            name="fk_risk_pauses_tenant_campaign",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "risk_rule_id"],
            ["risk_rules.tenant_id", "risk_rules.id"],
            name="fk_risk_pauses_tenant_rule",
        ),
        CheckConstraint("status IN ('active','resumed')", name="ck_risk_campaign_pauses_status"),
        CheckConstraint("version >= 1", name="ck_risk_campaign_pauses_version_positive"),
        Index("ix_risk_campaign_pauses_tenant_campaign_status", "tenant_id", "campaign_id", "status"),
    )


class RiskActionOutbox(Base):
    __tablename__ = "risk_action_outbox"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    receipt_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "receipt_id", "topic", name="uq_risk_outbox_receipt_topic"),
        ForeignKeyConstraint(
            ["tenant_id", "receipt_id"],
            ["risk_action_receipts.tenant_id", "risk_action_receipts.id"],
            name="fk_risk_outbox_tenant_receipt",
        ),
        CheckConstraint("attempts >= 0", name="ck_risk_action_outbox_attempts_nonnegative"),
        Index("ix_risk_action_outbox_pending", "published_at", "created_at"),
    )
