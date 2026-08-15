"""试点里程碑模型（beads: yimatong-bgag.1 / bgag.7）。

PRD docs/prd/pilot-learning-retrospective.md §4.1 + §6.2：里程碑是事实记录，
只由系统事件首次发生写入，只增不改（首次落库后不被后续事件覆盖）。
任何角色不能手工编辑或删除；纠错通过追加更正记录实现
（PilotMilestoneCorrection，不改写 PilotMilestone.achieved_at）。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.constants.pilot import PilotMilestoneType
from app.models.base import Base


class PilotMilestone(Base):
    """单个租户的某个试点里程碑首次达成时间点。

    (tenant_id, milestone_type) 唯一，保证幂等派生：同租户同里程碑只记录首次。
    任何对达成时间的更正不覆盖本行的 achieved_at，而是追加一条
    PilotMilestoneCorrection（PRD §6.2）。
    """

    __tablename__ = "pilot_milestones"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    milestone_type: Mapped[PilotMilestoneType] = mapped_column(String(40), nullable=False)
    achieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 派生来源说明（如 tenant.created_at / launch_releases.launched_at / scan_events），
    # 便于审计与回查，不参与业务判断。
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    fact_digest: Mapped[str] = mapped_column(String(64), nullable=False, default=lambda: "0" * 64)
    authority_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pilot_milestones_tenant_id_id"),
        UniqueConstraint("tenant_id", "milestone_type", name="uq_pilot_milestones_tenant_type"),
        Index("ix_pilot_milestones_tenant_type", "tenant_id", "milestone_type"),
    )


class PilotMilestoneCorrection(Base):
    """里程碑更正记录（PRD §6.2：纠错通过追加更正记录实现）。

    仅追加，不改写对应 PilotMilestone.achieved_at 原始事实。读取层在展示时
    若存在更正记录，以最新一条更正后的 achieved_at 作为展示值，并附更正链
    供审计回查。每条更正必须附 reason。
    """

    __tablename__ = "pilot_milestone_corrections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    milestone_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    milestone_type: Mapped[PilotMilestoneType] = mapped_column(String(40), nullable=False)
    # 更正后的达成时间（PRD §6.2：这是"追加"的更正事实，不回写里程碑本体）
    corrected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 更正后的事实来源说明
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    # 更正原因（必填，PRD §6.2 透明口径）
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # 更正操作人（accounts.id；平台/系统自动派生更正可留空）
    corrected_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    request_id: Mapped[uuid.UUID] = mapped_column(nullable=False, default=uuid7)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False, default="legacy")
    actor_principal: Mapped[str] = mapped_column(String(64), nullable=False, default="legacy-system")
    platform_auth_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("platform_auth_sessions.id"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, default=lambda: f"legacy:{uuid7()}")
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False, default=lambda: "0" * 64)
    authority_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "milestone_id"],
            ["pilot_milestones.tenant_id", "pilot_milestones.id"],
            name="fk_pilot_milestone_corrections_tenant_milestone",
            ondelete="CASCADE",
        ),
        Index(
            "ix_pilot_milestone_corrections_tenant_milestone",
            "tenant_id",
            "milestone_id",
            "created_at",
        ),
        Index("ix_pilot_corrections_platform_session_u09", "platform_auth_session_id"),
    )


class PilotAuthorityReceipt(Base):
    """Immutable idempotency and actor evidence for pilot mutations."""

    __tablename__ = "pilot_authority_receipts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    resource_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    actor_account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    auth_session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    platform_auth_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("platform_auth_sessions.id"), nullable=True
    )
    agency_authorization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agency_authorizations.id"))
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pilot_receipts_tenant_id_u09"),
        UniqueConstraint("tenant_id", "operation", "idempotency_key", name="uq_pilot_receipts_tenant_operation_idem"),
        ForeignKeyConstraint(
            ["actor_tenant_id", "actor_account_id"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_pilot_receipts_actor_account_u09",
        ),
        ForeignKeyConstraint(
            ["actor_tenant_id", "auth_session_id"],
            ["auth_sessions.tenant_id", "auth_sessions.id"],
            name="fk_pilot_receipts_auth_session_u09",
        ),
        Index("ix_pilot_receipts_tenant_auth_session_u09", "actor_tenant_id", "auth_session_id"),
        Index("ix_pilot_receipts_platform_session_u09", "platform_auth_session_id"),
    )
