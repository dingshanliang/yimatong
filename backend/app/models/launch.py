"""客户正式上线门禁模型。"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class LaunchReleaseStatus(StrEnum):
    preparing = "preparing"
    pending_confirmation = "pending_confirmation"
    confirmed = "confirmed"
    live = "live"
    invalidated = "invalidated"
    failed = "failed"
    suspended = "suspended"


class LaunchRelease(Base):
    """一个租户针对一组页面、活动和码批次的正式上线版本。"""

    __tablename__ = "launch_releases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    page_template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("page_templates.id"), nullable=False, index=True)
    page_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("page_versions.id"), nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), nullable=False, index=True)
    code_batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("code_batches.id"), nullable=False, index=True)
    status: Mapped[LaunchReleaseStatus] = mapped_column(
        String(30), nullable=False, default=LaunchReleaseStatus.preparing
    )
    readiness_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    brand_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    brand_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    brand_confirmation_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    launched_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    suspended_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspension_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_launch_releases_tenant_status", "tenant_id", "status"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_launch_releases_tenant_idempotency"),
    )
