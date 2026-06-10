import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.models.tenant import Tenant
from app.schemas.common import NOT_FOUND_EXAMPLE, ErrorDetail, PaginatedResponse
from app.schemas.tenant import CategoriesResponse, TenantCreate, TenantRead, TenantUpdate, TenantUpdateSelf
from app.services.tenant import (
    complete_onboarding_step,
    create_tenant,
    get_onboarding_progress_data,
    get_tenant,
    soft_delete_tenant,
    update_tenant,
)
from app.utils import escape_like_pattern
from app.utils.auth_rbac import require_role

TENANT_NOT_FOUND = {
    404: {
        "model": ErrorDetail,
        "description": "租户不存在",
        "content": {"application/json": {"example": NOT_FOUND_EXAMPLE}},
    },
}

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


# ---------------------------------------------------------------------------
# Platform admin only endpoints
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=TenantRead,
    status_code=201,
    summary="创建租户",
    response_description="租户创建成功",
)
async def create_tenant_endpoint(
    body: TenantCreate,
    db: AsyncSession = Depends(get_db),
    _role: str = Depends(require_role("platform_admin")),
):
    try:
        tenant = await create_tenant(
            db=db,
            name=body.name,
            slug=body.slug,
            plan=body.plan,
            admin_email=body.admin_email,
            admin_name=body.admin_name,
            admin_password=body.admin_password,
            industry=body.industry,
            notes=body.notes,
            template_id=body.template_id,
            tenant_type=body.tenant_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return tenant


@router.get(
    "",
    response_model=PaginatedResponse,
    summary="租户列表",
    response_description="分页返回租户列表",
)
async def list_tenants_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, description="搜索关键词"),
    plan: str | None = Query(None, description="按订阅计划过滤"),
    tenant_type: str | None = Query(None, description="按租户类型过滤"),
    status: str | None = Query(None, description="按状态过滤"),
    db: AsyncSession = Depends(get_db),
    _role: str = Depends(require_role("platform_admin")),
):
    """租户列表（支持分页、搜索和多维过滤）"""
    query = select(Tenant).where(Tenant.status != "terminated")
    count_query = select(func.count()).select_from(Tenant).where(Tenant.status != "terminated")

    if q:
        escaped = escape_like_pattern(q)
        query = query.where(Tenant.name.ilike(f"%{escaped}%", escape="\\"))
        count_query = count_query.where(Tenant.name.ilike(f"%{escaped}%", escape="\\"))

    if plan:
        query = query.where(Tenant.plan == plan)
        count_query = count_query.where(Tenant.plan == plan)

    if tenant_type:
        query = query.where(Tenant.tenant_type == tenant_type)
        count_query = count_query.where(Tenant.tenant_type == tenant_type)

    if status:
        query = query.where(Tenant.status == status)
        count_query = count_query.where(Tenant.status == status)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    return PaginatedResponse(
        items=[TenantRead.model_validate(t) for t in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/me", response_model=TenantRead, summary="获取当前租户")
async def get_current_tenant_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.get("/me/categories", response_model=CategoriesResponse, summary="获取当前租户品类列表")
async def get_current_tenant_categories(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return CategoriesResponse(categories=tenant.categories or [])


@router.patch("/me", response_model=TenantRead, summary="更新当前租户")
async def update_current_tenant_endpoint(
    body: TenantUpdateSelf,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    tenant = await update_tenant(
        db,
        tenant_id,
        name=body.name,
        industry=body.industry,
        notes=body.notes,
        onboarding_progress=body.onboarding_progress,
        enabled_features=body.enabled_features,
        categories=body.categories,
    )
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


# ---------------------------------------------------------------------------
# Onboarding向导
# ---------------------------------------------------------------------------


@router.get("/me/onboarding", summary="获取初始化向导进度")
async def get_onboarding_progress(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return get_onboarding_progress_data(tenant)


@router.post("/me/onboarding/step/{step}", summary="完成初始化向导某一步")
async def complete_onboarding_step_endpoint(
    step: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        tenant = await complete_onboarding_step(db, tenant_id, step)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"step": step, "completed": True}


# ---------------------------------------------------------------------------
# Platform admin: single-tenant CRUD (MUST be after /me routes)
# ---------------------------------------------------------------------------


@router.get("/{tenant_id}", response_model=TenantRead, summary="获取指定租户")
async def get_tenant_endpoint(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _role: str = Depends(require_role("platform_admin")),
):
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.patch("/{tenant_id}", response_model=TenantRead, summary="更新指定租户")
async def update_tenant_endpoint(
    tenant_id: uuid.UUID,
    body: TenantUpdate,
    db: AsyncSession = Depends(get_db),
    _role: str = Depends(require_role("platform_admin")),
):
    tenant = await update_tenant(
        db,
        tenant_id,
        name=body.name,
        industry=body.industry,
        notes=body.notes,
        quota=body.quota,
        compliance_settings=body.compliance_settings,
        plan_expires_at=body.plan_expires_at,
        onboarding_progress=body.onboarding_progress,
        enabled_features=body.enabled_features,
        tenant_type=body.tenant_type,
        categories=body.categories,
    )
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.delete("/{tenant_id}", status_code=204, summary="删除租户")
async def delete_tenant_endpoint(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _role: str = Depends(require_role("platform_admin")),
):
    deleted = await soft_delete_tenant(db, tenant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Tenant not found")
