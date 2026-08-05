"""yimatong-udt: 代运营工作台 API 验收测试"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_with_bypass
from app.core.dependencies import get_redis_cache
from app.main import app
from app.models.tenant import (
    Account,
    AgencyAuthorization,
    AgencyAuthStatus,
    OpsTask,
    Organization,
    Tenant,
    TenantStatus,
    TenantType,
)
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


class AllowingPlatformLoginCache:
    async def rate_limit_check_shared(self, key: str, max_attempts: int, window_seconds: int) -> tuple[bool, int]:
        return True, 9


def _platform_admin_headers() -> dict:
    from app.utils.security import create_access_token

    token = create_access_token("platform", "platform-admin", "platform_admin", tenant_type="platform")
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

    async def override_get_redis_cache():
        return AllowingPlatformLoginCache()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_with_bypass] = override_get_db
    app.dependency_overrides[get_redis_cache] = override_get_redis_cache
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def platform_admin_client(client: AsyncClient):
    """创建与服务端 HttpOnly 会话等价的平台 Cookie。"""
    token = create_access_token(
        "platform",
        "platform-admin",
        "platform_admin",
        tenant_type="platform",
    )
    client.cookies.set("platform_access_token", token)
    client.cookies.set("platform_csrf_token", "test-platform-csrf")
    client.headers["Origin"] = "http://localhost:3002"
    client.headers["X-Platform-CSRF"] = "test-platform-csrf"
    return client


@pytest.fixture
async def sample_tenants(client: AsyncClient):
    """创建多个测试租户"""
    tenants = []
    for i in range(3):
        resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": f"测试客户{i + 1}",
                "slug": f"test-client-{i + 1}",
                "plan": ["free", "starter", "pro"][i],
                "admin_email": f"admin{i + 1}@test.com",
                "admin_name": f"管理员{i + 1}",
                "admin_password": "Test1234",
            },
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 201
        tenants.append(resp.json())
    return tenants


class TestTenantList:
    @pytest.mark.anyio
    async def test_list_tenants_pagination(self, platform_admin_client: AsyncClient, sample_tenants):
        """AC: 代运营人员可查看客户列表（分页）"""
        resp = await platform_admin_client.get("/api/v1/tenants?page=1&page_size=2")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert len(data["items"]) <= 2
        assert data["total"] >= 3

    @pytest.mark.anyio
    async def test_list_tenants_search(self, platform_admin_client: AsyncClient, sample_tenants):
        """AC: 客户列表支持搜索"""
        resp = await platform_admin_client.get("/api/v1/tenants?q=测试客户1")
        assert resp.status_code == 200
        data = resp.json()
        assert any("测试客户1" in t["name"] for t in data["items"])

    @pytest.mark.anyio
    async def test_list_tenants_includes_plan_expires(self, platform_admin_client: AsyncClient, sample_tenants):
        """AC: 客户列表包含套餐到期信息"""
        resp = await platform_admin_client.get("/api/v1/tenants")
        assert resp.status_code == 200
        data = resp.json()
        for tenant in data["items"]:
            assert "plan_expires_at" in tenant
            assert "status" in tenant
            assert "plan" in tenant


class TestTenantStatus:
    @pytest.mark.anyio
    async def test_tenant_status_with_progress(self, platform_admin_client: AsyncClient, sample_tenants):
        """AC: 可查看开通状态和初始化进度"""
        tenant_id = sample_tenants[0]["id"]
        resp = await platform_admin_client.get(f"/api/v1/ops/clients/{tenant_id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "tenant_id" in data
        assert "products" in data
        assert "brands" in data
        assert "published_pages" in data
        assert "activated_batches" in data
        assert "active_campaigns" in data
        assert "onboarding_progress" in data
        assert isinstance(data["onboarding_progress"], dict)


class TestOpsTasks:
    @pytest.mark.anyio
    async def test_create_task(self, platform_admin_client: AsyncClient, sample_tenants):
        """AC: 代运营人员可创建待办任务"""
        tenant_id = sample_tenants[0]["id"]
        resp = await platform_admin_client.post(
            "/api/v1/ops/tasks",
            json={
                "tenant_id": tenant_id,
                "title": "配置品牌信息",
                "description": "为客户配置品牌 Logo 和简介",
                "priority": "high",
                "due_date": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "配置品牌信息"
        assert data["status"] == "pending"
        assert data["tenant_id"] == tenant_id

    @pytest.mark.anyio
    async def test_list_tasks_for_tenant(self, platform_admin_client: AsyncClient, sample_tenants):
        """AC: 可查看某个客户的待办任务列表"""
        tenant_id = sample_tenants[0]["id"]
        # 创建任务
        await platform_admin_client.post(
            "/api/v1/ops/tasks",
            json={
                "tenant_id": tenant_id,
                "title": "任务1",
                "priority": "medium",
            },
        )
        await platform_admin_client.post(
            "/api/v1/ops/tasks",
            json={
                "tenant_id": tenant_id,
                "title": "任务2",
                "priority": "low",
            },
        )

        resp = await platform_admin_client.get(f"/api/v1/ops/tasks?tenant_id={tenant_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 2

    @pytest.mark.anyio
    async def test_list_all_tasks(self, platform_admin_client: AsyncClient, sample_tenants):
        """AC: 可查看全部待办任务（跨客户）"""
        for tenant in sample_tenants:
            await platform_admin_client.post(
                "/api/v1/ops/tasks",
                json={
                    "tenant_id": tenant["id"],
                    "title": f"任务-{tenant['name']}",
                    "priority": "medium",
                },
            )

        resp = await platform_admin_client.get("/api/v1/ops/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["items"][0]["tenant_name"].startswith("测试客户")

    @pytest.mark.anyio
    async def test_ops_overview_summarizes_clients_and_tasks(self, platform_admin_client: AsyncClient, sample_tenants):
        """AC: 代运营工作台顶部指标由后端聚合"""
        await platform_admin_client.post(
            "/api/v1/ops/tasks",
            json={"tenant_id": sample_tenants[0]["id"], "title": "待跟进事项", "priority": "high"},
        )

        resp = await platform_admin_client.get("/api/v1/ops/overview")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_clients"] >= 3
        assert data["active_clients"] >= 3
        assert data["pending_tasks"] >= 1
        assert "ready_clients" in data

    @pytest.mark.anyio
    async def test_update_task_status(self, platform_admin_client: AsyncClient, sample_tenants):
        """AC: 可更新待办任务状态"""
        tenant_id = sample_tenants[0]["id"]
        create_resp = await platform_admin_client.post(
            "/api/v1/ops/tasks",
            json={
                "tenant_id": tenant_id,
                "title": "待完成任务",
                "priority": "high",
            },
        )
        task_id = create_resp.json()["id"]

        resp = await platform_admin_client.patch(
            f"/api/v1/ops/tasks/{task_id}",
            json={"status": "completed"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    @pytest.mark.anyio
    async def test_delete_task(self, platform_admin_client: AsyncClient, sample_tenants):
        """AC: 可删除待办任务"""
        tenant_id = sample_tenants[0]["id"]
        create_resp = await platform_admin_client.post(
            "/api/v1/ops/tasks",
            json={
                "tenant_id": tenant_id,
                "title": "待删除任务",
                "priority": "low",
            },
        )
        task_id = create_resp.json()["id"]

        resp = await platform_admin_client.delete(f"/api/v1/ops/tasks/{task_id}")
        assert resp.status_code == 204

        get_resp = await platform_admin_client.get(f"/api/v1/ops/tasks/{task_id}")
        assert get_resp.status_code == 404


class TestOpsWorkbench:
    @pytest.mark.anyio
    async def test_real_platform_login_cookie_can_access_workbench(self, client: AsyncClient):
        login = await client.post(
            "/api/v1/platform/auth/login",
            json={"email": "platform@yimatong.cn", "password": "platform_admin_2026"},
        )
        assert login.status_code == 200

        assert "platform_access_token=" in login.headers["set-cookie"]
        response = await client.get("/api/v1/ops/workbench")

        assert response.status_code == 200

    @pytest.mark.anyio
    async def test_platform_bearer_without_cookie_cannot_access_workbench(self, client: AsyncClient):
        token = create_access_token(
            "platform",
            "platform-admin",
            "platform_admin",
            tenant_type="platform",
        )

        response = await client.get(
            "/api/v1/ops/workbench",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Platform session cookie required"

    @pytest.mark.anyio
    async def test_pages_only_agency_sees_only_authorized_client_with_redacted_workbench(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        agency = Tenant(
            name="页面代运营",
            slug=f"agency-{uuid.uuid4().hex[:8]}",
            status=TenantStatus.active,
            tenant_type=TenantType.agency,
        )
        authorized = Tenant(
            name="已授权品牌",
            slug=f"brand-{uuid.uuid4().hex[:8]}",
            status=TenantStatus.active,
            tenant_type=TenantType.brand,
        )
        unauthorized = Tenant(
            name="未授权品牌",
            slug=f"brand-{uuid.uuid4().hex[:8]}",
            status=TenantStatus.active,
            tenant_type=TenantType.brand,
        )
        db_session.add_all([agency, authorized, unauthorized])
        await db_session.flush()
        organization = Organization(tenant_id=agency.id, name="代运营组织")
        db_session.add(organization)
        await db_session.flush()
        account = Account(
            tenant_id=agency.id,
            organization_id=organization.id,
            email="pages-only@example.com",
            hashed_password="unused",
            name="页面运营",
        )
        db_session.add_all(
            [
                account,
                AgencyAuthorization(
                    agency_tenant_id=agency.id,
                    client_tenant_id=authorized.id,
                    scope=["pages"],
                    status=AgencyAuthStatus.active,
                ),
                OpsTask(tenant_id=authorized.id, title="授权客户私有任务"),
                OpsTask(tenant_id=unauthorized.id, title="未授权客户私有任务"),
            ]
        )
        await db_session.commit()
        token = create_access_token(str(agency.id), str(account.id), "admin", tenant_type="agency")

        response = await client.get(
            "/api/v1/ops/workbench",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert [row["id"] for row in data["clients"]] == [str(authorized.id)]
        row = data["clients"][0]
        assert row["agency_scope"] == ["pages"]
        assert row["full_workbench_access"] is False
        assert row["readiness"]["missing_keys"] == ["authorization_scope"]
        assert data["tasks"] == []
        assert data["summary"]["blocked_clients"] == 0

        blocked_response = await client.get(
            "/api/v1/ops/workbench?readiness=blocked",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert blocked_response.status_code == 200
        assert blocked_response.json()["clients"] == []

    @pytest.mark.anyio
    async def test_partial_scope_contract_preserves_redaction_fields(
        self, platform_admin_client: AsyncClient, monkeypatch
    ):
        async def fake_workbench(*args, **kwargs):
            return {
                "summary": {"total_clients": 1, "active_clients": 1},
                "clients": [
                    {
                        "id": "00000000-0000-0000-0000-000000000010",
                        "name": "部分授权客户",
                        "status": "active",
                        "plan": "free",
                        "readiness": {
                            "ready": False,
                            "passed_count": 0,
                            "total_count": 0,
                            "percent": 0,
                            "missing_keys": ["authorization_scope"],
                            "missing_labels": ["当前授权仅允许进入指定业务模块"],
                        },
                        "task_summary": {"pending": 0, "in_progress": 0, "overdue": 0, "high_priority": 0},
                        "next_action": {"type": "scope", "label": "进入管理", "href": "/pages", "task_title": ""},
                        "agency_scope": ["pages"],
                        "full_workbench_access": False,
                    }
                ],
                "tasks": [],
                "total": 1,
                "page": 1,
                "page_size": 20,
            }

        monkeypatch.setattr("app.api.v1.ops.get_ops_workbench", fake_workbench)
        response = await platform_admin_client.get("/api/v1/ops/workbench")
        assert response.status_code == 200
        row = response.json()["clients"][0]
        assert row["agency_scope"] == ["pages"]
        assert row["full_workbench_access"] is False
        assert response.json()["tasks"] == []

    @pytest.mark.anyio
    async def test_ops_workbench_returns_client_queue_shape(
        self,
        platform_admin_client: AsyncClient,
        sample_tenants,
    ):
        resp = await platform_admin_client.get("/api/v1/ops/workbench")

        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert "clients" in data
        assert "tasks" in data
        assert "total" in data
        assert data["clients"]

        row = data["clients"][0]
        assert {"id", "name", "readiness", "task_summary", "next_action"} <= set(row)
        assert {"ready", "passed_count", "total_count", "percent", "missing_labels"} <= set(row["readiness"])
        assert {"pending", "in_progress", "overdue", "high_priority"} <= set(row["task_summary"])
        assert {"type", "label", "href", "task_title"} <= set(row["next_action"])

    @pytest.mark.anyio
    async def test_ops_workbench_prioritizes_missing_readiness_action(
        self,
        platform_admin_client: AsyncClient,
        sample_tenants,
    ):
        tenant_id = sample_tenants[0]["id"]

        resp = await platform_admin_client.get("/api/v1/ops/workbench")

        assert resp.status_code == 200
        row = next(client for client in resp.json()["clients"] if client["id"] == tenant_id)
        assert row["readiness"]["ready"] is False
        assert row["next_action"]["label"] == "配置品牌"
        assert row["next_action"]["href"] == "/brands"

    @pytest.mark.anyio
    async def test_ops_workbench_marks_overdue_tasks(
        self,
        platform_admin_client: AsyncClient,
        sample_tenants,
    ):
        await platform_admin_client.post(
            "/api/v1/ops/tasks",
            json={
                "tenant_id": sample_tenants[0]["id"],
                "title": "逾期配置产品",
                "priority": "high",
                "due_date": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            },
        )

        resp = await platform_admin_client.get("/api/v1/ops/workbench")

        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["overdue_tasks"] >= 1
        row = next(client for client in data["clients"] if client["id"] == sample_tenants[0]["id"])
        assert row["task_summary"]["overdue"] == 1
        assert row["next_action"]["label"] == "处理逾期任务"
        assert row["next_action"]["href"] == "/agency"

    @pytest.mark.anyio
    async def test_ops_workbench_supports_search_filter(
        self,
        platform_admin_client: AsyncClient,
        sample_tenants,
    ):
        resp = await platform_admin_client.get("/api/v1/ops/workbench?q=测试客户1")

        assert resp.status_code == 200
        names = [client["name"] for client in resp.json()["clients"]]
        assert names
        assert all("测试客户1" in name for name in names)

    @pytest.mark.anyio
    async def test_ops_workbench_supports_blocked_filter(
        self,
        platform_admin_client: AsyncClient,
        sample_tenants,
    ):
        resp = await platform_admin_client.get("/api/v1/ops/workbench?readiness=blocked")

        assert resp.status_code == 200
        assert resp.json()["clients"]
        assert all(client["readiness"]["ready"] is False for client in resp.json()["clients"])


class TestLaunchChecklist:
    @pytest.mark.anyio
    async def test_launch_checklist_from_backend(self, platform_admin_client: AsyncClient, sample_tenants):
        """AC: 上线检查清单从后端获取"""
        tenant_id = sample_tenants[0]["id"]
        resp = await platform_admin_client.get(f"/api/v1/ops/clients/{tenant_id}/launch-checklist")
        assert resp.status_code == 200
        data = resp.json()
        assert "ready" in data
        assert "checks" in data
        assert "passed_count" in data
        assert "total_count" in data
        assert isinstance(data["checks"], list)
        assert len(data["checks"]) > 0

    @pytest.mark.anyio
    async def test_launch_checklist_with_brand_and_product(self, platform_admin_client: AsyncClient, sample_tenants):
        """AC: 创建品牌和产品后检查清单更新"""
        tenant_id = sample_tenants[0]["id"]
        # 使用目标租户的 token 创建品牌和产品
        tenant_token = create_access_token(
            tenant_id,
            "00000000-0000-0000-0000-000000000001",
            "admin",
            tenant_type="brand",
        )
        tenant_headers = {"Authorization": f"Bearer {tenant_token}"}

        # 创建品牌
        brand_resp = await platform_admin_client.post(
            "/api/v1/brands",
            json={"name": "测试品牌"},
            headers=tenant_headers,
        )
        brand_id = brand_resp.json()["id"]

        # 创建产品
        await platform_admin_client.post(
            "/api/v1/products",
            json={"brand_id": brand_id, "name": "测试产品"},
            headers=tenant_headers,
        )

        resp = await platform_admin_client.get(
            f"/api/v1/ops/clients/{tenant_id}/launch-checklist",
        )
        data = resp.json()
        brand_check = next((c for c in data["checks"] if "品牌" in c["name"]), None)
        product_check = next((c for c in data["checks"] if "产品" in c["name"]), None)
        assert brand_check is not None
        assert product_check is not None
        assert brand_check["passed"] is True
        assert product_check["passed"] is True
