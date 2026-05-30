"""页面模板与版本模型"""

import uuid

from sqlalchemy import JSON, Index, String
from sqlalchemy.orm import Mapped, mapped_column

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

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
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


class PageVersion(Base):
    __tablename__ = "page_versions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    page_template_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PageVersionStatus.draft,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)

    __table_args__ = (Index("ix_page_versions_template_status", "page_template_id", "status"),)
