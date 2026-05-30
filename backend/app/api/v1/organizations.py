import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.account import AccountCreate, AccountRead, AccountUpdate, OrganizationCreate, OrganizationRead
from app.services.organization import (
    create_account,
    create_organization,
    list_accounts,
    list_organizations,
    update_account,
)

router = APIRouter(prefix="/api/v1", tags=["organizations", "accounts"])


@router.post("/organizations", response_model=OrganizationRead, status_code=201, summary="创建 org")
async def create_org_endpoint(
    body: OrganizationCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    org = await create_organization(db, tenant_id=tenant_id, name=body.name, parent_id=body.parent_id)
    return org


@router.get("/organizations", response_model=list[OrganizationRead], summary="orgs 列表")
async def list_orgs_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await list_organizations(db, tenant_id=tenant_id)


@router.post("/accounts", response_model=AccountRead, status_code=201, summary="创建 账号")
async def create_account_endpoint(
    body: AccountCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    account = await create_account(
        db=db,
        tenant_id=tenant_id,
        organization_id=body.organization_id,
        email=body.email,
        name=body.name,
        password=body.password,
        role_ids=body.role_ids,
    )
    return account


@router.get("/accounts", response_model=list[AccountRead], summary="账号 列表")
async def list_accounts_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await list_accounts(db, tenant_id=tenant_id)


@router.patch("/accounts/{account_id}", response_model=AccountRead, summary="更新 账号")
async def update_account_endpoint(
    account_id: uuid.UUID,
    body: AccountUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    account = await update_account(
        db=db,
        tenant_id=tenant_id,
        account_id=account_id,
        name=body.name,
        role_ids=body.role_ids,
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account
