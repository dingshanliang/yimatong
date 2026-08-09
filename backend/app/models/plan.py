import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    func,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class PlanDefinition(Base):
    __tablename__ = "plan_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid7()))
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    price_yearly: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # cents
    quota_defaults: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    feature_flags: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
    )


class TenantQuotaUsage(Base):
    """Constant-time, transactionally reserved cumulative quota usage."""

    __tablename__ = "tenant_quota_usage"
    __table_args__ = (
        CheckConstraint("codes >= 0", name="ck_tenant_quota_usage_codes_nonnegative"),
        CheckConstraint("scans >= 0", name="ck_tenant_quota_usage_scans_nonnegative"),
        CheckConstraint("campaigns >= 0", name="ck_tenant_quota_usage_campaigns_nonnegative"),
        CheckConstraint("products >= 0", name="ck_tenant_quota_usage_products_nonnegative"),
        CheckConstraint("accounts >= 0", name="ck_tenant_quota_usage_accounts_nonnegative"),
        CheckConstraint(
            "NOT enforcement_ready OR "
            "(reconciled_at IS NOT NULL AND source_revision IS NOT NULL AND length(trim(source_revision)) > 0)",
            name="ck_tenant_quota_usage_ready_provenance",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    codes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    scans: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    campaigns: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    products: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    accounts: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enforcement_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class QuotaRolloutPhase(StrEnum):
    bridge = "bridge"
    drained = "drained"
    active = "active"


class QuotaRolloutState(Base):
    """Durable global epoch which gates all cumulative quota enforcement."""

    __tablename__ = "quota_rollout_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_quota_rollout_state_singleton"),
        CheckConstraint(
            "length(trim(source_revision)) > 0",
            name="ck_quota_rollout_state_source_revision",
        ),
        CheckConstraint(
            "(phase = 'bridge' AND drained_at IS NULL AND activated_at IS NULL "
            "AND drained_by IS NULL AND activated_by IS NULL) OR "
            "(phase = 'drained' AND drained_at IS NOT NULL AND length(trim(drained_by)) > 0 "
            "AND activated_at IS NULL AND activated_by IS NULL) OR "
            "(phase = 'active' AND drained_at IS NOT NULL AND length(trim(drained_by)) > 0 "
            "AND activated_at IS NOT NULL AND length(trim(activated_by)) > 0)",
            name="ck_quota_rollout_state_phase_provenance",
        ),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1, server_default="1")
    source_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    phase: Mapped[QuotaRolloutPhase] = mapped_column(
        SQLEnum(
            QuotaRolloutPhase,
            name="quota_rollout_phase",
            native_enum=False,
            create_constraint=True,
            length=16,
        ),
        nullable=False,
        default=QuotaRolloutPhase.bridge,
        server_default=QuotaRolloutPhase.bridge.value,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    drained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    drained_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    activated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
