"""码管理数据模型：CodeBatch 和 CodeItem"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from app.models.base import Base


class CodeBatchStatus(StrEnum):
    pending = "pending"
    generating = "generating"
    completed = "completed"
    exported = "exported"
    printing = "printing"
    delivered = "delivered"
    activated = "activated"
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


class CodeGenerationMode(StrEnum):
    item_level = "item_level"
    batch_level = "batch_level"


class CodeBatch(Base):
    __tablename__ = "code_batches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"), nullable=False, index=True)
    production_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("production_batches.id"), nullable=True, index=True
    )
    batch_code: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[CodeBatchStatus] = mapped_column(default=CodeBatchStatus.pending, nullable=False)
    code_type: Mapped[str] = mapped_column(String(20), nullable=False, default=CodeType.single)
    generation_mode: Mapped[str] = mapped_column(String(20), nullable=False, default=CodeGenerationMode.item_level)
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    region_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_code_batches_tenant_batch", "tenant_id", "batch_code"),)

    product = relationship("Product", lazy="selectin")
    sku = relationship("SKU", lazy="selectin")
    production_batch = relationship("ProductionBatch", lazy="selectin")

    @property
    def product_name(self) -> str | None:
        product = self.__dict__.get("product")
        return product.name if product else None

    @property
    def sku_name(self) -> str | None:
        sku = self.__dict__.get("sku")
        return sku.name if sku else None

    @property
    def sku_code(self) -> str | None:
        sku = self.__dict__.get("sku")
        return sku.code if sku else None

    @property
    def production_batch_code(self) -> str | None:
        production_batch = self.__dict__.get("production_batch")
        return production_batch.batch_code if production_batch else None

    @property
    def production_date(self) -> date | None:
        production_batch = self.__dict__.get("production_batch")
        return production_batch.production_date if production_batch else None

    @property
    def production_origin(self) -> str | None:
        production_batch = self.__dict__.get("production_batch")
        return production_batch.origin if production_batch else None


class CodeItem(Base):
    __tablename__ = "code_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    code_batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("code_batches.id"), nullable=False, index=True)
    public_id: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    status: Mapped[CodeItemStatus] = mapped_column(default=CodeItemStatus.created, nullable=False)
    code_type: Mapped[str] = mapped_column(String(20), nullable=False, default=CodeType.single)
    pair_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    code_batch = relationship("CodeBatch", lazy="selectin")

    __table_args__ = (
        Index("ix_code_items_tenant_batch", "tenant_id", "code_batch_id"),
        Index("ix_code_items_tenant_status", "tenant_id", "status"),
        Index("ix_code_items_pair", "pair_id"),
    )
