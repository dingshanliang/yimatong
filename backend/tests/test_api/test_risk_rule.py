"""W13: 活动风控规则引擎测试"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
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


@pytest.fixture
async def setup_tenant(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "风控测试租户",
            "admin_email": "risk@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


class TestRiskRuleCRUD:
    """W13-001: RiskRule 模型与 CRUD"""

    @pytest.mark.anyio
    async def test_create_risk_rule(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/risk-rules",
            json={
                "name": "IP频率限制",
                "rule_type": "ip_frequency",
                "action": "block",
                "config": {"window_minutes": 10, "max_requests": 5},
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "IP频率限制"
        assert data["rule_type"] == "ip_frequency"
        assert data["action"] == "block"
        assert data["enabled"] is True

    @pytest.mark.anyio
    async def test_list_risk_rules(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/risk-rules",
            json={
                "name": "手机号频率限制",
                "rule_type": "phone_frequency",
                "action": "warn",
                "config": {"window_minutes": 60, "max_requests": 20},
            },
            headers=headers,
        )
        resp = await client.get("/api/v1/risk-rules", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    @pytest.mark.anyio
    async def test_update_risk_rule(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/risk-rules",
            json={
                "name": "地区限制",
                "rule_type": "region_restriction",
                "action": "block",
                "config": {"allowed_regions": ["北京", "上海"]},
            },
            headers=headers,
        )
        rule_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/risk-rules/{rule_id}",
            json={"enabled": False, "name": "地区限制-已禁用"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert resp.json()["name"] == "地区限制-已禁用"

    @pytest.mark.anyio
    async def test_delete_risk_rule(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/risk-rules",
            json={
                "name": "待删除规则",
                "rule_type": "budget_limit",
                "action": "block",
                "config": {"max_budget": 10000},
            },
            headers=headers,
        )
        rule_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/api/v1/risk-rules/{rule_id}",
            headers=headers,
        )
        assert resp.status_code == 200

        list_resp = await client.get("/api/v1/risk-rules", headers=headers)
        ids = [r["id"] for r in list_resp.json()]
        assert rule_id not in ids


class TestRuleEvaluation:
    """W13-002: 规则评估服务"""

    @pytest.mark.anyio
    async def test_evaluate_block_action(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant
        # 创建一个拦截规则
        await client.post(
            "/api/v1/risk-rules",
            json={
                "name": "高频IP拦截",
                "rule_type": "ip_frequency",
                "action": "block",
                "config": {"window_minutes": 10, "max_requests": 3},
            },
            headers=headers,
        )

        # 评估请求：传入满足触发条件的上下文
        resp = await client.post(
            "/api/v1/risk-rules/evaluate",
            json={
                "rule_type": "ip_frequency",
                "context": {"ip_hash": "abc123", "request_count": 5, "window_minutes": 10},
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "block"
        assert data["triggered"] is True

    @pytest.mark.anyio
    async def test_evaluate_no_trigger(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/risk-rules",
            json={
                "name": "宽松规则",
                "rule_type": "phone_frequency",
                "action": "warn",
                "config": {"window_minutes": 60, "max_requests": 100},
            },
            headers=headers,
        )

        resp = await client.post(
            "/api/v1/risk-rules/evaluate",
            json={
                "rule_type": "phone_frequency",
                "context": {"phone_hash": "xyz789", "request_count": 3, "window_minutes": 60},
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["triggered"] is False


class TestInterceptionRecord:
    """W13-003: 拦截记录与审计查询"""

    @pytest.mark.anyio
    async def test_interception_record_created_on_block(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/risk-rules",
            json={
                "name": "时间窗口限制",
                "rule_type": "time_window",
                "action": "block",
                "config": {"allowed_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18]},
            },
            headers=headers,
        )

        # 触发拦截
        await client.post(
            "/api/v1/risk-rules/evaluate",
            json={
                "rule_type": "time_window",
                "context": {"current_hour": 3, "consumer_id": "c-001"},
            },
            headers=headers,
        )

        # 查询拦截记录
        resp = await client.get(
            "/api/v1/risk-rules/interceptions",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert data["items"][0]["action"] == "block"

    @pytest.mark.anyio
    async def test_list_interceptions_with_filter(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant

        resp = await client.get(
            "/api/v1/risk-rules/interceptions",
            params={"action": "block"},
            headers=headers,
        )
        assert resp.status_code == 200


class TestCampaignRiskRules:
    """W13-004: 活动关联风控规则"""

    @pytest.mark.anyio
    async def test_attach_rule_to_campaign(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant
        # 创建活动
        campaign_resp = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "风控测试活动",
                "campaign_type": "coupon",
                "start_at": "2026-01-01T00:00:00Z",
                "end_at": "2026-12-31T23:59:59Z",
                "rules_json": {
                    "participation_conditions": {},
                    "claim_limits": {},
                    "validity_period": {},
                    "disclaimer": "",
                    "minor_notice": "",
                    "customer_service_contact": "",
                },
            },
            headers=headers,
        )
        campaign_id = campaign_resp.json()["id"]

        # 创建规则
        rule_resp = await client.post(
            "/api/v1/risk-rules",
            json={
                "name": "预算限制",
                "rule_type": "budget_limit",
                "action": "block",
                "config": {"max_budget": 5000},
            },
            headers=headers,
        )
        rule_id = rule_resp.json()["id"]

        # 关联
        resp = await client.post(
            f"/api/v1/risk-rules/{rule_id}/campaigns/{campaign_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["attached"] is True

    @pytest.mark.anyio
    async def test_evaluate_campaign_rules(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant
        campaign_resp = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "多规则活动",
                "campaign_type": "lottery",
                "start_at": "2026-01-01T00:00:00Z",
                "end_at": "2026-12-31T23:59:59Z",
                "rules_json": {
                    "participation_conditions": {},
                    "claim_limits": {},
                    "validity_period": {},
                    "disclaimer": "",
                    "minor_notice": "",
                    "customer_service_contact": "",
                },
            },
            headers=headers,
        )
        campaign_id = campaign_resp.json()["id"]

        rule_resp = await client.post(
            "/api/v1/risk-rules",
            json={
                "name": "库存限制",
                "rule_type": "stock_limit",
                "action": "block",
                "config": {"max_stock": 100},
            },
            headers=headers,
        )
        rule_id = rule_resp.json()["id"]

        await client.post(
            f"/api/v1/risk-rules/{rule_id}/campaigns/{campaign_id}",
            headers=headers,
        )

        # 评估活动关联规则
        resp = await client.post(
            f"/api/v1/risk-rules/evaluate/campaign/{campaign_id}",
            json={
                "context": {"stock_used": 150, "stock_total": 100},
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["triggered"] is True
        assert resp.json()["action"] == "block"


class TestRiskRuleConfig:
    """W13-005: 风控规则配置 API"""

    @pytest.mark.anyio
    async def test_toggle_rule_enabled(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/risk-rules",
            json={
                "name": "测试开关",
                "rule_type": "device_frequency",
                "action": "warn",
                "config": {"window_minutes": 30, "max_requests": 10},
            },
            headers=headers,
        )
        rule_id = create_resp.json()["id"]

        # 禁用
        resp = await client.patch(
            f"/api/v1/risk-rules/{rule_id}",
            json={"enabled": False},
            headers=headers,
        )
        assert resp.json()["enabled"] is False

        # 重新启用
        resp2 = await client.patch(
            f"/api/v1/risk-rules/{rule_id}",
            json={"enabled": True},
            headers=headers,
        )
        assert resp2.json()["enabled"] is True

    @pytest.mark.anyio
    async def test_get_rule_detail(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/risk-rules",
            json={
                "name": "详情查询",
                "rule_type": "ip_frequency",
                "action": "block",
                "config": {"window_minutes": 5, "max_requests": 100},
            },
            headers=headers,
        )
        rule_id = create_resp.json()["id"]

        resp = await client.get(
            f"/api/v1/risk-rules/{rule_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "详情查询"
        assert "config" in resp.json()
