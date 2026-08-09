import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.models.tenant import Organization
from app.schemas.account import (
    AccountCreate,
    AccountCreateResponse,
    AccountRead,
    AccountStatusUpdate,
    AccountUpdate,
    OrganizationCreate,
    OrganizationRead,
    OrganizationUpdate,
)
from app.schemas.common import PaginatedResponse
from app.services.audit import write_audit_log
from app.services.organization import (
    count_accounts_by_org,
    create_account,
    create_organization,
    delete_organization,
    generate_initial_password,
    list_accounts,
    list_organization_tree,
    list_organizations,
    set_account_active_status,
    update_account,
    update_organization,
)
from app.utils.auth_rbac import require_role

router = APIRouter(prefix="/api/v1", tags=["organizations", "accounts"])


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------


@router.post("/organizations", response_model=OrganizationRead, status_code=201, summary="创建 org")
async def create_org_endpoint(
    body: OrganizationCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    try:
        org = await create_organization(db, tenant_id=tenant_id, name=body.name, parent_id=body.parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "organization_created",
        f"organization:{org.id}",
        {"resource_name": org.name, "result": "success"},
    )
    return {"id": org.id, "tenant_id": org.tenant_id, "name": org.name, "parent_id": org.parent_id, "account_count": 0}


@router.get("/organizations", response_model=PaginatedResponse, summary="组织列表（分页）")
async def list_orgs_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, description="搜索关键词"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
):
    result = await list_organizations(db, tenant_id=tenant_id, page=page, page_size=page_size, q=q)
    account_counts = await count_accounts_by_org(db, tenant_id)
    items = [
        {
            "id": org.id,
            "tenant_id": org.tenant_id,
            "name": org.name,
            "parent_id": org.parent_id,
            "account_count": account_counts.get(org.id, 0),
        }
        for org in result["items"]
    ]
    return PaginatedResponse(items=items, total=result["total"], page=page, page_size=page_size)


@router.get("/organizations/tree", response_model=list[OrganizationRead], summary="完整组织树数据")
async def list_org_tree_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
):
    """返回当前租户全部组织节点，供层级展示与组织选择使用。"""
    organizations = await list_organization_tree(db, tenant_id=tenant_id)
    account_counts = await count_accounts_by_org(db, tenant_id)
    return [
        OrganizationRead(
            id=org.id,
            tenant_id=org.tenant_id,
            name=org.name,
            parent_id=org.parent_id,
            account_count=account_counts.get(org.id, 0),
            created_at=org.created_at,
        )
        for org in organizations
    ]


@router.patch("/organizations/{org_id}", response_model=OrganizationRead, summary="更新组织")
async def update_org_endpoint(
    org_id: uuid.UUID,
    body: OrganizationUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    updates = body.model_dump(exclude_unset=True)
    name = updates.get("name")
    parent_id = updates.get("parent_id")
    parent_id_provided = "parent_id" in updates

    try:
        org = await update_organization(
            db,
            tenant_id=tenant_id,
            org_id=org_id,
            name=name,
            parent_id=parent_id,
            parent_id_provided=parent_id_provided,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "organization_updated",
        f"organization:{org.id}",
        {"resource_name": org.name, "changed_fields": sorted(updates), "result": "success"},
    )

    account_counts = await count_accounts_by_org(db, tenant_id)
    return {
        "id": org.id,
        "tenant_id": org.tenant_id,
        "name": org.name,
        "parent_id": org.parent_id,
        "account_count": account_counts.get(org.id, 0),
    }


@router.delete("/organizations/{org_id}", status_code=204, summary="删除组织")
async def delete_org_endpoint(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    org = (
        await db.execute(select(Organization).where(Organization.id == org_id, Organization.tenant_id == tenant_id))
    ).scalar_one_or_none()
    try:
        await delete_organization(db, tenant_id=tenant_id, org_id=org_id)
    except ValueError as e:
        if "not found" in str(e):
            raise HTTPException(status_code=404, detail=str(e)) from e
        raise HTTPException(status_code=400, detail=str(e)) from e
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "organization_deleted",
        f"organization:{org_id}",
        {
            "resource_name": org.name if org else str(org_id),
            "before": "active",
            "after": "deleted",
            "result": "success",
        },
    )


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


@router.post(
    "/accounts",
    response_model=AccountCreateResponse,
    response_model_exclude_none=True,
    status_code=201,
    summary="创建 账号",
)
async def create_account_endpoint(
    body: AccountCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    initial_password = generate_initial_password() if not body.password else None
    try:
        account = await create_account(
            db=db,
            tenant_id=tenant_id,
            organization_id=body.organization_id,
            email=body.email,
            name=body.name,
            password=body.password or initial_password,
            role_ids=body.role_ids,
            must_change_password=initial_password is not None,
        )
    except ValueError as e:
        if "already exists" in str(e):
            raise HTTPException(status_code=409, detail=str(e)) from e
        raise HTTPException(status_code=400, detail=str(e)) from e

    org_name = (
        await db.execute(select(Organization.name).where(Organization.id == account.organization_id))
    ).scalar_one_or_none()
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "account_created",
        f"account:{account.id}",
        {"resource_name": account.name, "target_email": account.email, "result": "success"},
    )
    return {
        "id": account.id,
        "tenant_id": account.tenant_id,
        "organization_id": account.organization_id,
        "organization_name": org_name,
        "email": account.email,
        "name": account.name,
        "is_active": account.is_active,
        "must_change_password": account.must_change_password,
        "roles": [{"id": role.id, "name": role.name, "description": role.description} for role in account.roles],
        "initial_password": initial_password,
    }


@router.get("/accounts", response_model=PaginatedResponse, response_model_exclude_none=True, summary="账户列表（分页）")
async def list_accounts_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, description="搜索姓名或邮箱"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
):
    result = await list_accounts(db, tenant_id=tenant_id, page=page, page_size=page_size, q=q)
    org_names = {
        row[0]: row[1]
        for row in (
            await db.execute(select(Organization.id, Organization.name).where(Organization.tenant_id == tenant_id))
        ).all()
    }
    items = [
        {
            "id": account.id,
            "tenant_id": account.tenant_id,
            "organization_id": account.organization_id,
            "organization_name": org_names.get(account.organization_id),
            "email": account.email,
            "name": account.name,
            "is_active": account.is_active,
            "must_change_password": account.must_change_password,
            "roles": [{"id": role.id, "name": role.name, "description": role.description} for role in account.roles],
        }
        for account in result["items"]
    ]
    return PaginatedResponse(items=items, total=result["total"], page=page, page_size=page_size)


@router.patch("/accounts/{account_id}", response_model=AccountRead, summary="更新 账号")
async def update_account_endpoint(
    account_id: uuid.UUID,
    body: AccountUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    try:
        account = await update_account(
            db=db,
            tenant_id=tenant_id,
            account_id=account_id,
            actor_id=actor_id,
            name=body.name,
            organization_id=body.organization_id,
            role_ids=body.role_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "account_updated",
        f"account:{account.id}",
        {
            "resource_name": account.name,
            "changed_fields": sorted(body.model_dump(exclude_unset=True)),
            "result": "success",
        },
    )
    return account


@router.patch("/accounts/{account_id}/status", response_model=AccountRead, summary="启用或停用账户")
async def update_account_status_endpoint(
    account_id: uuid.UUID,
    body: AccountStatusUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    try:
        account = await set_account_active_status(
            db=db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            account_id=account_id,
            is_active=body.is_active,
            reason=body.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.delete("/accounts/{account_id}", status_code=204, summary="删除账户")
async def delete_account_endpoint(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    """停用账户并撤销现有会话；保留账户与审计历史。"""
    try:
        account = await set_account_active_status(
            db=db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            account_id=account_id,
            is_active=False,
            reason="账户删除操作",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
