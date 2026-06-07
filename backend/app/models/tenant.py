import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String, Table, UniqueConstraint, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from app.models.base import Base

account_roles = Table(
    "account_roles",
    Base.metadata,
    Column("account_id", ForeignKey("accounts.id"), primary_key=True),
    Column("role_id", ForeignKey("roles.id"), primary_key=True),
)

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", ForeignKey("roles.id"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id"), primary_key=True),
)


class TenantStatus(StrEnum):
    active = "active"
    suspended = "suspended"
    terminated = "terminated"


class TenantPlan(StrEnum):
    free = "free"
    starter = "starter"
    pro = "pro"
    enterprise = "enterprise"


class TenantType(StrEnum):
    brand = "brand"
    agency = "agency"
    regional_org = "regional_org"
    platform = "platform"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    status: Mapped[TenantStatus] = mapped_column(SQLEnum(TenantStatus), default=TenantStatus.active, nullable=False)
    plan: Mapped[TenantPlan] = mapped_column(SQLEnum(TenantPlan), default=TenantPlan.free, nullable=False)
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tenant_type: Mapped[TenantType] = mapped_column(
        SQLEnum(TenantType), default=TenantType.brand, nullable=False, server_default="brand"
    )
    industry: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    quota: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    compliance_settings: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    onboarding_progress: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    enabled_features: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    categories: Mapped[list | None] = mapped_column(JSON, default=list, nullable=True, comment="租户品类配置")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
    )

    organizations = relationship("Organization", back_populates="tenant", lazy="selectin")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant = relationship("Tenant", back_populates="organizations")
    accounts = relationship("Account", back_populates="organization", lazy="selectin")


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_account_tenant_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    failed_login_attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization = relationship("Organization", back_populates="accounts")
    roles = relationship("Role", secondary="account_roles", back_populates="accounts", lazy="selectin")


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    accounts = relationship("Account", secondary="account_roles", back_populates="roles", lazy="selectin")
    permissions = relationship("Permission", secondary="role_permissions", back_populates="roles", lazy="selectin")


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    roles = relationship("Role", secondary="role_permissions", back_populates="permissions", lazy="selectin")


class OpsTaskStatus(StrEnum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class OpsTaskPriority(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class OpsTask(Base):
    __tablename__ = "ops_tasks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[OpsTaskStatus] = mapped_column(SQLEnum(OpsTaskStatus), default=OpsTaskStatus.pending, nullable=False)
    priority: Mapped[OpsTaskPriority] = mapped_column(
        SQLEnum(OpsTaskPriority), default=OpsTaskPriority.medium, nullable=False
    )
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class AgencyAuthScope(StrEnum):
    pages = "pages"
    campaigns = "campaigns"
    analytics = "analytics"
    products = "products"
    codes = "codes"


class AgencyAuthStatus(StrEnum):
    active = "active"
    revoked = "revoked"
    expired = "expired"


class AgencyAuthorization(Base):
    __tablename__ = "agency_authorizations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    agency_tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    client_tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    scope: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[AgencyAuthStatus] = mapped_column(
        SQLEnum(AgencyAuthStatus), default=AgencyAuthStatus.active, nullable=False
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
    )
