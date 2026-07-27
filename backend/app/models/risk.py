"""风险预警与风控规则模型"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, Index, String, func
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_risk_rules_tenant_type", "tenant_id", "rule_type"),)


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

    __table_args__ = (Index("ix_campaign_risk_rules_unique", "campaign_id", "risk_rule_id", unique=True),)


class InterceptionRecord(Base):
    __tablename__ = "interception_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    risk_rule_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
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

    __table_args__ = (Index("ix_interceptions_tenant_rule", "tenant_id", "risk_rule_id"),)


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

    __table_args__ = (Index("ix_risk_notif_tenant_read", "tenant_id", "read"),)
