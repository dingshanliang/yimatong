"""W23: 现金红包插件测试"""

from collections.abc import AsyncGenerator
from datetime import datetime, timezone

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
async def setup_tenant(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "红包测试租户",
            "admin_email": "redpacket@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


class TestRedPacketRule:
    """W23-001: 红包规则管理"""

    @pytest.mark.anyio
    async def test_create_rule(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/redpacket/rules",
            json={
                "name": "扫码红包",
                "total_budget": 100000,
                "min_amount": 100,
                "max_amount": 1000,
                "daily_limit_per_user": 3,
                "single_limit_per_user": 10,
                "require_kyc": True,
                "start_time": "2026-06-01T00:00:00Z",
                "end_time": "2026-12-31T23:59:59Z",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "扫码红包"
        assert data["total_budget"] == 100000
        assert data["require_kyc"] is True

    @pytest.mark.anyio
    async def test_list_rules(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/redpacket/rules",
            json={
                "name": "规则A",
                "total_budget": 50000,
                "min_amount": 50,
                "max_amount": 500,
                "daily_limit_per_user": 1,
                "single_limit_per_user": 5,
                "require_kyc": False,
                "start_time": "2026-06-01T00:00:00Z",
                "end_time": "2026-12-31T23:59:59Z",
            },
            headers=headers,
        )
        resp = await client.get("/api/v1/redpacket/rules", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1


class TestKYCVerification:
    """W23-002: KYC 实名认证"""

    @pytest.mark.anyio
    async def test_submit_kyc(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/redpacket/kyc",
            json={
                "real_name": "张三",
                "id_number": "110101199001011234",
                "phone": "13800138000",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"
        assert data["real_name"] == "张三"

    @pytest.mark.anyio
    async def test_get_kyc_status(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/redpacket/kyc",
            json={
                "real_name": "李四",
                "id_number": "110101199202022345",
                "phone": "13900139000",
            },
            headers=headers,
        )
        resp = await client.get("/api/v1/redpacket/kyc/status", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] in ("pending", "approved", "rejected")


class TestRedPacketClaim:
    """W23-003: 红包领取"""

    @pytest.mark.anyio
    async def test_claim_redpacket(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        # 先创建规则
        rule_resp = await client.post(
            "/api/v1/redpacket/rules",
            json={
                "name": "领取测试规则",
                "total_budget": 100000,
                "min_amount": 100,
                "max_amount": 500,
                "daily_limit_per_user": 3,
                "single_limit_per_user": 10,
                "require_kyc": False,
                "start_time": "2026-01-01T00:00:00Z",
                "end_time": "2026-12-31T23:59:59Z",
            },
            headers=headers,
        )
        rule_id = rule_resp.json()["id"]

        # 领取红包
        resp = await client.post(
            "/api/v1/redpacket/claim",
            json={"rule_id": rule_id},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["amount"] >= 100
        assert data["amount"] <= 500
        assert data["status"] == "claimed"


class TestWithdrawal:
    """W23-004: 提现管理"""

    @pytest.mark.anyio
    async def test_request_withdrawal(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/redpacket/withdrawals",
            json={"amount": 500},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["amount"] == 500
        assert data["status"] == "pending"

    @pytest.mark.anyio
    async def test_list_withdrawals(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/redpacket/withdrawals",
            json={"amount": 300},
            headers=headers,
        )
        resp = await client.get("/api/v1/redpacket/withdrawals", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1


class TestRedPacketRisk:
    """W23-005: 红包风控"""

    @pytest.mark.anyio
    async def test_check_risk(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/redpacket/risk-check",
            json={"rule_id": "00000000-0000-0000-0000-000000000001", "amount": 500},
            headers=headers,
        )
        assert resp.status_code == 200
        assert "passed" in resp.json()

    @pytest.mark.anyio
    async def test_daily_limit_enforcement(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        # 创建 daily_limit=1 的规则
        rule_resp = await client.post(
            "/api/v1/redpacket/rules",
            json={
                "name": "限额测试",
                "total_budget": 100000,
                "min_amount": 100,
                "max_amount": 500,
                "daily_limit_per_user": 1,
                "single_limit_per_user": 5,
                "require_kyc": False,
                "start_time": "2026-01-01T00:00:00Z",
                "end_time": "2026-12-31T23:59:59Z",
            },
            headers=headers,
        )
        rule_id = rule_resp.json()["id"]

        # 第一次领取成功
        resp1 = await client.post(
            "/api/v1/redpacket/claim",
            json={"rule_id": rule_id},
            headers=headers,
        )
        assert resp1.status_code == 200

        # 第二次应被限额拦截
        resp2 = await client.post(
            "/api/v1/redpacket/claim",
            json={"rule_id": rule_id},
            headers=headers,
        )
        assert resp2.status_code == 429
