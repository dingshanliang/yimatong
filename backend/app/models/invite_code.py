import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class InviteCodeStatus(StrEnum):
    active = "active"
    inactive = "inactive"
    expired = "expired"
    depleted = "depleted"  # 使用次数已达上限


class TenantInviteCode(Base):
    __tablename__ = "tenant_invite_codes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    tenant_type: Mapped[str] = mapped_column(String(20), nullable=False, default="brand")
    max_uses: Mapped[int] = mapped_column(default=1, nullable=False)
    used_count: Mapped[int] = mapped_column(default=0, nullable=False)
    status: Mapped[InviteCodeStatus] = mapped_column(
        SQLEnum(InviteCodeStatus), default=InviteCodeStatus.active, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_actor: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
