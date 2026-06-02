"""yimatong-udt: 代运营工作台 API 验收测试"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


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


@pytest.fixture
async def platform_admin_client(client: AsyncClient):
    """创建平台管理员 token（代运营人员）"""
    token = create_access_token(
        "00000000-0000-0000-0000-000000000000",
        "00000000-0000-0000-0000-000000000001",
        "platform_admin",
    )
    client.headers["Authorization"] = f"Bearer {token}"
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
            headers=tenant_headers,
        )
        data = resp.json()
        brand_check = next((c for c in data["checks"] if "品牌" in c["name"]), None)
        product_check = next((c for c in data["checks"] if "产品" in c["name"]), None)
        assert brand_check is not None
        assert product_check is not None
        assert brand_check["passed"] is True
        assert product_check["passed"] is True
