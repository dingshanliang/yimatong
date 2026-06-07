"""码管理 Pydantic Schema：请求/响应模型"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.code import CodeGenerationMode, CodeItemStatus, CodeType
from app.schemas.common import PaginatedResponse

# ── 码批次 Schema ──────────────────────────────────────────────


class CodeBatchCreateRequest(BaseModel):
    """创建码批次请求"""

    product_id: uuid.UUID = Field(..., description="关联产品 ID")
    sku_id: uuid.UUID = Field(..., description="关联 SKU ID")
    production_batch_id: uuid.UUID = Field(..., description="关联生产批次 ID")
    batch_code: str | None = Field(None, max_length=100, description="批次编码（留空则取生产批次号）")
    quantity: int = Field(..., gt=0, le=100000, description="生成数量")
    code_type: str = Field(CodeType.single, description="码类型")
    generation_mode: CodeGenerationMode = Field(CodeGenerationMode.item_level, description="生成方式")


class CodeBatchUpdateRequest(BaseModel):
    """更新码批次请求"""

    batch_code: str | None = Field(None, max_length=100, description="批次编码")


class CodeBatchRead(BaseModel):
    """码批次列表项"""

    id: uuid.UUID
    tenant_id: uuid.UUID
    product_id: uuid.UUID
    sku_id: uuid.UUID
    production_batch_id: uuid.UUID | None = None
    batch_code: str
    quantity: int
    status: str
    code_type: str = CodeType.single
    generation_mode: str = CodeGenerationMode.item_level
    created_by: uuid.UUID
    product_name: str | None = None
    sku_name: str | None = None
    sku_code: str | None = None
    production_batch_code: str | None = None
    production_date: date | None = None
    production_origin: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class CodeBatchDetailRead(CodeBatchRead):
    """码批次详情（含各状态统计）"""

    stats: dict[str, int] = Field(default_factory=dict, description="各状态码数量统计")


class CodeBatchActivateResponse(BaseModel):
    """激活码批次响应"""

    activated: int = Field(..., description="激活数量")


class CodeBatchFreezeResponse(BaseModel):
    """冻结码批次响应"""

    frozen: int = Field(..., description="冻结数量")


class CodeBatchVoidResponse(BaseModel):
    """作废码批次响应"""

    voided: int = Field(..., description="作废数量")


class CodeBatchStatusResponse(BaseModel):
    """码批次状态变更响应"""

    status: str = Field(..., description="当前状态")


# ── 码项 Schema ────────────────────────────────────────────────


class CodeItemRead(BaseModel):
    """码项"""

    id: uuid.UUID
    tenant_id: uuid.UUID
    code_batch_id: uuid.UUID
    public_id: str
    status: CodeItemStatus
    code_type: str = CodeType.single
    pair_id: uuid.UUID | None = None
    activated_at: datetime | None = None
    bound_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class CodeItemUpdateRequest(BaseModel):
    """更新码项请求"""

    status: str | None = Field(None, description="目标状态")


class CodeItemResolveRead(BaseModel):
    """通过 public_id 解析码项"""

    id: str
    public_id: str
    status: str
    code_batch_id: str
    product_id: str | None = None
    sku_id: str | None = None


# ── 列表响应 ────────────────────────────────────────────────────


class CodeBatchListResponse(PaginatedResponse):
    """码批次分页列表"""

    items: list[CodeBatchRead] = Field(..., description="码批次列表")


class CodeItemListResponse(PaginatedResponse):
    """码项分页列表"""

    items: list[CodeItemRead] = Field(..., description="码项列表")


# ── 既有码导入 Schema ──────────────────────────────────────────


class ExistingCodeImportResponse(BaseModel):
    """既有码导入响应"""

    imported: int = Field(0, description="成功导入数量")
    skipped: int = Field(0, description="跳过（已存在）数量")
    failed: int = Field(0, description="失败数量")
    total: int = Field(0, description="文件中总行数")
    batch_id: str = Field(..., description="关联码批次 ID")
    errors: list[dict] = Field(default_factory=list, description="失败行详情")
