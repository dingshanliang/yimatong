"""角色→权限映射配置 及 租户类型守卫。

角色系统用于 API Key 认证。每个角色映射到一组 resource:action 权限。
租户类型守卫用于限制端点只能被特定 tenant_type 访问。
"""

from fastapi import Depends, HTTPException, Request

ROLE_PERMISSIONS: dict[str, list[str]] = {
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

VALID_ROLES = list(ROLE_PERMISSIONS.keys())
VALID_PERMISSIONS = sorted(set(p for perms in ROLE_PERMISSIONS.values() for p in perms))


def get_permissions_for_role(role: str) -> list[str]:
    """根据角色名获取权限列表。"""
    return ROLE_PERMISSIONS.get(role, [])


def role_has_permission(role: str, permission: str) -> bool:
    """检查角色是否包含指定权限。"""
    return permission in ROLE_PERMISSIONS.get(role, [])


async def _get_tenant_type_from_request(request: Request) -> str:
    """从 request.state 读取 tenant_type，默认 'brand'。"""
    return getattr(request.state, "tenant_type", "brand")


def require_tenant_type(*allowed_types: str):
    """FastAPI 依赖：限制只有特定 tenant_type 可访问。

    用法：
        @router.get(..., dependencies=[Depends(require_tenant_type("agency", "platform"))])
        或者作为函数参数依赖：
        _guard = Depends(require_tenant_type("brand"))
    """

    async def _check(tenant_type: str = Depends(_get_tenant_type_from_request)):
        if tenant_type not in allowed_types:
            raise HTTPException(
                status_code=403,
                detail=f"该操作需要 {', '.join(allowed_types)} 类型租户",
            )
        return tenant_type

    return _check
