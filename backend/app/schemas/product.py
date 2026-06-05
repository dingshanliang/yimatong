import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.product import (
    BatchStatus,
    BrandStatus,
    ProductAssetStatus,
    ProductAssetType,
    ProductStatus,
    SKUStatus,
)
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
    created_at: datetime | None = Field(None, description="创建时间")

    model_config = {"from_attributes": True}


class BrandStats(BaseModel):
    product_count: int = Field(0, description="产品数量")
    campaign_count: int = Field(0, description="营销活动数量")
    code_batch_count: int = Field(0, description="溯源码批次数量")
    batch_count: int = Field(0, description="生产批次数量")


class BrandDetailRead(BrandRead):
    stats: BrandStats = Field(default_factory=BrandStats, description="品牌统计数据")


class ProductCreate(BaseModel):
    brand_id: uuid.UUID = Field(..., description="所属品牌 ID")
    name: str = Field(..., min_length=1, max_length=200, description="产品名称", examples=["有机纯牛奶"])
    category: str | None = Field(None, description="产品分类")
    origin: str | None = Field(None, max_length=200, description="产地/原产地")
    image_url: str | None = Field(None, max_length=500, description="产品主图 URL")
    story_title: str | None = Field(None, max_length=200, description="品牌/产品故事标题")
    story_content: str | None = Field(None, description="品牌/产品故事正文")
    description: str | None = Field(None, description="产品描述")


class ProductUpdate(BaseModel):
    brand_id: uuid.UUID | None = Field(None, description="所属品牌 ID")
    name: str | None = Field(None, max_length=200, description="产品名称")
    category: str | None = Field(None, description="产品分类")
    origin: str | None = Field(None, max_length=200, description="产地/原产地")
    image_url: str | None = Field(None, max_length=500, description="产品主图 URL")
    story_title: str | None = Field(None, max_length=200, description="品牌/产品故事标题")
    story_content: str | None = Field(None, description="品牌/产品故事正文")
    description: str | None = Field(None, description="产品描述")
    status: ProductStatus | None = Field(None, description="产品状态")


class ProductRead(BaseModel):
    id: uuid.UUID = Field(..., description="产品 ID")
    tenant_id: uuid.UUID = Field(..., description="租户 ID")
    brand_id: uuid.UUID = Field(..., description="品牌 ID")
    brand_name: str | None = Field(None, description="品牌名称")
    name: str = Field(..., description="产品名称")
    category: str | None = Field(None, description="产品分类")
    origin: str | None = Field(None, description="产地/原产地")
    image_url: str | None = Field(None, description="产品主图 URL")
    story_title: str | None = Field(None, description="品牌/产品故事标题")
    story_content: str | None = Field(None, description="品牌/产品故事正文")
    description: str | None = Field(None, description="产品描述")
    status: ProductStatus = Field(..., description="产品状态")

    model_config = {"from_attributes": True}


class SKUCreate(BaseModel):
    product_id: uuid.UUID = Field(..., description="所属产品 ID")
    code: str = Field(..., min_length=1, max_length=100, description="SKU 编码", examples=["MILK-001-250ML"])
    name: str = Field(..., min_length=1, max_length=200, description="SKU 名称", examples=["250ml 盒装"])
    specifications: dict | None = Field(None, description="规格属性（如容量、重量等）")
    package_type: str | None = Field(None, max_length=100, description="包装类型")
    barcode: str | None = Field(None, max_length=100, description="条形码/GTIN")
    image_url: str | None = Field(None, max_length=500, description="SKU 图片 URL")


class SKUUpdate(BaseModel):
    code: str | None = Field(None, max_length=100, description="SKU 编码")
    name: str | None = Field(None, max_length=200, description="SKU 名称")
    specifications: dict | None = Field(None, description="规格属性")
    package_type: str | None = Field(None, max_length=100, description="包装类型")
    barcode: str | None = Field(None, max_length=100, description="条形码/GTIN")
    image_url: str | None = Field(None, max_length=500, description="SKU 图片 URL")
    status: SKUStatus | None = Field(None, description="SKU 状态")


class SKURead(BaseModel):
    id: uuid.UUID = Field(..., description="SKU ID")
    tenant_id: uuid.UUID = Field(..., description="租户 ID")
    product_id: uuid.UUID = Field(..., description="产品 ID")
    product_name: str | None = Field(None, description="产品名称")
    code: str = Field(..., description="SKU 编码")
    name: str = Field(..., description="SKU 名称")
    specifications: dict | None = Field(None, description="规格属性")
    package_type: str | None = Field(None, description="包装类型")
    barcode: str | None = Field(None, description="条形码/GTIN")
    image_url: str | None = Field(None, description="SKU 图片 URL")
    status: SKUStatus = Field(..., description="SKU 状态")

    model_config = {"from_attributes": True}


class ProductionBatchCreate(BaseModel):
    product_id: uuid.UUID = Field(..., description="产品 ID")
    sku_id: uuid.UUID = Field(..., description="SKU ID")
    batch_code: str = Field(..., min_length=1, max_length=100, description="批次号", examples=["B20250601A"])
    production_date: date = Field(..., description="生产日期")
    expiry_date: date = Field(..., description="保质期至")
    origin: str | None = Field(None, max_length=200, description="批次产地")


class ProductionBatchUpdate(BaseModel):
    batch_code: str | None = Field(None, max_length=100, description="批次号")
    production_date: date | None = Field(None, description="生产日期")
    expiry_date: date | None = Field(None, description="保质期至")
    origin: str | None = Field(None, max_length=200, description="批次产地")
    status: BatchStatus | None = Field(None, description="批次状态")


class ProductionBatchRead(BaseModel):
    id: uuid.UUID = Field(..., description="批次 ID")
    tenant_id: uuid.UUID = Field(..., description="租户 ID")
    product_id: uuid.UUID = Field(..., description="产品 ID")
    product_name: str | None = Field(None, description="产品名称")
    sku_id: uuid.UUID = Field(..., description="SKU ID")
    sku_name: str | None = Field(None, description="SKU 名称")
    sku_code: str | None = Field(None, description="SKU 编码")
    batch_code: str = Field(..., description="批次号")
    production_date: date = Field(..., description="生产日期")
    expiry_date: date = Field(..., description="保质期至")
    origin: str | None = Field(None, description="批次产地")
    status: BatchStatus = Field(..., description="批次状态")

    model_config = {"from_attributes": True}


class CSVImportResult(BaseModel):
    imported: int = Field(..., description="成功导入数量")
    errors: list[str] = Field(..., description="错误信息列表")


class ProductAssetCreate(BaseModel):
    asset_type: ProductAssetType = Field(..., description="资料类型")
    name: str = Field(..., min_length=1, max_length=200, description="资料名称")
    description: str | None = Field(None, max_length=1000, description="资料说明")
    issuer: str | None = Field(None, max_length=200, description="签发/检测机构")
    valid_until: date | None = Field(None, description="有效期至")
    file_url: str | None = Field(None, max_length=500, description="文件 URL")
    image_url: str | None = Field(None, max_length=500, description="图片 URL")
    content_text: str | None = Field(None, description="正文内容")
    metadata_json: dict | None = Field(None, description="扩展元数据")


class ProductAssetUpdate(BaseModel):
    asset_type: ProductAssetType | None = Field(None, description="资料类型")
    name: str | None = Field(None, min_length=1, max_length=200, description="资料名称")
    description: str | None = Field(None, max_length=1000, description="资料说明")
    issuer: str | None = Field(None, max_length=200, description="签发/检测机构")
    valid_until: date | None = Field(None, description="有效期至")
    file_url: str | None = Field(None, max_length=500, description="文件 URL")
    image_url: str | None = Field(None, max_length=500, description="图片 URL")
    content_text: str | None = Field(None, description="正文内容")
    metadata_json: dict | None = Field(None, description="扩展元数据")
    status: ProductAssetStatus | None = Field(None, description="资料状态")


class ProductAssetRead(BaseModel):
    id: uuid.UUID = Field(..., description="资料 ID")
    tenant_id: uuid.UUID = Field(..., description="租户 ID")
    product_id: uuid.UUID = Field(..., description="产品 ID")
    asset_type: ProductAssetType = Field(..., description="资料类型")
    name: str = Field(..., description="资料名称")
    description: str | None = Field(None, description="资料说明")
    issuer: str | None = Field(None, description="签发/检测机构")
    valid_until: date | None = Field(None, description="有效期至")
    file_url: str | None = Field(None, description="文件 URL")
    image_url: str | None = Field(None, description="图片 URL")
    content_text: str | None = Field(None, description="正文内容")
    metadata_json: dict | None = Field(None, description="扩展元数据")
    status: ProductAssetStatus = Field(..., description="资料状态")

    model_config = {"from_attributes": True}
