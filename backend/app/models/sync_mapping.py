"""CRM 消费者与外部系统映射模型"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class SyncMapping(Base):
    __tablename__ = "sync_mappings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    local_entity_type: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "consumer_profile", "product"
    local_entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_system: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "wecom", "csv_import"
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    external_phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sync_direction: Mapped[str] = mapped_column(
        String(20), nullable=False, default="bidirectional"
    )  # "bidirectional" / "push_only"
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "external_id",
            name="uq_sync_mappings_tenant_source_ext",
        ),
        Index(
            "ix_sync_mappings_entity",
            "tenant_id",
            "local_entity_type",
            "local_entity_id",
        ),
    )
