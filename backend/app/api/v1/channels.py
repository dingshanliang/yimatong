"""渠道管理 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.channel import (
    assign_batch_to_channel,
    create_distributor,
    create_region,
    create_store,
    list_distributors,
    list_diversion_clues,
    list_regions,
)

channel_router = APIRouter(prefix="/api/v1/channels", tags=["channels"])


class DistributorCreate(BaseModel):
    name: str
    code: str
    contact_name: str | None = None
    contact_phone: str | None = None


class RegionCreate(BaseModel):
    name: str
    code: str
    province: str | None = None
    city: str | None = None
    distributor_id: uuid.UUID | None = None


class StoreCreate(BaseModel):
    name: str
    code: str
    region_id: uuid.UUID | None = None
    distributor_id: uuid.UUID | None = None
    address: str | None = None


class BatchAssign(BaseModel):
    distributor_id: uuid.UUID | None = None
    region_id: uuid.UUID | None = None


# --- Distributor ---


@channel_router.post("/distributors", status_code=201, summary="创建 distributor")
async def create_distributor_endpoint(
    body: DistributorCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    dist = await create_distributor(
        db,
        tenant_id,
        body.name,
        body.code,
        contact_name=body.contact_name,
        contact_phone=body.contact_phone,
    )
    return {"id": str(dist.id), "name": dist.name, "code": dist.code}


@channel_router.get("/distributors", summary="distributors 列表")
async def list_distributors_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    dists, total = await list_distributors(db, tenant_id, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[{"id": str(d.id), "name": d.name, "code": d.code, "status": d.status} for d in dists],
        total=total,
        page=page,
        page_size=page_size,
    )


# --- Region ---


@channel_router.post("/regions", status_code=201, summary="创建 region")
async def create_region_endpoint(
    body: RegionCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    region = await create_region(
        db,
        tenant_id,
        body.name,
        body.code,
        province=body.province,
        city=body.city,
        distributor_id=body.distributor_id,
    )
    return {"id": str(region.id), "name": region.name, "code": region.code, "city": region.city}


@channel_router.get("/regions", summary="regions 列表")
async def list_regions_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    regions, total = await list_regions(db, tenant_id, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[{"id": str(r.id), "name": r.name, "code": r.code, "city": r.city} for r in regions],
        total=total,
        page=page,
        page_size=page_size,
    )


# --- Store ---


@channel_router.post("/stores", status_code=201, summary="创建 store")
async def create_store_endpoint(
    body: StoreCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    store = await create_store(
        db,
        tenant_id,
        body.name,
        body.code,
        region_id=body.region_id,
        distributor_id=body.distributor_id,
        address=body.address,
    )
    return {"id": str(store.id), "name": store.name, "code": store.code}


# --- Batch Assignment ---


@channel_router.post("/code-batches/{batch_id}/assign")
async def assign_batch_endpoint(
    batch_id: uuid.UUID,
    body: BatchAssign,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    result = await assign_batch_to_channel(
        db,
        tenant_id,
        batch_id,
        distributor_id=body.distributor_id,
        region_id=body.region_id,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Code batch not found")
    return result


# --- Diversion Clues ---


@channel_router.get("/diversion-clues", summary="diversion clues 列表")
async def list_diversion_clues_endpoint(
    resolved: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    clues, total = await list_diversion_clues(
        db,
        tenant_id,
        resolved=resolved,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[
            {
                "id": str(c.id),
                "public_id": c.public_id,
                "expected_region": c.expected_region,
                "detected_city": c.detected_city,
                "resolved": c.resolved,
            }
            for c in clues
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
