from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.exceptions import UnauthorizedError
from app.utils.security import verify_access_token

# Open API 路径前缀，使用 API Key 认证
OPEN_API_PREFIX = "/open/v1/"


class TenantScopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 公开路由跳过认证
        public_paths = {"/health", "/health/detail", "/docs", "/openapi.json", "/redoc"}
        public_auth_paths = {"/api/v1/auth/login", "/api/v1/auth/refresh"}
        if (
            request.url.path in public_paths
            or request.url.path in public_auth_paths
            or request.url.path.startswith("/c/")
            or request.url.path == "/api/v1/platform/auth/login"
            or (request.url.path == "/api/v1/tenants" and request.method == "POST")
            or request.url.path.startswith("/api/v1/connectors/connectors/") and request.url.path.endswith("/callback")
            or request.url.path.startswith("/api/v1/wechat/")
        ):
            return await call_next(request)

        # Open API 路径：使用 API Key 认证
        if request.url.path.startswith(OPEN_API_PREFIX):
            return await self._authenticate_api_key(request, call_next)

        # 默认：JWT Bearer Token 认证
        return await self._authenticate_jwt(request, call_next)

    async def _authenticate_jwt(self, request: Request, call_next):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise UnauthorizedError("Missing or invalid token")

        token = auth_header[7:]
        payload = await verify_access_token(token)
        if payload is None:
            raise UnauthorizedError("Invalid or expired token")

        tenant_id = payload.get("tenant_id")
        request.state.tenant_id = tenant_id
        request.state.account_id = payload.get("sub")
        request.state.role = payload.get("role")
        request.state.auth_method = "jwt"

        # Set context var for RLS (consumed by get_db)
        from app.core.context import set_request_tenant_id

        set_request_tenant_id(tenant_id)
        try:
            return await call_next(request)
        finally:
            set_request_tenant_id(None)

    async def _authenticate_api_key(self, request: Request, call_next):
        api_key_str = request.headers.get("X-Api-Key", "")
        if not api_key_str:
            raise UnauthorizedError("Missing X-Api-Key header")

        # 查询数据库验证 API Key
        from app.core.database import async_session_factory
        from app.models.webhook import ApiKey

        async with async_session_factory() as db:
            from sqlalchemy import select

            result = await db.execute(select(ApiKey).where(ApiKey.key == api_key_str, ApiKey.revoked.is_(False)))
            key = result.scalar_one_or_none()

            if not key:
                raise UnauthorizedError("Invalid or revoked API key")

            from datetime import UTC, datetime

            if key.expires_at and key.expires_at < datetime.now(UTC):
                raise UnauthorizedError("API key has expired")

            # 更新 last_used_at
            key.last_used_at = datetime.now(UTC)
            await db.commit()

            tenant_id = str(key.tenant_id)
            request.state.tenant_id = tenant_id
            request.state.account_id = None
            request.state.role = key.role
            request.state.permissions = key.permissions
            request.state.auth_method = "api_key"
            request.state.api_key_id = str(key.id)

        # Set context var for RLS
        from app.core.context import set_request_tenant_id

        set_request_tenant_id(tenant_id)
        try:
            return await call_next(request)
        finally:
            set_request_tenant_id(None)
