"""Migration ledger for reversible fixed-role normalization."""

import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class RoleTemplateBackup(Base):
    __tablename__ = "role_template_backups"
    __table_args__ = (UniqueConstraint("tenant_id", "role_name", name="uq_role_template_backup_tenant_role"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    role_name: Mapped[str] = mapped_column(String(50), nullable=False)
    role_created: Mapped[bool] = mapped_column(Boolean, nullable=False)
    prior_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prior_permission_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_permission_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class TenantPlatformRoleAssignmentBackup(Base):
    """Reversal ledger for historical tenant-level platform_admin assignments."""

    __tablename__ = "tenant_platform_role_assignment_backups"

    account_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    role_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    auth_version_before: Mapped[int] = mapped_column(nullable=False)


class OrganizationParentRepairBackup(Base):
    """Reversal ledger for historical cross-tenant organization parents."""

    __tablename__ = "organization_parent_repair_backups"

    organization_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    prior_parent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)


class OperatorCampaignManageGrant(Base):
    """Reversal ledger for campaign:manage grants added to built-in operators."""

    __tablename__ = "operator_campaign_manage_grants"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    permission_created: Mapped[bool] = mapped_column(Boolean, nullable=False)
