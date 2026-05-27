ROLES = {
    "admin": [
        "tenant:manage",
        "account:manage",
        "role:manage",
        "product:create",
        "product:update",
        "product:delete",
        "code:generate",
        "code:export",
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
}


def require_role(*allowed_roles: str):
    """FastAPI 依赖注入：检查当前用户角色是否在允许列表中"""
    from fastapi import HTTPException
    from starlette.requests import Request

    async def _check(request: Request):
        role = getattr(request.state, "role", None)
        if role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return role

    return _check
