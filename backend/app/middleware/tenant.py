from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.security import verify_access_token


class TenantScopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 公开路由跳过认证
        public_paths = {"/health", "/health/detail", "/docs", "/openapi.json", "/redoc"}
        if request.url.path in public_paths or request.url.path.startswith("/c/"):
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
