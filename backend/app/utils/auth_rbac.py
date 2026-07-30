"""统一 RBAC 系统 — Web 用户 + API Key 共享权限命名空间。

Web 用户角色（JWT）: admin, operator, platform_admin
API Key 角色: data_reader, coupon_operator, webhook_admin, full_access

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
    ],
    "operator": [
        "product:create",
        "product:update",
        "code:generate",
        "code:export",
        "page:create",
        "page:publish",
        "campaign:create",
        "analytics:view",
    ],
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
        "campaign:status",
        "code:batch_create",
        "code:batch_update",
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
        return role

    return _check


def require_permission(permission: str):
    """FastAPI 依赖：检查当前请求的权限。支持 JWT 和 API Key。"""

    async def _check(request: Request) -> None:
        permissions: list[str] = getattr(request.state, "permissions", [])
        if permission not in permissions:
            raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")

    return _check
