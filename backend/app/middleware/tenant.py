import hashlib
import hmac
import re
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.middleware.request_body_limit import (
    is_benefit_claim_status_path,
    is_public_benefit_claim_path,
    is_wecom_callback_path,
)
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable
from app.utils.security import verify_access_token

# Open API 路径前缀，使用 API Key 认证
OPEN_API_PREFIX = "/open/v1/"
_OPEN_API_RATE_WINDOW_SECONDS = 60
_OPEN_API_GLOBAL_RATE_LIMIT = 6000
_OPEN_API_IP_RATE_LIMIT = 300
_OPEN_API_KEY_RATE_LIMIT = 600
_open_api_security_cache = AsyncRedisCache(prefix="open_api_security", default_ttl=_OPEN_API_RATE_WINDOW_SECONDS)


def _api_key_metadata_is_invalid(exc: Exception) -> bool:
    """Recognize fail-closed catalog drift from the DB credential resolver."""

    return getattr(getattr(exc, "orig", None), "sqlstate", None) == "22023"


_AGENCY_AUTHORIZATION_DETAIL_PATH = re.compile(
    r"/api/v1/ops/authorizations/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_RETROSPECTIVE_DETAIL_PATH = re.compile(
    r"/api/v1/retrospectives/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_MEMBER_COUPON_STORE_TOKEN_PATH = re.compile(
    r"/api/v1/consumers/membership/coupons/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/store-token"
)


def _is_member_coupon_scan_token_path(path: str) -> bool:
    return path == "/api/v1/consumers/membership/coupons" or bool(_MEMBER_COUPON_STORE_TOKEN_PATH.fullmatch(path))


def _is_member_notification_scan_token_path(path: str) -> bool:
    return path in {
        "/api/v1/consumers/membership/notifications",
        "/api/v1/consumers/membership/notification-preferences",
        "/api/v1/consumers/membership/notification-preferences/marketing-subscription",
        "/api/v1/consumers/membership/notification-preferences/service-wechat",
        "/api/v1/consumers/membership/notification-channel-grants",
    }


def _is_commerce_public_path(path: str) -> bool:
    return path in {
        "/api/v1/consumers/membership/commerce-handoffs",
        "/api/v1/commerce/handoffs/redeem",
        "/api/v1/commerce/events",
        "/api/v1/commerce/coupons/eligible",
        "/api/v1/commerce/coupons/transitions",
    }


def _is_member_miniprogram_public_path(path: str) -> bool:
    return path in {
        "/api/v1/consumers/membership/miniprogram-session",
        "/api/v1/consumers/membership/miniprogram-bind",
    }


def _is_member_privacy_scan_token_path(path: str) -> bool:
    return path == "/api/v1/consumers/membership/privacy-requests"


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
            "/api/v1/auth/logout",
            "/api/v1/auth/confirm-reset-password",
            "/api/v1/auth/reset-page",
            "/api/v1/consumers/lead-capture",
            "/api/v1/consumers/me",
            "/api/v1/consumers/membership/join",
            "/api/v1/consumers/membership/merge",
            "/api/v1/consumers/membership/recover",
            "/api/v1/invite-codes/register",
        }
        # scan-events / public consents 使用 scan_token 自校验（与 /c/ 同语义），不走 admin JWT。
        # yimatong-zgb1.4：scan-events 之前未放行导致 H5 telemetry 真实场景必 401。
        # yimatong-zgb1.5：public/consents 同样用 scan_token 自校验，需放行（否则 grant/withdraw 必 401）。
        # scan_event_router uses the canonical API prefix and self-validates scan_token.
        # consents withdraw 路径含 {consent_id} 路径参数，用前缀匹配 is_public_consent。
        scan_token_paths = {"/api/v1/scan-events", "/api/v1/public/leads"}
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
            or _is_member_coupon_scan_token_path(request.url.path)
            or _is_member_notification_scan_token_path(request.url.path)
            or _is_commerce_public_path(request.url.path)
            or _is_member_miniprogram_public_path(request.url.path)
            or _is_member_privacy_scan_token_path(request.url.path)
            or request.url.path.startswith("/api/v1/files/public/")
            or request.url.path == "/api/v1/benefit-claims"
            # 发放状态查询（GET /benefit-claims/{id}/status）自校验 scan_token/回访凭证；
            # 精确匹配注册路由形状，白名单是方法无关的路径匹配——同前缀的新路由
            # 不得静默继承放行（新增时必须显式扩展该 matcher 并补隔离测试）。
            or is_benefit_claim_status_path(request.url.path)
            or is_public_benefit_claim_path(request.url.path)
            or request.url.path.startswith("/api/v1/takeover/gateway")
            or is_wecom_callback_path(request.url.path)
            or request.url.path == "/api/v1/integrations/wecom/contact-way"
            or request.url.path == "/api/v1/platform/auth/login"
            or (
                request.url.path.startswith("/api/v1/connectors/connectors/") and request.url.path.endswith("/callback")
            )
            or request.url.path in {"/api/v1/wechat/auth-url", "/api/v1/wechat/oauth-callback"}
        ):
            return await call_next(request)

        # Open API 路径：使用 API Key 认证
        if request.url.path.startswith(OPEN_API_PREFIX):
            return await self._authenticate_api_key(request, call_next)

        # The platform control plane is cookie-only. Never let an Admin bearer
        # token (or even a valid platform bearer token copied into JavaScript)
        # cross this independent authentication boundary.
        is_platform_control_plane = (
            request.url.path.startswith("/api/v1/platform/")
            or (
                request.url.path.startswith("/api/v1/invite-codes")
                and request.url.path != "/api/v1/invite-codes/register"
            )
            or (
                request.url.path.startswith("/api/v1/tenants") and not request.url.path.startswith("/api/v1/tenants/me")
            )
            or (request.url.path.startswith("/api/v1/ops") and request.cookies.get("platform_access_token") is not None)
        )
        if is_platform_control_plane:
            return await self._authenticate_platform_cookie(request, call_next)

        # 默认：JWT Bearer Token 认证
        return await self._authenticate_jwt(request, call_next)

    async def _authenticate_platform_cookie(self, request: Request, call_next):
        from starlette.responses import JSONResponse

        token = request.cookies.get("platform_access_token")
        if not token:
            return JSONResponse(status_code=401, content={"detail": "Missing platform session"})
        try:
            payload = await verify_access_token(token)
        except SharedSecurityCacheUnavailable:
            return JSONResponse(status_code=503, content={"detail": "认证服务暂时不可用"})
        if payload is None:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired platform session"})
        if (
            payload.get("role") != "platform_admin"
            or payload.get("sub") != "platform-admin"
            or payload.get("tenant_id") != "platform"
            or payload.get("tenant_type") != "platform"
        ):
            return JSONResponse(status_code=403, content={"detail": "Invalid platform principal"})
        if not await self._load_platform_session_access(payload.get("sid")):
            return JSONResponse(status_code=401, content={"detail": "Platform session has been revoked or expired"})
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("Origin") != settings.platform_public_url:
                return JSONResponse(status_code=403, content={"detail": "Invalid platform request origin"})
            csrf_cookie = request.cookies.get("platform_csrf_token", "")
            csrf_header = request.headers.get("X-Platform-CSRF", "")
            if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
                return JSONResponse(status_code=403, content={"detail": "Invalid platform CSRF token"})

        request.state.tenant_id = "platform"
        request.state.account_id = "platform-admin"
        request.state.tenant_type = "platform"
        request.state.role = "platform_admin"
        request.state.permissions = []
        request.state.auth_method = "platform_cookie"
        request.state.session_id = payload.get("sid")
        return await self._call_with_security_credential(
            request,
            call_next,
            "platform_session",
            payload.get("sid"),
        )

    async def _load_platform_session_access(self, session_id: str | None) -> bool:
        """Validate the durable control-plane session after cache checks."""

        from app.core.database import _is_pg

        # Existing SQLite API tests use synthetic platform JWTs to exercise
        # authorization paths. Production PostgreSQL rejects every legacy
        # platform token that predates authoritative session IDs.
        if not session_id:
            return not _is_pg
        try:
            import uuid

            from sqlalchemy import func, select

            from app.core.database import control_session_factory
            from app.models.auth_security import PlatformAuthSession

            validated_session_id = uuid.UUID(session_id)
            async with control_session_factory() as db:
                active_session_id = await db.scalar(
                    select(PlatformAuthSession.id).where(
                        PlatformAuthSession.id == validated_session_id,
                        PlatformAuthSession.principal == "platform-admin",
                        PlatformAuthSession.revoked_at.is_(None),
                        PlatformAuthSession.expires_at > func.now(),
                    )
                )
                return active_session_id is not None
        except Exception:
            return False

    async def _authenticate_jwt(self, request: Request, call_next):
        from starlette.responses import JSONResponse

        # API clients attach the current token in Authorization. Prefer it over
        # cookies so stale localhost cookies from another session cannot shadow it.
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else request.cookies.get("access_token")

        if not token:
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid token"})

        try:
            payload = await verify_access_token(token)
        except SharedSecurityCacheUnavailable:
            return JSONResponse(status_code=503, content={"detail": "认证服务暂时不可用"})
        if payload is None:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        if (
            payload.get("role") == "platform_admin"
            or payload.get("tenant_id") == "platform"
            or payload.get("tenant_type") == "platform"
        ):
            return JSONResponse(status_code=401, content={"detail": "Platform session cookie required"})

        tenant_id = payload.get("tenant_id")
        acting_tenant_id = payload.get("acting_tenant_id")
        if not await self._load_auth_session_access(
            payload.get("sid"),
            payload.get("sub"),
            tenant_id,
            payload.get("auth_version"),
        ):
            return JSONResponse(status_code=401, content={"detail": "登录会话已撤销或过期"})
        request.state.tenant_id = tenant_id
        request.state.account_id = payload.get("sub")
        request.state.tenant_type = payload.get("tenant_type", "brand")
        request.state.auth_method = "jwt"
        # 加载数据库中的权限，并校验账户状态与 token 版本。
        has_access, permissions = await self._load_account_access(
            payload.get("sub"),
            payload.get("role"),
            payload.get("auth_version"),
            tenant_id,
            payload.get("tenant_type"),
        )
        if not has_access:
            return JSONResponse(status_code=401, content={"detail": "账户已停用或登录状态已失效"})
        request.state.permissions = permissions
        request.state.role = payload.get("role")
        request.state.auth_version = payload.get("auth_version", 0)
        request.state.session_id = payload.get("sid")

        if payload.get("must_change_password") and request.url.path not in {
            "/api/v1/auth/change-password",
            "/api/v1/auth/logout",
        }:
            return JSONResponse(status_code=403, content={"detail": "首次登录必须先修改临时密码"})

        # Agency context switching: if acting_tenant_id is present, use it for RLS
        if acting_tenant_id:
            if request.url.path == "/api/v1/agency/exit-context":
                # The original agency account and tenant were validated above.
                # Exiting must remain possible after the client authorization is
                # revoked, expires, or the client tenant is suspended.
                request.state.acting_tenant_id = acting_tenant_id
                request.state.original_tenant_id = tenant_id
                request.state.tenant_id = tenant_id
                request.state.agency_scopes = []
                rls_tenant_id = tenant_id
            else:
                live_scopes = await self._load_acting_authorization(tenant_id, acting_tenant_id)
                if live_scopes is None:
                    return JSONResponse(status_code=403, content={"detail": "代运营授权已失效"})
                if not self._acting_path_is_explicitly_supported(request.url.path, live_scopes, request.method):
                    return JSONResponse(status_code=403, content={"detail": "当前代运营授权不允许访问该功能"})
                request.state.acting_tenant_id = acting_tenant_id
                request.state.original_tenant_id = tenant_id
                request.state.tenant_id = acting_tenant_id
                request.state.agency_scopes = live_scopes
                rls_tenant_id = acting_tenant_id
        else:
            request.state.acting_tenant_id = None
            request.state.original_tenant_id = None
            if request.state.tenant_type == "agency" and self._requires_client_workspace(
                request.url.path, request.method
            ):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "代运营服务商必须先进入已授权的客户工作区才能修改品牌数据"},
                )
            rls_tenant_id = tenant_id

        if self._requires_active_plan(request) and await self._tenant_plan_blocks_write(rls_tenant_id):
            return self._plan_expired_response()

        # Set context var for RLS (consumed by get_db)
        from app.core.context import reset_request_tenant_id, set_request_tenant_id

        context_token = set_request_tenant_id(rls_tenant_id)
        credential_token = None
        if request.state.session_id:
            from app.core.database import set_request_security_credential

            credential_token = set_request_security_credential("auth_session", str(request.state.session_id))
        try:
            return await call_next(request)
        finally:
            if credential_token is not None:
                from app.core.database import reset_request_security_credential

                reset_request_security_credential(credential_token)
            reset_request_tenant_id(context_token)

    async def _authenticate_api_key(self, request: Request, call_next):
        from starlette.responses import JSONResponse

        api_key_str = request.headers.get("X-Api-Key", "")
        rate_response = await self._enforce_open_api_request_rate_limit(request)
        if rate_response is not None:
            return rate_response
        if not api_key_str:
            return JSONResponse(status_code=401, content={"detail": "Invalid API key"})

        api_key_digest = hashlib.sha256(api_key_str.encode()).hexdigest()

        # PostgreSQL resolves credentials through a constrained SECURITY DEFINER
        # function. SQLite keeps an equivalent ORM path for isolated API tests.
        from app.core.database import _session_uses_postgresql, async_session_factory

        async with async_session_factory() as db:
            from datetime import UTC, datetime

            from sqlalchemy import or_, select, text

            if _session_uses_postgresql(db):
                try:
                    resolved = (
                        (
                            await db.execute(
                                text("SELECT * FROM public.resolve_active_api_key(:requested_digest)"),
                                {"requested_digest": api_key_digest},
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                except Exception as exc:
                    if _api_key_metadata_is_invalid(exc):
                        return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
                    raise
                if resolved is None:
                    return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
                key_id = resolved["api_key_id"]
                key_tenant_id = resolved["tenant_id"]
                key_role = resolved["role"]
                key_permissions = resolved["permissions"]
                from app.core.database import set_session_tenant_context

                await set_session_tenant_context(db, key_tenant_id)
            else:
                from app.models.tenant import Tenant, TenantStatus, TenantType
                from app.models.webhook import ApiKey

                key = (
                    await db.execute(
                        select(ApiKey)
                        .join(Tenant, Tenant.id == ApiKey.tenant_id)
                        .where(
                            ApiKey.key_digest == api_key_digest,
                            ApiKey.revoked.is_(False),
                            or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > datetime.now(UTC)),
                            Tenant.status == TenantStatus.active,
                            Tenant.tenant_type == TenantType.brand,
                        )
                    )
                ).scalar_one_or_none()
                if key is None:
                    return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
                key.last_used_at = datetime.now(UTC)
                key_id = key.id
                key_tenant_id = key.tenant_id
                key_role = key.role
                key_permissions = key.permissions

            key_rate_response = await self._enforce_open_api_key_rate_limit(key_id)
            if key_rate_response is not None:
                return key_rate_response

            if self._requires_active_plan(request):
                from app.models.tenant import Tenant
                from app.services.entitlement import is_plan_expired

                tenant = await db.get(Tenant, key_tenant_id)
                if tenant is None or is_plan_expired(tenant.plan_expires_at):
                    return self._plan_expired_response()
            await db.commit()

            tenant_id = str(key_tenant_id)
            request.state.tenant_id = tenant_id
            request.state.account_id = None
            request.state.role = key_role
            request.state.permissions = key_permissions
            request.state.tenant_type = "brand"
            request.state.auth_method = "api_key"
            request.state.api_key_id = str(key_id)
            request.state.api_key_digest = api_key_digest

        # Set context var for RLS
        from app.core.context import reset_request_tenant_id, set_request_tenant_id

        context_token = set_request_tenant_id(tenant_id)
        from app.core.database import set_request_security_credential

        credential_token = set_request_security_credential("api_key", str(request.state.api_key_id))
        try:
            return await call_next(request)
        finally:
            from app.core.database import reset_request_security_credential

            reset_request_security_credential(credential_token)
            reset_request_tenant_id(context_token)

    @staticmethod
    async def _check_open_api_rate_limit(checks: list[tuple[str, int]]):
        from starlette.responses import JSONResponse

        try:
            for key, limit in checks:
                allowed, _ = await _open_api_security_cache.rate_limit_check_shared(
                    key,
                    max_attempts=limit,
                    window_seconds=_OPEN_API_RATE_WINDOW_SECONDS,
                )
                if not allowed:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Open API 请求过于频繁，请稍后重试"},
                        headers={"Retry-After": str(_OPEN_API_RATE_WINDOW_SECONDS)},
                    )
        except SharedSecurityCacheUnavailable:
            return JSONResponse(status_code=503, content={"detail": "Open API 认证服务暂时不可用"})
        return None

    @staticmethod
    async def _enforce_open_api_request_rate_limit(request: Request):
        keyed_secret = (settings.hmac_pepper or settings.secret_key).encode()
        peer_host = request.client.host if request.client else "unknown"
        ip_digest = hmac.new(keyed_secret, peer_host.encode(), hashlib.sha256).hexdigest()
        return await TenantScopeMiddleware._check_open_api_rate_limit(
            [
                ("global:requests", _OPEN_API_GLOBAL_RATE_LIMIT),
                (f"ip:{ip_digest}", _OPEN_API_IP_RATE_LIMIT),
            ]
        )

    @staticmethod
    async def _enforce_open_api_key_rate_limit(api_key_id: uuid.UUID):
        keyed_secret = (settings.hmac_pepper or settings.secret_key).encode()
        key_digest = hmac.new(keyed_secret, str(api_key_id).encode(), hashlib.sha256).hexdigest()
        return await TenantScopeMiddleware._check_open_api_rate_limit([(f"key:{key_digest}", _OPEN_API_KEY_RATE_LIMIT)])

    @staticmethod
    async def _call_with_security_credential(
        request: Request,
        call_next,
        kind: str,
        identifier: str | None,
    ):
        if not identifier:
            return await call_next(request)
        from app.core.database import reset_request_security_credential, set_request_security_credential

        credential_token = set_request_security_credential(kind, str(identifier))
        try:
            return await call_next(request)
        finally:
            reset_request_security_credential(credential_token)

    @staticmethod
    def _requires_active_plan(request: Request) -> bool:
        if request.method in {"GET", "HEAD"}:
            return False
        from app.core.database import is_plan_recovery_write

        if is_plan_recovery_write(request):
            return False
        # Revocation is a security recovery action: an expired brand must still
        # be able to remove an agency's access. Keep the exception bound to the
        # canonical DELETE detail route; creation/renewal and neighboring paths
        # continue through the active-plan gate.
        if request.method == "DELETE" and _AGENCY_AUTHORIZATION_DETAIL_PATH.fullmatch(request.url.path):
            return False
        return request.url.path not in {
            "/api/v1/auth/change-password",
            "/api/v1/auth/logout",
            "/api/v1/agency/exit-context",
        }

    @staticmethod
    def _plan_expired_response():
        from starlette.responses import JSONResponse

        from app.services.entitlement import PLAN_EXPIRED_CODE, PLAN_EXPIRED_DETAIL

        return JSONResponse(
            status_code=403,
            content={"code": PLAN_EXPIRED_CODE, "detail": PLAN_EXPIRED_DETAIL},
        )

    async def _tenant_plan_blocks_write(self, tenant_id: str | None) -> bool:
        """生产从数据库读取实时到期时间；读取失败时写请求 fail closed。"""

        if tenant_id in {None, "platform"}:
            return False
        from app.core.database import _is_pg

        if not _is_pg:
            # 套餐到期是生产计费状态，由 PostgreSQL 实时读取。SQLite 仅用于单连接
            # API/单元测试：control_session_factory 是独立引擎，没有 fixture 数据，
            # 强行查询会抛 "no such table" 并被下面的 fail-closed 分支误判为套餐过期。
            # 状态机本身由 helper/middleware 单测覆盖；真实运行时均使用 PostgreSQL。
            # 注意：判定依据是运行时方言（_is_pg），而不是 async_session_factory 的
            # 对象身份——后者会被测试 patch 改变，从而使本短路失效。
            return False
        try:
            import uuid

            from sqlalchemy import select

            from app.models.tenant import Tenant
            from app.services.entitlement import is_plan_expired

            validated_tenant_id = uuid.UUID(tenant_id)
            from app.core.database import control_session_factory, set_session_tenant_context

            async with control_session_factory() as db:
                if _is_pg:
                    await set_session_tenant_context(db, validated_tenant_id)
                expires_at = await db.scalar(select(Tenant.plan_expires_at).where(Tenant.id == validated_tenant_id))
                return is_plan_expired(expires_at)
        except Exception:
            return True

    async def _load_permissions(self, account_id: str | None, role: str | None) -> list[str]:
        """从数据库加载账户权限；普通租户不得回退到代码模板。"""
        _, permissions = await self._load_account_access(account_id, role, None, None)
        return permissions

    async def _load_auth_session_access(
        self,
        session_id: str | None,
        account_id: str | None,
        tenant_id: str | None,
        token_auth_version: int | None,
    ) -> bool:
        """Use durable family state so a cache restart cannot revive access."""

        # Access tokens issued before refresh families existed remain valid only
        # for their original short JWT lifetime and keep the rollout compatible.
        if not session_id:
            return True
        try:
            import uuid

            from sqlalchemy import func, select

            from app.core.database import _is_pg, control_session_factory
            from app.models.auth_security import AuthSession

            if not account_id or not tenant_id:
                return False
            validated_session_id = uuid.UUID(session_id)
            validated_account_id = uuid.UUID(account_id)
            validated_tenant_id = uuid.UUID(tenant_id)
            if not _is_pg:
                # SQLite API tests exercise revocation through the shared cache;
                # their independent control engine does not share fixture rows.
                return True
            async with control_session_factory() as db:
                active_session_id = await db.scalar(
                    select(AuthSession.id).where(
                        AuthSession.id == validated_session_id,
                        AuthSession.account_id == validated_account_id,
                        AuthSession.tenant_id == validated_tenant_id,
                        AuthSession.auth_version == (token_auth_version or 0),
                        AuthSession.revoked_at.is_(None),
                        AuthSession.expires_at > func.now(),
                    )
                )
                return active_session_id is not None
        except Exception:
            return False

    async def _load_account_access(
        self,
        account_id: str | None,
        role: str | None,
        token_auth_version: int | None,
        tenant_id: str | None,
        expected_tenant_type: str | None = None,
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

            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            from app.models.tenant import Account, Role, Tenant, TenantStatus

            async with async_session_factory() as db:
                if _is_pg:
                    if tenant_id is None:
                        return False, []
                    validated_tenant_id = str(uuid.UUID(tenant_id))
                    from app.core.database import set_session_tenant_context

                    await set_session_tenant_context(db, validated_tenant_id)
                result = await db.execute(
                    select(Account, Tenant.status, Tenant.tenant_type)
                    .options(selectinload(Account.roles).selectinload(Role.permissions))
                    .join(Tenant, Tenant.id == Account.tenant_id)
                    .where(
                        Account.id == uuid.UUID(account_id),
                        Account.tenant_id == uuid.UUID(tenant_id),
                    )
                )
                row = result.one_or_none()
                if not row:
                    return False, []
                account, tenant_status, live_tenant_type = row
                account_auth_version = getattr(account, "auth_version", 0)
                if not isinstance(account_auth_version, int):
                    account_auth_version = 0
                if (
                    account.is_active is False
                    or tenant_status != TenantStatus.active
                    or (expected_tenant_type is not None and live_tenant_type.value != expected_tenant_type)
                    or (token_auth_version or 0) != account_auth_version
                ):
                    return False, []
                permissions = set()
                for role_obj in account.roles:
                    if role_obj.name not in {"admin", "operator", "viewer", "distributor", "store_guide"}:
                        continue
                    for perm in role_obj.permissions:
                        permissions.add(perm.code)
                return True, list(permissions)
        except Exception:
            # 权限读取异常必须 fail-closed，避免数据库故障扩大权限。
            return False, []

    @staticmethod
    def _is_brand_write_surface(path: str, method: str) -> bool:
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return False
        import_write_paths = {
            "/api/v1/imports/excel",
            "/api/v1/imports/products",
            "/api/v1/imports/existing-codes",
        }
        if path in import_write_paths:
            return method == "POST"
        brand_prefixes = (
            "/api/v1/brands",
            "/api/v1/products",
            "/api/v1/product-assets",
            "/api/v1/skus",
            "/api/v1/production-batches",
            "/api/v1/page-templates",
            "/api/v1/page-versions",
            "/api/v1/campaigns",
            "/api/v1/benefits",
            "/api/v1/connectors",
            "/api/v1/code-batches",
            "/api/v1/code-items",
            "/api/v1/risk-alerts",
        )
        return path == "/api/v1/files/upload" or any(
            path == prefix or path.startswith(f"{prefix}/") for prefix in brand_prefixes
        )

    @classmethod
    def _requires_client_workspace(cls, path: str, method: str) -> bool:
        """Keep agency-owned data separate from client risk data and brand mutations."""
        if path == "/api/v1/takeovers" or path.startswith("/api/v1/takeovers/"):
            return True
        if path == "/api/v1/risk-alerts" or path.startswith("/api/v1/risk-alerts/"):
            return True
        return cls._is_brand_write_surface(path, method)

    @staticmethod
    def _acting_path_is_explicitly_supported(path: str, scopes: list[str], method: str = "GET") -> bool:
        """Map live agency scopes to explicit API surfaces; unmatched routes fail closed."""
        if path == "/api/v1/agency/exit-context":
            return True
        # Dashboard shell needs exactly one live entitlement read after entering
        # any authorized client workspace. It exposes no profile or quota data.
        if path == "/api/v1/tenants/me/entitlement":
            return method == "GET"
        if path == "/api/v1/pilot-milestones":
            return method == "GET" and bool({"analytics", "campaigns"}.intersection(scopes))
        if path == "/api/v1/retrospectives" or _RETROSPECTIVE_DETAIL_PATH.fullmatch(path):
            if method == "GET":
                return bool({"analytics", "campaigns"}.intersection(scopes))
            return method == "PATCH" and "campaigns" in scopes and bool(_RETROSPECTIVE_DETAIL_PATH.fullmatch(path))
        if path.startswith("/api/v1/imports"):
            if method == "GET":
                return (
                    path
                    in {
                        "/api/v1/imports/template",
                        "/api/v1/imports/records",
                    }
                    and "products" in scopes
                )
            required_scope = {
                "/api/v1/imports/excel": "products",
                "/api/v1/imports/products": "products",
                "/api/v1/imports/existing-codes": "codes",
            }.get(path)
            return method == "POST" and required_scope is not None and required_scope in scopes
        if path.startswith("/api/v1/risk-alerts"):
            if "codes" not in scopes:
                return False
            if path == "/api/v1/risk-alerts":
                return method == "GET"
            parts = path.split("/")
            if len(parts) == 6 and parts[4] and parts[5] == "resolve":
                return method == "POST"
            if len(parts) == 7 and parts[4] == "code-items" and parts[5] and parts[6] in {"freeze", "unfreeze"}:
                return method == "POST"
            return False
        if path == "/api/v1/takeovers" or path.startswith("/api/v1/takeovers/"):
            return "codes" in scopes
        scope_prefixes = {
            "products": (
                "/api/v1/brands",
                "/api/v1/products",
                "/api/v1/skus",
                "/api/v1/production-batches",
            ),
            "pages": ("/api/v1/page-templates", "/api/v1/page-versions"),
            "campaigns": ("/api/v1/campaigns", "/api/v1/benefits", "/api/v1/connectors"),
            "codes": ("/api/v1/code-batches", "/api/v1/code-items"),
            "analytics": ("/api/v1/analytics",),
        }
        read_dependencies = {
            "pages": ("/api/v1/products", "/api/v1/brands", "/api/v1/skus"),
            "campaigns": (
                "/api/v1/products",
                "/api/v1/brands",
                "/api/v1/skus",
                "/api/v1/integrations/wecom",
            ),
            "codes": (
                "/api/v1/brands",
                "/api/v1/products",
                "/api/v1/skus",
                "/api/v1/production-batches",
            ),
        }
        if path.startswith("/api/v1/ops/launch-releases"):
            if path == "/api/v1/ops/launch-releases":
                if method == "GET":
                    return "pages" in scopes or "release:execute" in scopes
                return method == "POST" and "pages" in scopes
            parts = path.split("/")
            if len(parts) == 6 and parts[5]:
                return method == "GET" and ("pages" in scopes or "release:execute" in scopes)
            if len(parts) == 7 and parts[5] and parts[6] == "request-confirmation":
                return method == "POST" and "pages" in scopes
            if len(parts) == 7 and parts[5] and parts[6] == "publish":
                return method == "POST" and "release:execute" in scopes
            return False
        if path.startswith("/api/v1/product-assets/"):
            return method in {"PATCH", "DELETE"} and "products" in scopes
        # Product material editing uploads a file before attaching its returned
        # URL to a product asset. Keep this exception exact and method-bound so
        # the products scope does not become general access to the files API.
        if path == "/api/v1/files/upload":
            return method == "POST" and "products" in scopes
        # Product forms read the current tenant's category options. Keep this
        # dependency exact and read-only: products scope must not gain access
        # to tenant profile or category mutation surfaces.
        if path == "/api/v1/tenants/me/categories":
            return method == "GET" and "products" in scopes
        if path.startswith("/api/v1/integrations/wecom"):
            return (
                method == "GET"
                and path
                in {
                    "/api/v1/integrations/wecom",
                    "/api/v1/integrations/wecom/members",
                }
                and "campaigns" in scopes
            )
        if any(
            path == prefix or path.startswith(f"{prefix}/")
            for scope in scopes
            for prefix in scope_prefixes.get(scope, ())
        ):
            return True
        return method == "GET" and any(
            path == prefix or path.startswith(f"{prefix}/")
            for scope in scopes
            for prefix in read_dependencies.get(scope, ())
        )

    async def _load_acting_authorization(self, agency_tenant_id: str, client_tenant_id: str) -> list[str] | None:
        """Load the live authorization; JWT scope is never the runtime truth."""
        try:
            import uuid
            from datetime import UTC, datetime

            from sqlalchemy import select, text

            from app.core.database import _is_pg, async_session_factory, control_session_factory
            from app.models.tenant import AgencyAuthorization, AgencyAuthStatus, Tenant, TenantStatus, TenantType

            agency_id = uuid.UUID(agency_tenant_id)
            client_id = uuid.UUID(client_tenant_id)
            session_factory = control_session_factory if _is_pg else async_session_factory
            async with session_factory() as db:
                if _is_pg:
                    await db.execute(text("SELECT set_config('app.tenant_id', '', true)"))
                    await db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
                authorization = (
                    await db.execute(
                        select(AgencyAuthorization).where(
                            AgencyAuthorization.agency_tenant_id == agency_id,
                            AgencyAuthorization.client_tenant_id == client_id,
                            AgencyAuthorization.status == AgencyAuthStatus.active,
                        )
                    )
                ).scalar_one_or_none()
                if authorization is None:
                    return None
                expires_at = authorization.expires_at
                if expires_at is not None:
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=UTC)
                    if expires_at <= datetime.now(UTC):
                        return None
                tenant_rows = {
                    tenant_id: (status, tenant_type)
                    for tenant_id, status, tenant_type in (
                        await db.execute(
                            select(Tenant.id, Tenant.status, Tenant.tenant_type).where(
                                Tenant.id.in_([agency_id, client_id])
                            )
                        )
                    ).all()
                }
                agency_row = tenant_rows.get(agency_id)
                client_row = tenant_rows.get(client_id)
                if (
                    agency_row is None
                    or agency_row[0] != TenantStatus.active
                    or agency_row[1] != TenantType.agency
                    or client_row is None
                    or client_row[0] != TenantStatus.active
                    or client_row[1] != TenantType.brand
                ):
                    return None
                return list(authorization.scope)
        except Exception:
            return None
