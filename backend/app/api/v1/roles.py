"""角色与权限管理 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.models.tenant import Permission, Role, account_roles, role_permissions
from app.schemas.role import PermissionCreate, PermissionRead, RoleCreate, RoleRead, RoleUpdate
from app.utils.auth_rbac import require_role

router = APIRouter(prefix="/api/v1/roles", tags=["roles"])


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


@router.get("", response_model=list[RoleRead], summary="角色列表")
async def list_roles(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
):
    result = await db.execute(select(Role).where(Role.tenant_id == tenant_id))
    return result.scalars().all()


@router.post("", response_model=RoleRead, status_code=201, summary="创建角色")
async def create_role(
    body: RoleCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),
):
    existing = await db.execute(select(Role).where(Role.tenant_id == tenant_id, Role.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="角色名称已存在")

    role = Role(tenant_id=tenant_id, name=body.name, description=body.description)
    db.add(role)
    await db.flush()
    await db.refresh(role)
    return role


@router.patch("/{role_id}", response_model=RoleRead, summary="更新角色")
async def update_role(
    role_id: uuid.UUID,
    body: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),
):
    result = await db.execute(select(Role).where(Role.id == role_id, Role.tenant_id == tenant_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    updates = body.model_dump(exclude_unset=True)
    if "name" in updates:
        # Check name uniqueness
        if updates["name"] != role.name:
            dup = await db.execute(select(Role).where(Role.tenant_id == tenant_id, Role.name == updates["name"]))
            if dup.scalar_one_or_none():
                raise HTTPException(status_code=409, detail="角色名称已存在")
        role.name = updates["name"]
    if "description" in updates:
        role.description = updates["description"]

    await db.flush()
    await db.refresh(role)
    return role


@router.delete("/{role_id}", status_code=204, summary="删除角色")
async def delete_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),
):
    result = await db.execute(select(Role).where(Role.id == role_id, Role.tenant_id == tenant_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    # Check if any accounts are using this role
    count_result = await db.execute(
        select(func.count()).select_from(account_roles).where(account_roles.c.role_id == role_id)
    )
    count = count_result.scalar() or 0
    if count > 0:
        raise HTTPException(status_code=409, detail=f"该角色正在被 {count} 个账户使用，请先解除关联后再删除")

    await db.delete(role)
    await db.flush()


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


@router.get("/permissions", response_model=list[PermissionRead], summary="权限列表")
async def list_permissions(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
):
    result = await db.execute(select(Permission).where(Permission.tenant_id == tenant_id))
    return result.scalars().all()


@router.post("/permissions", response_model=PermissionRead, status_code=201, summary="创建权限")
async def create_permission(
    body: PermissionCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),
):
    existing = await db.execute(
        select(Permission).where(Permission.tenant_id == tenant_id, Permission.code == body.code)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="权限代码已存在")

    perm = Permission(tenant_id=tenant_id, code=body.code, description=body.description)
    db.add(perm)
    await db.flush()
    await db.refresh(perm)
    return perm


# ---------------------------------------------------------------------------
# Role-Permission assignment
# ---------------------------------------------------------------------------


@router.post("/{role_id}/permissions/{permission_id}", status_code=200, summary="分配权限给角色")
async def assign_permission(
    role_id: uuid.UUID,
    permission_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),
):
    role_result = await db.execute(select(Role).where(Role.id == role_id, Role.tenant_id == tenant_id))
    if not role_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="角色不存在")
    perm_result = await db.execute(
        select(Permission).where(Permission.id == permission_id, Permission.tenant_id == tenant_id)
    )
    if not perm_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="权限不存在")

    try:
        await db.execute(role_permissions.insert().values(role_id=role_id, permission_id=permission_id))
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="该角色已拥有此权限")
    return {"ok": True}


@router.delete("/{role_id}/permissions/{permission_id}", status_code=204, summary="解除角色权限")
async def unassign_permission(
    role_id: uuid.UUID,
    permission_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin")),
):
    # Validate role belongs to tenant
    role_result = await db.execute(select(Role).where(Role.id == role_id, Role.tenant_id == tenant_id))
    if not role_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="角色不存在")

    await db.execute(
        role_permissions.delete().where(
            role_permissions.c.role_id == role_id,
            role_permissions.c.permission_id == permission_id,
        )
    )
    await db.flush()
