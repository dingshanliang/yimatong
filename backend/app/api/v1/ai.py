"""AI 资料识别与文案生成 API"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.ai import (
    extract_product_fields,
    generate_campaign,
    generate_copywriting,
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
