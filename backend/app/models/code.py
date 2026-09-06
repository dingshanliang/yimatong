"""码管理数据模型：CodeBatch 和 CodeItem"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from app.models.base import Base


def _contains_only(column: str, allowed: str) -> str:
    """Build a PostgreSQL/SQLite-compatible exact character whitelist."""

    remainder = column
    for character in allowed:
        remainder = f"replace({remainder}, '{character}', '')"
    return f"{remainder} = ''"


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


# ── 权威四状态生命周期契约（yimatong-zgb1.3）─────────────────────────────
# 这是统一读取层，**不**替换 DB 里的 CodeItemStatus（AC4 兼容期内保留旧值）。
# 旧值通过 LEGACY_TO_LIFECYCLE 映射到这四个互斥状态（THREE_LINE_PRODUCT_SPEC
# Decision 10/11）。bound 归一化为 active（binding 是事件，不是生命周期）；
# expired 归一化为 voided（失效即作废，不再作为独立终端）。
class CodeLifecycle(StrEnum):
    unactivated = "unactivated"
    active = "active"
    frozen = "frozen"
    voided = "voided"


LEGACY_TO_LIFECYCLE: dict[CodeItemStatus, CodeLifecycle] = {
    CodeItemStatus.created: CodeLifecycle.unactivated,
    CodeItemStatus.activated: CodeLifecycle.active,
    CodeItemStatus.bound: CodeLifecycle.active,
    CodeItemStatus.frozen: CodeLifecycle.frozen,
    CodeItemStatus.revoked: CodeLifecycle.voided,
    CodeItemStatus.expired: CodeLifecycle.voided,
}


def to_lifecycle(status: CodeItemStatus | str) -> CodeLifecycle:
    """把旧 CodeItemStatus（或其字符串值）映射为权威 CodeLifecycle。

    未知值（理论上不应出现）保守映射为 voided，避免把异常状态当作可消费的 active。
    """
    if isinstance(status, str) and not isinstance(status, CodeItemStatus):
        try:
            status = CodeItemStatus(status)
        except ValueError:
            return CodeLifecycle.voided
    return LEGACY_TO_LIFECYCLE.get(status, CodeLifecycle.voided)


class CodeType(StrEnum):
    single = "single"
    paired = "paired"
    outer = "outer"
    inner = "inner"


class CodeGenerationMode(StrEnum):
    item_level = "item_level"
    batch_level = "batch_level"


class CodeBatchSource(StrEnum):
    generated = "generated"
    imported = "imported"


def _default_expected_item_count(context) -> int:
    values = context.get_current_parameters()
    quantity = int(values.get("quantity") or 1)
    if values.get("generation_mode") == CodeGenerationMode.batch_level:
        return 1
    return quantity * (2 if values.get("code_type") == CodeType.paired else 1)


class CodeBatch(Base):
    __tablename__ = "code_batches"

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_code_batches_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_code_batches_tenant_product",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id", "sku_id"],
            ["skus.tenant_id", "skus.product_id", "skus.id"],
            name="fk_code_batches_tenant_product_sku",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id", "sku_id", "production_batch_id"],
            [
                "production_batches.tenant_id",
                "production_batches.product_id",
                "production_batches.sku_id",
                "production_batches.id",
            ],
            name="fk_code_batches_tenant_product_sku_production_batch",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "export_manifest_id"],
            ["export_logs.tenant_id", "export_logs.id"],
            name="fk_code_batches_tenant_export_manifest",
            use_alter=True,
        ),
        UniqueConstraint("tenant_id", "id", name="uq_code_batches_tenant_id_id"),
        UniqueConstraint("tenant_id", "batch_code", name="uq_code_batches_tenant_batch_code"),
        CheckConstraint("contract_version IN (0, 1)", name="ck_code_batches_contract_version"),
        CheckConstraint("source IN ('generated', 'imported')", name="ck_code_batches_source"),
        CheckConstraint(
            "contract_version = 0 OR (expected_item_count BETWEEN 1 AND 10000)",
            name="ck_code_batches_expected_item_count_cap",
        ),
        CheckConstraint(
            "exported_item_count IS NULL OR (exported_item_count BETWEEN 1 AND 10000)",
            name="ck_code_batches_exported_item_count_cap",
        ),
        CheckConstraint(
            "contract_version = 0 OR ("
            "(source = 'generated' AND generation_mode = 'batch_level' AND code_type = 'single' "
            "AND quantity = 1 AND expected_item_count = 1) OR "
            "(source = 'generated' AND generation_mode = 'item_level' AND code_type = 'single' "
            "AND quantity > 0 AND expected_item_count = quantity) OR "
            "(source = 'generated' AND generation_mode = 'item_level' AND code_type = 'paired' "
            "AND quantity > 0 AND expected_item_count = quantity * 2) OR "
            "(source = 'imported' AND generation_mode = 'item_level' AND code_type = 'single' "
            "AND quantity > 0 AND expected_item_count = quantity))",
            name="ck_code_batches_generation_shape",
        ),
        CheckConstraint(
            "(printing_at IS NULL OR (exported_at IS NOT NULL AND printing_at >= exported_at)) AND "
            "(delivered_at IS NULL OR (printing_at IS NOT NULL AND delivered_at >= printing_at))",
            name="ck_code_batches_delivery_timestamps",
        ),
        CheckConstraint(
            "(delivered_at IS NULL AND delivery_recipient IS NULL) OR "
            "(delivered_at IS NOT NULL AND NULLIF(trim(delivery_recipient), '') IS NOT NULL)",
            name="ck_code_batches_delivery_recipient",
        ),
        Index("ix_code_batches_tenant_batch", "tenant_id", "batch_code"),
        Index("ix_code_batches_tenant_product", "tenant_id", "product_id"),
        Index("ix_code_batches_tenant_product_sku", "tenant_id", "product_id", "sku_id"),
        Index(
            "ix_code_batches_tenant_product_sku_production_batch",
            "tenant_id",
            "product_id",
            "sku_id",
            "production_batch_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"), nullable=False, index=True)
    production_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("production_batches.id"), nullable=False, index=True
    )
    batch_code: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[CodeBatchStatus] = mapped_column(default=CodeBatchStatus.pending, nullable=False)
    code_type: Mapped[str] = mapped_column(String(20), nullable=False, default=CodeType.single)
    generation_mode: Mapped[str] = mapped_column(String(20), nullable=False, default=CodeGenerationMode.item_level)
    # Version 0 is a rolling-deploy compatibility marker for pre-contract rows.
    # New application writes explicitly set version 1; PostgreSQL rejects
    # runtime attempts to create new version-0 rows after rollout finalization.
    contract_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=CodeBatchSource.generated,
        server_default=CodeBatchSource.generated.value,
    )
    expected_item_count: Mapped[int] = mapped_column(
        nullable=False,
        default=_default_expected_item_count,
    )
    export_manifest_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Rows actually bound to the export artifact. NULL for batches exported
    # before exclusion-aware exports; replay falls back to expected_item_count.
    exported_item_count: Mapped[int | None] = mapped_column(nullable=True)
    printing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_recipient: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    region_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    product = relationship(
        "Product",
        lazy="selectin",
        primaryjoin="and_(CodeBatch.tenant_id == Product.tenant_id, CodeBatch.product_id == Product.id)",
        foreign_keys="[CodeBatch.tenant_id, CodeBatch.product_id]",
    )
    sku = relationship(
        "SKU",
        lazy="selectin",
        primaryjoin=(
            "and_(CodeBatch.tenant_id == SKU.tenant_id, CodeBatch.product_id == SKU.product_id, "
            "CodeBatch.sku_id == SKU.id)"
        ),
        foreign_keys="[CodeBatch.tenant_id, CodeBatch.product_id, CodeBatch.sku_id]",
        overlaps="product",
    )
    production_batch = relationship(
        "ProductionBatch",
        lazy="selectin",
        primaryjoin=(
            "and_(CodeBatch.tenant_id == ProductionBatch.tenant_id, "
            "CodeBatch.product_id == ProductionBatch.product_id, CodeBatch.sku_id == ProductionBatch.sku_id, "
            "CodeBatch.production_batch_id == ProductionBatch.id)"
        ),
        foreign_keys=("[CodeBatch.tenant_id, CodeBatch.product_id, CodeBatch.sku_id, CodeBatch.production_batch_id]"),
        overlaps="product,sku",
    )

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

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_code_items_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "code_batch_id"],
            ["code_batches.tenant_id", "code_batches.id"],
            name="fk_code_items_tenant_code_batch",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_code_items_tenant_id_id"),
        UniqueConstraint("tenant_id", "public_id", name="uq_code_items_tenant_public_id"),
        CheckConstraint(
            "(status = 'frozen' AND frozen_from_status IS NOT NULL "
            "AND frozen_from_status IN ('activated', 'bound') AND frozen_at IS NOT NULL "
            "AND freeze_provenance_version IS NOT NULL AND freeze_provenance_version IN (0, 1) "
            "AND ((freeze_provenance_version = 0 AND frozen_by IS NULL AND freeze_reason IS NULL) OR "
            "(freeze_provenance_version = 1 AND NULLIF(trim(frozen_by), '') IS NOT NULL "
            "AND NULLIF(trim(freeze_reason), '') IS NOT NULL))) OR "
            "(status <> 'frozen' AND frozen_from_status IS NULL AND frozen_at IS NULL "
            "AND frozen_by IS NULL AND freeze_reason IS NULL AND freeze_provenance_version IS NULL)",
            name="ck_code_items_frozen_provenance",
        ),
        Index("ix_code_items_tenant_batch", "tenant_id", "code_batch_id"),
        Index("ix_code_items_tenant_status", "tenant_id", "status"),
        Index("ix_code_items_pair", "pair_id"),
    )

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
    frozen_from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    frozen_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    freeze_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    freeze_provenance_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    first_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    code_batch = relationship(
        "CodeBatch",
        primaryjoin="and_(CodeItem.tenant_id == CodeBatch.tenant_id, CodeItem.code_batch_id == CodeBatch.id)",
        foreign_keys="[CodeItem.tenant_id, CodeItem.code_batch_id]",
    )


class CodeBatchGenerationReceipt(Base):
    """Tenant-owned idempotency receipt for one physical code generation request."""

    __tablename__ = "code_batch_generation_receipts"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_code_batch_generation_receipts_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_code_batch_generation_receipts_tenant_creator",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "code_batch_id"],
            ["code_batches.tenant_id", "code_batches.id"],
            name="fk_code_batch_generation_receipts_tenant_code_batch",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_digest",
            name="uq_code_batch_generation_receipts_tenant_idempotency",
        ),
        CheckConstraint(
            "length(idempotency_digest) = 64 AND idempotency_digest = lower(idempotency_digest) AND "
            + _contains_only("idempotency_digest", "0123456789abcdef"),
            name="ck_code_batch_generation_receipts_idempotency_digest",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64 AND request_fingerprint = lower(request_fingerprint) AND "
            + _contains_only("request_fingerprint", "0123456789abcdef"),
            name="ck_code_batch_generation_receipts_request_fingerprint",
        ),
        Index("ix_code_batch_generation_receipts_tenant_batch", "tenant_id", "code_batch_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    idempotency_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    code_batch_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
