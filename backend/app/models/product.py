import uuid
from datetime import date
from enum import StrEnum

from sqlalchemy import JSON, Date, ForeignKey, String, Text
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

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[BrandStatus] = mapped_column(default=BrandStatus.active, nullable=False)

    products = relationship("Product", back_populates="brand", lazy="selectin")


class Product(Base, ExternalRefMixin):
    __tablename__ = "products"

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

    brand = relationship("Brand", back_populates="products")
    skus = relationship("SKU", back_populates="product", lazy="selectin")
    batches = relationship("ProductionBatch", back_populates="product", lazy="selectin")
    assets = relationship("ProductAsset", back_populates="product", lazy="selectin", cascade="all, delete-orphan")

    @property
    def brand_name(self) -> str | None:
        brand = self.__dict__.get("brand")
        return brand.name if brand else None


class SKU(Base, ExternalRefMixin):
    __tablename__ = "skus"

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

    product = relationship("Product", back_populates="skus")
    batches = relationship("ProductionBatch", back_populates="sku", lazy="selectin")

    @property
    def product_name(self) -> str | None:
        product = self.__dict__.get("product")
        return product.name if product else None


class ProductionBatch(Base, ExternalRefMixin):
    __tablename__ = "production_batches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"), nullable=False, index=True)
    batch_code: Mapped[str] = mapped_column(String(100), nullable=False)
    production_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    origin: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[BatchStatus] = mapped_column(default=BatchStatus.active, nullable=False)

    product = relationship("Product", back_populates="batches")
    sku = relationship("SKU", back_populates="batches")

    @property
    def product_name(self) -> str | None:
        product = self.__dict__.get("product")
        return product.name if product else None

    @property
    def sku_name(self) -> str | None:
        sku = self.__dict__.get("sku")
        return sku.name if sku else None


class ProductAsset(Base, ExternalRefMixin):
    __tablename__ = "product_assets"

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

    product = relationship("Product", back_populates="assets")
