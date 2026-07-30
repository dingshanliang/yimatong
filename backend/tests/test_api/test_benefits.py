"""Benefits API 测试"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.campaign import BenefitClaim
from app.models.connector import BenefitDelivery
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    from app.utils.security import create_access_token

    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {"Authorization": f"Bearer {token}"}


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
        headers=_platform_admin_headers(),
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
    async def test_create_standalone_benefit(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        resp = await client.post(
            "/api/v1/benefits",
            json={
                "name": "权益库优惠券",
                "benefit_type": "platform_coupon",
                "config_json": {"amount": 20, "validity_period": "领取后7天内有效"},
                "stock_total": 100,
                "per_person_limit": 1,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "权益库优惠券"
        assert data["campaign_id"] is None

    @pytest.mark.anyio
    async def test_attach_standalone_benefit_to_campaign(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        campaign = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "使用权益活动",
                "campaign_type": "coupon",
                "start_at": "2026-06-01T00:00:00",
                "end_at": "2026-06-30T23:59:59",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        benefit = await client.post(
            "/api/v1/benefits",
            json={
                "name": "可选用权益",
                "benefit_type": "platform_coupon",
                "config_json": {"amount": 10},
                "stock_total": 50,
                "per_person_limit": 1,
            },
            headers=headers,
        )
        resp = await client.post(
            f"/api/v1/campaigns/{campaign.json()['id']}/benefits/{benefit.json()['id']}/attach",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["campaign_id"] == campaign.json()["id"]

    @pytest.mark.anyio
    async def test_list_benefits_paginated(self, client: AsyncClient, campaign_and_benefit):
        _, _, headers = campaign_and_benefit
        resp = await client.get("/api/v1/benefits", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 1
        assert len(data["items"]) >= 1

    @pytest.mark.anyio
    async def test_list_benefits_with_pagination(self, client: AsyncClient, campaign_and_benefit):
        _, _, headers = campaign_and_benefit
        resp = await client.get("/api/v1/benefits?page=1&page_size=10", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 1
        assert data["page_size"] == 10

    @pytest.mark.anyio
    async def test_list_benefits_filters_and_summary(self, client: AsyncClient, campaign_and_benefit):
        _, _, headers = campaign_and_benefit
        filtered = await client.get("/api/v1/benefits?benefit_type=platform_coupon&status=active", headers=headers)
        assert filtered.status_code == 200
        assert filtered.json()["total"] >= 1

        summary = await client.get("/api/v1/benefits/summary", headers=headers)
        assert summary.status_code == 200
        data = summary.json()
        assert data["total"] >= 1
        assert data["stock_total"] >= 100
        assert data["stock_remaining"] >= 100

    @pytest.mark.anyio
    async def test_external_link_requires_url(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        resp = await client.post(
            "/api/v1/benefits",
            json={
                "name": "外链权益",
                "benefit_type": "external_link",
                "config_json": {},
                "stock_total": 10,
                "per_person_limit": 1,
            },
            headers=headers,
        )
        assert resp.status_code == 422


class TestBenefitDetail:
    @pytest.mark.anyio
    async def test_get_benefit_by_id(self, client: AsyncClient, campaign_and_benefit):
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
    async def test_get_benefit_not_found(self, client: AsyncClient, campaign_and_benefit):
        _, _, headers = campaign_and_benefit
        resp = await client.get("/api/v1/benefits/00000000-0000-0000-0000-000000000099", headers=headers)
        assert resp.status_code == 404


class TestBenefitUpdate:
    @pytest.mark.anyio
    async def test_update_benefit_name_and_stock(self, client: AsyncClient, campaign_and_benefit):
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
    async def test_update_benefit_config_json(self, client: AsyncClient, campaign_and_benefit):
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
    async def test_update_benefit_not_found(self, client: AsyncClient, campaign_and_benefit):
        _, _, headers = campaign_and_benefit
        resp = await client.patch(
            "/api/v1/benefits/00000000-0000-0000-0000-000000000099",
            json={"name": "x"},
            headers=headers,
        )
        assert resp.status_code == 404


class TestBenefitDelete:
    @pytest.mark.anyio
    async def test_delete_unused_standalone_benefit(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        benefit = await client.post(
            "/api/v1/benefits",
            json={
                "name": "可删除权益",
                "benefit_type": "platform_coupon",
                "config_json": {"amount": 5},
                "stock_total": 10,
                "per_person_limit": 1,
            },
            headers=headers,
        )
        bid = benefit.json()["id"]
        resp = await client.delete(f"/api/v1/benefits/{bid}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        resp2 = await client.get(f"/api/v1/benefits/{bid}", headers=headers)
        assert resp2.status_code == 404

    @pytest.mark.anyio
    async def test_delete_attached_benefit_requires_disable(self, client: AsyncClient, campaign_and_benefit):
        _, bid, headers = campaign_and_benefit
        resp = await client.delete(f"/api/v1/benefits/{bid}", headers=headers)
        assert resp.status_code == 409
        assert "不能删除" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_delete_benefit_not_found(self, client: AsyncClient, campaign_and_benefit):
        _, _, headers = campaign_and_benefit
        resp = await client.delete("/api/v1/benefits/00000000-0000-0000-0000-000000000099", headers=headers)
        assert resp.status_code == 404


class TestBenefitClaimsAdmin:
    @pytest.mark.anyio
    async def test_list_benefit_claims_admin(self, client: AsyncClient, campaign_and_benefit):
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
        assert claim["benefit_name"] == "测试权益"
        assert claim["campaign_name"] == "权益活动"
        assert claim["claimed_at"]

        benefit = await client.get(f"/api/v1/benefits/{bid}", headers=headers)
        assert benefit.json()["stock_total"] == 100
        assert benefit.json()["stock_used"] == 1

    @pytest.mark.anyio
    async def test_list_benefit_claims_admin_pagination(self, client: AsyncClient, campaign_and_benefit):
        _, bid, headers = campaign_and_benefit
        await client.post(
            f"/api/v1/campaigns/benefits/{bid}/claim",
            json={"consumer_id": "user-002", "idempotency_key": "key-002"},
            headers=headers,
        )

        resp = await client.get("/api/v1/benefits/admin/claims?page=1&page_size=5", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 1
        assert data["page_size"] == 5

    @pytest.mark.anyio
    async def test_list_benefit_claims_admin_normalizes_status_and_filters(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        auth_setup,
    ):
        tenant_id, headers = auth_setup
        campaign = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "筛选活动",
                "campaign_type": "coupon",
                "start_at": "2026-06-01T00:00:00",
                "end_at": "2026-06-30T23:59:59",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        cid = campaign.json()["id"]
        first_benefit = await client.post(
            f"/api/v1/campaigns/{cid}/benefits",
            json={
                "name": "发放失败权益",
                "benefit_type": "platform_coupon",
                "config_json": {"amount": 10},
                "stock_total": 100,
                "per_person_limit": 2,
            },
            headers=headers,
        )
        second_benefit = await client.post(
            f"/api/v1/campaigns/{cid}/benefits",
            json={
                "name": "正常权益",
                "benefit_type": "platform_coupon",
                "config_json": {"amount": 5},
                "stock_total": 100,
                "per_person_limit": 2,
            },
            headers=headers,
        )
        first_bid = first_benefit.json()["id"]
        second_bid = second_benefit.json()["id"]

        await client.post(
            f"/api/v1/campaigns/benefits/{first_bid}/claim",
            json={"consumer_id": "consumer-failed-001", "idempotency_key": "claim-filter-001"},
            headers=headers,
        )
        await client.post(
            f"/api/v1/campaigns/benefits/{second_bid}/claim",
            json={"consumer_id": "consumer-normal-002", "idempotency_key": "claim-filter-002"},
            headers=headers,
        )

        failed_claim = (
            await db_session.execute(
                select(BenefitClaim).where(
                    BenefitClaim.benefit_id == uuid.UUID(first_bid),
                    BenefitClaim.consumer_id == "consumer-failed-001",
                )
            )
        ).scalar_one()
        failed_claim.delivery_status = "failed"
        failed_claim.created_at = None
        delivery_id = uuid.uuid4()
        db_session.add(
            BenefitDelivery(
                id=delivery_id,
                tenant_id=uuid.UUID(tenant_id),
                connector_id=uuid.uuid4(),
                consumer_id="consumer-failed-001",
                benefit_type="coupon",
                benefit_config={"benefit_id": first_bid},
                status="failed",
                retry_count=2,
                max_retries=5,
                benefit_id=uuid.UUID(first_bid),
                claim_id=failed_claim.id,
                next_retry_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
            )
        )
        await db_session.flush()

        resp = await client.get(
            (
                "/api/v1/benefits/admin/claims"
                f"?q=failed&benefit_id={first_bid}&campaign_id={cid}"
                "&status=claimed&delivery_status=failed"
            ),
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        claim = data["items"][0]
        assert claim["consumer_id"] == "consumer-failed-001"
        assert claim["status"] == "claimed"
        assert claim["delivery_status"] == "failed"
        assert claim["claimed_at"] is None
        assert claim["latest_delivery_id"] == str(delivery_id)
        assert claim["latest_delivery_status"] == "failed"
        assert claim["delivery_retry_count"] == 2
        assert claim["delivery_next_retry_at"].startswith("2026-06-01T12:00:00")


class TestBenefitTypeValidation:
    """验证不同权益类型的配置验证"""

    @pytest.mark.anyio
    async def test_cash_red_packet_requires_connector(self, client: AsyncClient, auth_setup):
        """现金红包必须关联 connector_id"""
        _, headers = auth_setup
        resp = await client.post(
            "/api/v1/benefits",
            json={
                "name": "红包测试",
                "benefit_type": "cash_red_packet",
                "config_json": {"amount_type": "fixed", "fixed_amount": 100},
                "stock_total": 100,
                "per_person_limit": 1,
            },
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_private_domain_requires_qr_image(self, client: AsyncClient, auth_setup):
        """私域权益必须提供 qr_image_url"""
        _, headers = auth_setup
        resp = await client.post(
            "/api/v1/benefits",
            json={
                "name": "私域测试",
                "benefit_type": "private_domain",
                "config_json": {},
                "stock_total": 100,
                "per_person_limit": 1,
            },
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_benefit_status_toggle(self, client: AsyncClient, auth_setup):
        """权益状态切换 active → inactive → active"""
        _, headers = auth_setup
        resp = await client.post(
            "/api/v1/benefits",
            json={
                "name": "状态切换测试",
                "benefit_type": "platform_coupon",
                "config_json": {"url": "https://example.com"},
                "stock_total": 100,
                "per_person_limit": 1,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        bid = resp.json()["id"]

        # 切为 inactive
        resp = await client.patch(
            f"/api/v1/benefits/{bid}",
            json={"status": "inactive"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "inactive"

        # 切回 active
        resp = await client.patch(
            f"/api/v1/benefits/{bid}",
            json={"status": "active"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"
