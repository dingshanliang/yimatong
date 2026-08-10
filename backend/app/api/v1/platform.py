import hashlib
import json
import secrets
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import case, func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import _is_pg, get_db_with_bypass
from app.core.dependencies import get_redis_cache
from app.models.auth_security import PlatformAuthSession
from app.models.plan import PlanDefinition, QuotaRolloutState, TenantQuotaUsage
from app.models.platform_opening import PlatformTenantOpening
from app.models.tenant import Account, Organization, Tenant, TenantPlan, TenantStatus, TenantType
from app.models.tenant_health import TenantHealthMetrics
from app.modules.brand_tenant_initialization import (
    BrandTenantAlreadyExists,
    BrandTenantInitialization,
    InitializeBrandTenant,
    PlanDefinitionUnavailable,
    PlatformOpening,
)
from app.modules.initial_admin_activation import InitialAdminActivation, InitialAdminNotPending
from app.schemas.common import PaginatedResponse
from app.services.audit import query_audit_logs, write_audit_log
from app.services.auth import logout_session
from app.services.entitlement import is_plan_expired, validate_feature_flags
from app.services.platform_auth import PlatformSessionUnavailable, revoke_platform_session
from app.services.quota import is_quota_usage_effectively_ready, validate_quota_config
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable
from app.services.tenant import (
    TenantTypeTransitionConflict,
)
from app.services.tenant import (
    update_tenant as update_tenant_service,
)
from app.services.tenant_health import refresh_all_health_metrics
from app.services.tenant_lifecycle import TenantStatusTransitionError, terminate_tenant, transition_tenant_status
from app.utils.auth_rbac import require_role
from app.utils.security import create_access_token, decode_token, verify_password

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PlatformLoginRequest(BaseModel):
    email: str
    password: str


class PlatformSessionResponse(BaseModel):
    authenticated: bool
    principal: str


class AuditLogRead(BaseModel):
    id: uuid.UUID
    operator_id: str
    target_tenant_id: str
    action: str
    resource: str
    details: dict | None = None
    timestamp: datetime

    model_config = {"from_attributes": True}


class TenantCreate(BaseModel):
    name: str
    plan: TenantPlan = TenantPlan.free
    industry: str | None = None
    notes: str | None = None
    admin_email: EmailStr
    admin_name: str
    tenant_type: TenantType = TenantType.brand


class TenantUpdate(BaseModel):
    name: str | None = None
    industry: str | None = None
    notes: str | None = None
    quota: dict | None = None
    enabled_features: dict | None = None

    model_config = {"extra": "forbid"}

    @field_validator("quota")
    @classmethod
    def validate_quota(cls, value: dict | None) -> dict | None:
        return validate_quota_config(value) if value is not None else None

    @field_validator("enabled_features")
    @classmethod
    def validate_features(cls, value: dict | None) -> dict | None:
        return validate_feature_flags(value) if value is not None else None


class TenantStatusUpdate(BaseModel):
    status: TenantStatus


class TenantRead(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: TenantStatus
    plan: TenantPlan
    plan_expires_at: datetime | None
    industry: str | None
    notes: str | None
    quota: dict | None
    enabled_features: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TenantOpeningRead(TenantRead):
    initial_admin_id: uuid.UUID
    initial_admin_state: str
    activation_url: str | None = None
    activation_retryable: bool = False


class ActivationLinkRead(BaseModel):
    initial_admin_id: uuid.UUID
    activation_url: str


class TenantListRead(TenantRead):
    initial_admin_state: str | None = None
    activation_retryable: bool = False


class TenantDetail(TenantRead):
    account_count: int = 0
    organization_count: int = 0
    initial_admin_state: str | None = None
    activation_retryable: bool = False


class DashboardSummary(BaseModel):
    total_tenants: int
    active_tenants: int
    suspended_tenants: int
    terminated_tenants: int
    expiring_soon: int  # expires within 30 days
    recent_audit_logs: list[AuditLogRead]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@router.post("/auth/login", response_model=PlatformSessionResponse)
async def platform_login(
    body: PlatformLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_with_bypass),
    cache: AsyncRedisCache = Depends(get_redis_cache),
):
    """平台管理员独立认证路径"""
    # request.client is populated by the ASGI server. Forwarded headers are only
    # safe when the server itself is configured with a trusted proxy boundary.
    client_ip = request.client.host if request.client else "unknown"
    normalized_email = body.email.strip().lower()
    account_key = hashlib.sha256(normalized_email.encode()).hexdigest()
    try:
        ip_allowed, _ = await cache.rate_limit_check_shared(
            f"platform_login_rate:ip:{client_ip}", max_attempts=10, window_seconds=300
        )
        account_allowed, _ = await cache.rate_limit_check_shared(
            f"platform_login_rate:account:{account_key}", max_attempts=10, window_seconds=300
        )
    except SharedSecurityCacheUnavailable as exc:
        raise HTTPException(status_code=503, detail="登录服务暂时不可用，请稍后重试") from exc
    if not ip_allowed or not account_allowed:
        raise HTTPException(status_code=429, detail="尝试过于频繁", headers={"Retry-After": "300"})

    if not settings.platform_admin_password_hash:
        raise HTTPException(status_code=500, detail="Platform admin not configured")
    if body.email != settings.platform_admin_email or not verify_password(
        body.password, settings.platform_admin_password_hash
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    session_id = uuid.uuid4()
    token = create_access_token(
        tenant_id="platform",
        account_id="platform-admin",
        role="platform_admin",
        tenant_type="platform",
        extra={"sid": str(session_id)},
    )
    session_expires_at = datetime.fromtimestamp(decode_token(token)["exp"], tz=UTC)
    db.add(
        PlatformAuthSession(
            id=session_id,
            principal="platform-admin",
            expires_at=session_expires_at,
        )
    )

    # 登录审计、持久会话与凭证签发采用 fail-closed 边界。
    await write_audit_log(db, "platform-admin", "platform", "platform_login", f"ip:{client_ip}")
    await db.commit()

    response = JSONResponse(content={"authenticated": True, "principal": "platform_admin"})
    csrf_token = secrets.token_urlsafe(32)
    # Platform uses separate cookie name
    response.set_cookie(
        "platform_access_token",
        token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain or None,
        path="/",
    )
    response.set_cookie(
        "platform_csrf_token",
        csrf_token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain or None,
        path="/",
    )
    return response


@router.post("/auth/logout")
async def platform_logout(
    request: Request,
    db: AsyncSession = Depends(get_db_with_bypass),
    cache: AsyncRedisCache = Depends(get_redis_cache),
    _role: str = Depends(require_role("platform_admin")),
):
    """撤销平台 access token，并清除独立的 HttpOnly cookie。"""
    token = request.cookies.get("platform_access_token")
    session_id = getattr(request.state, "session_id", None)
    try:
        await revoke_platform_session(db, session_id)
    except (PlatformSessionUnavailable, SQLAlchemyError) as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail="登出服务暂时不可用，请稍后重试") from exc

    try:
        await logout_session(db=db, access_token=token, refresh_token_str=None, cache=cache)
    except SharedSecurityCacheUnavailable:
        # The control database is authoritative. A failed cache hint cannot
        # revive the durably revoked session and must not strand the browser
        # with a cookie that every subsequent request will reject.
        pass
    response = JSONResponse(content={"status": "ok"})
    response.delete_cookie(
        "platform_access_token",
        domain=settings.cookie_domain or None,
        path="/",
    )
    response.delete_cookie(
        "platform_csrf_token",
        domain=settings.cookie_domain or None,
        path="/",
    )
    return response


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard", response_model=DashboardSummary)
async def get_dashboard(
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """平台首页看板数据"""
    now = datetime.now(UTC)

    # Single query with conditional aggregation instead of 5 separate COUNT queries
    counts_row = (
        await db.execute(
            select(
                func.count(Tenant.id).label("total"),
                func.sum(case((Tenant.status == TenantStatus.active, 1), else_=0)).label("active"),
                func.sum(case((Tenant.status == TenantStatus.suspended, 1), else_=0)).label("suspended"),
                func.sum(case((Tenant.status == TenantStatus.terminated, 1), else_=0)).label("terminated"),
            )
        )
    ).one()
    total, active, suspended, terminated = (
        int(counts_row[0] or 0),
        int(counts_row[1] or 0),
        int(counts_row[2] or 0),
        int(counts_row[3] or 0),
    )

    soon_threshold = now + timedelta(days=30)
    expiring_soon = (
        await db.execute(
            select(func.count(Tenant.id)).where(
                Tenant.plan_expires_at != None,  # noqa: E711
                Tenant.plan_expires_at <= soon_threshold,
                Tenant.plan_expires_at > now,
                Tenant.status == TenantStatus.active,
            )
        )
    ).scalar() or 0

    recent_logs = await query_audit_logs(db, limit=5)

    return DashboardSummary(
        total_tenants=total,
        active_tenants=active,
        suspended_tenants=suspended,
        terminated_tenants=terminated,
        expiring_soon=expiring_soon,
        recent_audit_logs=recent_logs,
    )


# ---------------------------------------------------------------------------
# Tenants CRUD
# ---------------------------------------------------------------------------


@router.get("/tenants", response_model=PaginatedResponse)
async def list_tenants(
    db: AsyncSession = Depends(get_db_with_bypass),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: TenantStatus | None = Query(None),
    plan: TenantPlan | None = Query(None),
    search: str | None = Query(None),
    _role: str = Depends(require_role("platform_admin")),
):
    """租户列表（分页、筛选）"""
    stmt = (
        select(Tenant, PlatformTenantOpening.initial_admin_state)
        .outerjoin(PlatformTenantOpening, PlatformTenantOpening.tenant_id == Tenant.id)
        .order_by(Tenant.created_at.desc())
    )
    count_stmt = select(func.count()).select_from(Tenant)

    if status:
        stmt = stmt.where(Tenant.status == status)
        count_stmt = count_stmt.where(Tenant.status == status)
    if plan:
        stmt = stmt.where(Tenant.plan == plan)
        count_stmt = count_stmt.where(Tenant.plan == plan)
    if search:
        filter_expr = Tenant.name.ilike(f"%{search}%") | Tenant.slug.ilike(f"%{search}%")
        stmt = stmt.where(filter_expr)
        count_stmt = count_stmt.where(filter_expr)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    rows = list(result.all())

    return PaginatedResponse(
        items=[
            TenantListRead(
                **TenantRead.model_validate(tenant).model_dump(),
                initial_admin_state=initial_admin_state,
                activation_retryable=(
                    tenant.status != TenantStatus.terminated and initial_admin_state == "pending_activation"
                ),
            )
            for tenant, initial_admin_state in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/tenants", response_model=TenantOpeningRead, status_code=201)
async def create_tenant(
    body: TenantCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=100),
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """初始化品牌租户；初始管理员通过单次链接自行设置密码。"""
    request_hash = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    opening = (
        await db.execute(select(PlatformTenantOpening).where(PlatformTenantOpening.idempotency_key == idempotency_key))
    ).scalar_one_or_none()
    if opening is not None and opening.request_hash != request_hash:
        raise HTTPException(status_code=409, detail="同一幂等键不能用于不同的租户创建请求")

    created_now = False
    if opening is None:
        candidate = PlatformTenantOpening(idempotency_key=idempotency_key, request_hash=request_hash)
        try:
            async with db.begin_nested():
                db.add(candidate)
                await db.flush()
            opening = candidate
            created_now = True
        except IntegrityError:
            opening = (
                await db.execute(
                    select(PlatformTenantOpening).where(PlatformTenantOpening.idempotency_key == idempotency_key)
                )
            ).scalar_one()
            if opening.request_hash != request_hash:
                raise HTTPException(status_code=409, detail="同一幂等键不能用于不同的租户创建请求") from None

    if opening.tenant_id is None:
        try:
            receipt = await BrandTenantInitialization(db).initialize(
                InitializeBrandTenant(
                    name=body.name,
                    admin_name=body.admin_name,
                    admin_email=str(body.admin_email),
                    industry=body.industry,
                    notes=body.notes,
                    opening=PlatformOpening(
                        operator_id="platform-admin",
                        plan_name=body.plan.value,
                        tenant_type=body.tenant_type.value,
                    ),
                )
            )
        except BrandTenantAlreadyExists as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PlanDefinitionUnavailable as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        opening.tenant_id = receipt.tenant_id
        opening.initial_admin_id = receipt.initial_admin_id
        opening.initial_admin_state = receipt.initial_admin_state.value
    elif opening.initial_admin_id is None or opening.initial_admin_state is None:  # pragma: no cover
        raise HTTPException(status_code=500, detail="租户创建幂等回执不完整")

    # 租户初始化是原子边界；链接签发失败不得撤销已经初始化完成的租户。
    await db.commit()
    if _is_pg:
        # SET LOCAL 会在显式 commit 后失效；激活签发仍是受控跨租户操作。
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
    tenant = await db.get(Tenant, opening.tenant_id)
    if tenant is None:  # pragma: no cover
        raise HTTPException(status_code=500, detail="租户初始化结果不可读取")

    activation_url: str | None = None
    activation_retryable = not created_now and opening.initial_admin_state == "pending_activation"
    if created_now:
        try:
            ticket = await InitialAdminActivation(db, AsyncRedisCache()).issue_or_reissue(
                tenant_id=opening.tenant_id,
                operator_id="platform-admin",
                initial_admin_id=opening.initial_admin_id,
            )
            activation_url = ticket.url
        except Exception:
            activation_retryable = True

    tenant_data = TenantRead.model_validate(tenant).model_dump()
    return TenantOpeningRead(
        **tenant_data,
        initial_admin_id=opening.initial_admin_id,
        initial_admin_state=opening.initial_admin_state,
        activation_url=activation_url,
        activation_retryable=activation_retryable,
    )


@router.post(
    "/tenants/{tenant_id}/initial-admin-activation",
    response_model=ActivationLinkRead,
)
async def reissue_initial_admin_activation(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """重新签发初始管理员激活链接；新链接会覆盖旧链接。"""
    tenant_status = await db.scalar(select(Tenant.status).where(Tenant.id == tenant_id).with_for_update())
    if tenant_status == TenantStatus.terminated:
        raise HTTPException(status_code=409, detail="已终止租户不能重新签发管理员激活链接")
    opening = (
        await db.execute(
            select(PlatformTenantOpening)
            .where(
                PlatformTenantOpening.tenant_id == tenant_id,
                PlatformTenantOpening.initial_admin_state == "pending_activation",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if opening is None or opening.initial_admin_id is None:
        raise HTTPException(status_code=409, detail="没有待激活的初始管理员")
    try:
        ticket = await InitialAdminActivation(db, AsyncRedisCache()).issue_or_reissue(
            tenant_id=tenant_id,
            operator_id="platform-admin",
            initial_admin_id=opening.initial_admin_id,
        )
    except InitialAdminNotPending as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ActivationLinkRead(
        initial_admin_id=ticket.initial_admin_id,
        activation_url=ticket.url,
    )


@router.get("/tenants/{tenant_id}", response_model=TenantDetail)
async def get_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """租户详情"""
    # Single query with scalar subqueries instead of 3 separate round-trips
    row = (
        await db.execute(
            select(
                Tenant,
                select(func.count(Account.id))
                .where(Account.tenant_id == tenant_id)
                .correlate(Tenant)
                .scalar_subquery()
                .label("account_count"),
                select(func.count(Organization.id))
                .where(Organization.tenant_id == tenant_id)
                .correlate(Tenant)
                .scalar_subquery()
                .label("organization_count"),
                select(PlatformTenantOpening.initial_admin_state)
                .where(PlatformTenantOpening.tenant_id == tenant_id)
                .correlate(Tenant)
                .scalar_subquery()
                .label("initial_admin_state"),
            ).where(Tenant.id == tenant_id)
        )
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant, account_count, org_count, initial_admin_state = row
    return TenantDetail(
        **{c.name: getattr(tenant, c.name) for c in tenant.__table__.columns},
        account_count=int(account_count or 0),
        organization_count=int(org_count or 0),
        initial_admin_state=initial_admin_state,
        activation_retryable=(tenant.status != TenantStatus.terminated and initial_admin_state == "pending_activation"),
    )


@router.patch("/tenants/{tenant_id}", response_model=TenantRead)
async def update_tenant(
    tenant_id: uuid.UUID,
    body: TenantUpdate,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """更新租户信息"""
    try:
        tenant = await update_tenant_service(
            db,
            tenant_id,
            name=body.name,
            industry=body.industry,
            notes=body.notes,
            quota=body.quota,
            enabled_features=body.enabled_features,
            actor_id="platform-admin",
        )
    except TenantTypeTransitionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.patch("/tenants/{tenant_id}/status", response_model=TenantRead)
async def update_tenant_status(
    tenant_id: uuid.UUID,
    body: TenantStatusUpdate,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """租户状态变更（暂停/恢复/终止）"""
    try:
        tenant = await transition_tenant_status(
            db,
            tenant_id=tenant_id,
            target_status=body.status,
            operator_id="platform-admin",
        )
    except TenantStatusTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.delete("/tenants/{tenant_id}", status_code=204)
async def delete_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """软删除租户（设为 terminated）"""
    tenant = await terminate_tenant(db, tenant_id=tenant_id, operator_id="platform-admin")
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")


# ---------------------------------------------------------------------------
# Audit Logs
# ---------------------------------------------------------------------------


@router.get("/audit-logs", response_model=list[AuditLogRead])
async def list_audit_logs(
    db: AsyncSession = Depends(get_db_with_bypass),
    start_time: datetime | None = Query(None),
    end_time: datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    _role: str = Depends(require_role("platform_admin")),
):
    """查询审计记录（支持时间范围过滤）"""
    return await query_audit_logs(db, start_time=start_time, end_time=end_time, limit=limit)


# ---------------------------------------------------------------------------
# Plan Definitions
# ---------------------------------------------------------------------------


class PlanCreate(BaseModel):
    name: TenantPlan
    display_name: str
    description: str | None = None
    price_yearly: int = 0
    quota_defaults: dict | None = None
    feature_flags: dict | None = None
    is_active: bool = True
    sort_order: int = 0

    @field_validator("feature_flags")
    @classmethod
    def validate_features(cls, value: dict | None) -> dict | None:
        return validate_feature_flags(value) if value is not None else None

    @field_validator("quota_defaults")
    @classmethod
    def validate_quota(cls, value: dict | None) -> dict | None:
        return validate_quota_config(value) if value is not None else None


class PlanUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    price_yearly: int | None = None
    quota_defaults: dict | None = None
    feature_flags: dict | None = None
    is_active: bool | None = None
    sort_order: int | None = None

    @field_validator("feature_flags")
    @classmethod
    def validate_features(cls, value: dict | None) -> dict | None:
        return validate_feature_flags(value) if value is not None else None

    @field_validator("quota_defaults")
    @classmethod
    def validate_quota(cls, value: dict | None) -> dict | None:
        return validate_quota_config(value) if value is not None else None


class PlanRead(BaseModel):
    id: str
    name: str
    display_name: str
    description: str | None
    price_yearly: int
    quota_defaults: dict | None
    feature_flags: dict | None
    is_active: bool
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}


class AssignPlanRequest(BaseModel):
    plan_id: str
    expires_on: date | None = None
    override_quota: dict | None = None

    @field_validator("override_quota")
    @classmethod
    def validate_quota(cls, value: dict | None) -> dict | None:
        return validate_quota_config(value) if value is not None else None


BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def plan_expiry_for_business_date(expires_on: date) -> datetime:
    """Return the inclusive end of a China business day as a UTC instant."""
    return datetime.combine(expires_on, time.max, tzinfo=BUSINESS_TIMEZONE).astimezone(UTC)


class QuotaUsageItem(BaseModel):
    tenant_id: str
    tenant_name: str
    plan: str
    quota: dict | None
    status: str
    plan_expires_at: datetime | None = None
    read_only: bool
    quota_enforcement_state: Literal["ready", "reconciliation_pending"]
    enforcement_ready: bool
    reconciled_at: datetime | None = None
    source_revision: str | None = None
    rollout_phase: str | None = None
    rollout_source_revision: str | None = None
    usage: dict = {}


@router.get("/plans", response_model=list[PlanRead])
async def list_plans(
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """套餐定义列表"""
    result = await db.execute(select(PlanDefinition).order_by(PlanDefinition.sort_order))
    return list(result.scalars().all())


@router.get("/plans/active", response_model=list[PlanRead])
async def list_active_plans(
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """平台开租户表单可选择的实时有效套餐定义。"""
    supported_names = tuple(item.value for item in TenantPlan)
    result = await db.execute(
        select(PlanDefinition)
        .where(PlanDefinition.is_active.is_(True), PlanDefinition.name.in_(supported_names))
        .order_by(PlanDefinition.sort_order)
    )
    return list(result.scalars().all())


@router.post("/plans", response_model=PlanRead, status_code=201)
async def create_plan(
    body: PlanCreate,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """创建套餐定义"""
    plan = PlanDefinition(**body.model_dump())
    db.add(plan)
    await db.flush()

    await write_audit_log(db, "platform-admin", "platform", "create_plan", f"plan:{plan.name}")
    await db.flush()
    return plan


@router.patch("/plans/{plan_id}", response_model=PlanRead)
async def update_plan(
    plan_id: str,
    body: PlanUpdate,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """更新套餐定义"""
    plan = (await db.execute(select(PlanDefinition).where(PlanDefinition.id == plan_id))).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(plan, key, value)
    await db.flush()

    await write_audit_log(db, "platform-admin", "platform", "update_plan", f"plan:{plan.name}")
    await db.flush()
    return plan


@router.post("/tenants/{tenant_id}/assign-plan", response_model=TenantRead)
async def assign_plan_to_tenant(
    tenant_id: uuid.UUID,
    body: AssignPlanRequest,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """为租户分配套餐"""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    plan = (await db.execute(select(PlanDefinition).where(PlanDefinition.id == body.plan_id))).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan definition not found")
    if not plan.is_active:
        raise HTTPException(status_code=409, detail="Plan definition is inactive")
    try:
        plan_quota = validate_quota_config(plan.quota_defaults)
        plan_features = validate_feature_flags(plan.feature_flags)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Plan definition configuration is invalid") from exc

    try:
        tenant.plan = TenantPlan(plan.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Unsupported plan definition name") from exc
    tenant.quota = body.override_quota if body.override_quota is not None else plan_quota
    tenant.enabled_features = plan_features
    if "expires_on" in body.model_fields_set:
        tenant.plan_expires_at = plan_expiry_for_business_date(body.expires_on) if body.expires_on else None
    await db.flush()

    await write_audit_log(db, "platform-admin", str(tenant_id), "assign_plan", f"plan:{plan.name}")
    await db.flush()
    return tenant


# ---------------------------------------------------------------------------
# Quota Usage
# ---------------------------------------------------------------------------


@router.get("/quota-usage", response_model=list[QuotaUsageItem])
async def list_quota_usage(
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """全租户额度使用汇总（读取事务化累计状态，不扫描业务事实表）。"""
    result = await db.execute(
        select(Tenant, TenantQuotaUsage, QuotaRolloutState)
        .outerjoin(TenantQuotaUsage, TenantQuotaUsage.tenant_id == Tenant.id)
        .outerjoin(QuotaRolloutState, QuotaRolloutState.id == 1)
        .where(Tenant.status != TenantStatus.terminated)
        .order_by(Tenant.name)
    )

    items = []
    for t, usage, rollout_state in result.all():
        enforcement_ready = is_quota_usage_effectively_ready(usage, rollout_state)
        items.append(
            QuotaUsageItem(
                tenant_id=str(t.id),
                tenant_name=t.name,
                plan=t.plan.value,
                quota=t.quota,
                status=t.status.value,
                plan_expires_at=t.plan_expires_at,
                read_only=is_plan_expired(t.plan_expires_at),
                quota_enforcement_state="ready" if enforcement_ready else "reconciliation_pending",
                enforcement_ready=enforcement_ready,
                reconciled_at=usage.reconciled_at if usage else None,
                source_revision=usage.source_revision if usage else None,
                rollout_phase=rollout_state.phase.value if rollout_state else None,
                rollout_source_revision=rollout_state.source_revision if rollout_state else None,
                usage={
                    "campaigns": usage.campaigns if usage else 0,
                    "products": usage.products if usage else 0,
                    "accounts": usage.accounts if usage else 0,
                    "codes": usage.codes if usage else 0,
                    "scans": usage.scans if usage else 0,
                },
            )
        )
    return items


# ---------------------------------------------------------------------------
# System Config
# ---------------------------------------------------------------------------


class PlatformConfigRead(BaseModel):
    feature_flags: dict = {}
    notification_settings: dict = {}
    compliance_defaults: dict = {}


class PlatformConfigUpdate(BaseModel):
    feature_flags: dict | None = None
    notification_settings: dict | None = None
    compliance_defaults: dict | None = None


_DEFAULTS = {
    "feature_flags": {"ai_assistant": True, "risk_module": True, "channel_portal": True},
    "notification_settings": {"email_enabled": True, "webhook_enabled": True},
    "compliance_defaults": {"data_retention_days": 365},
}


@router.get("/config", response_model=PlatformConfigRead)
async def get_platform_config(
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """读取平台级系统配置"""
    from app.models.platform_config import PlatformConfig

    result = await db.execute(select(PlatformConfig))
    configs = result.scalars().all()
    data = {c.key: c.value for c in configs}
    return PlatformConfigRead(
        feature_flags=data.get("feature_flags", _DEFAULTS["feature_flags"]),
        notification_settings=data.get("notification_settings", _DEFAULTS["notification_settings"]),
        compliance_defaults=data.get("compliance_defaults", _DEFAULTS["compliance_defaults"]),
    )


@router.patch("/config", response_model=PlatformConfigRead)
async def update_platform_config(
    body: PlatformConfigUpdate,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """更新系统配置"""
    from app.models.platform_config import PlatformConfig

    for key, value in body.model_dump(exclude_unset=True).items():
        existing = await db.execute(select(PlatformConfig).where(PlatformConfig.key == key))
        config = existing.scalar_one_or_none()
        if config:
            config.value = value
        else:
            db.add(PlatformConfig(key=key, value=value))
    await db.flush()

    await write_audit_log(db, "platform-admin", "platform", "update_config", "platform_config")
    await db.flush()

    return await get_platform_config(db=db, _role=_role)


# ---------------------------------------------------------------------------
# Health Metrics
# ---------------------------------------------------------------------------


class HealthMetricsRead(BaseModel):
    tenant_id: str
    tenant_name: str
    health_score: int
    health_status: str
    last_scan_at: datetime | None
    scans_last_7d: int
    scans_last_30d: int
    active_campaigns: int
    days_until_expiry: int | None
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


class HealthOverview(BaseModel):
    healthy: int
    warning: int
    critical: int
    dormant: int


@router.get("/health-overview", response_model=HealthOverview)
async def get_health_overview(
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """健康度概览"""
    result = await db.execute(select(TenantHealthMetrics))
    metrics = list(result.scalars().all())
    counts = {"healthy": 0, "warning": 0, "critical": 0, "dormant": 0}
    for m in metrics:
        counts[m.health_status] = counts.get(m.health_status, 0) + 1
    return HealthOverview(**counts)


@router.get("/health-tenants", response_model=list[HealthMetricsRead])
async def list_health_tenants(
    db: AsyncSession = Depends(get_db_with_bypass),
    status: str | None = Query(None),
    _role: str = Depends(require_role("platform_admin")),
):
    """带健康指标的租户列表"""
    stmt = (
        select(TenantHealthMetrics, Tenant.name)
        .join(Tenant, TenantHealthMetrics.tenant_id == Tenant.id)
        .order_by(TenantHealthMetrics.health_score.asc())
    )
    if status:
        stmt = stmt.where(TenantHealthMetrics.health_status == status)
    result = await db.execute(stmt)
    rows = result.all()
    return [
        HealthMetricsRead(
            tenant_id=str(m.tenant_id),
            tenant_name=name,
            health_score=m.health_score,
            health_status=m.health_status,
            last_scan_at=m.last_scan_at,
            scans_last_7d=m.scans_last_7d,
            scans_last_30d=m.scans_last_30d,
            active_campaigns=m.active_campaigns,
            days_until_expiry=m.days_until_expiry,
            last_login_at=m.last_login_at,
        )
        for m, name in rows
    ]


@router.post("/health/refresh")
async def trigger_health_refresh(
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """触发批量健康度重算"""
    count = await refresh_all_health_metrics(db)
    await write_audit_log(db, "platform-admin", "platform", "refresh_health", f"tenants:{count}")
    await db.flush()
    return {"refreshed": count}


# ---------------------------------------------------------------------------
# Service Providers (Agency tenants)
# ---------------------------------------------------------------------------


class ProviderRead(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    managed_client_ids: list[str] = []


@router.get("/service-providers", response_model=list[ProviderRead])
async def list_service_providers(
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """服务商列表（agency 类型租户）"""
    from sqlalchemy import select

    from app.models.tenant import AgencyAuthorization, TenantType

    result = await db.execute(
        select(Tenant)
        .where(Tenant.status == TenantStatus.active, Tenant.tenant_type == TenantType.agency)
        .order_by(Tenant.name)
    )
    agencies = list(result.scalars().all())

    # Fetch managed clients for each agency
    providers = []
    for agency in agencies:
        auth_result = await db.execute(
            select(AgencyAuthorization.client_tenant_id).where(
                AgencyAuthorization.agency_tenant_id == agency.id,
                AgencyAuthorization.status == "active",
            )
        )
        client_ids = [str(row[0]) for row in auth_result.all()]
        providers.append(
            ProviderRead(
                id=str(agency.id),
                name=agency.name,
                slug=agency.slug,
                status=agency.status.value,
                managed_client_ids=client_ids,
            )
        )
    return providers
