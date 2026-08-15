"""客户正式上线门禁模型。"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
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
    page_template_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    page_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    code_batch_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    status: Mapped[LaunchReleaseStatus] = mapped_column(
        String(30), nullable=False, default=LaunchReleaseStatus.preparing
    )
    readiness_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    readiness_manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    readiness_scan_event_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    readiness_code_item_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    first_valid_scan_event_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    first_valid_scan_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    brand_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    brand_confirmed_by_tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    brand_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    brand_confirmation_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    launched_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    launched_by_tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    suspended_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    suspended_by_tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspension_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_by_tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidation_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_launch_releases_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "page_template_id"],
            ["page_templates.tenant_id", "page_templates.id"],
            name="fk_launch_releases_tenant_page_template",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["campaigns.tenant_id", "campaigns.id"],
            name="fk_launch_releases_tenant_campaign",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "code_batch_id"],
            ["code_batches.tenant_id", "code_batches.id"],
            name="fk_launch_releases_tenant_code_batch",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "readiness_code_item_id"],
            ["code_items.tenant_id", "code_items.id"],
            name="fk_launch_releases_tenant_readiness_code_item",
        ),
        ForeignKeyConstraint(
            ["created_by_tenant_id", "created_by"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_launch_releases_creator_tenant_account",
        ),
        ForeignKeyConstraint(
            ["brand_confirmed_by_tenant_id", "brand_confirmed_by"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_launch_releases_confirmer_tenant_account",
        ),
        ForeignKeyConstraint(
            ["launched_by_tenant_id", "launched_by"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_launch_releases_launcher_tenant_account",
        ),
        ForeignKeyConstraint(
            ["suspended_by_tenant_id", "suspended_by"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_launch_releases_suspender_tenant_account",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "page_template_id", "page_version_id"],
            ["page_versions.tenant_id", "page_versions.page_template_id", "page_versions.id"],
            name="fk_launch_releases_tenant_page_version",
        ),
        Index(
            "ix_launch_releases_tenant_page_version",
            "tenant_id",
            "page_template_id",
            "page_version_id",
        ),
        Index("ix_launch_releases_tenant_status", "tenant_id", "status"),
        Index("ix_launch_releases_tenant_readiness_code_item", "tenant_id", "readiness_code_item_id"),
        Index(
            "uq_launch_releases_tenant_code_batch_live",
            "tenant_id",
            "code_batch_id",
            unique=True,
            postgresql_where=text("status = 'live'"),
            sqlite_where=text("status = 'live'"),
        ),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_launch_releases_tenant_idempotency"),
    )


class LaunchReleaseAction(Base):
    """Append-only idempotency receipt for an authoritative launch mutation."""

    __tablename__ = "launch_release_actions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    release_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    result_status: Mapped[str] = mapped_column(String(30), nullable=False)
    result_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    replayed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_launch_release_actions_tenant"),
        UniqueConstraint("tenant_id", "id", name="uq_launch_release_actions_tenant_id_id"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_launch_release_actions_tenant_idempotency"),
        ForeignKeyConstraint(
            ["tenant_id", "release_id"],
            ["launch_releases.tenant_id", "launch_releases.id"],
            name="fk_launch_release_actions_tenant_release",
        ),
        ForeignKeyConstraint(
            ["actor_tenant_id", "actor_id"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_launch_release_actions_actor_tenant_account",
        ),
        CheckConstraint(
            "action IN ('create','confirm','launch','suspend','resume','invalidate')",
            name="ck_launch_release_actions_action",
        ),
        Index("ix_launch_release_actions_tenant_release", "tenant_id", "release_id", "created_at"),
    )
