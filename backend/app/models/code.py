"""码管理数据模型：CodeBatch 和 CodeItem"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


class CodeBatchStatus(StrEnum):
    pending = "pending"
    generating = "generating"
    completed = "completed"
    failed = "failed"


class CodeItemStatus(StrEnum):
    created = "created"
    activated = "activated"
    bound = "bound"
    expired = "expired"
    revoked = "revoked"
    frozen = "frozen"


class CodeType(StrEnum):
    single = "single"
    paired = "paired"
    outer = "outer"
    inner = "inner"


class CodeBatch(Base):
    __tablename__ = "code_batches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"), nullable=False, index=True)
    batch_code: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[CodeBatchStatus] = mapped_column(default=CodeBatchStatus.pending, nullable=False)
    code_type: Mapped[str] = mapped_column(String(20), nullable=False, default=CodeType.single)
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    region_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)

    __table_args__ = (
        Index("ix_code_batches_tenant_batch", "tenant_id", "batch_code"),
    )


class CodeItem(Base):
    __tablename__ = "code_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    code_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("code_batches.id"), nullable=False, index=True
    )
    public_id: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    status: Mapped[CodeItemStatus] = mapped_column(
        default=CodeItemStatus.created, nullable=False
    )
    code_type: Mapped[str] = mapped_column(String(20), nullable=False, default=CodeType.single)
    pair_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    bound_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_code_items_tenant_batch", "tenant_id", "code_batch_id"),
        Index("ix_code_items_tenant_status", "tenant_id", "status"),
        Index("ix_code_items_pair", "pair_id"),
    )
