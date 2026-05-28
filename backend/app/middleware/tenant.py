from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.security import verify_access_token


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
        ):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=401, content={"detail": "Missing or invalid token"})

        token = auth_header[7:]
        payload = verify_access_token(token)
        if payload is None:
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        request.state.tenant_id = payload.get("tenant_id")
        request.state.account_id = payload.get("sub")
        request.state.role = payload.get("role")

        return await call_next(request)
