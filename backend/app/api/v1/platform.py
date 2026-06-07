import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_with_bypass
from app.models.plan import PlanDefinition
from app.models.tenant import Account, Organization, Tenant, TenantPlan, TenantStatus
from app.services.audit import query_audit_logs, write_audit_log
from app.services.redis_cache import AsyncRedisCache
from app.utils.auth_rbac import require_role
from app.utils.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PlatformLoginRequest(BaseModel):
    email: str
    password: str


class PlatformTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuditLogRead(BaseModel):
    id: uuid.UUID
    operator_id: str
    target_tenant_id: str
    action: str
    resource: str
    timestamp: datetime

    model_config = {"from_attributes": True}


class TenantCreate(BaseModel):
    name: str
    slug: str
    plan: TenantPlan = TenantPlan.free
    industry: str | None = None
    notes: str | None = None
    admin_email: EmailStr
    admin_name: str
    admin_password: str


class TenantUpdate(BaseModel):
    name: str | None = None
    plan: TenantPlan | None = None
    industry: str | None = None
    notes: str | None = None
    quota: dict | None = None
    enabled_features: dict | None = None
    plan_expires_at: datetime | None = None


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


class TenantDetail(TenantRead):
    account_count: int = 0
    organization_count: int = 0


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


@router.post("/auth/login", response_model=PlatformTokenResponse)
async def platform_login(body: PlatformLoginRequest, request: Request):
    """平台管理员独立认证路径"""
    # IP 速率限制
    client_ip = (
        request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
        .split(",")[0]
        .strip()
    )
    cache = AsyncRedisCache()
    allowed, _ = await cache.rate_limit_check(
        f"platform_login_rate:{client_ip}", max_attempts=10, window_seconds=300
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="尝试过于频繁", headers={"Retry-After": "300"})

    if not settings.platform_admin_password_hash:
        raise HTTPException(status_code=500, detail="Platform admin not configured")
    if (
        body.email != settings.platform_admin_email
        or not verify_password(body.password, settings.platform_admin_password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(
        tenant_id="platform",
        account_id="platform-admin",
        role="platform_admin",
    )

    # 审计日志
    try:
        from app.core.database import async_session_factory

        async with async_session_factory() as db:
            await write_audit_log(db, "platform-admin", "platform", "platform_login", f"ip:{client_ip}")
            await db.commit()
    except Exception:
        pass  # 审计失败不影响登录

    response = JSONResponse(content={"access_token": token, "token_type": "bearer"})
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
    total, active, suspended, terminated = int(counts_row[0] or 0), int(counts_row[1] or 0), int(counts_row[2] or 0), int(counts_row[3] or 0)

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


from app.schemas.common import PaginatedResponse

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
    stmt = select(Tenant).order_by(Tenant.created_at.desc())
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
    items = list(result.scalars().all())

    return PaginatedResponse(
        items=[TenantRead.model_validate(t) for t in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/tenants", response_model=TenantRead, status_code=201)
async def create_tenant(
    body: TenantCreate,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """创建租户（同时创建 Organization + 初始 admin 账号）"""
    # 检查 slug 唯一性
    existing = await db.execute(select(Tenant).where(Tenant.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Slug '{body.slug}' already exists")

    tenant = Tenant(
        name=body.name,
        slug=body.slug,
        plan=body.plan,
        industry=body.industry,
        notes=body.notes,
    )
    db.add(tenant)
    await db.flush()

    org = Organization(
        tenant_id=tenant.id,
        name=body.name,
    )
    db.add(org)
    await db.flush()

    account = Account(
        tenant_id=tenant.id,
        organization_id=org.id,
        email=body.admin_email,
        hashed_password=hash_password(body.admin_password),
        name=body.admin_name,
    )
    db.add(account)
    await db.flush()

    # 创建 admin 角色
    from app.models.tenant import Role

    admin_role = Role(
        tenant_id=tenant.id,
        name="admin",
        description="品牌管理员",
    )
    db.add(admin_role)
    await db.flush()

    # 关联角色
    from app.models.tenant import account_roles

    await db.execute(account_roles.insert().values(account_id=account.id, role_id=admin_role.id))

    await write_audit_log(
        db,
        operator_id="platform-admin",
        target_tenant_id=str(tenant.id),
        action="create_tenant",
        resource=f"tenant:{tenant.slug}",
    )

    await db.flush()
    return tenant


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
            ).where(Tenant.id == tenant_id)
        )
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant, account_count, org_count = row
    return TenantDetail(
        **{c.name: getattr(tenant, c.name) for c in tenant.__table__.columns},
        account_count=int(account_count or 0),
        organization_count=int(org_count or 0),
    )


@router.patch("/tenants/{tenant_id}", response_model=TenantRead)
async def update_tenant(
    tenant_id: uuid.UUID,
    body: TenantUpdate,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """更新租户信息"""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(tenant, key, value)

    await db.flush()

    await write_audit_log(
        db,
        operator_id="platform-admin",
        target_tenant_id=str(tenant_id),
        action="update_tenant",
        resource=f"tenant:{tenant.slug}",
    )

    await db.flush()
    return tenant


@router.patch("/tenants/{tenant_id}/status", response_model=TenantRead)
async def update_tenant_status(
    tenant_id: uuid.UUID,
    body: TenantStatusUpdate,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """租户状态变更（暂停/恢复/终止）"""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    old_status = tenant.status
    tenant.status = body.status
    await db.flush()

    await write_audit_log(
        db,
        operator_id="platform-admin",
        target_tenant_id=str(tenant_id),
        action=f"status_change:{old_status.value}->{body.status.value}",
        resource=f"tenant:{tenant.slug}",
    )

    await db.flush()
    return tenant


@router.delete("/tenants/{tenant_id}", status_code=204)
async def delete_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """软删除租户（设为 terminated）"""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.status = TenantStatus.terminated

    await write_audit_log(
        db,
        operator_id="platform-admin",
        target_tenant_id=str(tenant_id),
        action="delete_tenant",
        resource=f"tenant:{tenant.slug}",
    )

    await db.flush()


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
    name: str
    display_name: str
    description: str | None = None
    price_yearly: int = 0
    quota_defaults: dict | None = None
    feature_flags: dict | None = None
    is_active: bool = True
    sort_order: int = 0


class PlanUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    price_yearly: int | None = None
    quota_defaults: dict | None = None
    feature_flags: dict | None = None
    is_active: bool | None = None
    sort_order: int | None = None


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
    expires_at: datetime | None = None
    override_quota: dict | None = None


class QuotaUsageItem(BaseModel):
    tenant_id: str
    tenant_name: str
    plan: str
    quota: dict | None
    status: str
    usage: dict = {}


@router.get("/plans", response_model=list[PlanRead])
async def list_plans(
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """套餐定义列表"""
    result = await db.execute(select(PlanDefinition).order_by(PlanDefinition.sort_order))
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

    # Map plan name to TenantPlan enum
    plan_enum_map = {"free": TenantPlan.free, "starter": TenantPlan.starter, "pro": TenantPlan.pro, "enterprise": TenantPlan.enterprise}
    tenant.plan = plan_enum_map.get(plan.name, TenantPlan.free)
    tenant.quota = body.override_quota or plan.quota_defaults or {}
    tenant.enabled_features = plan.feature_flags or {}
    if body.expires_at:
        tenant.plan_expires_at = body.expires_at
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
    """全租户额度使用汇总（含实际用量）"""
    from sqlalchemy import func, select
    from app.models.campaign import Campaign
    from app.models.code import CodeItem
    from app.models.product import Product
    from app.models.tenant import Account

    result = await db.execute(
        select(Tenant).where(Tenant.status != TenantStatus.terminated).order_by(Tenant.name)
    )
    tenants = list(result.scalars().all())

    items = []
    for t in tenants:
        tid = t.id
        campaigns = (
            await db.execute(select(func.count(Campaign.id)).where(Campaign.tenant_id == tid))
        ).scalar() or 0
        products = (
            await db.execute(select(func.count(Product.id)).where(Product.tenant_id == tid))
        ).scalar() or 0
        accounts = (
            await db.execute(select(func.count(Account.id)).where(Account.tenant_id == tid))
        ).scalar() or 0
        codes = (
            await db.execute(select(func.count(CodeItem.id)).where(CodeItem.tenant_id == tid))
        ).scalar() or 0

        items.append(
            QuotaUsageItem(
                tenant_id=str(t.id),
                tenant_name=t.name,
                plan=t.plan.value,
                quota=t.quota,
                status=t.status.value,
                usage={
                    "campaigns": campaigns,
                    "products": products,
                    "accounts": accounts,
                    "codes": codes,
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


from app.models.tenant_health import TenantHealthMetrics
from app.services.tenant_health import refresh_all_health_metrics


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
            select(AgencyAuthorization.client_tenant_id)
            .where(
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
