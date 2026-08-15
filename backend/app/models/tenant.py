import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    func,
    select,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from uuid6 import uuid7

from app.models.base import Base
from app.utils.email import normalize_email


def _account_role_tenant_id(context) -> uuid.UUID:
    """Derive association ownership for ORM secondary inserts.

    SQLAlchemy's ``relationship(..., secondary=...)`` writes only the endpoint
    identifiers.  The database trigger is the authoritative guard for raw SQL;
    this default preserves the same interface for ORM and SQLite-backed tests.
    """

    parameters = context.get_current_parameters()
    accounts_table = Base.metadata.tables["accounts"]
    roles_table = Base.metadata.tables["roles"]
    account_tenant_id = context.connection.execute(
        select(accounts_table.c.tenant_id).where(accounts_table.c.id == parameters["account_id"])
    ).scalar_one_or_none()
    role_tenant_id = context.connection.execute(
        select(roles_table.c.tenant_id).where(roles_table.c.id == parameters["role_id"])
    ).scalar_one_or_none()
    if account_tenant_id is None or account_tenant_id != role_tenant_id:
        raise ValueError("Account and role must exist in the same tenant")
    return account_tenant_id


def _role_permission_tenant_id(context) -> uuid.UUID:
    """Derive tenant ownership for ORM role-permission secondary inserts."""

    parameters = context.get_current_parameters()
    roles_table = Base.metadata.tables["roles"]
    permissions_table = Base.metadata.tables["permissions"]
    role_tenant_id = context.connection.execute(
        select(roles_table.c.tenant_id).where(roles_table.c.id == parameters["role_id"])
    ).scalar_one_or_none()
    permission_tenant_id = context.connection.execute(
        select(permissions_table.c.tenant_id).where(permissions_table.c.id == parameters["permission_id"])
    ).scalar_one_or_none()
    if role_tenant_id is None or role_tenant_id != permission_tenant_id:
        raise ValueError("Role and permission must exist in the same tenant")
    return role_tenant_id


account_roles = Table(
    "account_roles",
    Base.metadata,
    Column("tenant_id", Uuid(), nullable=False, default=_account_role_tenant_id),
    Column("account_id", ForeignKey("accounts.id"), primary_key=True),
    Column("role_id", ForeignKey("roles.id"), primary_key=True),
    ForeignKeyConstraint(
        ["tenant_id", "account_id"],
        ["accounts.tenant_id", "accounts.id"],
        name="fk_account_roles_tenant_account",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "role_id"],
        ["roles.tenant_id", "roles.id"],
        name="fk_account_roles_tenant_role",
    ),
    CheckConstraint("tenant_id IS NOT NULL", name="ck_account_roles_tenant_id_nn"),
    Index("ix_account_roles_role_id", "role_id"),
    Index("ix_account_roles_tenant_id", "tenant_id"),
)

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("tenant_id", Uuid(), nullable=False, default=_role_permission_tenant_id),
    Column("role_id", ForeignKey("roles.id"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id"), primary_key=True),
    ForeignKeyConstraint(
        ["tenant_id", "role_id"],
        ["roles.tenant_id", "roles.id"],
        name="fk_role_permissions_tenant_role",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "permission_id"],
        ["permissions.tenant_id", "permissions.id"],
        name="fk_role_permissions_tenant_permission",
    ),
    CheckConstraint("tenant_id IS NOT NULL", name="ck_role_permissions_tenant_id_nn"),
    Index("ix_role_permissions_permission_id", "permission_id"),
    Index("ix_role_permissions_tenant_id", "tenant_id"),
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
    brand_profile: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="租户品牌定制槽位：primary_color/radius_preset/background_preset/hide_yimatong_brand/logo_url/support_phone/support_wecom_url",
    )
    onboarding_progress: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    enabled_features: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    categories: Mapped[list | None] = mapped_column(JSON, default=list, nullable=True, comment="租户品类配置")
    custom_domain: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="自定义域名")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
    )

    organizations = relationship("Organization", back_populates="tenant", lazy="selectin")


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_organizations_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["organizations.tenant_id", "organizations.id"],
            name="fk_organizations_tenant_parent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant = relationship("Tenant", back_populates="organizations")
    accounts = relationship(
        "Account",
        back_populates="organization",
        foreign_keys="[Account.organization_id]",
        lazy="noload",
    )


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    auth_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    failed_login_attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("email = lower(trim(email))", name="ck_accounts_email_canonical"),
        UniqueConstraint("tenant_id", "id", name="uq_accounts_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "organization_id"],
            ["organizations.tenant_id", "organizations.id"],
            name="fk_accounts_tenant_organization",
        ),
        Index("uq_accounts_tenant_email_ci", tenant_id, func.lower(email), unique=True),
    )

    @validates("email")
    def _normalize_email(self, _key: str, value: str) -> str:
        return normalize_email(value)

    organization = relationship("Organization", back_populates="accounts", foreign_keys=[organization_id])
    roles = relationship(
        "Role",
        secondary="account_roles",
        primaryjoin="Account.id == account_roles.c.account_id",
        secondaryjoin="Role.id == account_roles.c.role_id",
        foreign_keys="[account_roles.c.account_id, account_roles.c.role_id]",
        back_populates="accounts",
        lazy="selectin",
    )


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_roles_tenant_id_id"),
        UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    accounts = relationship(
        "Account",
        secondary="account_roles",
        primaryjoin="Role.id == account_roles.c.role_id",
        secondaryjoin="Account.id == account_roles.c.account_id",
        foreign_keys="[account_roles.c.account_id, account_roles.c.role_id]",
        back_populates="roles",
        lazy="selectin",
    )
    permissions = relationship(
        "Permission",
        secondary="role_permissions",
        primaryjoin="Role.id == role_permissions.c.role_id",
        secondaryjoin="Permission.id == role_permissions.c.permission_id",
        foreign_keys="[role_permissions.c.role_id, role_permissions.c.permission_id]",
        back_populates="roles",
        lazy="selectin",
    )


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_permissions_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_permissions_tenant_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    roles = relationship(
        "Role",
        secondary="role_permissions",
        primaryjoin="Permission.id == role_permissions.c.permission_id",
        secondaryjoin="Role.id == role_permissions.c.role_id",
        foreign_keys="[role_permissions.c.role_id, role_permissions.c.permission_id]",
        back_populates="permissions",
        lazy="selectin",
    )


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
    release_execute = "release:execute"


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

    __table_args__ = (
        ForeignKeyConstraint(
            ["client_tenant_id", "granted_by"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_agency_authorizations_client_grantor",
        ),
        CheckConstraint(
            "agency_tenant_id <> client_tenant_id",
            name="ck_agency_authorizations_distinct_tenants",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at >= granted_at",
            name="ck_agency_authorizations_expiry_after_grant",
        ),
        CheckConstraint(
            "(status = 'active' AND revoked_at IS NULL) "
            "OR (status = 'revoked' AND revoked_at IS NOT NULL AND revoked_at >= granted_at) "
            "OR (status = 'expired' AND revoked_at IS NULL AND expires_at IS NOT NULL AND expires_at >= granted_at)",
            name="ck_agency_authorizations_lifecycle",
        ),
        Index("ix_agency_auth_agency_client", "agency_tenant_id", "client_tenant_id"),
        Index(
            "uq_agency_auth_active",
            "agency_tenant_id",
            "client_tenant_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )
