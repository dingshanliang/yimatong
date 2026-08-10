import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from app.models.base import Base


class ExternalRefMixin:
    """为 ERP 导入提供外部系统引用的混入字段"""

    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_system: Mapped[str | None] = mapped_column(String(50), nullable=True)


class BrandStatus(StrEnum):
    active = "active"
    inactive = "inactive"


class ProductStatus(StrEnum):
    active = "active"
    inactive = "inactive"
    draft = "draft"


class SKUStatus(StrEnum):
    active = "active"
    inactive = "inactive"


class BatchStatus(StrEnum):
    active = "active"
    recalled = "recalled"
    expired = "expired"


class ProductAssetType(StrEnum):
    image = "image"
    video = "video"
    test_report = "test_report"
    certificate = "certificate"
    story = "story"
    other = "other"


class ProductAssetStatus(StrEnum):
    active = "active"
    inactive = "inactive"


class Brand(Base, ExternalRefMixin):
    __tablename__ = "brands"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_brands_tenant"),
        UniqueConstraint("tenant_id", "id", name="uq_brands_tenant_id_id"),
        UniqueConstraint("tenant_id", "name", name="uq_brands_tenant_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[BrandStatus] = mapped_column(default=BrandStatus.active, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    products = relationship(
        "Product",
        back_populates="brand",
        lazy="selectin",
        primaryjoin="and_(Brand.tenant_id == Product.tenant_id, Brand.id == Product.brand_id)",
        foreign_keys="[Product.tenant_id, Product.brand_id]",
    )


class Product(Base, ExternalRefMixin):
    __tablename__ = "products"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_products_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "brand_id"],
            ["brands.tenant_id", "brands.id"],
            name="fk_products_tenant_brand",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_products_tenant_id_id"),
        Index("ix_products_tenant_brand", "tenant_id", "brand_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(200), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    story_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    story_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[ProductStatus] = mapped_column(default=ProductStatus.active, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    brand = relationship(
        "Brand",
        back_populates="products",
        primaryjoin="and_(Product.tenant_id == Brand.tenant_id, Product.brand_id == Brand.id)",
        foreign_keys="[Product.tenant_id, Product.brand_id]",
    )
    skus = relationship(
        "SKU",
        back_populates="product",
        lazy="selectin",
        primaryjoin="and_(Product.tenant_id == SKU.tenant_id, Product.id == SKU.product_id)",
        foreign_keys="[SKU.tenant_id, SKU.product_id]",
    )
    batches = relationship(
        "ProductionBatch",
        back_populates="product",
        lazy="selectin",
        primaryjoin=("and_(Product.tenant_id == ProductionBatch.tenant_id, Product.id == ProductionBatch.product_id)"),
        foreign_keys="[ProductionBatch.tenant_id, ProductionBatch.product_id]",
        overlaps="batches,sku",
    )
    assets = relationship(
        "ProductAsset",
        back_populates="product",
        lazy="selectin",
        cascade="all, delete-orphan",
        primaryjoin=("and_(Product.tenant_id == ProductAsset.tenant_id, Product.id == ProductAsset.product_id)"),
        foreign_keys="[ProductAsset.tenant_id, ProductAsset.product_id]",
    )

    @property
    def brand_name(self) -> str | None:
        brand = self.__dict__.get("brand")
        return brand.name if brand else None


class SKU(Base, ExternalRefMixin):
    __tablename__ = "skus"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_skus_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_skus_tenant_product",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_skus_tenant_id_id"),
        UniqueConstraint("tenant_id", "product_id", "id", name="uq_skus_tenant_product_id_id"),
        UniqueConstraint("tenant_id", "product_id", "code", name="uq_skus_tenant_product_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    specifications: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    package_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(100), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[SKUStatus] = mapped_column(default=SKUStatus.active, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    product = relationship(
        "Product",
        back_populates="skus",
        primaryjoin="and_(SKU.tenant_id == Product.tenant_id, SKU.product_id == Product.id)",
        foreign_keys="[SKU.tenant_id, SKU.product_id]",
    )
    batches = relationship(
        "ProductionBatch",
        back_populates="sku",
        lazy="selectin",
        primaryjoin=(
            "and_(SKU.tenant_id == ProductionBatch.tenant_id, "
            "SKU.product_id == ProductionBatch.product_id, SKU.id == ProductionBatch.sku_id)"
        ),
        foreign_keys=("[ProductionBatch.tenant_id, ProductionBatch.product_id, ProductionBatch.sku_id]"),
        overlaps="batches",
    )

    @property
    def product_name(self) -> str | None:
        product = self.__dict__.get("product")
        return product.name if product else None


class ProductionBatch(Base, ExternalRefMixin):
    __tablename__ = "production_batches"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_production_batches_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_production_batches_tenant_product",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id", "sku_id"],
            ["skus.tenant_id", "skus.product_id", "skus.id"],
            name="fk_production_batches_tenant_product_sku",
        ),
        CheckConstraint(
            "expiry_date >= production_date",
            name="ck_production_batches_expiry_not_before_production",
        ),
        CheckConstraint(
            "((status = 'recalled' AND NULLIF(trim(recall_reason), '') IS NOT NULL "
            "AND recalled_at IS NOT NULL AND recalled_by IS NOT NULL) "
            "OR (status <> 'recalled' AND recall_reason IS NULL "
            "AND recalled_at IS NULL AND recalled_by IS NULL))",
            name="ck_production_batches_recall_metadata",
        ),
        UniqueConstraint("tenant_id", "batch_code", name="uq_production_batches_tenant_batch_code"),
        UniqueConstraint(
            "tenant_id",
            "product_id",
            "sku_id",
            "id",
            name="uq_production_batches_tenant_product_sku_id",
        ),
        Index("ix_production_batches_tenant_product", "tenant_id", "product_id"),
        Index("ix_production_batches_tenant_product_sku", "tenant_id", "product_id", "sku_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"), nullable=False, index=True)
    batch_code: Mapped[str] = mapped_column(String(100), nullable=False)
    production_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    origin: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[BatchStatus] = mapped_column(default=BatchStatus.active, nullable=False)
    recall_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    recalled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recalled_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    product = relationship(
        "Product",
        back_populates="batches",
        primaryjoin=("and_(ProductionBatch.tenant_id == Product.tenant_id, ProductionBatch.product_id == Product.id)"),
        foreign_keys="[ProductionBatch.tenant_id, ProductionBatch.product_id]",
        overlaps="batches",
    )
    sku = relationship(
        "SKU",
        back_populates="batches",
        primaryjoin=(
            "and_(ProductionBatch.tenant_id == SKU.tenant_id, "
            "ProductionBatch.product_id == SKU.product_id, ProductionBatch.sku_id == SKU.id)"
        ),
        foreign_keys="[ProductionBatch.tenant_id, ProductionBatch.product_id, ProductionBatch.sku_id]",
        overlaps="batches,product",
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


class ProductAsset(Base, ExternalRefMixin):
    __tablename__ = "product_assets"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_product_assets_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_product_assets_tenant_product",
        ),
        CheckConstraint(
            "status <> 'active' OR asset_type NOT IN ('test_report', 'certificate') OR "
            "(NULLIF(trim(issuer), '') IS NOT NULL AND "
            "(COALESCE(trim(file_url), '') LIKE 'https://%' OR "
            "(COALESCE(trim(file_url), '') LIKE '/api/v1/files/public/%' AND "
            "COALESCE(trim(file_url), '') NOT LIKE '%/../%' AND "
            "COALESCE(trim(file_url), '') NOT LIKE '%/./%' AND "
            "COALESCE(trim(file_url), '') NOT LIKE '%\\%' AND "
            "instr(COALESCE(trim(file_url), ''), '%') = 0) OR "
            "COALESCE(trim(image_url), '') LIKE 'https://%' OR "
            "(COALESCE(trim(image_url), '') LIKE '/api/v1/files/public/%' AND "
            "COALESCE(trim(image_url), '') NOT LIKE '%/../%' AND "
            "COALESCE(trim(image_url), '') NOT LIKE '%/./%' AND "
            "COALESCE(trim(image_url), '') NOT LIKE '%\\%' AND "
            "instr(COALESCE(trim(image_url), ''), '%') = 0)) AND "
            "(valid_until IS NULL OR valid_until >= CAST(created_at AS DATE)))",
            name="ck_product_assets_active_trust_evidence",
        ),
        Index("ix_product_assets_tenant_product", "tenant_id", "product_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    asset_type: Mapped[ProductAssetType] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    issuer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[ProductAssetStatus] = mapped_column(default=ProductAssetStatus.active, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    product = relationship(
        "Product",
        back_populates="assets",
        primaryjoin=("and_(ProductAsset.tenant_id == Product.tenant_id, ProductAsset.product_id == Product.id)"),
        foreign_keys="[ProductAsset.tenant_id, ProductAsset.product_id]",
    )
