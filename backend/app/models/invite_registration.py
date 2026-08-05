"""Public invite registration idempotency receipts."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class InviteRegistrationReceipt(Base):
    """Control-plane receipt for one public registration request.

    This table intentionally has no ``tenant_id`` ownership column or RLS policy:
    it is queried before a tenant exists and only through the public registration
    control path. ``tenant_id`` below is the completed result, not row ownership.
    """

    __tablename__ = "invite_registration_receipts"
    __table_args__ = (
        CheckConstraint(
            "(tenant_id IS NULL AND tenant_slug IS NULL) OR (tenant_id IS NOT NULL AND tenant_slug IS NOT NULL)",
            name="ck_invite_registration_receipt_completion_pair",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"), unique=True, nullable=True)
    tenant_slug: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
