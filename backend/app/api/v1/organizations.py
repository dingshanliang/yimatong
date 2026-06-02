import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.models.tenant import Organization
from app.schemas.account import AccountCreate, AccountRead, AccountUpdate, OrganizationCreate, OrganizationRead
from app.services.organization import (
    count_accounts_by_org,
    create_account,
    create_organization,
    generate_initial_password,
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
    return {"id": org.id, "tenant_id": org.tenant_id, "name": org.name, "parent_id": org.parent_id, "account_count": 0}


@router.get("/organizations", response_model=list[OrganizationRead], summary="orgs 列表")
async def list_orgs_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    orgs = await list_organizations(db, tenant_id=tenant_id)
    account_counts = await count_accounts_by_org(db, tenant_id)
    return [
        {
            "id": org.id,
            "tenant_id": org.tenant_id,
            "name": org.name,
            "parent_id": org.parent_id,
            "account_count": account_counts.get(org.id, 0),
        }
        for org in orgs
    ]


@router.post(
    "/accounts",
    response_model=AccountRead,
    response_model_exclude_none=True,
    status_code=201,
    summary="创建 账号",
)
async def create_account_endpoint(
    body: AccountCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    initial_password = generate_initial_password() if not body.password else None
    account = await create_account(
        db=db,
        tenant_id=tenant_id,
        organization_id=body.organization_id,
        email=body.email,
        name=body.name,
        password=body.password or initial_password,
        role_ids=body.role_ids,
    )
    org_name = (
        await db.execute(select(Organization.name).where(Organization.id == account.organization_id))
    ).scalar_one_or_none()
    return {
        "id": account.id,
        "tenant_id": account.tenant_id,
        "organization_id": account.organization_id,
        "organization_name": org_name,
        "email": account.email,
        "name": account.name,
        "initial_password": initial_password,
    }


@router.get("/accounts", response_model=list[AccountRead], response_model_exclude_none=True, summary="账号 列表")
async def list_accounts_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    accounts = await list_accounts(db, tenant_id=tenant_id)
    org_names = {
        row[0]: row[1]
        for row in (
            await db.execute(select(Organization.id, Organization.name).where(Organization.tenant_id == tenant_id))
        ).all()
    }
    return [
        {
            "id": account.id,
            "tenant_id": account.tenant_id,
            "organization_id": account.organization_id,
            "organization_name": org_names.get(account.organization_id),
            "email": account.email,
            "name": account.name,
        }
        for account in accounts
    ]


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
