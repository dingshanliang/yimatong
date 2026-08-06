"""试点里程碑模型（beads: yimatong-bgag.1）。

PRD docs/prd/pilot-learning-retrospective.md §4.1 + §6.2：里程碑是事实记录，
只由系统事件首次发生写入，只增不改（首次落库后不被后续事件覆盖）。
任何角色不能手工编辑或删除；纠错通过追加更正记录实现。
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.constants.pilot import PilotMilestoneType
from app.models.base import Base


class PilotMilestone(Base):
    """单个租户的某个试点里程碑首次达成时间点。

    (tenant_id, milestone_type) 唯一，保证幂等派生：同租户同里程碑只记录首次。
    """

    __tablename__ = "pilot_milestones"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    milestone_type: Mapped[PilotMilestoneType] = mapped_column(String(40), nullable=False)
    achieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 派生来源说明（如 tenant.created_at / launch_releases.launched_at / scan_events），
    # 便于审计与回查，不参与业务判断。
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "milestone_type", name="uq_pilot_milestones_tenant_type"),
        Index("ix_pilot_milestones_tenant_type", "tenant_id", "milestone_type"),
    )
