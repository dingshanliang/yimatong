import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant, TenantStatus, TenantType
from app.modules.brand_tenant_initialization import (
    BrandTenantInitialization,
    InitializeBrandTenant,
    TrustedAutomationOpening,
)


async def _audit(
    db: AsyncSession,
    operator_id: str,
    tenant_id: str,
    action: str,
    resource: str,
) -> None:
    """写入审计日志，失败不影响主流程。"""
    try:
        from app.services.audit import write_audit_log

        await write_audit_log(
            db,
            operator_id=operator_id,
            target_tenant_id=tenant_id,
            action=action,
            resource=resource,
        )
    except Exception:
        pass


async def create_tenant(
    db: AsyncSession,
    name: str,
    slug: str | None,
    plan: str,
    admin_email: str,
    admin_name: str,
    admin_password: str,
    industry: str | None = None,
    notes: str | None = None,
    template_id: int | None = None,
    tenant_type: str = "brand",
) -> Tenant:
    """兼容旧调用方的受信自动化 adapter。

    新业务代码应直接依赖 BrandTenantInitialization interface。页面模板不属于
    “租户初始化完成”的原子范围，调用方需要在初始化成功后显式创建。
    """
    if tenant_type != "brand":
        raise ValueError("BrandTenantInitialization 仅支持品牌租户")
    if template_id is not None:
        raise ValueError("行业页面模板不属于租户初始化，请在初始化成功后单独创建")

    try:
        receipt = await BrandTenantInitialization(db).initialize(
            InitializeBrandTenant(
                name=name,
                admin_name=admin_name,
                admin_email=admin_email,
                industry=industry,
                notes=notes,
                opening=TrustedAutomationOpening(
                    actor="legacy:create_tenant",
                    chosen_password=admin_password,
                    plan_name=plan,
                    stable_tenant_key=slug,
                ),
            )
        )
    except Exception as exc:
        from app.modules.brand_tenant_initialization.interface import BrandTenantInitializationError

        if isinstance(exc, BrandTenantInitializationError):
            raise ValueError(str(exc)) from exc
        raise

    tenant = await db.get(Tenant, receipt.tenant_id)
    if tenant is None:  # pragma: no cover - protected by initialization postcondition
        raise RuntimeError("租户初始化结果不可读取")
    return tenant


async def get_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none()


async def update_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str | None = None,
    industry: str | None = None,
    notes: str | None = None,
    quota: dict | None = None,
    compliance_settings: dict | None = None,
    plan_expires_at: datetime | None = None,
    onboarding_progress: dict | None = None,
    enabled_features: dict | None = None,
    tenant_type: str | None = None,
    categories: list[str] | None = None,
    brand_profile: dict | None = None,
) -> Tenant | None:
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        return None
    if name:
        tenant.name = name
    if industry is not None:
        tenant.industry = industry
    if notes is not None:
        tenant.notes = notes
    if quota is not None:
        tenant.quota = quota
    if compliance_settings is not None:
        existing = dict(tenant.compliance_settings or {})
        existing.update(compliance_settings)
        tenant.compliance_settings = existing
    if plan_expires_at is not None:
        tenant.plan_expires_at = plan_expires_at
    if onboarding_progress is not None:
        tenant.onboarding_progress = onboarding_progress
    if enabled_features is not None:
        tenant.enabled_features = enabled_features
    if tenant_type is not None:
        tenant.tenant_type = TenantType(tenant_type)
    if categories is not None:
        tenant.categories = categories
    if brand_profile is not None:
        existing = dict(tenant.brand_profile or {})
        existing.update(brand_profile)
        tenant.brand_profile = existing
    await db.flush()
    await db.refresh(tenant)
    await _audit(db, "system", str(tenant.id), "tenant_update", f"tenant:{tenant_id}")
    return tenant


ONBOARDING_STEPS = [
    "create_product",
    "create_batch",
    "create_page",
    "create_campaign",
    "activate",
]


def get_onboarding_progress_data(tenant: Tenant) -> dict:
    """Compute onboarding progress from tenant state."""
    progress = tenant.onboarding_progress or {}
    completed = progress.get("completed_steps", [])
    return {
        "steps": ONBOARDING_STEPS,
        "completed_steps": completed,
        "current_step": next((s for s in ONBOARDING_STEPS if s not in completed), None),
        "is_complete": all(s in completed for s in ONBOARDING_STEPS),
    }


async def complete_onboarding_step(db: AsyncSession, tenant_id: uuid.UUID, step: str) -> Tenant | None:
    """Mark an onboarding step as completed for a tenant."""
    if step not in ONBOARDING_STEPS:
        raise ValueError(f"Invalid step: {step}")
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        return None
    progress = tenant.onboarding_progress or {}
    completed = set(progress.get("completed_steps", []))
    completed.add(step)
    progress["completed_steps"] = list(completed)
    tenant.onboarding_progress = progress
    await db.flush()
    await db.refresh(tenant)
    return tenant


async def soft_delete_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        return False
    tenant.status = TenantStatus.terminated
    await db.flush()
    await _audit(db, "system", str(tenant.id), "tenant_delete", f"tenant:{tenant_id}")
    return True
