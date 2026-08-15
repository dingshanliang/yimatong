"""Authentication dependencies shared by risk route modules."""

from fastapi import Depends, HTTPException, Request

from app.utils.auth_rbac import require_durable_session, require_permission


async def require_brand_risk_principal(request: Request) -> None:
    """Risk data and actions are available only to live direct brand sessions."""

    await require_durable_session(request)
    if (
        getattr(request.state, "tenant_type", None) != "brand"
        or getattr(request.state, "auth_method", None) != "jwt"
        or getattr(request.state, "acting_tenant_id", None)
    ):
        raise HTTPException(status_code=403, detail="Brand risk access required")


def risk_dependencies(permission: str) -> list:
    return [Depends(require_brand_risk_principal), Depends(require_permission(permission))]

