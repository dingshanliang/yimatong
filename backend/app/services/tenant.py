import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Account, Organization, Tenant, TenantPlan, TenantStatus
from app.utils.security import hash_password


def _generate_slug(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9-]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug or slug == "-":
        slug = f"tenant-{uuid.uuid4().hex[:8]}"
    return slug[:50]


async def create_tenant(
    db: AsyncSession,
    name: str,
    slug: str | None,
    plan: str,
    admin_email: str,
    admin_name: str,
    admin_password: str,
) -> Tenant:
    if not slug:
        slug = _generate_slug(name)

    tenant = Tenant(
        name=name,
        slug=slug,
        status=TenantStatus.active,
        plan=TenantPlan(plan),
        quota={"max_codes": 10000, "max_campaigns": 50, "max_accounts": 10},
    )
    db.add(tenant)
    await db.flush()

    org = Organization(tenant_id=tenant.id, name=f"{name} 默认组织")
    db.add(org)
    await db.flush()

    hashed = hash_password(admin_password)
    account = Account(
        tenant_id=tenant.id,
        organization_id=org.id,
        email=admin_email,
        hashed_password=hashed,
        name=admin_name,
    )
    db.add(account)
    await db.flush()

    await db.flush()
    await db.refresh(tenant)
    return tenant


async def get_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none()


async def update_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str | None = None,
    quota: dict | None = None,
    compliance_settings: dict | None = None,
    plan_expires_at: datetime | None = None,
    onboarding_progress: dict | None = None,
    enabled_features: dict | None = None,
) -> Tenant | None:
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        return None
    if name:
        tenant.name = name
    if quota is not None:
        tenant.quota = quota
    if compliance_settings is not None:
        tenant.compliance_settings = compliance_settings
    if plan_expires_at is not None:
        tenant.plan_expires_at = plan_expires_at
    if onboarding_progress is not None:
        tenant.onboarding_progress = onboarding_progress
    if enabled_features is not None:
        tenant.enabled_features = enabled_features
    await db.flush()
    await db.refresh(tenant)
    return tenant


async def soft_delete_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        return False
    tenant.status = TenantStatus.terminated
    await db.flush()
    return True
