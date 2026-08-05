"""平台创建租户的幂等回执。"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class PlatformTenantOpening(Base):
    """把一次平台创建请求稳定映射到唯一的租户和初始管理员。"""

    __tablename__ = "platform_tenant_openings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"), unique=True, nullable=True)
    initial_admin_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), unique=True, nullable=True)
    initial_admin_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
