import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.tenant import TenantCreate, TenantRead, TenantUpdate
from app.services.tenant import create_tenant, get_tenant, soft_delete_tenant, update_tenant

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


@router.post("", response_model=TenantRead, status_code=201)
async def create_tenant_endpoint(body: TenantCreate, db: AsyncSession = Depends(get_db)):
    tenant = await create_tenant(
        db=db,
        name=body.name,
        slug=body.slug,
        plan=body.plan,
        admin_email=body.admin_email,
        admin_name=body.admin_name,
        admin_password=body.admin_password,
    )
    return tenant


@router.get("/me", response_model=TenantRead)
async def get_current_tenant_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.patch("/me", response_model=TenantRead)
async def update_current_tenant_endpoint(
    body: TenantUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    tenant = await update_tenant(
        db, tenant_id,
        name=body.name,
        quota=body.quota,
        compliance_settings=body.compliance_settings,
    )
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.get("/{tenant_id}", response_model=TenantRead)
async def get_tenant_endpoint(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.patch("/{tenant_id}", response_model=TenantRead)
async def update_tenant_endpoint(tenant_id: uuid.UUID, body: TenantUpdate, db: AsyncSession = Depends(get_db)):
    tenant = await update_tenant(
        db, tenant_id,
        name=body.name,
        quota=body.quota,
        compliance_settings=body.compliance_settings,
    )
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.delete("/{tenant_id}", status_code=204)
async def delete_tenant_endpoint(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    deleted = await soft_delete_tenant(db, tenant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Tenant not found")
