"""区域品牌/协会模型"""

import uuid

from sqlalchemy import JSON, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class RegionalOrg(Base):
    __tablename__ = "regional_orgs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    org_type: Mapped[str] = mapped_column(String(50), nullable=False, default="association")
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_regional_orgs_tenant", "tenant_id"),
    )


class RegionalOrgMember(Base):
    __tablename__ = "regional_org_members"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    member_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    __table_args__ = (
        Index("ix_regional_org_member_unique", "org_id", "tenant_id", unique=True),
    )


class RegionalTemplate(Base):
    __tablename__ = "regional_templates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class RegionalProductAuth(Base):
    __tablename__ = "regional_product_auths"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)

    __table_args__ = (
        Index("ix_regional_product_auth_unique", "org_id", "product_id", "tenant_id", unique=True),
    )


class RegionalCodeRule(Base):
    __tablename__ = "regional_code_rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    rule_name: Mapped[str] = mapped_column(String(200), nullable=False)
    pattern: Mapped[str] = mapped_column(String(50), nullable=False)
    prefix: Mapped[str] = mapped_column(String(20), nullable=False, default="")


class WhitelabelConfig(Base):
    __tablename__ = "whitelabel_configs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    org_id: Mapped[uuid.UUID] = mapped_column(nullable=False, unique=True)
    brand_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    hide_yimatong: Mapped[bool] = mapped_column(default=False, nullable=False)
    primary_color: Mapped[str] = mapped_column(String(20), nullable=False, default="#000000")
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
