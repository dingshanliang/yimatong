"""扫码事件模型"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ScanEvent(Base):
    __tablename__ = "scan_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    public_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    scan_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_first_scan: Mapped[bool] = mapped_column(default=False, nullable=False)
    environment: Mapped[str | None] = mapped_column(String(20), nullable=True)
