import uuid

from fastapi import Depends, HTTPException, Request


async def get_current_tenant(request: Request) -> uuid.UUID:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context not found")
    return uuid.UUID(tenant_id)


async def get_current_account_id(request: Request) -> uuid.UUID:
    account_id = getattr(request.state, "account_id", None)
    if not account_id:
        raise HTTPException(status_code=401, detail="Account context not found")
    return uuid.UUID(account_id)


async def get_current_role(request: Request) -> str:
    role = getattr(request.state, "role", None)
    if not role:
        raise HTTPException(status_code=401, detail="Role context not found")
    return role


async def get_current_tenant_type(request: Request) -> str:
    return getattr(request.state, "tenant_type", "brand")


ALLOWED_OPS_ROLES = {"platform_admin", "operator"}
ALLOWED_OPS_TENANT_TYPES = {"agency", "platform"}


async def get_ops_user(
    request: Request,
    account_id: uuid.UUID = Depends(get_current_account_id),
    role: str = Depends(get_current_role),
    tenant_type: str = Depends(get_current_tenant_type),
) -> tuple[uuid.UUID, str, uuid.UUID | None, str]:
    """Verify the current user is authenticated and has ops workbench access.

    Returns (account_id, role, tenant_id, tenant_type).
    """
    if role not in ALLOWED_OPS_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"Role '{role}' is not allowed to access operations workbench",
        )
    if tenant_type not in ALLOWED_OPS_TENANT_TYPES:
        raise HTTPException(
            status_code=403,
            detail="Tenant type not allowed to access operations workbench",
        )
    tenant_id_str = getattr(request.state, "tenant_id", None)
    tenant_id = uuid.UUID(tenant_id_str) if tenant_id_str else None
    return account_id, role, tenant_id, tenant_type
