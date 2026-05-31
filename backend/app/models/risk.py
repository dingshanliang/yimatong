"""风险预警与风控规则模型"""

import uuid
from enum import StrEnum

from sqlalchemy import JSON, Boolean, Index, String
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

    __table_args__ = (
        Index("ix_risk_alerts_tenant_type", "tenant_id", "alert_type"),
        Index("ix_risk_alerts_tenant_resolved", "tenant_id", "resolved"),
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

    __table_args__ = (Index("ix_risk_rules_tenant_type", "tenant_id", "rule_type"),)


class CampaignRiskRule(Base):
    __tablename__ = "campaign_risk_rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    risk_rule_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)

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

    __table_args__ = (
        Index("ix_risk_notif_tenant_read", "tenant_id", "read"),
    )
