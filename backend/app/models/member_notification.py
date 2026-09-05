"""Brand-member message center, preferences, grants, and delivery evidence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class MemberNotificationPreference(Base):
    __tablename__ = "member_notification_preferences"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    marketing_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    service_wechat_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    critical_sms_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    marketing_consent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    marketing_opted_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    marketing_opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_member_notification_preferences_membership",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "marketing_consent_id"],
            ["consent_records.tenant_id", "consent_records.id"],
            name="fk_member_notification_preferences_consent",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_member_notification_preferences_tenant_id"),
        UniqueConstraint("tenant_id", "membership_id", name="uq_member_notification_preferences_membership"),
        CheckConstraint("version>0", name="ck_member_notification_preferences_version"),
        CheckConstraint(
            "(marketing_enabled AND marketing_consent_id IS NOT NULL AND marketing_opted_in_at IS NOT NULL "
            "AND marketing_opted_out_at IS NULL) OR (NOT marketing_enabled)",
            name="ck_member_notification_preferences_marketing",
        ),
    )


class MemberChannelGrant(Base):
    __tablename__ = "member_channel_grants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    template_code: Mapped[str] = mapped_column(String(80), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    authorization_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_member_channel_grants_membership",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_member_channel_grants_tenant_id"),
        UniqueConstraint("tenant_id", "authorization_ref", name="uq_member_channel_grants_authorization_ref"),
        Index("ix_member_channel_grants_available", "tenant_id", "membership_id", "template_code", "status"),
        CheckConstraint("channel='wechat_subscription'", name="ck_member_channel_grants_channel"),
        CheckConstraint("purpose IN ('service','marketing')", name="ck_member_channel_grants_purpose"),
        CheckConstraint(
            "status IN ('available','consumed','expired','revoked')", name="ck_member_channel_grants_status"
        ),
        CheckConstraint("expires_at>authorized_at", name="ck_member_channel_grants_validity"),
        CheckConstraint(
            "(status='consumed' AND consumed_at IS NOT NULL) OR (status<>'consumed' AND consumed_at IS NULL)",
            name="ck_member_channel_grants_consumption",
        ),
    )


class MemberNotification(Base):
    __tablename__ = "member_notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    notification_class: Mapped[str] = mapped_column(String(20), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_product: Mapped[str] = mapped_column(String(30), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_event_version: Mapped[int] = mapped_column(nullable=False)
    object_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    action_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    facts: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["brand_memberships.tenant_id", "brand_memberships.id"],
            name="fk_member_notifications_membership",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_member_notifications_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "source_product",
            "source_event_id",
            "source_event_version",
            "notification_type",
            name="uq_member_notifications_source_event",
        ),
        Index("ix_member_notifications_inbox", "tenant_id", "membership_id", "occurred_at"),
        CheckConstraint("notification_class IN ('service','marketing')", name="ck_member_notifications_class"),
        CheckConstraint("source_event_version>0", name="ck_member_notifications_event_version"),
    )


class MemberNotificationDelivery(Base):
    __tablename__ = "member_notification_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    notification_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    channel_grant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    template_code: Mapped[str] = mapped_column(String(80), nullable=False)
    template_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    suppression_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exhausted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    channel_message_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    consent_snapshot: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "notification_id"],
            ["member_notifications.tenant_id", "member_notifications.id"],
            name="fk_member_notification_deliveries_notification",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "channel_grant_id"],
            ["member_channel_grants.tenant_id", "member_channel_grants.id"],
            name="fk_member_notification_deliveries_grant",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_member_notification_deliveries_tenant_id"),
        UniqueConstraint("tenant_id", "notification_id", "channel", name="uq_member_notification_deliveries_channel"),
        Index("ix_member_notification_deliveries_due", "tenant_id", "status", "next_attempt_at"),
        CheckConstraint("channel IN ('wechat_subscription','sms')", name="ck_member_notification_deliveries_channel"),
        CheckConstraint(
            "status IN ('authorization_missing','pending','delivering','accepted','failed','exhausted','suppressed')",
            name="ck_member_notification_deliveries_status",
        ),
        CheckConstraint(
            "(status='delivering' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status<>'delivering' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_member_notification_deliveries_lease",
        ),
        CheckConstraint("attempt_count BETWEEN 0 AND 3", name="ck_member_notification_deliveries_attempts"),
    )
