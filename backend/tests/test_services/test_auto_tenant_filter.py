"""A2-004: 自动查询过滤 service 层测试 — 覆盖 3 种模型"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Role, Tenant
from app.services.organization import create_account, create_organization, list_accounts, list_organizations
from app.services.tenant import create_tenant


@pytest.fixture
async def tenant_a(db: AsyncSession) -> Tenant:
    return await create_tenant(
        db,
        name="租户A",
        slug=None,
        plan="free",
        admin_email="a@test.com",
        admin_name="A",
        admin_password="Pass1234",
    )


@pytest.fixture
async def tenant_b(db: AsyncSession) -> Tenant:
    return await create_tenant(
        db,
        name="租户B",
        slug=None,
        plan="free",
        admin_email="b@test.com",
        admin_name="B",
        admin_password="Pass1234",
    )


class TestOrganizationAutoFilter:
    @pytest.mark.anyio
    async def test_list_only_own_tenant_orgs(self, db: AsyncSession, tenant_a: Tenant, tenant_b: Tenant):
        await create_organization(db, tenant_a.id, "A部门", None)
        await create_organization(db, tenant_b.id, "B部门", None)

        result = await list_organizations(db, tenant_a.id)
        a_orgs = result["items"]
        assert all(o.tenant_id == tenant_a.id for o in a_orgs)
        # create_tenant seeds a default org, so we have >= 2 (seeded + created)
        assert len(a_orgs) == 2


class TestAccountAutoFilter:
    @pytest.mark.anyio
    async def test_list_only_own_tenant_accounts(self, db: AsyncSession, tenant_a: Tenant, tenant_b: Tenant):
        org_a = await create_organization(db, tenant_a.id, "A部门", None)
        org_b = await create_organization(db, tenant_b.id, "B部门", None)

        await create_account(db, tenant_a.id, org_a.id, "a_user@test.com", "用户A", "Pass1234")
        await create_account(db, tenant_b.id, org_b.id, "b_user@test.com", "用户B", "Pass1234")

        result = await list_accounts(db, tenant_a.id)
        a_accounts = result["items"]
        assert all(acc.tenant_id == tenant_a.id for acc in a_accounts)
        # create_tenant seeds an admin account, so we have >= 2 (seeded + created)
        assert len(a_accounts) == 2


class TestRoleAutoFilter:
    @pytest.mark.anyio
    async def test_roles_filtered_by_tenant(self, db: AsyncSession, tenant_a: Tenant, tenant_b: Tenant):
        from sqlalchemy import select

        role_a = Role(tenant_id=tenant_a.id, name="A管理员")
        role_b = Role(tenant_id=tenant_b.id, name="B管理员")
        db.add_all([role_a, role_b])
        await db.flush()

        result = await db.execute(select(Role).where(Role.tenant_id == tenant_a.id))
        roles = list(result.scalars().all())
        assert all(r.tenant_id == tenant_a.id for r in roles)
        assert {role.name for role in roles} == {"A管理员", "admin", "operator", "viewer"}
