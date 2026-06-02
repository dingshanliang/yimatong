"""角色与权限管理 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.models.tenant import Permission, Role, account_roles, role_permissions

router = APIRouter(prefix="/api/v1/roles", tags=["roles"])


class RoleCreateRequest(BaseModel):
    name: str = Field(..., max_length=50)
    description: str | None = Field(None, max_length=255)


class PermissionCreateRequest(BaseModel):
    code: str = Field(..., max_length=100)
    description: str | None = Field(None, max_length=255)


@router.get("")
async def list_roles(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    result = await db.execute(select(Role).where(Role.tenant_id == tenant_id))
    roles = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "description": r.description,
            "permissions": [{"id": str(p.id), "code": p.code, "description": p.description} for p in r.permissions],
        }
        for r in roles
    ]


@router.post("", status_code=201)
async def create_role(
    body: RoleCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    existing = await db.execute(select(Role).where(Role.tenant_id == tenant_id, Role.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Role name already exists")

    role = Role(tenant_id=tenant_id, name=body.name, description=body.description)
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return {
        "id": str(role.id),
        "name": role.name,
        "description": role.description,
        "permissions": [],
    }


@router.get("/permissions")
async def list_permissions(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    result = await db.execute(select(Permission).where(Permission.tenant_id == tenant_id))
    perms = result.scalars().all()
    return [{"id": str(p.id), "code": p.code, "description": p.description} for p in perms]


@router.post("/permissions", status_code=201)
async def create_permission(
    body: PermissionCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    existing = await db.execute(
        select(Permission).where(Permission.tenant_id == tenant_id, Permission.code == body.code)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Permission code already exists")

    perm = Permission(tenant_id=tenant_id, code=body.code, description=body.description)
    db.add(perm)
    await db.commit()
    await db.refresh(perm)
    return {"id": str(perm.id), "code": perm.code, "description": perm.description}


@router.post("/{role_id}/permissions/{permission_id}")
async def assign_permission(
    role_id: uuid.UUID,
    permission_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    role = await db.get(Role, role_id)
    if not role or role.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Role not found")
    perm = await db.get(Permission, permission_id)
    if not perm or perm.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Permission not found")

    try:
        await db.execute(role_permissions.insert().values(role_id=role_id, permission_id=permission_id))
        await db.commit()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Permission already assigned to this role")
    return {"ok": True}


@router.delete("/{role_id}")
async def delete_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    role = await db.get(Role, role_id)
    if not role or role.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Role not found")

    # Check if any accounts are using this role
    result = await db.execute(
        select(func.count()).select_from(account_roles).where(account_roles.c.role_id == role_id)
    )
    count = result.scalar() or 0
    if count > 0:
        raise HTTPException(status_code=409, detail=f"该角色正在被 {count} 个账户使用，请先解除关联后再删除")

    await db.delete(role)
    await db.commit()
    return {"ok": True}
