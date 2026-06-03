"""渠道管理 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.channel import (
    allocate_codes_to_store,
    allocation_to_dict,
    assign_batch_to_channel,
    create_account_scope,
    create_distributor,
    create_region,
    create_store,
    delete_account_scope,
    delete_store,
    diversion_clue_to_dict,
    get_channel_overview,
    get_distributor_portal_summary,
    get_distributor_stats,
    get_region_stats,
    get_store_portal_summary,
    get_store_stats,
    list_account_scopes,
    list_allocations,
    list_distributors,
    list_diversion_clues,
    list_regions,
    list_stores,
    region_coverage_label,
    resolve_store_for_code,
    update_distributor,
    update_region,
    update_store,
)
from app.utils.crypto import CryptoError, decrypt_phone, mask_phone

channel_router = APIRouter(prefix="/api/v1/channels", tags=["channels"])


async def require_store_enabled(
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
) -> None:
    """检查租户是否启用了门店模块"""
    from app.services.tenant import get_tenant

    tenant = await get_tenant(db, tenant_id)
    enabled = tenant.enabled_features or {}
    if not enabled.get("channel_store", False):
        raise HTTPException(status_code=403, detail="门店模块未启用")


class DistributorCreate(BaseModel):
    name: str
    code: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    status: str = "active"


class DistributorUpdate(BaseModel):
    name: str | None = None
    status: str | None = None


class RegionCoverageArea(BaseModel):
    province: str | None = None
    city: str | None = None


class RegionCreate(BaseModel):
    name: str
    code: str | None = None
    province: str | None = None
    city: str | None = None
    coverage_type: str | None = Field("city", pattern="^(city|province|multi_province)$")
    coverage_areas: list[RegionCoverageArea] | None = None
    distributor_id: uuid.UUID | None = None
    status: str = "active"


class RegionUpdate(BaseModel):
    name: str | None = None
    province: str | None = None
    city: str | None = None
    coverage_type: str | None = Field(None, pattern="^(city|province|multi_province)$")
    coverage_areas: list[RegionCoverageArea] | None = None
    distributor_id: uuid.UUID | None = None
    status: str | None = None


class StoreCreate(BaseModel):
    name: str
    code: str | None = None
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
    target_type: str | None = Field(None, pattern="^(distributor|region|store)$")
    distributor_id: uuid.UUID | None = None
    region_id: uuid.UUID | None = None
    store_id: uuid.UUID | None = None
    quantity: int = Field(..., gt=0)


class AccountScopeCreate(BaseModel):
    account_id: uuid.UUID
    scope_type: str = Field(..., pattern="^(distributor|region|store)$")
    distributor_id: uuid.UUID | None = None
    region_id: uuid.UUID | None = None
    store_id: uuid.UUID | None = None


def _distributor_item(dist, stats: dict | None = None) -> dict:
    stats = stats or {}
    contact_phone_masked = None
    if dist.contact_phone_encrypted:
        try:
            contact_phone_masked = mask_phone(decrypt_phone(dist.contact_phone_encrypted))
        except CryptoError:
            contact_phone_masked = None
    return {
        "id": str(dist.id),
        "name": dist.name,
        "code": dist.code,
        "contact_name": dist.contact_name,
        "contact_phone_masked": contact_phone_masked,
        "status": dist.status,
        "region_count": stats.get("region_count", 0),
        "store_count": stats.get("store_count", 0),
        "allocated_quantity": stats.get("allocated_quantity", 0),
        "updated_at": dist.updated_at.isoformat() if dist.updated_at else None,
    }


def _region_item(region, stats: dict | None = None) -> dict:
    stats = stats or {}
    return {
        "id": str(region.id),
        "name": region.name,
        "code": region.code,
        "province": region.province,
        "city": region.city,
        "coverage_type": region.coverage_type,
        "coverage_areas": region.coverage_areas or [],
        "coverage_label": region_coverage_label(region),
        "status": region.status,
        "distributor_id": str(region.distributor_id) if region.distributor_id else None,
        "distributor_name": stats.get("distributor_name"),
        "store_count": stats.get("store_count", 0),
        "allocated_quantity": stats.get("allocated_quantity", 0),
        "updated_at": region.updated_at.isoformat() if region.updated_at else None,
    }


def _store_item(store, stats: dict | None = None) -> dict:
    stats = stats or {}
    return {
        "id": str(store.id),
        "name": store.name,
        "code": store.code,
        "address": store.address,
        "region_id": str(store.region_id) if store.region_id else None,
        "region_name": stats.get("region_name"),
        "distributor_id": str(store.distributor_id) if store.distributor_id else None,
        "distributor_name": stats.get("distributor_name"),
        "allocated_quantity": stats.get("allocated_quantity", 0),
        "status": store.status,
        "updated_at": store.updated_at.isoformat() if store.updated_at else None,
    }


def _scope_item(scope) -> dict:
    return {
        "id": str(scope.id),
        "account_id": str(scope.account_id),
        "scope_type": scope.scope_type,
        "distributor_id": str(scope.distributor_id) if scope.distributor_id else None,
        "region_id": str(scope.region_id) if scope.region_id else None,
        "store_id": str(scope.store_id) if scope.store_id else None,
        "updated_at": scope.updated_at.isoformat() if scope.updated_at else None,
    }


def _diversion_item(clue) -> dict:
    return {
        "id": str(clue.id),
        "public_id": clue.public_id,
        "expected_region": clue.expected_region,
        "detected_city": clue.detected_city,
        "distributor_id": str(clue.distributor_id) if clue.distributor_id else None,
        "region_id": str(clue.region_id) if clue.region_id else None,
        "resolved": clue.resolved,
        "resolution_note": clue.resolution_note,
        "resolved_by_account_id": str(clue.resolved_by_account_id) if clue.resolved_by_account_id else None,
        "resolved_at": clue.resolved_at.isoformat() if clue.resolved_at else None,
    }


@channel_router.get("/overview", summary="渠道管理概览")
async def channel_overview_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_channel_overview(db, tenant_id)


# ── 经销商 ────────────────────────────────────────


@channel_router.post("/distributors", status_code=201, summary="创建经销商")
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
        status=body.status,
    )
    return _distributor_item(dist)


@channel_router.get("/distributors", summary="经销商列表")
async def list_distributors_endpoint(
    q: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    dists, total = await list_distributors(db, tenant_id, page=page, page_size=page_size, q=q, status=status)
    stats = await get_distributor_stats(db, tenant_id, [d.id for d in dists])
    return PaginatedResponse(
        items=[_distributor_item(d, stats.get(d.id)) for d in dists],
        total=total,
        page=page,
        page_size=page_size,
    )


@channel_router.put("/distributors/{distributor_id}", summary="更新经销商")
@channel_router.patch("/distributors/{distributor_id}", summary="局部更新经销商")
async def update_distributor_endpoint(
    distributor_id: uuid.UUID,
    body: DistributorUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    dist = await update_distributor(db, tenant_id, distributor_id, name=body.name, status=body.status)
    if not dist:
        raise HTTPException(404, "Distributor not found")
    stats = await get_distributor_stats(db, tenant_id, [dist.id])
    return _distributor_item(dist, stats.get(dist.id))


# ── 区域 ──────────────────────────────────────────


@channel_router.post("/regions", status_code=201, summary="创建区域")
async def create_region_endpoint(
    body: RegionCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        region = await create_region(
            db,
            tenant_id,
            body.name,
            body.code,
            province=body.province,
            city=body.city,
            coverage_type=body.coverage_type,
            coverage_areas=[area.model_dump() for area in body.coverage_areas] if body.coverage_areas else None,
            distributor_id=body.distributor_id,
            status=body.status,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    stats = await get_region_stats(db, tenant_id, [region])
    return _region_item(region, stats.get(region.id))


@channel_router.get("/regions", summary="区域列表")
async def list_regions_endpoint(
    q: str | None = Query(None),
    status: str | None = Query(None),
    distributor_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    regions, total = await list_regions(
        db,
        tenant_id,
        page=page,
        page_size=page_size,
        q=q,
        status=status,
        distributor_id=distributor_id,
    )
    stats = await get_region_stats(db, tenant_id, regions)
    return PaginatedResponse(
        items=[_region_item(r, stats.get(r.id)) for r in regions],
        total=total,
        page=page,
        page_size=page_size,
    )


@channel_router.put("/regions/{region_id}", summary="更新区域")
@channel_router.patch("/regions/{region_id}", summary="局部更新区域")
async def update_region_endpoint(
    region_id: uuid.UUID,
    body: RegionUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        region = await update_region(
            db,
            tenant_id,
            region_id,
            name=body.name,
            province=body.province,
            city=body.city,
            coverage_type=body.coverage_type,
            coverage_areas=[area.model_dump() for area in body.coverage_areas] if body.coverage_areas else None,
            distributor_id=body.distributor_id,
            status=body.status,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not region:
        raise HTTPException(404, "Region not found")
    stats = await get_region_stats(db, tenant_id, [region])
    return _region_item(region, stats.get(region.id))


# ── 门店 ──────────────────────────────────────────


@channel_router.post("/stores", status_code=201, summary="创建门店")
async def create_store_endpoint(
    body: StoreCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _store_check: None = Depends(require_store_enabled),
):
    try:
        store = await create_store(
            db,
            tenant_id,
            body.name,
            body.code,
            region_id=body.region_id,
            distributor_id=body.distributor_id,
            address=body.address,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    stats = await get_store_stats(db, tenant_id, [store])
    return _store_item(store, stats.get(store.id))


@channel_router.get("/stores", summary="门店列表")
async def list_stores_endpoint(
    q: str | None = Query(None),
    status: str | None = Query("active"),
    region_id: uuid.UUID | None = Query(None),
    distributor_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _store_check: None = Depends(require_store_enabled),
):
    stores, total = await list_stores(
        db,
        tenant_id,
        region_id=region_id,
        distributor_id=distributor_id,
        page=page,
        page_size=page_size,
        q=q,
        status=status,
    )
    stats = await get_store_stats(db, tenant_id, stores)
    return PaginatedResponse(
        items=[_store_item(s, stats.get(s.id)) for s in stores],
        total=total,
        page=page,
        page_size=page_size,
    )


@channel_router.put("/stores/{store_id}", summary="更新门店")
@channel_router.patch("/stores/{store_id}", summary="局部更新门店")
async def update_store_endpoint(
    store_id: uuid.UUID,
    body: StoreUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _store_check: None = Depends(require_store_enabled),
):
    try:
        store = await update_store(
            db,
            tenant_id,
            store_id,
            name=body.name,
            region_id=body.region_id,
            distributor_id=body.distributor_id,
            address=body.address,
            status=body.status,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not store:
        raise HTTPException(404, "Store not found")
    stats = await get_store_stats(db, tenant_id, [store])
    return _store_item(store, stats.get(store.id))


@channel_router.delete("/stores/{store_id}", summary="删除门店")
async def delete_store_endpoint(
    store_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _store_check: None = Depends(require_store_enabled),
):
    ok = await delete_store(db, tenant_id, store_id)
    return {"success": ok}


# ── 渠道流向登记 ──────────────────────────────────────


@channel_router.post("/code-batches/{batch_id}/assign", summary="批次流向登记到经销商/区域")
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
        raise HTTPException(404, "Code batch not found")
    return result


@channel_router.post("/code-allocations", status_code=201, summary="已赋码货品流向登记")
async def allocate_to_store_endpoint(
    body: StoreAllocation,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        target_type = body.target_type or ("store" if body.store_id else "region" if body.region_id else "distributor")
        alloc = await allocate_codes_to_store(
            db,
            tenant_id,
            body.batch_id,
            body.store_id if target_type == "store" else None,
            body.quantity,
            distributor_id=body.distributor_id if target_type == "distributor" else None,
            region_id=body.region_id if target_type == "region" else None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not alloc:
        raise HTTPException(400, "Allocation failed: batch or store not found")
    return await allocation_to_dict(db, tenant_id, alloc)


@channel_router.get("/code-allocations", summary="渠道流向登记列表")
async def list_allocations_endpoint(
    batch_id: uuid.UUID | None = Query(None),
    store_id: uuid.UUID | None = Query(None),
    region_id: uuid.UUID | None = Query(None),
    distributor_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    allocs, total = await list_allocations(
        db,
        tenant_id,
        batch_id=batch_id,
        store_id=store_id,
        region_id=region_id,
        distributor_id=distributor_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[await allocation_to_dict(db, tenant_id, alloc) for alloc in allocs],
        total=total,
        page=page,
        page_size=page_size,
    )


@channel_router.get("/code-items/{public_id}/store", summary="扫码查门店归属")
async def resolve_store_endpoint(
    public_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await resolve_store_for_code(db, public_id)
    if not result:
        return {"matched": False}
    return {"matched": True, **result}


# ── 账号范围和渠道入口 ─────────────────────────────


@channel_router.post("/account-scopes", status_code=201, summary="绑定账号渠道范围")
async def create_account_scope_endpoint(
    body: AccountScopeCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        scope = await create_account_scope(
            db,
            tenant_id,
            body.account_id,
            body.scope_type,
            distributor_id=body.distributor_id,
            region_id=body.region_id,
            store_id=body.store_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _scope_item(scope)


@channel_router.get("/account-scopes", summary="账号渠道范围列表")
async def list_account_scopes_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    scopes = await list_account_scopes(db, tenant_id)
    return [_scope_item(scope) for scope in scopes]


@channel_router.delete("/account-scopes/{scope_id}", summary="删除账号渠道范围")
async def delete_account_scope_endpoint(
    scope_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return {"success": await delete_account_scope(db, tenant_id, scope_id)}


@channel_router.get("/portal/distributor/summary", summary="经销商入口概览")
async def distributor_portal_summary_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    summary = await get_distributor_portal_summary(db, tenant_id, account_id)
    if not summary:
        raise HTTPException(404, "Distributor scope not found")
    return summary


@channel_router.get("/portal/store/summary", summary="门店入口概览")
async def store_portal_summary_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _store_check: None = Depends(require_store_enabled),
):
    summary = await get_store_portal_summary(db, tenant_id, account_id)
    if not summary:
        raise HTTPException(404, "Store scope not found")
    return summary


# ── 窜货线索 ──────────────────────────────────────


@channel_router.get("/diversion-clues", summary="窜货线索列表")
async def list_diversion_clues_endpoint(
    resolved: bool | None = Query(None),
    severity: str | None = Query(None, pattern="^(high|medium|low)$"),
    distributor_id: uuid.UUID | None = Query(None),
    region_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    clues, total = await list_diversion_clues(
        db,
        tenant_id,
        resolved=resolved,
        severity=severity,
        distributor_id=distributor_id,
        region_id=region_id,
        q=q,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[await diversion_clue_to_dict(db, tenant_id, c) for c in clues],
        total=total,
        page=page,
        page_size=page_size,
    )
