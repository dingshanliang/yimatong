"""Authentication dependencies shared by channel route modules."""

from fastapi import Depends, HTTPException, Request

from app.utils.auth_rbac import require_durable_session, require_permission


async def require_brand_channel_principal(request: Request) -> None:
    """Keep tenant Admin channel surfaces closed to API keys and acting agencies."""

    await require_durable_session(request)
    if (
        getattr(request.state, "tenant_type", None) != "brand"
        or getattr(request.state, "auth_method", None) != "jwt"
        or getattr(request.state, "acting_tenant_id", None)
    ):
        raise HTTPException(status_code=403, detail="Brand channel access required")


def channel_dependencies(permission: str) -> list:
    return [Depends(require_brand_channel_principal), Depends(require_permission(permission))]


async def require_distributor_portal_principal(request: Request) -> None:
    await require_durable_session(request)
    if (
        getattr(request.state, "tenant_type", None) != "brand"
        or getattr(request.state, "auth_method", None) != "jwt"
        or getattr(request.state, "acting_tenant_id", None)
        or getattr(request.state, "role", None) != "distributor"
    ):
        raise HTTPException(status_code=403, detail="Distributor portal access required")


async def require_store_portal_principal(request: Request) -> None:
    await require_durable_session(request)
    if (
        getattr(request.state, "tenant_type", None) != "brand"
        or getattr(request.state, "auth_method", None) != "jwt"
        or getattr(request.state, "acting_tenant_id", None)
        or getattr(request.state, "role", None) != "store_guide"
    ):
        raise HTTPException(status_code=403, detail="Store portal access required")
