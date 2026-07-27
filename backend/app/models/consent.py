"""消费者同意记录模型"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
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


class ConsentRecord(Base):
    __tablename__ = "consent_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    consumer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("consumer_profiles.id"),
        nullable=True,
    )
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

    __table_args__ = (Index("ix_consent_records_tenant_type", "tenant_id", "consent_type"),)
