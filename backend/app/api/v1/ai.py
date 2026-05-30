"""AI 资料识别与文案生成 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.ai import (
    AIRateLimitError,
    AIServiceError,
    AIServiceUnavailableError,
    extract_product_fields,
    extract_product_from_image,
    generate_campaign,
    generate_copywriting,
    generate_page_copy,
    suggest_page_structure,
)

ai_router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


# ──────────────────── Request Schemas ────────────────────


class CopywritingRequest(BaseModel):
    type: str = Field(..., description="文案类型：brand_story | selling_points")
    product_name: str = Field(..., min_length=1, max_length=200)
    keywords: list[str] = Field(default_factory=list)
    target_type: str | None = Field(None, description="关联实体类型")
    target_id: uuid.UUID | None = Field(None, description="关联实体 ID")


class ExtractTextRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)


class ExtractImageRequest(BaseModel):
    image_url: str = Field(..., description="已上传到 MinIO 的图片 URL")
    filename: str = Field(default="image.jpg", max_length=255)


class PageSuggestRequest(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=200)
    category: str = Field(..., min_length=1, max_length=50)
    target_type: str | None = Field(None)
    target_id: uuid.UUID | None = Field(None)


class PageCopyRequest(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=200)
    category: str = Field(default="其他", max_length=50)
    keywords: list[str] = Field(default_factory=list)
    target_type: str | None = Field(None)
    target_id: uuid.UUID | None = Field(None)


class CampaignRequest(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=200)
    goal: str = Field(..., description="活动目标：promotion | retention | brand_awareness | festival")
    target_audience: str = Field(..., min_length=1, max_length=200)
    target_type: str | None = Field(None)
    target_id: uuid.UUID | None = Field(None)


# ──────────────────── Error Handling ────────────────────


def _handle_ai_error(e: AIServiceError) -> HTTPException:
    if isinstance(e, AIRateLimitError):
        return HTTPException(status_code=429, detail=str(e))
    if isinstance(e, AIServiceUnavailableError):
        return HTTPException(status_code=503, detail=str(e))
    return HTTPException(status_code=500, detail=str(e))


# ──────────────────── Endpoints ────────────────────


@ai_router.post("/copywriting")
async def copywriting_endpoint(
    body: CopywritingRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """AI-02: 生成文案（品牌故事/产品卖点）"""
    try:
        return await generate_copywriting(
            copy_type=body.type,
            product_name=body.product_name,
            keywords=body.keywords,
            tenant_id=tenant_id,
            db=db,
            target_type=body.target_type,
            target_id=body.target_id,
        )
    except AIServiceError as e:
        raise _handle_ai_error(e)


@ai_router.post("/extract")
async def extract_text_endpoint(
    body: ExtractTextRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """AI-01a: 从文本提取产品字段"""
    try:
        return await extract_product_fields(
            text=body.text,
            tenant_id=tenant_id,
            db=db,
        )
    except AIServiceError as e:
        raise _handle_ai_error(e)


@ai_router.post("/recognize-image")
async def recognize_image_endpoint(
    body: ExtractImageRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """AI-01b: 从图片识别产品信息（需先上传到 MinIO）"""
    try:
        return await extract_product_from_image(
            image_url=body.image_url,
            filename=body.filename,
            tenant_id=tenant_id,
            db=db,
        )
    except AIServiceError as e:
        raise _handle_ai_error(e)


@ai_router.post("/page-copy")
async def page_copy_endpoint(
    body: PageCopyRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """AI-02+03: 生成页面文案和推荐模板"""
    try:
        return await generate_page_copy(
            product_name=body.product_name,
            category=body.category,
            keywords=body.keywords,
            tenant_id=tenant_id,
            db=db,
            target_type=body.target_type,
            target_id=body.target_id,
        )
    except AIServiceError as e:
        raise _handle_ai_error(e)


@ai_router.post("/page-suggest")
async def page_suggest_endpoint(
    body: PageSuggestRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """AI-03: 页面结构建议"""
    try:
        return await suggest_page_structure(
            product_name=body.product_name,
            category=body.category,
            tenant_id=tenant_id,
            db=db,
            target_type=body.target_type,
            target_id=body.target_id,
        )
    except AIServiceError as e:
        raise _handle_ai_error(e)


@ai_router.post("/campaign")
async def campaign_endpoint(
    body: CampaignRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """AI-04: 生成活动方案"""
    try:
        return await generate_campaign(
            product_name=body.product_name,
            goal=body.goal,
            target_audience=body.target_audience,
            tenant_id=tenant_id,
            db=db,
            target_type=body.target_type,
            target_id=body.target_id,
        )
    except AIServiceError as e:
        raise _handle_ai_error(e)
