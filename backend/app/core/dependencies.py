import uuid

from fastapi import HTTPException, Request


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
