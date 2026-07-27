"""防窜调查状态变更历史（yimatong-zgb1.18 AC3）。

每次 investigation_status 变更记录操作人/时间/前后值/原因。
支持重开（AC2）：重开时旧结论保留在历史中，可追溯。
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class DiversionInvestigationHistory(Base):
    __tablename__ = "diversion_investigation_history"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    clue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("diversion_clues.id"), nullable=False, index=True
    )
    # 前后状态（AC3 前后值）
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)
    # 操作人 + 时间（AC3）
    changed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # 变更原因（AC3）
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
