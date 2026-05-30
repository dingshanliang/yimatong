"""AI 资料识别与文案生成 API"""

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.ai import (
    extract_product_fields,
    extract_product_from_image,
    generate_campaign,
    generate_copywriting,
    generate_page_copy,
    suggest_page_structure,
)

ai_router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


class CopywritingRequest(BaseModel):
    type: str
    product_name: str
    keywords: list[str] = []


class ExtractRequest(BaseModel):
    text: str


class PageSuggestRequest(BaseModel):
    product_name: str
    category: str


class CampaignRequest(BaseModel):
    product_name: str
    goal: str
    target_audience: str


class PageCopyRequest(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=200)
    category: str = Field(default="其他", max_length=50)
    keywords: list[str] = Field(default_factory=list)


@ai_router.post("/copywriting")
async def copywriting_endpoint(
    body: CopywritingRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return generate_copywriting(body.type, body.product_name, body.keywords)


@ai_router.post("/extract")
async def extract_endpoint(
    body: ExtractRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return extract_product_fields(body.text)


@ai_router.post("/recognize-image")
async def recognize_image_endpoint(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """上传产品图片，AI 自动识别产品信息"""
    content = await file.read()
    try:
        result = extract_product_from_image(
            filename=file.filename or "unknown.jpg",
            content_type=file.content_type or "image/jpeg",
            content=content,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return result


@ai_router.post("/page-copy")
async def page_copy_endpoint(
    body: PageCopyRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """AI 生成页面文案和推荐模板"""
    return generate_page_copy(body.product_name, body.category, body.keywords)


@ai_router.post("/page-suggest")
async def page_suggest_endpoint(
    body: PageSuggestRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return suggest_page_structure(body.product_name, body.category)


@ai_router.post("/campaign")
async def campaign_endpoint(
    body: CampaignRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return generate_campaign(body.product_name, body.goal, body.target_audience)
