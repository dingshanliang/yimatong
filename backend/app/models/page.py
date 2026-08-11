"""页面模板与版本模型"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
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


class TemplateType:
    product_info = "product_info"
    traceability = "traceability"
    brand_story = "brand_story"


class PageTemplateStatus:
    active = "active"
    archived = "archived"


class PageVersionStatus:
    draft = "draft"
    published = "published"
    archived = "archived"


class PageTemplate(Base):
    __tablename__ = "page_templates"

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_page_templates_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_page_templates_tenant_product",
        ),
        CheckConstraint("status IN ('active','archived')", name="ck_page_templates_status"),
        CheckConstraint(
            "template_type IN ('product_info','traceability','brand_story')",
            name="ck_page_templates_type",
        ),
        Index("ix_page_templates_tenant_product", "tenant_id", "product_id"),
        Index(
            "uq_page_templates_one_active_product",
            "tenant_id",
            "product_id",
            unique=True,
            postgresql_where=text("status = 'active' AND product_id IS NOT NULL"),
            sqlite_where=text("status = 'active' AND product_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    template_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PageTemplateStatus.active,
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class PageVersion(Base):
    __tablename__ = "page_versions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    page_template_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PageVersionStatus.draft,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_by_tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "page_template_id", "id", name="uq_page_versions_tenant_template_id"),
        ForeignKeyConstraint(
            ["tenant_id", "page_template_id"],
            ["page_templates.tenant_id", "page_templates.id"],
            name="fk_page_versions_tenant_template",
        ),
        ForeignKeyConstraint(
            ["created_by_tenant_id", "created_by"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_page_versions_tenant_creator",
        ),
        CheckConstraint("version > 0", name="ck_page_versions_version_positive"),
        CheckConstraint("status IN ('draft','published','archived')", name="ck_page_versions_status"),
        CheckConstraint(
            "(status = 'published' AND published_at IS NOT NULL) "
            "OR (status = 'draft' AND published_at IS NULL) "
            "OR status = 'archived'",
            name="ck_page_versions_publish_timestamp",
        ),
        Index("ix_page_versions_tenant_creator", "created_by_tenant_id", "created_by"),
        Index("ix_page_versions_template_status", "page_template_id", "status"),
        Index(
            "uq_page_versions_one_published",
            "tenant_id",
            "page_template_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
            sqlite_where=text("status = 'published'"),
        ),
        UniqueConstraint(
            "page_template_id",
            "version",
            name="uq_page_versions_template_version",
        ),
    )
