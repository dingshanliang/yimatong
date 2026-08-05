"""权益管理 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.campaign import BenefitCreateRequest, BenefitUpdateRequest
from app.schemas.common import PaginatedResponse
from app.services.campaign import (
    create_benefit,
    delete_benefit,
    get_benefit,
    get_benefit_summary,
    list_all_benefits,
    list_benefit_claims_admin,
    update_benefit,
)
from app.utils.auth_rbac import require_permission

benefit_router = APIRouter(prefix="/api/v1/benefits", tags=["benefits"])


@benefit_router.post("", status_code=201, summary="创建权益")
async def create_benefit_endpoint(
    body: BenefitCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("campaign:create")),
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
    q: str | None = Query(None),
    benefit_type: str | None = Query(None),
    status: str | None = Query(None),
    campaign_id: uuid.UUID | None = Query(None),
    usage: str | None = Query(None, pattern="^(used|unused)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("analytics:view")),
):
    items, total = await list_all_benefits(
        db,
        tenant_id,
        q=q,
        benefit_type=benefit_type,
        status=status,
        campaign_id=campaign_id,
        usage=usage,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@benefit_router.get("/summary", summary="权益概览")
async def benefit_summary_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("analytics:view")),
):
    return await get_benefit_summary(db, tenant_id)


# --- Admin benefit claims ---


@benefit_router.get("/admin/claims", summary="benefit claims admin 列表")
async def list_benefit_claims_admin_endpoint(
    q: str | None = Query(None),
    benefit_id: uuid.UUID | None = Query(None),
    campaign_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    delivery_status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("analytics:view")),
):
    items, total = await list_benefit_claims_admin(
        db,
        tenant_id,
        q=q,
        benefit_id=benefit_id,
        campaign_id=campaign_id,
        status=status,
        delivery_status=delivery_status,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@benefit_router.get("/{benefit_id}", summary="获取 权益")
async def get_benefit_endpoint(
    benefit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("analytics:view")),
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
    _permission: None = Depends(require_permission("campaign:manage")),
):
    try:
        data = await update_benefit(
            db,
            tenant_id,
            benefit_id,
            **body.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not data:
        raise HTTPException(status_code=404, detail="Benefit not found")
    return data


@benefit_router.delete("/{benefit_id}", summary="删除 权益")
async def delete_benefit_endpoint(
    benefit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("campaign:manage")),
):
    deleted = await delete_benefit(db, tenant_id, benefit_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="Benefit not found")
    if deleted == "in_use":
        raise HTTPException(status_code=409, detail="已有活动使用或领取记录，不能删除，请停用权益")
    return {"status": "deleted"}
