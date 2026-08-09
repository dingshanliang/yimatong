import uuid

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.redis_cache import AsyncRedisCache


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


async def get_redis_cache() -> AsyncRedisCache:
    """提供 AsyncRedisCache 实例用于依赖注入。"""
    return AsyncRedisCache()


def require_tenant_feature(feature_key: str):
    """Build a cached FastAPI dependency for one server-side feature gate."""

    async def dependency(
        tenant_id: uuid.UUID = Depends(get_current_tenant),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        from app.services.entitlement import (
            TenantFeatureDisabledError,
        )
        from app.services.entitlement import (
            require_tenant_feature as require_feature,
        )

        try:
            await require_feature(db, tenant_id, feature_key)
        except TenantFeatureDisabledError as exc:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "TENANT_FEATURE_DISABLED",
                    "feature": exc.feature_key,
                    "message": "当前套餐未开通此功能",
                },
            ) from exc

    dependency.__name__ = f"require_{feature_key}_feature"
    return dependency


ALLOWED_OPS_ROLES = {"platform_admin", "admin", "operator"}
ALLOWED_OPS_TENANT_TYPES = {"agency", "platform"}


async def get_ops_user(
    request: Request,
    role: str = Depends(get_current_role),
    tenant_type: str = Depends(get_current_tenant_type),
) -> tuple[str | uuid.UUID, str, uuid.UUID | None, str]:
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
    account_id_str = getattr(request.state, "account_id", None)
    tenant_id_str = getattr(request.state, "tenant_id", None)
    if not account_id_str:
        raise HTTPException(status_code=401, detail="Account context not found")

    if tenant_type == "platform":
        if (
            role != "platform_admin"
            or account_id_str != "platform-admin"
            or tenant_id_str != "platform"
            or getattr(request.state, "auth_method", None) != "platform_cookie"
        ):
            raise HTTPException(status_code=403, detail="Invalid platform operations principal")
        return account_id_str, role, None, tenant_type

    if role not in {"admin", "operator"}:
        raise HTTPException(status_code=403, detail="Invalid agency operations principal")
    try:
        account_id = uuid.UUID(account_id_str)
        tenant_id = uuid.UUID(tenant_id_str) if tenant_id_str else None
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid operations principal") from exc
    return account_id, role, tenant_id, tenant_type
