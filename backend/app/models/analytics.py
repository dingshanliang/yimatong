"""统计模型"""

import uuid
from datetime import date

from sqlalchemy import Date, Integer, UniqueConstraint
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
