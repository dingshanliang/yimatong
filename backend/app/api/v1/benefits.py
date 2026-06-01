"""权益管理 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.campaign import (
    create_benefit,
    delete_benefit,
    get_benefit,
    list_all_benefits,
    list_benefit_claims_admin,
    update_benefit,
    validate_benefit_config_shape,
)

benefit_router = APIRouter(prefix="/api/v1/benefits", tags=["benefits"])


class BenefitUpdateRequest(BaseModel):
    name: str | None = None
    benefit_type: str | None = None
    config_json: dict | None = None
    stock_total: int | None = None
    per_person_limit: int | None = None
    status: str | None = None
    connector_id: uuid.UUID | None = None

    @field_validator("config_json")
    @classmethod
    def validate_config_json(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        return validate_benefit_config_shape(v)


class BenefitCreateRequest(BaseModel):
    name: str
    benefit_type: str
    config_json: dict
    stock_total: int
    per_person_limit: int = 1
    connector_id: uuid.UUID | None = None

    @field_validator("config_json")
    @classmethod
    def validate_config_json(cls, v: dict) -> dict:
        return validate_benefit_config_shape(v)


@benefit_router.post("", status_code=201, summary="创建权益")
async def create_benefit_endpoint(
    body: BenefitCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await create_benefit(
        db,
        tenant_id,
        None,
        body.name,
        body.benefit_type,
        body.config_json,
        body.stock_total,
        body.per_person_limit,
        connector_id=body.connector_id,
    )


@benefit_router.get("", summary="权益 列表")
async def list_benefits_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items, total = await list_all_benefits(
        db,
        tenant_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@benefit_router.get("/{benefit_id}", summary="获取 权益")
async def get_benefit_endpoint(
    benefit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    data = await get_benefit(db, tenant_id, benefit_id)
    if not data:
        raise HTTPException(status_code=404, detail="Benefit not found")
    return data


@benefit_router.patch("/{benefit_id}", summary="更新 权益")
async def update_benefit_endpoint(
    benefit_id: uuid.UUID,
    body: BenefitUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    data = await update_benefit(
        db,
        tenant_id,
        benefit_id,
        **body.model_dump(exclude_none=True),
    )
    if not data:
        raise HTTPException(status_code=404, detail="Benefit not found")
    return data


@benefit_router.delete("/{benefit_id}", summary="删除 权益")
async def delete_benefit_endpoint(
    benefit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    deleted = await delete_benefit(db, tenant_id, benefit_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Benefit not found")
    return {"status": "deleted"}


# --- Admin benefit claims ---


@benefit_router.get("/admin/claims", summary="benefit claims admin 列表")
async def list_benefit_claims_admin_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items, total = await list_benefit_claims_admin(
        db,
        tenant_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)
