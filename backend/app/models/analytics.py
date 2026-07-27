"""统计模型"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DailyScanStats(Base):
    __tablename__ = "daily_scan_stats"
    __table_args__ = (UniqueConstraint("tenant_id", "date", name="uq_tenant_date"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    total_scans: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uv: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_scans: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rescans: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # yimatong-zgb1.10 Decision 20+21：有效访问指标（headline 分母）。
    # valid_visits = 有效访问次数（activated/frozen + 非 robot）
    # valid_uv = 有效访问的独立访客数（distinct visitor_id where is_valid_visit=true）
    # raw scan（total_scans）保持为诊断指标，不替代有效访问作分母。
    valid_visits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_uv: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
