"""Benefits API 测试"""

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
            "name": "权益测试租户",
            "admin_email": "benefit@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return tid, {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def campaign_and_benefit(client: AsyncClient, auth_setup):
    _, headers = auth_setup
    campaign = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "权益活动",
            "campaign_type": "coupon",
            "start_at": "2026-06-01T00:00:00",
            "end_at": "2026-06-30T23:59:59",
            "rules_json": RULES_JSON,
        },
        headers=headers,
    )
    cid = campaign.json()["id"]
    benefit = await client.post(
        f"/api/v1/campaigns/{cid}/benefits",
        json={
            "name": "测试权益",
            "benefit_type": "platform_coupon",
            "config_json": {"amount": 10, "min_order": 50},
            "stock_total": 100,
            "per_person_limit": 2,
        },
        headers=headers,
    )
    bid = benefit.json()["id"]
    return cid, bid, headers


class TestBenefitsList:
    @pytest.mark.anyio
    async def test_list_benefits_paginated(
        self, client: AsyncClient, campaign_and_benefit
    ):
        _, _, headers = campaign_and_benefit
        resp = await client.get("/api/v1/benefits", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 1
        assert len(data["items"]) >= 1

    @pytest.mark.anyio
    async def test_list_benefits_with_pagination(
        self, client: AsyncClient, campaign_and_benefit
    ):
        _, _, headers = campaign_and_benefit
        resp = await client.get("/api/v1/benefits?page=1&page_size=10", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 1
        assert data["page_size"] == 10


class TestBenefitDetail:
    @pytest.mark.anyio
    async def test_get_benefit_by_id(
        self, client: AsyncClient, campaign_and_benefit
    ):
        _, bid, headers = campaign_and_benefit
        resp = await client.get(f"/api/v1/benefits/{bid}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == bid
        assert data["name"] == "测试权益"
        assert data["benefit_type"] == "platform_coupon"
        assert data["stock_total"] == 100
        assert data["stock_used"] == 0
        assert data["per_person_limit"] == 2
        assert data["config_json"]["amount"] == 10

    @pytest.mark.anyio
    async def test_get_benefit_not_found(
        self, client: AsyncClient, campaign_and_benefit
    ):
        _, _, headers = campaign_and_benefit
        resp = await client.get(
            "/api/v1/benefits/00000000-0000-0000-0000-000000000099", headers=headers
        )
        assert resp.status_code == 404


class TestBenefitUpdate:
    @pytest.mark.anyio
    async def test_update_benefit_name_and_stock(
        self, client: AsyncClient, campaign_and_benefit
    ):
        _, bid, headers = campaign_and_benefit
        resp = await client.patch(
            f"/api/v1/benefits/{bid}",
            json={"name": "更新后的权益", "stock_total": 200},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "更新后的权益"
        assert data["stock_total"] == 200

    @pytest.mark.anyio
    async def test_update_benefit_config_json(
        self, client: AsyncClient, campaign_and_benefit
    ):
        _, bid, headers = campaign_and_benefit
        resp = await client.patch(
            f"/api/v1/benefits/{bid}",
            json={"config_json": {"amount": 20, "min_order": 100}},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["config_json"]["amount"] == 20
        assert data["config_json"]["min_order"] == 100

    @pytest.mark.anyio
    async def test_update_benefit_not_found(
        self, client: AsyncClient, campaign_and_benefit
    ):
        _, _, headers = campaign_and_benefit
        resp = await client.patch(
            "/api/v1/benefits/00000000-0000-0000-0000-000000000099",
            json={"name": "x"},
            headers=headers,
        )
        assert resp.status_code == 404


class TestBenefitDelete:
    @pytest.mark.anyio
    async def test_delete_benefit(
        self, client: AsyncClient, campaign_and_benefit
    ):
        _, bid, headers = campaign_and_benefit
        resp = await client.delete(f"/api/v1/benefits/{bid}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        # Verify it's gone
        resp2 = await client.get(f"/api/v1/benefits/{bid}", headers=headers)
        assert resp2.status_code == 404

    @pytest.mark.anyio
    async def test_delete_benefit_not_found(
        self, client: AsyncClient, campaign_and_benefit
    ):
        _, _, headers = campaign_and_benefit
        resp = await client.delete(
            "/api/v1/benefits/00000000-0000-0000-0000-000000000099", headers=headers
        )
        assert resp.status_code == 404


class TestBenefitClaimsAdmin:
    @pytest.mark.anyio
    async def test_list_benefit_claims_admin(
        self, client: AsyncClient, campaign_and_benefit
    ):
        cid, bid, headers = campaign_and_benefit
        # Create a claim first
        await client.post(
            f"/api/v1/campaigns/benefits/{bid}/claim",
            json={"consumer_id": "user-001", "idempotency_key": "key-001"},
            headers=headers,
        )

        resp = await client.get("/api/v1/benefits/admin/claims", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 1
        claim = data["items"][0]
        assert claim["benefit_id"] == bid
        assert claim["campaign_id"] == cid
        assert claim["consumer_id"] == "user-001"

    @pytest.mark.anyio
    async def test_list_benefit_claims_admin_pagination(
        self, client: AsyncClient, campaign_and_benefit
    ):
        _, bid, headers = campaign_and_benefit
        await client.post(
            f"/api/v1/campaigns/benefits/{bid}/claim",
            json={"consumer_id": "user-002", "idempotency_key": "key-002"},
            headers=headers,
        )

        resp = await client.get(
            "/api/v1/benefits/admin/claims?page=1&page_size=5", headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 1
        assert data["page_size"] == 5
