"""渠道管理 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.channel import (
    allocate_codes_to_store,
    assign_batch_to_channel,
    create_distributor,
    create_region,
    create_store,
    delete_store,
    list_allocations,
    list_distributors,
    list_diversion_clues,
    list_regions,
    list_stores,
    resolve_store_for_code,
    update_distributor,
    update_region,
    update_store,
)

channel_router = APIRouter(prefix="/api/v1/channels", tags=["channels"])


class DistributorCreate(BaseModel):
    name: str
    code: str
    contact_name: str | None = None
    contact_phone: str | None = None


class DistributorUpdate(BaseModel):
    name: str | None = None
    status: str | None = None


class RegionCreate(BaseModel):
    name: str
    code: str
    province: str | None = None
    city: str | None = None
    distributor_id: uuid.UUID | None = None


class RegionUpdate(BaseModel):
    name: str | None = None
    distributor_id: uuid.UUID | None = None


class StoreCreate(BaseModel):
    name: str
    code: str
    region_id: uuid.UUID | None = None
    distributor_id: uuid.UUID | None = None
    address: str | None = None


class StoreUpdate(BaseModel):
    name: str | None = None
    region_id: uuid.UUID | None = None
    distributor_id: uuid.UUID | None = None
    address: str | None = None
    status: str | None = None


class BatchAssign(BaseModel):
    distributor_id: uuid.UUID | None = None
    region_id: uuid.UUID | None = None


class StoreAllocation(BaseModel):
    batch_id: uuid.UUID
    store_id: uuid.UUID
    quantity: int


# ── 经销商 ────────────────────────────────────────


@channel_router.post("/distributors", status_code=201, summary="创建经销商")
async def create_distributor_endpoint(
    body: DistributorCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    dist = await create_distributor(db, tenant_id, body.name, body.code,
                                     contact_name=body.contact_name, contact_phone=body.contact_phone)
    return {"id": str(dist.id), "name": dist.name, "code": dist.code, "status": dist.status}


@channel_router.get("/distributors", summary="经销商列表")
async def list_distributors_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    dists, total = await list_distributors(db, tenant_id, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[{"id": str(d.id), "name": d.name, "code": d.code, "contact_name": d.contact_name, "status": d.status} for d in dists],
        total=total, page=page, page_size=page_size,
    )


@channel_router.put("/distributors/{distributor_id}", summary="更新经销商")
async def update_distributor_endpoint(
    distributor_id: uuid.UUID,
    body: DistributorUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    dist = await update_distributor(db, distributor_id, name=body.name, status=body.status)
    if not dist:
        raise HTTPException(404, "Distributor not found")
    return {"id": str(dist.id), "name": dist.name, "status": dist.status}


# ── 区域 ──────────────────────────────────────────


@channel_router.post("/regions", status_code=201, summary="创建区域")
async def create_region_endpoint(
    body: RegionCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    region = await create_region(db, tenant_id, body.name, body.code,
                                  province=body.province, city=body.city, distributor_id=body.distributor_id)
    return {"id": str(region.id), "name": region.name, "code": region.code, "city": region.city}


@channel_router.get("/regions", summary="区域列表")
async def list_regions_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    regions, total = await list_regions(db, tenant_id, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[{"id": str(r.id), "name": r.name, "code": r.code, "province": r.province, "city": r.city,
                "distributor_id": str(r.distributor_id) if r.distributor_id else None} for r in regions],
        total=total, page=page, page_size=page_size,
    )


@channel_router.put("/regions/{region_id}", summary="更新区域")
async def update_region_endpoint(
    region_id: uuid.UUID,
    body: RegionUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    region = await update_region(db, region_id, name=body.name, distributor_id=body.distributor_id)
    if not region:
        raise HTTPException(404, "Region not found")
    return {"id": str(region.id), "name": region.name, "code": region.code}


# ── 门店 ──────────────────────────────────────────


@channel_router.post("/stores", status_code=201, summary="创建门店")
async def create_store_endpoint(
    body: StoreCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    store = await create_store(db, tenant_id, body.name, body.code,
                                region_id=body.region_id, distributor_id=body.distributor_id, address=body.address)
    return {"id": str(store.id), "name": store.name, "code": store.code, "status": store.status}


@channel_router.get("/stores", summary="门店列表")
async def list_stores_endpoint(
    region_id: uuid.UUID | None = Query(None),
    distributor_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    stores, total = await list_stores(db, tenant_id, region_id=region_id, distributor_id=distributor_id,
                                       page=page, page_size=page_size)
    return PaginatedResponse(
        items=[{"id": str(s.id), "name": s.name, "code": s.code, "address": s.address,
                "region_id": str(s.region_id) if s.region_id else None,
                "distributor_id": str(s.distributor_id) if s.distributor_id else None,
                "status": s.status} for s in stores],
        total=total, page=page, page_size=page_size,
    )


@channel_router.put("/stores/{store_id}", summary="更新门店")
async def update_store_endpoint(
    store_id: uuid.UUID,
    body: StoreUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    store = await update_store(db, store_id, name=body.name, region_id=body.region_id,
                                distributor_id=body.distributor_id, address=body.address, status=body.status)
    if not store:
        raise HTTPException(404, "Store not found")
    return {"id": str(store.id), "name": store.name, "status": store.status}


@channel_router.delete("/stores/{store_id}", summary="删除门店")
async def delete_store_endpoint(
    store_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    ok = await delete_store(db, store_id)
    return {"success": ok}


# ── 码段分配 ──────────────────────────────────────


@channel_router.post("/code-batches/{batch_id}/assign", summary="批次分配给经销商/区域")
async def assign_batch_endpoint(
    batch_id: uuid.UUID,
    body: BatchAssign,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    result = await assign_batch_to_channel(db, tenant_id, batch_id,
                                            distributor_id=body.distributor_id, region_id=body.region_id)
    if not result:
        raise HTTPException(404, "Code batch not found")
    return result


@channel_router.post("/code-allocations", status_code=201, summary="门店级码段分配")
async def allocate_to_store_endpoint(
    body: StoreAllocation,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    alloc = await allocate_codes_to_store(db, tenant_id, body.batch_id, body.store_id, body.quantity)
    if not alloc:
        raise HTTPException(400, "Allocation failed: batch or store not found")
    return {
        "id": str(alloc.id), "batch_id": str(alloc.batch_id), "store_id": str(alloc.store_id),
        "quantity": alloc.quantity, "allocated_at": alloc.allocated_at,
    }


@channel_router.get("/code-allocations", summary="码段分配列表")
async def list_allocations_endpoint(
    batch_id: uuid.UUID | None = Query(None),
    store_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    allocs = await list_allocations(db, tenant_id, batch_id=batch_id, store_id=store_id)
    return [
        {"id": str(a.id), "batch_id": str(a.batch_id), "store_id": str(a.store_id),
         "distributor_id": str(a.distributor_id) if a.distributor_id else None,
         "quantity": a.quantity, "allocated_at": a.allocated_at}
        for a in allocs
    ]


@channel_router.get("/code-items/{public_id}/store", summary="扫码查门店归属")
async def resolve_store_endpoint(
    public_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await resolve_store_for_code(db, public_id)
    if not result:
        return {"matched": False}
    return {"matched": True, **result}


# ── 窜货线索 ──────────────────────────────────────


@channel_router.get("/diversion-clues", summary="窜货线索列表")
async def list_diversion_clues_endpoint(
    resolved: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    clues, total = await list_diversion_clues(db, tenant_id, resolved=resolved, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[{"id": str(c.id), "public_id": c.public_id, "expected_region": c.expected_region,
                "detected_city": c.detected_city, "resolved": c.resolved} for c in clues],
        total=total, page=page, page_size=page_size,
    )
