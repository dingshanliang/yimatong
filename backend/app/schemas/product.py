import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.product import (
    BatchStatus,
    BrandStatus,
    ProductAssetStatus,
    ProductAssetType,
    ProductStatus,
    SKUStatus,
)
from app.schemas.common import PaginatedResponse  # noqa: F401 — re-exported
from app.utils.public_url import normalize_public_url


class CatalogWriteSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("logo_url", "image_url", "file_url", check_fields=False)
    @classmethod
    def validate_public_url(cls, value: str | None) -> str | None:
        return normalize_public_url(value) if value is not None else None


def _validate_specifications(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if len(value) > 50:
        raise ValueError("Specifications may contain at most 50 entries")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = raw_key.strip()
        item = raw_value.strip()
        if not key or not item:
            raise ValueError("Specification keys and values must be nonblank")
        if len(key) > 100 or len(item) > 500:
            raise ValueError("Specification key or value is too long")
        normalized[key] = item
    return normalized


class BrandCreate(CatalogWriteSchema):
    name: str = Field(..., min_length=1, max_length=100, description="品牌名称", examples=["示例食品"])
    logo_url: str | None = Field(None, max_length=500, description="品牌 Logo URL")
    description: str | None = Field(None, max_length=500, description="品牌描述")


class BrandUpdate(CatalogWriteSchema):
    name: str | None = Field(None, min_length=1, max_length=100, description="品牌名称")
    logo_url: str | None = Field(None, max_length=500, description="品牌 Logo URL")
    description: str | None = Field(None, max_length=500, description="品牌描述")
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


class ProductCreate(CatalogWriteSchema):
    brand_id: uuid.UUID = Field(..., description="所属品牌 ID")
    name: str = Field(..., min_length=1, max_length=200, description="产品名称", examples=["有机纯牛奶"])
    category: str | None = Field(None, max_length=100, description="产品分类")
    origin: str | None = Field(None, max_length=200, description="产地/原产地")
    image_url: str | None = Field(None, max_length=500, description="产品主图 URL")
    story_title: str | None = Field(None, max_length=200, description="品牌/产品故事标题")
    story_content: str | None = Field(None, max_length=20_000, description="品牌/产品故事正文")
    description: str | None = Field(None, max_length=1000, description="产品描述")


class ProductUpdate(CatalogWriteSchema):
    brand_id: uuid.UUID | None = Field(None, description="所属品牌 ID")
    name: str | None = Field(None, min_length=1, max_length=200, description="产品名称")
    category: str | None = Field(None, max_length=100, description="产品分类")
    origin: str | None = Field(None, max_length=200, description="产地/原产地")
    image_url: str | None = Field(None, max_length=500, description="产品主图 URL")
    story_title: str | None = Field(None, max_length=200, description="品牌/产品故事标题")
    story_content: str | None = Field(None, max_length=20_000, description="品牌/产品故事正文")
    description: str | None = Field(None, max_length=1000, description="产品描述")
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
    created_at: datetime | None = Field(None, description="创建时间")

    model_config = {"from_attributes": True}


class SKUCreate(CatalogWriteSchema):
    product_id: uuid.UUID = Field(..., description="所属产品 ID")
    code: str = Field(..., min_length=1, max_length=100, description="SKU 编码", examples=["MILK-001-250ML"])
    name: str = Field(..., min_length=1, max_length=200, description="SKU 名称", examples=["250ml 盒装"])
    specifications: dict[str, str] | None = Field(None, description="规格属性（如容量、重量等）")
    package_type: str | None = Field(None, max_length=100, description="包装类型")
    barcode: str | None = Field(None, max_length=100, description="条形码/GTIN")
    image_url: str | None = Field(None, max_length=500, description="SKU 图片 URL")

    _normalize_specifications = field_validator("specifications")(_validate_specifications)


class SKUUpdate(CatalogWriteSchema):
    code: str | None = Field(None, min_length=1, max_length=100, description="SKU 编码")
    name: str | None = Field(None, min_length=1, max_length=200, description="SKU 名称")
    specifications: dict[str, str] | None = Field(None, description="规格属性")
    package_type: str | None = Field(None, max_length=100, description="包装类型")
    barcode: str | None = Field(None, max_length=100, description="条形码/GTIN")
    image_url: str | None = Field(None, max_length=500, description="SKU 图片 URL")
    status: SKUStatus | None = Field(None, description="SKU 状态")

    _normalize_specifications = field_validator("specifications")(_validate_specifications)


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
    created_at: datetime | None = Field(None, description="创建时间")

    model_config = {"from_attributes": True}


class ProductionBatchCreate(CatalogWriteSchema):
    product_id: uuid.UUID = Field(..., description="产品 ID")
    sku_id: uuid.UUID = Field(..., description="SKU ID")
    batch_code: str = Field(..., min_length=1, max_length=100, description="批次号", examples=["B20250601A"])
    production_date: date = Field(..., description="生产日期")
    expiry_date: date = Field(..., description="保质期至")
    origin: str | None = Field(None, max_length=200, description="批次产地")

    @model_validator(mode="after")
    def validate_date_order(self):
        if self.expiry_date < self.production_date:
            raise ValueError("Expiry date cannot be earlier than production date")
        return self


class ProductionBatchUpdate(CatalogWriteSchema):
    batch_code: str | None = Field(None, min_length=1, max_length=100, description="批次号")
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
    created_at: datetime | None = Field(None, description="创建时间")

    model_config = {"from_attributes": True}


class CSVImportResult(BaseModel):
    imported: int = Field(..., description="成功导入数量")
    errors: list[str] = Field(..., description="错误信息列表")


class ProductAssetCreate(CatalogWriteSchema):
    asset_type: ProductAssetType = Field(..., description="资料类型")
    name: str = Field(..., min_length=1, max_length=200, description="资料名称")
    description: str | None = Field(None, max_length=1000, description="资料说明")
    issuer: str | None = Field(None, max_length=200, description="签发/检测机构")
    valid_until: date | None = Field(None, description="有效期至")
    file_url: str | None = Field(None, max_length=500, description="文件 URL")
    image_url: str | None = Field(None, max_length=500, description="图片 URL")
    content_text: str | None = Field(None, max_length=50_000, description="正文内容")
    metadata_json: dict[str, object] | None = Field(None, description="扩展元数据")

    @model_validator(mode="after")
    def validate_active_trust_evidence(self):
        if self.asset_type in {ProductAssetType.test_report, ProductAssetType.certificate}:
            if not self.issuer or not (self.file_url or self.image_url):
                raise ValueError("Active trust evidence requires an issuer and a public evidence URL")
            if self.valid_until is not None and self.valid_until < date.today():
                raise ValueError("Active trust evidence must not already be expired")
        return self


class ProductAssetUpdate(CatalogWriteSchema):
    asset_type: ProductAssetType | None = Field(None, description="资料类型")
    name: str | None = Field(None, min_length=1, max_length=200, description="资料名称")
    description: str | None = Field(None, max_length=1000, description="资料说明")
    issuer: str | None = Field(None, max_length=200, description="签发/检测机构")
    valid_until: date | None = Field(None, description="有效期至")
    file_url: str | None = Field(None, max_length=500, description="文件 URL")
    image_url: str | None = Field(None, max_length=500, description="图片 URL")
    content_text: str | None = Field(None, max_length=50_000, description="正文内容")
    metadata_json: dict[str, object] | None = Field(None, description="扩展元数据")
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
    created_at: datetime | None = Field(None, description="创建时间")

    model_config = {"from_attributes": True}
