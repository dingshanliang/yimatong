import pytest
from sqlalchemy import func, select

from app.models.audit import PlatformAuditLog
from app.models.plan import PlanDefinition
from app.models.product import Product
from app.models.tenant import Permission, Role, Tenant, role_permissions
from app.modules.brand_tenant_initialization import (
    BrandTenantInitialization,
    InitializeBrandTenant,
    TrustedAutomationOpening,
)
from app.utils.auth_rbac import WEB_ROLE_PERMISSIONS

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def test_brand_tenant_initialization_contract_on_real_postgres(bypass_session):
    plan = (await bypass_session.execute(select(PlanDefinition).where(PlanDefinition.name == "free"))).scalar_one()
    plan.quota_defaults = {"max_codes": 1234, "max_campaigns": 7, "max_accounts": 3}
    plan.feature_flags = {"risk_module": False}
    await bypass_session.flush()

    receipt = await BrandTenantInitialization(bypass_session).initialize(
        InitializeBrandTenant(
            name="PG 初始化验收租户",
            admin_name="验收管理员",
            admin_email="pg-init@example.com",
            industry="食品饮料",
            opening=TrustedAutomationOpening(
                actor="acceptance",
                chosen_password="AcceptancePass123",
                stable_tenant_key="pg-init-acceptance",
            ),
        )
    )

    tenant = await bypass_session.get(Tenant, receipt.tenant_id)
    role = (
        await bypass_session.execute(select(Role).where(Role.tenant_id == receipt.tenant_id, Role.name == "admin"))
    ).scalar_one()
    permission_codes = set(
        (
            await bypass_session.execute(
                select(Permission.code)
                .join(role_permissions, role_permissions.c.permission_id == Permission.id)
                .where(role_permissions.c.role_id == role.id)
            )
        )
        .scalars()
        .all()
    )

    assert tenant.quota == {"max_codes": 1234, "max_campaigns": 7, "max_accounts": 3}
    assert tenant.enabled_features == {"risk_module": False}
    assert tenant.categories
    assert permission_codes == set(WEB_ROLE_PERMISSIONS["admin"])
    assert (
        await bypass_session.scalar(
            select(func.count())
            .select_from(PlatformAuditLog)
            .where(
                PlatformAuditLog.target_tenant_id == str(receipt.tenant_id),
                PlatformAuditLog.action == "brand_tenant_initialized",
            )
        )
        == 1
    )
    assert (
        await bypass_session.scalar(
            select(func.count()).select_from(Product).where(Product.tenant_id == receipt.tenant_id)
        )
        == 0
    )
