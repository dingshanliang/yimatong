"""角色→权限映射配置。

角色系统用于 API Key 认证。每个角色映射到一组 resource:action 权限。
"""

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
