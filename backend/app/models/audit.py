import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class PlatformAuditLog(Base):
    __tablename__ = "platform_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    operator_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


Index(
    "ix_platform_audit_target_timeline",
    PlatformAuditLog.target_tenant_id,
    PlatformAuditLog.timestamp.desc(),
    PlatformAuditLog.id.desc(),
)
