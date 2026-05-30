"""W12: 轻量会员与积分测试"""

from collections.abc import AsyncGenerator

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
            "name": "会员测试租户",
            "admin_email": "member@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


class TestConsumerProfile:
    """W12-001: 消费者档案与会员等级"""

    @pytest.mark.anyio
    async def test_create_consumer(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/members/consumers",
            json={"phone": "13800138000"},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["member_level"] == "normal"
        assert data["total_points"] == 0

    @pytest.mark.anyio
    async def test_get_consumer_profile(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        consumer_id = consumer.json()["id"]

        resp = await client.get(
            f"/api/v1/members/consumers/{consumer_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert "total_points" in resp.json()
        assert "recent_transactions" in resp.json()


class TestPointsAward:
    """W12-003: 积分发放"""

    @pytest.mark.anyio
    async def test_award_points(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        resp = await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 100, "reason": "扫码奖励"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["amount"] == 100
        assert resp.json()["balance_after"] == 100

    @pytest.mark.anyio
    async def test_award_points_accumulates(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 50, "reason": "首次扫码"},
            headers=headers,
        )
        resp = await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 30, "reason": "重复扫码"},
            headers=headers,
        )
        assert resp.json()["balance_after"] == 80

    @pytest.mark.anyio
    async def test_member_level_upgrade(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        # 发放 1000 积分 → silver
        await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 1000, "reason": "大批奖励"},
            headers=headers,
        )

        profile = await client.get(
            f"/api/v1/members/consumers/{cid}",
            headers=headers,
        )
        assert profile.json()["member_level"] == "silver"


class TestPointsSpend:
    """W12-004: 积分消费"""

    @pytest.mark.anyio
    async def test_spend_points(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 200, "reason": "初始积分"},
            headers=headers,
        )

        resp = await client.post(
            "/api/v1/members/points/spend",
            json={"consumer_id": cid, "points": 50, "reason": "兑换优惠券"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["amount"] == -50
        assert resp.json()["balance_after"] == 150

    @pytest.mark.anyio
    async def test_spend_insufficient_points(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        resp = await client.post(
            "/api/v1/members/points/spend",
            json={"consumer_id": cid, "points": 100, "reason": "余额不足"},
            headers=headers,
        )
        assert resp.status_code == 400


class TestPointRules:
    """W12-003: 积分规则配置"""

    @pytest.mark.anyio
    async def test_create_point_rule(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/members/point-rules",
            json={"rule_type": "scan", "points": 10},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["rule_type"] == "scan"
        assert resp.json()["points"] == 10

    @pytest.mark.anyio
    async def test_list_point_rules(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/members/point-rules",
            json={"rule_type": "first_scan", "points": 50},
            headers=headers,
        )
        resp = await client.get("/api/v1/members/point-rules", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1


class TestTransactions:
    """W12-005: 积分流水查询"""

    @pytest.mark.anyio
    async def test_list_transactions(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={},
            headers=headers,
        )
        cid = consumer.json()["id"]

        await client.post(
            "/api/v1/members/points/award",
            json={"consumer_id": cid, "points": 100, "reason": "扫码"},
            headers=headers,
        )
        await client.post(
            "/api/v1/members/points/spend",
            json={"consumer_id": cid, "points": 30, "reason": "兑换"},
            headers=headers,
        )

        resp = await client.get(
            f"/api/v1/members/consumers/{cid}/transactions",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["items"][0]["txn_type"] == "spending"
        assert data["items"][1]["txn_type"] == "earning"
