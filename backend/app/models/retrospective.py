"""试点复盘实体模型（beads: yimatong-bgag.2，PRD pilot-learning-retrospective §4.3/§6.1/§7）。

每个租户上线后第 7/14/30 天各一期复盘。生成时物化 scorecard 快照，
完成后快照冻结，只允许追加 supplementary_notes（PRD §6.1/§7）。
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.constants.retrospective import RetrospectiveStatus
from app.models.base import Base


class Retrospective(Base):
    """单个租户某一期（7/14/30 天）的试点复盘。

    (tenant_id, period_day) 唯一，保证 poller 幂等生成。
    """

    __tablename__ = "retrospectives"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    # 期次：7/14/30
    period_day: Mapped[int] = mapped_column(Integer, nullable=False)
    # 统计窗口（上线日 → 复盘日）
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 下次验证日期（默认下一期复盘日，可调整）
    next_review_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[RetrospectiveStatus] = mapped_column(String(20), nullable=False, default=RetrospectiveStatus.PENDING)
    # 本期目标（首期由代运营填写）
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 数据快照：生成时点物化的 scorecard（PRD §4.3.3/§4.4），完成后冻结
    scorecard_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # 问题与判断（人工填写）
    issues: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 动作清单：[{content, owner_id, due_date, status}]
    actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # 完成信息
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    # 完成后追加的补充说明（快照冻结后唯一可写字段）
    supplementary_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 关联的 OpsTask 提醒入口（PRD §4.2/§6.3：OpsTask 仅作提醒，不承载复盘数据）
    ops_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ops_tasks.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "period_day", name="uq_retrospectives_tenant_period"),
        Index("ix_retrospectives_tenant_period", "tenant_id", "period_day"),
    )
