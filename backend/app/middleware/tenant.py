from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.security import verify_access_token

# Open API 路径前缀，使用 API Key 认证
OPEN_API_PREFIX = "/open/v1/"


class TenantScopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # CORS preflight requests must bypass authentication
        if request.method == "OPTIONS":
            return await call_next(request)

        # 公开路由跳过认证
        public_paths = {"/health", "/health/detail", "/docs", "/openapi.json", "/redoc"}
        public_auth_paths = {
            "/api/v1/auth/login",
            "/api/v1/auth/refresh",
            "/api/v1/consumers/lead-capture",
            "/api/v1/consumers/me",
        }
        # SSE 端点使用 query-param 认证，不走 middleware JWT
        query_auth_paths = {"/api/v1/risk-dashboard/alerts/stream"}
        if (
            request.url.path in public_paths
            or request.url.path in public_auth_paths
            or request.url.path in query_auth_paths
            or request.url.path.startswith("/c/")
            or request.url.path.startswith("/api/v1/consumers/points/")
            or request.url.path.startswith("/api/v1/files/public/")
            or request.url.path == "/api/v1/benefit-claims"
            or request.url.path.startswith("/api/v1/integrations/wecom/callback/")
            or request.url.path == "/api/v1/integrations/wecom/contact-way"
            or request.url.path == "/api/v1/integrations/wecom/mock-added"
            or request.url.path == "/api/v1/platform/auth/login"
            or request.url.path.startswith("/api/v1/connectors/connectors/")
            and request.url.path.endswith("/callback")
            or request.url.path.startswith("/api/v1/wechat/")
        ):
            return await call_next(request)

        # Open API 路径：使用 API Key 认证
        if request.url.path.startswith(OPEN_API_PREFIX):
            return await self._authenticate_api_key(request, call_next)

        # 默认：JWT Bearer Token 认证
        return await self._authenticate_jwt(request, call_next)

    async def _authenticate_jwt(self, request: Request, call_next):
        from starlette.responses import JSONResponse

        # 优先从 cookie 读取，回退到 Authorization header
        token = request.cookies.get("access_token")
        if not token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]

        if not token:
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid token"})

        payload = await verify_access_token(token)
        if payload is None:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        tenant_id = payload.get("tenant_id")
        acting_tenant_id = payload.get("acting_tenant_id")
        request.state.tenant_id = tenant_id
        request.state.account_id = payload.get("sub")
        request.state.role = payload.get("role")
        request.state.tenant_type = payload.get("tenant_type", "brand")
        request.state.auth_method = "jwt"
        # 加载数据库中的权限到 request.state.permissions
        request.state.permissions = await self._load_permissions(
            payload.get("sub"), payload.get("role")
        )

        # Agency context switching: if acting_tenant_id is present, use it for RLS
        if acting_tenant_id:
            request.state.acting_tenant_id = acting_tenant_id
            request.state.original_tenant_id = tenant_id
            rls_tenant_id = acting_tenant_id
        else:
            request.state.acting_tenant_id = None
            request.state.original_tenant_id = None
            rls_tenant_id = tenant_id

        # Set context var for RLS (consumed by get_db)
        from app.core.context import set_request_tenant_id

        set_request_tenant_id(rls_tenant_id)
        try:
            return await call_next(request)
        finally:
            set_request_tenant_id(None)

    async def _authenticate_api_key(self, request: Request, call_next):
        from starlette.responses import JSONResponse

        api_key_str = request.headers.get("X-Api-Key", "")
        if not api_key_str:
            return JSONResponse(status_code=401, content={"detail": "Missing X-Api-Key header"})

        # 查询数据库验证 API Key
        from app.core.database import async_session_factory
        from app.models.webhook import ApiKey

        async with async_session_factory() as db:
            from sqlalchemy import select

            result = await db.execute(select(ApiKey).where(ApiKey.key == api_key_str, ApiKey.revoked.is_(False)))
            key = result.scalar_one_or_none()

            if not key:
                return JSONResponse(status_code=401, content={"detail": "Invalid or revoked API key"})

            from datetime import UTC, datetime

            if key.expires_at and key.expires_at < datetime.now(UTC):
                return JSONResponse(status_code=401, content={"detail": "API key has expired"})

            # 更新 last_used_at
            key.last_used_at = datetime.now(UTC)
            await db.commit()

            tenant_id = str(key.tenant_id)
            request.state.tenant_id = tenant_id
            request.state.account_id = None
            request.state.role = key.role
            request.state.permissions = key.permissions
            request.state.tenant_type = "brand"
            request.state.auth_method = "api_key"
            request.state.api_key_id = str(key.id)

        # Set context var for RLS
        from app.core.context import set_request_tenant_id

        set_request_tenant_id(tenant_id)
        try:
            return await call_next(request)
        finally:
            set_request_tenant_id(None)

    async def _load_permissions(self, account_id: str | None, role: str | None) -> list[str]:
        """从数据库加载账户的权限列表"""
        if not account_id:
            return []
        try:
            from app.core.database import async_session_factory
            from app.models.tenant import Account, Role
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload
            import uuid

            async with async_session_factory() as db:
                result = await db.execute(
                    select(Account)
                    .options(selectinload(Account.roles).selectinload(Role.permissions))
                    .where(Account.id == uuid.UUID(account_id))
                )
                account = result.scalar_one_or_none()
                if not account:
                    return []
                permissions = set()
                for role_obj in account.roles:
                    for perm in role_obj.permissions:
                        permissions.add(perm.code)
                return list(permissions)
        except Exception:
            # 权限加载失败不应阻断请求，降级为空权限
            return []
