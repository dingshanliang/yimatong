"""统一 RBAC 系统 — Web 用户 + API Key 共享权限命名空间。

Web 用户角色（JWT）: admin, operator, platform_admin
API Key 角色: data_reader, coupon_operator, webhook_admin, erp_sync, full_access

权限码统一使用 resource:action 格式。
"""

from fastapi import Depends, HTTPException, Request

# ── Web 用户角色 → 权限映射 ──────────────────────────────────────

WEB_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin": [
        "tenant:manage",
        "account:manage",
        "role:manage",
        "product:create",
        "product:update",
        "product:delete",
        "code:generate",
        "code:export",
        "code:manage",
        "page:create",
        "page:publish",
        "campaign:create",
        "campaign:manage",
        "analytics:view",
        "export:run",
        "takeover:prepare",
        "takeover:approve",
        "takeover:execute",
        "takeover:rollback",
        "takeover:audit",
    ],
    "operator": [
        "product:create",
        "product:update",
        "code:generate",
        "code:export",
        "page:create",
        "page:publish",
        "campaign:create",
        "campaign:manage",
        "analytics:view",
        "takeover:prepare",
        "takeover:audit",
    ],
    "viewer": [],
    "platform_admin": [
        "platform:admin",
        "tenant:manage",
        "account:manage",
        "role:manage",
        "product:create",
        "product:update",
        "product:delete",
        "code:generate",
        "code:export",
        "code:manage",
        "page:create",
        "page:publish",
        "campaign:create",
        "campaign:manage",
        "analytics:view",
        "export:run",
        "takeover:prepare",
        "takeover:approve",
        "takeover:execute",
        "takeover:rollback",
        "takeover:audit",
        "pilot:manage",
    ],
}

# ── API Key 角色 → 权限映射 ──────────────────────────────────────

API_KEY_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "data_reader": [
        "scan:list",
        "scan:detail",
        "consumer:list",
        "consumer:detail",
        "claim:list",
        "claim:detail",
        "event:list",
        "event:detail",
    ],
    "coupon_operator": [
        "scan:list",
        "scan:detail",
        "consumer:list",
        "consumer:detail",
        "claim:list",
        "claim:detail",
        "event:list",
        "event:detail",
        "coupon:issue",
        "coupon:redeem",
        "coupon:detail",
    ],
    "webhook_admin": [
        "webhook:endpoint_create",
        "webhook:endpoint_list",
        "webhook:endpoint_update",
        "webhook:endpoint_delete",
        "webhook:delivery_list",
        "webhook:delivery_retry",
        "api_key:create",
        "api_key:list",
        "api_key:revoke",
    ],
    # 外部 ERP / WMS 主数据同步：只给商品目录读写，不给营销、券、风控或 webhook。
    "erp_sync": [
        "product:list",
        "product:create",
        "product:update",
    ],
    "full_access": [
        "scan:list",
        "scan:detail",
        "consumer:list",
        "consumer:detail",
        "claim:list",
        "claim:detail",
        "claim:create",
        "claim:update",
        "event:list",
        "event:detail",
        "coupon:issue",
        "coupon:redeem",
        "coupon:detail",
        "coupon:delete",
        "campaign:update",
        "code:batch_create",
        "code:batch_update",
        # 产品目录同步（ERP 集成）；full_access 保持所有 API Key 权限的超集约定。
        "product:list",
        "product:create",
        "product:update",
        "webhook:endpoint_create",
        "webhook:endpoint_list",
        "webhook:endpoint_update",
        "webhook:endpoint_delete",
        "webhook:delivery_list",
        "webhook:delivery_retry",
        "api_key:create",
        "api_key:list",
        "api_key:revoke",
    ],
}

# ── 合并 ────────────────────────────────────────────────────────

ALL_ROLE_PERMISSIONS = {**WEB_ROLE_PERMISSIONS, **API_KEY_ROLE_PERMISSIONS}
VALID_WEB_ROLES = list(WEB_ROLE_PERMISSIONS.keys())
VALID_API_KEY_ROLES = list(API_KEY_ROLE_PERMISSIONS.keys())

# Backward compatibility aliases
ROLE_PERMISSIONS = API_KEY_ROLE_PERMISSIONS  # For webhook service
VALID_ROLES = list(ALL_ROLE_PERMISSIONS.keys())
VALID_PERMISSIONS = sorted(set(p for perms in ALL_ROLE_PERMISSIONS.values() for p in perms))


def get_permissions_for_role(role: str) -> list[str]:
    """根据角色名获取权限列表（Web 和 API Key 统一接口）。"""
    return ALL_ROLE_PERMISSIONS.get(role, [])


def role_has_permission(role: str, permission: str) -> bool:
    """检查角色是否包含指定权限。"""
    return permission in ALL_ROLE_PERMISSIONS.get(role, [])


async def _get_tenant_type_from_request(request: Request) -> str:
    """从 request.state 读取 tenant_type，默认 'brand'。"""
    return getattr(request.state, "tenant_type", "brand")


def require_tenant_type(*allowed_types: str):
    """FastAPI 依赖：限制只有特定 tenant_type 可访问。"""

    async def _check(tenant_type: str = Depends(_get_tenant_type_from_request)):
        if tenant_type not in allowed_types:
            raise HTTPException(
                status_code=403,
                detail=f"该操作需要 {', '.join(allowed_types)} 类型租户",
            )
        return tenant_type

    return _check


def require_role(*allowed_roles: str):
    """FastAPI 依赖注入：检查当前用户角色是否在允许列表中。"""

    async def _check(request: Request):
        role = getattr(request.state, "role", None)
        if role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        if role == "platform_admin" and (
            getattr(request.state, "account_id", None) != "platform-admin"
            or getattr(request.state, "tenant_id", None) != "platform"
            or getattr(request.state, "tenant_type", None) != "platform"
            or getattr(request.state, "auth_method", None) != "platform_cookie"
        ):
            raise HTTPException(status_code=403, detail="Invalid platform principal")
        return role

    return _check


def require_permission(permission: str):
    """FastAPI 依赖：按固定角色契约与实际授权双重检查权限。"""

    async def _check(request: Request) -> None:
        role = getattr(request.state, "role", None)
        permissions: list[str] = getattr(request.state, "permissions", [])
        if permission not in ALL_ROLE_PERMISSIONS.get(role, []) or permission not in permissions:
            raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")

    return _check


async def require_api_key_admin(request: Request) -> None:
    """Restrict API credential lifecycle operations to durable brand admins."""

    if (
        getattr(request.state, "auth_method", None) != "jwt"
        or getattr(request.state, "role", None) != "admin"
        or getattr(request.state, "tenant_type", None) != "brand"
        or not getattr(request.state, "session_id", None)
        or "tenant:manage" not in (getattr(request.state, "permissions", []) or [])
    ):
        raise HTTPException(status_code=403, detail="Only a brand administrator may manage API keys")


async def require_durable_session(request: Request) -> None:
    """Reject API keys and legacy JWTs for actor-bound business mutations."""

    from app.core.config import settings

    is_sqlite_adapter = settings.database_url.startswith("sqlite")
    if getattr(request.state, "auth_method", None) != "jwt" or (
        not getattr(request.state, "session_id", None) and not is_sqlite_adapter
    ):
        raise HTTPException(status_code=403, detail="A live login session is required")
