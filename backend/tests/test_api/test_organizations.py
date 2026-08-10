"""A2-002: 组织与账号 CRUD 验收测试"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.audit import PlatformAuditLog
from app.models.tenant import Account, Organization
from app.utils.security import create_access_token, decode_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    from app.utils.security import create_access_token

    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=test-platform-csrf",
        "Origin": "http://localhost:3002",
        "X-Platform-CSRF": "test-platform-csrf",
    }


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _auth_headers(tenant_id: str) -> dict:
    token = create_access_token(tenant_id, "00000000-0000-0000-0000-000000000001", "admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def tenant_with_auth(client: AsyncClient):
    """创建租户并返回 (tenant_id, auth_headers)"""
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "Test",
            "admin_email": "a@b.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    assert resp.status_code == 201
    tenant_id = resp.json()["id"]
    return tenant_id, _auth_headers(tenant_id)


class TestOrganizationCRUD:
    @pytest.mark.anyio
    async def test_create_organization(self, client: AsyncClient, tenant_with_auth):
        tenant_id, headers = tenant_with_auth
        resp = await client.post("/api/v1/organizations", json={"name": "研发部"}, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "研发部"
        assert data["tenant_id"] == tenant_id

    @pytest.mark.anyio
    async def test_create_organization_rejects_parent_from_another_tenant(self, client: AsyncClient, tenant_with_auth):
        first_tenant_id, first_headers = tenant_with_auth
        second_resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "Other Tenant",
                "admin_email": "other@example.com",
                "admin_name": "Other Admin",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        assert second_resp.status_code == 201
        second_headers = _auth_headers(second_resp.json()["id"])
        parent_resp = await client.post(
            "/api/v1/organizations",
            json={"name": "Other Parent"},
            headers=second_headers,
        )
        assert parent_resp.status_code == 201

        response = await client.post(
            "/api/v1/organizations",
            json={"name": "Invalid Child", "parent_id": parent_resp.json()["id"]},
            headers=first_headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Parent organization not found in current tenant"
        assert first_tenant_id != second_resp.json()["id"]

    @pytest.mark.anyio
    async def test_list_organizations(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        await client.post("/api/v1/organizations", json={"name": "部门A"}, headers=headers)
        resp = await client.get("/api/v1/organizations", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert isinstance(data["items"], list)

    @pytest.mark.anyio
    async def test_organization_tree_returns_every_tenant_node_without_pagination(
        self, client: AsyncClient, db_session: AsyncSession, tenant_with_auth
    ):
        tenant_id, headers = tenant_with_auth
        baseline = await client.get("/api/v1/organizations/tree", headers=headers)
        baseline_ids = {item["id"] for item in baseline.json()}
        parent_id = None
        created_ids: list[str] = []
        for index in range(105):
            org_id = uuid.uuid4()
            db_session.add(
                Organization(
                    id=org_id,
                    tenant_id=uuid.UUID(tenant_id),
                    name=f"层级组织 {index:03d}",
                    parent_id=parent_id,
                )
            )
            created_ids.append(str(org_id))
            parent_id = org_id
        await db_session.flush()

        response = await client.get("/api/v1/organizations/tree", headers=headers)

        assert response.status_code == 200
        returned = response.json()
        returned_ids = {item["id"] for item in returned}
        assert returned_ids == baseline_ids | set(created_ids)
        assert len(returned) > 100
        by_id = {item["id"]: item for item in returned}
        assert by_id[created_ids[-1]]["parent_id"] == created_ids[-2]

    @pytest.mark.anyio
    async def test_custom_role_creation_is_rejected_until_fine_grained_gates_are_supported(
        self, client: AsyncClient, tenant_with_auth
    ):
        _, headers = tenant_with_auth
        resp = await client.post(
            "/api/v1/roles",
            json={"name": "custom-product-manager", "permission_ids": []},
            headers=headers,
        )

        assert resp.status_code == 409


class TestAccountCRUD:
    @pytest.mark.anyio
    async def test_create_account(self, client: AsyncClient, tenant_with_auth):
        tenant_id, headers = tenant_with_auth

        # 创建组织
        org_resp = await client.post("/api/v1/organizations", json={"name": "Test Org"}, headers=headers)
        assert org_resp.status_code == 201
        org_id = org_resp.json()["id"]

        # 创建账号
        resp = await client.post(
            "/api/v1/accounts",
            json={
                "email": "user@test.com",
                "name": "Test User",
                "password": "Test1234",
                "organization_id": org_id,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["email"] == "user@test.com"
        assert resp.json()["is_active"] is True

    @pytest.mark.anyio
    async def test_create_account_rejects_case_variant_of_existing_email(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        org_resp = await client.post("/api/v1/organizations", json={"name": "Identity Org"}, headers=headers)
        org_id = org_resp.json()["id"]
        first = await client.post(
            "/api/v1/accounts",
            json={
                "email": "member@example.com",
                "name": "First Member",
                "password": "Test1234",
                "organization_id": org_id,
            },
            headers=headers,
        )
        duplicate = await client.post(
            "/api/v1/accounts",
            json={
                "email": "  MEMBER@EXAMPLE.COM  ",
                "name": "Duplicate Member",
                "password": "Test1234",
                "organization_id": org_id,
            },
            headers=headers,
        )

        assert first.status_code == 201
        assert first.json()["email"] == "member@example.com"
        assert duplicate.status_code == 409

    @pytest.mark.anyio
    async def test_create_account_can_generate_initial_password(self, client: AsyncClient, tenant_with_auth):
        tenant_id, headers = tenant_with_auth
        org_resp = await client.post("/api/v1/organizations", json={"name": "运营部"}, headers=headers)
        org_id = org_resp.json()["id"]

        resp = await client.post(
            "/api/v1/accounts",
            json={"email": "ops@test.com", "name": "运营账号", "organization_id": org_id},
            headers=headers,
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["organization_name"] == "运营部"
        assert len(data["initial_password"]) >= 12
        assert data["must_change_password"] is True

        tenant_slug = (await client.get("/api/v1/tenants/me", headers=headers)).json()["slug"]
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "ops@test.com",
                "password": data["initial_password"],
                "tenant_slug": tenant_slug,
            },
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        assert decode_token(token)["must_change_password"] is True

        blocked = await client.get(
            "/api/v1/tenants/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert blocked.status_code == 403
        assert blocked.json()["detail"] == "首次登录必须先修改临时密码"

        changed = await client.post(
            "/api/v1/auth/change-password",
            json={"old_password": data["initial_password"], "new_password": "ChangedPass34"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert changed.status_code == 200
        relogin = await client.post(
            "/api/v1/auth/login",
            json={"email": "ops@test.com", "password": "ChangedPass34", "tenant_slug": tenant_slug},
        )
        assert relogin.status_code == 200
        assert decode_token(relogin.json()["access_token"])["must_change_password"] is False
        assert tenant_id

    @pytest.mark.anyio
    async def test_lists_include_business_context(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        org_resp = await client.post("/api/v1/organizations", json={"name": "销售部"}, headers=headers)
        org_id = org_resp.json()["id"]
        await client.post(
            "/api/v1/accounts",
            json={"email": "sales@test.com", "name": "销售账号", "organization_id": org_id},
            headers=headers,
        )

        orgs_resp = await client.get("/api/v1/organizations", headers=headers)
        accounts_resp = await client.get("/api/v1/accounts", headers=headers)

        assert orgs_resp.status_code == 200
        org_data = orgs_resp.json()
        org_item = next(item for item in org_data["items"] if item["id"] == org_id)
        assert org_item["account_count"] == 1
        assert accounts_resp.status_code == 200
        account_data = accounts_resp.json()
        account_item = next(item for item in account_data["items"] if item["email"] == "sales@test.com")
        assert account_item["organization_name"] == "销售部"
        assert account_item["is_active"] is True
        assert "initial_password" not in account_item

    @pytest.mark.anyio
    async def test_account_roles_round_trip_through_create_list_and_update(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        org_resp = await client.post("/api/v1/organizations", json={"name": "角色测试部"}, headers=headers)
        roles_resp = await client.get("/api/v1/roles", headers=headers)
        roles = {role["name"]: role for role in roles_resp.json()}

        create_resp = await client.post(
            "/api/v1/accounts",
            json={
                "email": "role-user@test.com",
                "name": "角色账号",
                "password": "RolePass1234",
                "organization_id": org_resp.json()["id"],
                "role_ids": [roles["operator"]["id"]],
            },
            headers=headers,
        )

        assert create_resp.status_code == 201
        assert [role["name"] for role in create_resp.json()["roles"]] == ["operator"]

        list_resp = await client.get("/api/v1/accounts", headers=headers)
        listed = next(item for item in list_resp.json()["items"] if item["email"] == "role-user@test.com")
        assert [role["name"] for role in listed["roles"]] == ["operator"]

        update_resp = await client.patch(
            f"/api/v1/accounts/{create_resp.json()['id']}",
            json={"role_ids": [roles["viewer"]["id"]]},
            headers=headers,
        )
        assert update_resp.status_code == 200
        assert [role["name"] for role in update_resp.json()["roles"]] == ["viewer"]

    @pytest.mark.anyio
    async def test_admin_can_disable_and_enable_another_account(self, client: AsyncClient, tenant_with_auth):
        tenant_id, headers = tenant_with_auth
        org_resp = await client.post("/api/v1/organizations", json={"name": "客服部"}, headers=headers)
        account_resp = await client.post(
            "/api/v1/accounts",
            json={
                "email": "support@test.com",
                "name": "客服账号",
                "organization_id": org_resp.json()["id"],
            },
            headers=headers,
        )
        account_id = account_resp.json()["id"]

        disable_resp = await client.patch(
            f"/api/v1/accounts/{account_id}/status",
            json={"is_active": False, "reason": "员工离职"},
            headers=headers,
        )
        assert disable_resp.status_code == 200
        assert disable_resp.json()["tenant_id"] == tenant_id
        assert disable_resp.json()["is_active"] is False

        enable_resp = await client.patch(
            f"/api/v1/accounts/{account_id}/status",
            json={"is_active": True, "reason": "重新入职"},
            headers=headers,
        )
        assert enable_resp.status_code == 200
        assert enable_resp.json()["is_active"] is True

    @pytest.mark.anyio
    async def test_operator_cannot_change_account_status(self, client: AsyncClient, tenant_with_auth):
        tenant_id, headers = tenant_with_auth
        org_resp = await client.post("/api/v1/organizations", json={"name": "渠道部"}, headers=headers)
        account_resp = await client.post(
            "/api/v1/accounts",
            json={
                "email": "channel@test.com",
                "name": "渠道账号",
                "organization_id": org_resp.json()["id"],
            },
            headers=headers,
        )
        operator_token = create_access_token(
            tenant_id,
            "00000000-0000-0000-0000-000000000002",
            "operator",
        )

        resp = await client.patch(
            f"/api/v1/accounts/{account_resp.json()['id']}/status",
            json={"is_active": False, "reason": "无权操作"},
            headers={"Authorization": f"Bearer {operator_token}"},
        )

        assert resp.status_code == 403

    @pytest.mark.anyio
    async def test_account_cannot_disable_itself(self, client: AsyncClient, tenant_with_auth):
        tenant_id, headers = tenant_with_auth
        org_resp = await client.post("/api/v1/organizations", json={"name": "财务部"}, headers=headers)
        account_resp = await client.post(
            "/api/v1/accounts",
            json={
                "email": "finance@test.com",
                "name": "财务管理员",
                "organization_id": org_resp.json()["id"],
            },
            headers=headers,
        )
        account_id = account_resp.json()["id"]
        self_token = create_access_token(tenant_id, account_id, "admin")

        resp = await client.patch(
            f"/api/v1/accounts/{account_id}/status",
            json={"is_active": False, "reason": "误操作"},
            headers={"Authorization": f"Bearer {self_token}"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "不能停用当前登录账户"

    @pytest.mark.anyio
    async def test_list_accounts(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        resp = await client.get("/api/v1/accounts", headers=headers)
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_update_account(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        resp = await client.patch(
            "/api/v1/accounts/00000000-0000-0000-0000-000000000000",
            json={"name": "New Name"},
            headers=headers,
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_cross_tenant_mutations_leave_target_and_audit_unchanged(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        tenant_with_auth,
    ):
        first_tenant_id, first_headers = tenant_with_auth
        second_resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "Cross Tenant Target",
                "admin_email": "cross-target@example.com",
                "admin_name": "Target Admin",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        assert second_resp.status_code == 201
        second_tenant_id = second_resp.json()["id"]
        second_headers = _auth_headers(second_tenant_id)
        target_org_resp = await client.post(
            "/api/v1/organizations",
            json={"name": "Target Empty Organization"},
            headers=second_headers,
        )
        assert target_org_resp.status_code == 201
        target_org_id = target_org_resp.json()["id"]
        member_org_resp = await client.post(
            "/api/v1/organizations",
            json={"name": "Target Member Organization"},
            headers=second_headers,
        )
        target_account_resp = await client.post(
            "/api/v1/accounts",
            json={
                "email": "cross-tenant-member@example.com",
                "name": "Cross Tenant Member",
                "password": "MemberPass1234",
                "organization_id": member_org_resp.json()["id"],
            },
            headers=second_headers,
        )
        assert target_account_resp.status_code == 201
        target_account_id = target_account_resp.json()["id"]
        target_resources = [f"organization:{target_org_id}", f"account:{target_account_id}"]
        baseline_target_audits = await db_session.scalar(
            select(func.count()).select_from(PlatformAuditLog).where(PlatformAuditLog.resource.in_(target_resources))
        )

        rejected = [
            await client.patch(
                f"/api/v1/organizations/{target_org_id}",
                json={"name": "Unauthorized Rename"},
                headers=first_headers,
            ),
            await client.delete(f"/api/v1/organizations/{target_org_id}", headers=first_headers),
            await client.patch(
                f"/api/v1/accounts/{target_account_id}",
                json={"name": "Unauthorized Member Rename"},
                headers=first_headers,
            ),
            await client.patch(
                f"/api/v1/accounts/{target_account_id}/status",
                json={"is_active": False, "reason": "cross-tenant attempt"},
                headers=first_headers,
            ),
        ]

        assert [response.status_code for response in rejected] == [404, 404, 404, 404]
        db_session.expire_all()
        target_org = await db_session.get(Organization, uuid.UUID(target_org_id))
        target_account = await db_session.get(Account, uuid.UUID(target_account_id))
        assert target_org is not None and target_org.name == "Target Empty Organization"
        assert target_account is not None
        assert target_account.name == "Cross Tenant Member"
        assert target_account.is_active is True
        rejected_audits = await db_session.scalar(
            select(func.count())
            .select_from(PlatformAuditLog)
            .where(
                PlatformAuditLog.operator_id == "00000000-0000-0000-0000-000000000001",
                PlatformAuditLog.target_tenant_id == first_tenant_id,
                PlatformAuditLog.resource.in_(target_resources),
            )
        )
        target_audits_after_rejection = await db_session.scalar(
            select(func.count()).select_from(PlatformAuditLog).where(PlatformAuditLog.resource.in_(target_resources))
        )
        assert rejected_audits == 0
        assert target_audits_after_rejection == baseline_target_audits
        assert first_tenant_id != second_tenant_id

    @pytest.mark.anyio
    async def test_pagination_params(self, client: AsyncClient, tenant_with_auth):
        """分页参数正确传递并返回分页结构"""
        _, headers = tenant_with_auth
        # 创建 3 个组织
        for name in ["部门X", "部门Y", "部门Z"]:
            await client.post("/api/v1/organizations", json={"name": name}, headers=headers)

        resp = await client.get("/api/v1/organizations?page=1&page_size=2", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert len(data["items"]) <= 2
        assert data["total"] >= 3  # 至少有 3 个新创建的 + 默认的

    @pytest.mark.anyio
    async def test_search_by_keyword(self, client: AsyncClient, tenant_with_auth):
        """关键词搜索过滤结果"""
        _, headers = tenant_with_auth
        await client.post("/api/v1/organizations", json={"name": "独一无二部门"}, headers=headers)
        await client.post("/api/v1/organizations", json={"name": "普通部门"}, headers=headers)

        resp = await client.get("/api/v1/organizations?q=独一无二", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any("独一无二" in item["name"] for item in data["items"])
