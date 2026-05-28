import uuid
from datetime import date

from pydantic import BaseModel, Field

from app.models.product import BatchStatus, BrandStatus, ProductStatus, SKUStatus


class BrandCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    logo_url: str | None = None
    description: str | None = None


class BrandUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    logo_url: str | None = None
    description: str | None = None
    status: BrandStatus | None = None


class BrandRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    logo_url: str | None = None
    description: str | None = None
    status: BrandStatus

    model_config = {"from_attributes": True}


class ProductCreate(BaseModel):
    brand_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=200)
    category: str | None = None
    description: str | None = None


class ProductUpdate(BaseModel):
    name: str | None = Field(None, max_length=200)
    category: str | None = None
    description: str | None = None
    status: ProductStatus | None = None


class ProductRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    brand_id: uuid.UUID
    name: str
    category: str | None = None
    description: str | None = None
    status: ProductStatus

    model_config = {"from_attributes": True}


class SKUCreate(BaseModel):
    product_id: uuid.UUID
    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    specifications: dict | None = None


class SKUUpdate(BaseModel):
    code: str | None = Field(None, max_length=100)
    name: str | None = Field(None, max_length=200)
    specifications: dict | None = None
    status: SKUStatus | None = None


class SKURead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    product_id: uuid.UUID
    code: str
    name: str
    specifications: dict | None = None
    status: SKUStatus

    model_config = {"from_attributes": True}


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    page_size: int


class ProductionBatchCreate(BaseModel):
    product_id: uuid.UUID
    sku_id: uuid.UUID
    batch_code: str = Field(..., min_length=1, max_length=100)
    production_date: date
    expiry_date: date


class ProductionBatchUpdate(BaseModel):
    batch_code: str | None = Field(None, max_length=100)
    production_date: date | None = None
    expiry_date: date | None = None
    status: BatchStatus | None = None


class ProductionBatchRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    product_id: uuid.UUID
    sku_id: uuid.UUID
    batch_code: str
    production_date: date
    expiry_date: date
    status: BatchStatus

    model_config = {"from_attributes": True}


class CSVImportResult(BaseModel):
    imported: int
    errors: list[str]
