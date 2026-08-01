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

        # yimatong-zgb1.5：scan_token 自校验的路径前缀（consents grant + withdraw 都在此下）
        is_public_consent = request.url.path.startswith("/api/v1/public/consents")

        # 公开路由跳过认证
        public_paths = {"/health", "/health/detail", "/docs", "/openapi.json", "/redoc"}
        public_auth_paths = {
            "/api/v1/auth/login",
            "/api/v1/auth/refresh",
            "/api/v1/auth/confirm-reset-password",
            "/api/v1/auth/reset-page",
            "/api/v1/consumers/lead-capture",
            "/api/v1/consumers/me",
            "/api/v1/invite-codes/register",
        }
        # scan-events / public consents 使用 scan_token 自校验（与 /c/ 同语义），不走 admin JWT。
        # yimatong-zgb1.4：scan-events 之前未放行导致 H5 telemetry 真实场景必 401。
        # yimatong-zgb1.5：public/consents 同样用 scan_token 自校验，需放行（否则 grant/withdraw 必 401）。
        # 注意：scan_event_router 注册时无 prefix，实际路径是 /scan-events（不是 /api/v1/scan-events）。
        # consents withdraw 路径含 {consent_id} 路径参数，用前缀匹配 is_public_consent。
        scan_token_paths = {"/scan-events"}
        # SSE 端点使用 query-param 认证，不走 middleware JWT
        query_auth_paths = {"/api/v1/risk-dashboard/alerts/stream"}
        if (
            request.url.path in public_paths
            or request.url.path in public_auth_paths
            or request.url.path in query_auth_paths
            or request.url.path in scan_token_paths
            or is_public_consent
            or request.url.path.startswith("/c/")
            or request.url.path.startswith("/api/v1/consumers/points/")
            or request.url.path.startswith("/api/v1/files/public/")
            or request.url.path == "/api/v1/benefit-claims"
            or request.url.path.startswith("/api/v1/takeover/gateway")
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

        # API clients attach the current token in Authorization. Prefer it over
        # cookies so stale localhost cookies from another session cannot shadow it.
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else request.cookies.get("access_token")

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
        # 加载数据库中的权限，并校验账户状态与 token 版本。
        has_access, permissions = await self._load_account_access(
            payload.get("sub"),
            payload.get("role"),
            payload.get("auth_version"),
            tenant_id,
        )
        if not has_access:
            return JSONResponse(status_code=401, content={"detail": "账户已停用或登录状态已失效"})
        request.state.permissions = permissions

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
        """从数据库加载账户权限；普通租户不得回退到代码模板。"""
        _, permissions = await self._load_account_access(account_id, role, None, None)
        return permissions

    async def _load_account_access(
        self,
        account_id: str | None,
        role: str | None,
        token_auth_version: int | None,
        tenant_id: str | None,
    ) -> tuple[bool, list[str]]:
        """加载权限并判断持久化账户是否仍允许当前 token 访问。"""
        # Platform 使用独立认证边界，没有租户 Account 行；只对这一明确边界保留
        # 代码权限。普通租户必须以数据库 Role/Permission 关联为运行时真相。
        if role == "platform_admin" and tenant_id == "platform":
            from app.utils.auth_rbac import get_permissions_for_role

            return True, get_permissions_for_role(role)
        if not account_id:
            return False, []
        from app.core.database import _is_pg, async_session_factory, engine

        uses_default_sqlite_factory = not _is_pg and async_session_factory.kw.get("bind") is engine
        if uses_default_sqlite_factory:
            # SQLite 仅用于单连接测试，middleware 另开 session 会回滚测试 fixture
            # 的未提交事务；生产和本地运行均使用 PostgreSQL，并走下方 fail-closed 路径。
            from app.utils.auth_rbac import get_permissions_for_role

            return True, get_permissions_for_role(role) if role else []
        try:
            import uuid

            from sqlalchemy import select, text
            from sqlalchemy.orm import selectinload

            from app.models.tenant import Account, Role

            async with async_session_factory() as db:
                if _is_pg:
                    if tenant_id is None:
                        return False, []
                    validated_tenant_id = str(uuid.UUID(tenant_id))
                    await db.execute(text(f"SET LOCAL app.tenant_id = '{validated_tenant_id}'"))
                result = await db.execute(
                    select(Account)
                    .options(selectinload(Account.roles).selectinload(Role.permissions))
                    .where(Account.id == uuid.UUID(account_id))
                )
                account = result.scalar_one_or_none()
                if not account:
                    return False, []
                account_auth_version = getattr(account, "auth_version", 0)
                if not isinstance(account_auth_version, int):
                    account_auth_version = 0
                if account.is_active is False or (token_auth_version or 0) != account_auth_version:
                    return False, []
                permissions = set()
                for role_obj in account.roles:
                    for perm in role_obj.permissions:
                        permissions.add(perm.code)
                return True, list(permissions)
        except Exception:
            # 权限读取异常必须 fail-closed，避免数据库故障扩大权限。
            return False, []
