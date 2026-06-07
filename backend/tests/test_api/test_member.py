"""W12: 轻量会员与积分测试"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_with_bypass
from app.main import app
from app.models.code import CodeBatch, CodeItem, CodeItemStatus
from app.services.scan_token import create_scan_token
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal

def _platform_admin_headers() -> dict:
    from app.utils.security import create_access_token
    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {"Authorization": f"Bearer {token}"}




@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_with_bypass] = override_get_db
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
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


async def create_scan_context(db_session: AsyncSession, tenant_id: str) -> str:
    batch_id = uuid.uuid4()
    public_id = f"TEST{uuid.uuid4().hex[:10]}"
    db_session.add(
        CodeBatch(
            id=batch_id,
            tenant_id=uuid.UUID(tenant_id),
            product_id=uuid.uuid4(),
            sku_id=uuid.uuid4(),
            batch_code=f"B-{public_id}",
            quantity=1,
            status="activated",
            code_type="single",
            created_by=uuid.uuid4(),
        )
    )
    db_session.add(
        CodeItem(
            tenant_id=uuid.UUID(tenant_id),
            code_batch_id=batch_id,
            public_id=public_id,
            status=CodeItemStatus.activated,
            code_type="single",
        )
    )
    await db_session.flush()
    return create_scan_token(public_id, "test-ip")


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

    @pytest.mark.anyio
    async def test_search_consumer_by_phone_and_nickname(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        consumer = await client.post(
            "/api/v1/members/consumers",
            json={"phone": "13800138000", "nickname": "王小明"},
            headers=headers,
        )
        assert consumer.status_code == 201

        by_phone = await client.get(
            "/api/v1/members/consumers/search",
            params={"keyword": "13800138000", "lookup_type": "phone"},
            headers=headers,
        )
        assert by_phone.status_code == 200
        assert by_phone.json()["items"][0]["phone"] == "138****8000"

        by_name = await client.get(
            "/api/v1/members/consumers/search",
            params={"keyword": "小明", "lookup_type": "nickname"},
            headers=headers,
        )
        assert by_name.status_code == 200
        assert by_name.json()["items"][0]["nickname"] == "王小明"


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


class TestMemberOverview:
    @pytest.mark.anyio
    async def test_overview_counts_rules_products_points_and_redemptions(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant
        consumer = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        cid = consumer.json()["id"]
        await client.post("/api/v1/members/point-rules", json={"rule_type": "scan", "points": 10}, headers=headers)
        product = await client.post(
            "/api/v1/members/point-products",
            json={"name": "兑换券", "points_cost": 20, "stock": 2, "enabled": True},
            headers=headers,
        )
        await client.post("/api/v1/members/points/award", json={"consumer_id": cid, "points": 100, "reason": "测试"}, headers=headers)
        token = await create_scan_context(db_session, tid)
        exchange = await client.post(
            "/api/v1/consumers/points/exchanges",
            json={"consumer_id": cid, "product_id": product.json()["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert exchange.status_code == 200

        overview = await client.get("/api/v1/members/overview", headers=headers)
        assert overview.status_code == 200
        data = overview.json()
        assert data["enabled_rules"] == 1
        assert data["active_products"] == 1
        assert data["points_awarded_7d"] == 100
        assert data["points_spent_7d"] == 20
        assert data["redemptions_7d"] == 1


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


class TestPointProducts:
    @pytest.mark.anyio
    async def test_product_crud_returns_validity_and_limits(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/members/point-products",
            json={
                "name": "积分礼品",
                "points_cost": 30,
                "stock": 5,
                "per_consumer_limit": 2,
                "sort_order": 3,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["per_consumer_limit"] == 2
        assert data["sort_order"] == 3

        updated = await client.put(
            f"/api/v1/members/point-products/{data['id']}",
            json={"enabled": False, "per_consumer_limit": 0},
            headers=headers,
        )
        assert updated.status_code == 200
        assert updated.json()["enabled"] is False
        assert updated.json()["per_consumer_limit"] == 0

    @pytest.mark.anyio
    async def test_h5_exchange_success_writes_redemption_and_refreshes_products(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers = setup_tenant
        consumer = await client.post("/api/v1/members/consumers", json={}, headers=headers)
        cid = consumer.json()["id"]
        await client.post("/api/v1/members/points/award", json={"consumer_id": cid, "points": 80, "reason": "初始"}, headers=headers)
        product = await client.post(
            "/api/v1/members/point-products",
            json={"name": "积分券", "points_cost": 50, "stock": 1, "per_consumer_limit": 1},
            headers=headers,
        )
        token = await create_scan_context(db_session, tid)

        products = await client.get(
            "/api/v1/consumers/points/products",
            params={"consumer_id": cid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert products.status_code == 200
        assert products.json()["items"][0]["can_exchange"] is True

        exchange = await client.post(
            "/api/v1/consumers/points/exchanges",
            json={"consumer_id": cid, "product_id": product.json()["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert exchange.status_code == 200
        assert exchange.json()["points_spent"] == 50
        assert exchange.json()["balance_after"] == 30

        redemptions = await client.get("/api/v1/members/point-redemptions", headers=headers)
        assert redemptions.status_code == 200
        assert redemptions.json()["total"] == 1
        assert redemptions.json()["items"][0]["product_name"] == "积分券"

        blocked = await client.post(
            "/api/v1/consumers/points/exchanges",
            json={"consumer_id": cid, "product_id": product.json()["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert blocked.status_code == 400
