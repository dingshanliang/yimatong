import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class TenantHealthMetrics(Base):
    __tablename__ = "tenant_health_metrics"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), unique=True, nullable=False, index=True
    )
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scans_last_7d: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    scans_last_30d: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_campaigns: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    days_until_expiry: Mapped[int | None] = mapped_column(Integer, nullable=True)
    health_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0-100
    health_status: Mapped[str] = mapped_column(
        String(20), default="healthy", nullable=False
    )  # healthy/warning/critical/dormant
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
