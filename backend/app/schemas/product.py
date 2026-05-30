import uuid
from datetime import date

from pydantic import BaseModel, Field

from app.models.product import BatchStatus, BrandStatus, ProductStatus, SKUStatus
from app.schemas.common import PaginatedResponse  # noqa: F401 — re-exported


class BrandCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="品牌名称", examples=["示例食品"])
    logo_url: str | None = Field(None, description="品牌 Logo URL")
    description: str | None = Field(None, description="品牌描述")


class BrandUpdate(BaseModel):
    name: str | None = Field(None, max_length=100, description="品牌名称")
    logo_url: str | None = Field(None, description="品牌 Logo URL")
    description: str | None = Field(None, description="品牌描述")
    status: BrandStatus | None = Field(None, description="品牌状态")


class BrandRead(BaseModel):
    id: uuid.UUID = Field(..., description="品牌 ID")
    tenant_id: uuid.UUID = Field(..., description="租户 ID")
    name: str = Field(..., description="品牌名称")
    logo_url: str | None = Field(None, description="品牌 Logo URL")
    description: str | None = Field(None, description="品牌描述")
    status: BrandStatus = Field(..., description="品牌状态")

    model_config = {"from_attributes": True}


class ProductCreate(BaseModel):
    brand_id: uuid.UUID = Field(..., description="所属品牌 ID")
    name: str = Field(..., min_length=1, max_length=200, description="产品名称", examples=["有机纯牛奶"])
    category: str | None = Field(None, description="产品分类")
    description: str | None = Field(None, description="产品描述")


class ProductUpdate(BaseModel):
    name: str | None = Field(None, max_length=200, description="产品名称")
    category: str | None = Field(None, description="产品分类")
    description: str | None = Field(None, description="产品描述")
    status: ProductStatus | None = Field(None, description="产品状态")


class ProductRead(BaseModel):
    id: uuid.UUID = Field(..., description="产品 ID")
    tenant_id: uuid.UUID = Field(..., description="租户 ID")
    brand_id: uuid.UUID = Field(..., description="品牌 ID")
    name: str = Field(..., description="产品名称")
    category: str | None = Field(None, description="产品分类")
    description: str | None = Field(None, description="产品描述")
    status: ProductStatus = Field(..., description="产品状态")

    model_config = {"from_attributes": True}


class SKUCreate(BaseModel):
    product_id: uuid.UUID = Field(..., description="所属产品 ID")
    code: str = Field(..., min_length=1, max_length=100, description="SKU 编码", examples=["MILK-001-250ML"])
    name: str = Field(..., min_length=1, max_length=200, description="SKU 名称", examples=["250ml 盒装"])
    specifications: dict | None = Field(None, description="规格属性（如容量、重量等）")


class SKUUpdate(BaseModel):
    code: str | None = Field(None, max_length=100, description="SKU 编码")
    name: str | None = Field(None, max_length=200, description="SKU 名称")
    specifications: dict | None = Field(None, description="规格属性")
    status: SKUStatus | None = Field(None, description="SKU 状态")


class SKURead(BaseModel):
    id: uuid.UUID = Field(..., description="SKU ID")
    tenant_id: uuid.UUID = Field(..., description="租户 ID")
    product_id: uuid.UUID = Field(..., description="产品 ID")
    code: str = Field(..., description="SKU 编码")
    name: str = Field(..., description="SKU 名称")
    specifications: dict | None = Field(None, description="规格属性")
    status: SKUStatus = Field(..., description="SKU 状态")

    model_config = {"from_attributes": True}


class ProductionBatchCreate(BaseModel):
    product_id: uuid.UUID = Field(..., description="产品 ID")
    sku_id: uuid.UUID = Field(..., description="SKU ID")
    batch_code: str = Field(..., min_length=1, max_length=100, description="批次号", examples=["B20250601A"])
    production_date: date = Field(..., description="生产日期")
    expiry_date: date = Field(..., description="保质期至")


class ProductionBatchUpdate(BaseModel):
    batch_code: str | None = Field(None, max_length=100, description="批次号")
    production_date: date | None = Field(None, description="生产日期")
    expiry_date: date | None = Field(None, description="保质期至")
    status: BatchStatus | None = Field(None, description="批次状态")


class ProductionBatchRead(BaseModel):
    id: uuid.UUID = Field(..., description="批次 ID")
    tenant_id: uuid.UUID = Field(..., description="租户 ID")
    product_id: uuid.UUID = Field(..., description="产品 ID")
    sku_id: uuid.UUID = Field(..., description="SKU ID")
    batch_code: str = Field(..., description="批次号")
    production_date: date = Field(..., description="生产日期")
    expiry_date: date = Field(..., description="保质期至")
    status: BatchStatus = Field(..., description="批次状态")

    model_config = {"from_attributes": True}


class CSVImportResult(BaseModel):
    imported: int = Field(..., description="成功导入数量")
    errors: list[str] = Field(..., description="错误信息列表")
