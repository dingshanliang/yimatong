import uuid
from datetime import date
from enum import StrEnum

from sqlalchemy import JSON, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from app.models.base import Base


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


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[BrandStatus] = mapped_column(default=BrandStatus.active, nullable=False)

    products = relationship("Product", back_populates="brand", lazy="selectin")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[ProductStatus] = mapped_column(default=ProductStatus.active, nullable=False)

    brand = relationship("Brand", back_populates="products")
    skus = relationship("SKU", back_populates="product", lazy="selectin")


class SKU(Base):
    __tablename__ = "skus"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    specifications: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[SKUStatus] = mapped_column(default=SKUStatus.active, nullable=False)

    product = relationship("Product", back_populates="skus")


class ProductionBatch(Base):
    __tablename__ = "production_batches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"), nullable=False, index=True)
    batch_code: Mapped[str] = mapped_column(String(100), nullable=False)
    production_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[BatchStatus] = mapped_column(default=BatchStatus.active, nullable=False)
