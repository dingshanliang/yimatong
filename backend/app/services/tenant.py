import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.categories import get_default_categories
from app.models.tenant import Account, Organization, Tenant, TenantPlan, TenantStatus, TenantType
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
    industry: str | None = None,
    notes: str | None = None,
    template_id: int | None = None,
    tenant_type: str = "brand",
) -> Tenant:
    if not slug:
        slug = _generate_slug(name)

    tenant = Tenant(
        name=name,
        slug=slug,
        status=TenantStatus.active,
        plan=TenantPlan(plan),
        tenant_type=TenantType(tenant_type),
        industry=industry,
        notes=notes,
        quota={"max_codes": 10000, "max_campaigns": 50, "max_accounts": 10},
        categories=get_default_categories(industry),
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

    # 应用行业模板（如果指定）
    if template_id is not None:
        from app.models.page import PageTemplate, PageVersion, PageVersionStatus
        from app.services.industry_templates import ALL_TEMPLATES

        if 0 <= template_id < len(ALL_TEMPLATES):
            template_def = ALL_TEMPLATES[template_id]
            tmpl = PageTemplate(
                tenant_id=tenant.id,
                name=template_def["name"],
                template_type=template_def["template_type"],
                status="draft",
            )
            db.add(tmpl)
            await db.flush()

            version = PageVersion(
                tenant_id=tenant.id,
                page_template_id=tmpl.id,
                version_number=1,
                config_json=template_def["config_json"],
                status=PageVersionStatus.draft,
            )
            db.add(version)
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
    industry: str | None = None,
    notes: str | None = None,
    quota: dict | None = None,
    compliance_settings: dict | None = None,
    plan_expires_at: datetime | None = None,
    onboarding_progress: dict | None = None,
    enabled_features: dict | None = None,
    tenant_type: str | None = None,
    categories: list[str] | None = None,
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
        existing = tenant.compliance_settings or {}
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
