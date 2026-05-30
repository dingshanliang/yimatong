"""B3: 活动与权益 API 测试"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal

RULES_JSON = {
    "participation_conditions": "扫码即可参与",
    "claim_limits": "每人限领1次",
    "validity_period": "领取后7天内有效",
    "disclaimer": "最终解释权归品牌方所有",
    "minor_notice": "未成年人请在监护人陪同下参与",
    "customer_service_contact": "400-123-4567",
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
async def auth_setup(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "活动测试",
            "admin_email": "campaign@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return tid, {"Authorization": f"Bearer {token}"}


class TestCampaignCRUD:
    @pytest.mark.anyio
    async def test_create_campaign(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        resp = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "新春活动",
                "campaign_type": "coupon",
                "start_at": "2026-06-01T00:00:00",
                "end_at": "2026-06-30T23:59:59",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "新春活动"
        assert data["status"] == "draft"

    @pytest.mark.anyio
    async def test_create_campaign_missing_rules(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        resp = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "缺规则",
                "campaign_type": "coupon",
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": {"participation_conditions": "test"},
            },
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_list_campaigns(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        await client.post(
            "/api/v1/campaigns",
            json={
                "name": "活动1",
                "campaign_type": "coupon",
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        resp = await client.get("/api/v1/campaigns", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    @pytest.mark.anyio
    async def test_campaign_status_change(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        create = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "状态测试",
                "campaign_type": "coupon",
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        cid = create.json()["id"]
        resp = await client.post(
            f"/api/v1/campaigns/{cid}/status",
            json={"status": "active"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    @pytest.mark.anyio
    async def test_delete_draft_campaign(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        create = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "待删除",
                "campaign_type": "coupon",
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        cid = create.json()["id"]
        resp = await client.delete(f"/api/v1/campaigns/{cid}", headers=headers)
        assert resp.status_code == 200


class TestBenefitAndClaim:
    @pytest.mark.anyio
    async def test_create_benefit(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        campaign = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "权益测试",
                "campaign_type": "coupon",
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        cid = campaign.json()["id"]

        resp = await client.post(
            f"/api/v1/campaigns/{cid}/benefits",
            json={
                "name": "5元优惠券",
                "benefit_type": "platform_coupon",
                "config_json": {"amount": 5, "min_order": 20},
                "stock_total": 100,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["stock_total"] == 100
        assert resp.json()["stock_used"] == 0

    @pytest.mark.anyio
    async def test_claim_benefit_success(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        campaign = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "领取测试",
                "campaign_type": "coupon",
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        cid = campaign.json()["id"]
        benefit = await client.post(
            f"/api/v1/campaigns/{cid}/benefits",
            json={
                "name": "优惠券",
                "benefit_type": "platform_coupon",
                "config_json": {"amount": 10},
                "stock_total": 10,
            },
            headers=headers,
        )
        bid = benefit.json()["id"]

        resp = await client.post(
            f"/api/v1/campaigns/benefits/{bid}/claim",
            json={"consumer_id": "user-001", "idempotency_key": "key-001"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

    @pytest.mark.anyio
    async def test_claim_idempotent(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        campaign = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "幂等测试",
                "campaign_type": "coupon",
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        cid = campaign.json()["id"]
        benefit = await client.post(
            f"/api/v1/campaigns/{cid}/benefits",
            json={
                "name": "优惠券",
                "benefit_type": "platform_coupon",
                "config_json": {},
                "stock_total": 10,
            },
            headers=headers,
        )
        bid = benefit.json()["id"]

        # First claim
        await client.post(
            f"/api/v1/campaigns/benefits/{bid}/claim",
            json={"consumer_id": "user-002", "idempotency_key": "key-dup"},
            headers=headers,
        )
        # Duplicate claim
        resp = await client.post(
            f"/api/v1/campaigns/benefits/{bid}/claim",
            json={"consumer_id": "user-002", "idempotency_key": "key-dup"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "idempotent"

    @pytest.mark.anyio
    async def test_claim_out_of_stock(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        campaign = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "售罄测试",
                "campaign_type": "coupon",
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        cid = campaign.json()["id"]
        benefit = await client.post(
            f"/api/v1/campaigns/{cid}/benefits",
            json={
                "name": "限量券",
                "benefit_type": "platform_coupon",
                "config_json": {},
                "stock_total": 1,
            },
            headers=headers,
        )
        bid = benefit.json()["id"]

        # First claim succeeds
        await client.post(
            f"/api/v1/campaigns/benefits/{bid}/claim",
            json={"consumer_id": "user-a", "idempotency_key": "k-a"},
            headers=headers,
        )
        # Second claim fails
        resp = await client.post(
            f"/api/v1/campaigns/benefits/{bid}/claim",
            json={"consumer_id": "user-b", "idempotency_key": "k-b"},
            headers=headers,
        )
        assert resp.status_code == 410

    @pytest.mark.anyio
    async def test_claim_per_person_limit(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        campaign = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "限额测试",
                "campaign_type": "coupon",
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        cid = campaign.json()["id"]
        benefit = await client.post(
            f"/api/v1/campaigns/{cid}/benefits",
            json={
                "name": "每人限1",
                "benefit_type": "platform_coupon",
                "config_json": {},
                "stock_total": 100,
                "per_person_limit": 1,
            },
            headers=headers,
        )
        bid = benefit.json()["id"]

        # First claim
        await client.post(
            f"/api/v1/campaigns/benefits/{bid}/claim",
            json={"consumer_id": "user-x", "idempotency_key": "k-1"},
            headers=headers,
        )
        # Second claim same user
        resp = await client.post(
            f"/api/v1/campaigns/benefits/{bid}/claim",
            json={"consumer_id": "user-x", "idempotency_key": "k-2"},
            headers=headers,
        )
        assert resp.status_code == 403
