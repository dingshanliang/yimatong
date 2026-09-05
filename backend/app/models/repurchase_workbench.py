"""Operational queue and immutable history for the member repurchase workbench."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class RepurchaseWorkItem(Base):
    __tablename__ = "repurchase_work_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    business_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    impact_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    owner_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    conclusion: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_repurchase_work_items_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "owner_account_id"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_repurchase_work_items_owner",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_repurchase_work_items_tenant_id"),
        UniqueConstraint("tenant_id", "category", "business_ref", name="uq_repurchase_work_items_business"),
        CheckConstraint(
            "category IN ('coupon_issue_failure','coupon_expiry_unreached','notification_failure',"
            "'payment_redemption_conflict','refund_unsynced','membership_mapping_conflict',"
            "'unattributed_order','source_coverage')",
            name="ck_repurchase_work_items_category",
        ),
        CheckConstraint("priority IN ('urgent','high','normal')", name="ck_repurchase_work_items_priority"),
        CheckConstraint(
            "status IN ('pending','in_progress','waiting_external','resolved','no_action')",
            name="ck_repurchase_work_items_status",
        ),
        CheckConstraint(
            "(status IN ('resolved','no_action') AND resolved_at IS NOT NULL AND conclusion IS NOT NULL) OR "
            "(status NOT IN ('resolved','no_action') AND resolved_at IS NULL)",
            name="ck_repurchase_work_items_resolution",
        ),
        Index("ix_repurchase_work_items_queue", "tenant_id", "status", "priority", "due_at"),
    )


class RepurchaseWorkItemEvent(Base):
    __tablename__ = "repurchase_work_item_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    work_item_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    before_snapshot: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
    )
    after_snapshot: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict)
    actor_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "work_item_id"],
            ["repurchase_work_items.tenant_id", "repurchase_work_items.id"],
            name="fk_repurchase_work_item_events_item",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_account_id"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_repurchase_work_item_events_actor",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_repurchase_work_item_events_tenant_id"),
        CheckConstraint(
            "action IN ('created','reopened','transitioned','reassigned','deadline_changed',"
            "'resync_requested','correction_submitted')",
            name="ck_repurchase_work_item_events_action",
        ),
        Index("ix_repurchase_work_item_events_item_time", "tenant_id", "work_item_id", "occurred_at"),
    )
