"""B3: 活动与权益 API 测试"""

import uuid
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
    return {"Authorization": f"Bearer {token}"}



RULES_JSON = {
    "participation_conditions": "扫码即可参与",
    "claim_limits": "每人限领1次",
    "validity_period": "领取后7天内有效",
    "disclaimer": "最终解释权归品牌方所有",
    "minor_notice": "未成年人请在监护人陪同下参与",
    "customer_service_contact": "400-123-4567",
}


async def create_product(client: AsyncClient, headers: dict[str, str], name: str = "活动产品") -> str:
    brand = await client.post("/api/v1/brands", json={"name": f"{name}品牌"}, headers=headers)
    brand_id = brand.json()["id"]
    product = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": name, "category": "大米"},
        headers=headers,
    )
    return product.json()["id"]


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
        headers=_platform_admin_headers(),
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
    async def test_create_campaign_rejects_invalid_structured_rules(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        resp = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "错误规则活动",
                "campaign_type": "lottery",
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": {
                    **RULES_JSON,
                    "campaign_goal": "lottery",
                    "participation_condition_type": "unknown",
                    "claim_limit_count": 0,
                },
            },
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_create_campaign_rejects_unverifiable_wecom_condition(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        resp = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "企微门槛活动",
                "campaign_type": "coupon",
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": {
                    **RULES_JSON,
                    "campaign_goal": "private_domain_repurchase",
                    "participation_condition_type": "wecom_required",
                    "claim_limit_count": 1,
                },
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
    async def test_create_campaign_with_product_and_list_stats(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        product_id = await create_product(client, headers)
        campaign = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "产品绑定活动",
                "campaign_type": "coupon",
                "product_id": product_id,
                "start_at": "2026-06-01T00:00:00",
                "end_at": "2026-06-30T23:59:59",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        assert campaign.status_code == 201
        cid = campaign.json()["id"]
        assert campaign.json()["product_id"] == product_id
        assert campaign.json()["product_name"] == "活动产品"

        benefit = await client.post(
            f"/api/v1/campaigns/{cid}/benefits",
            json={
                "name": "5元优惠券",
                "benefit_type": "platform_coupon",
                "config_json": {"amount": 5},
                "stock_total": 10,
            },
            headers=headers,
        )
        bid = benefit.json()["id"]
        await client.post(
            f"/api/v1/campaigns/benefits/{bid}/claim",
            json={"consumer_id": "campaign-user", "idempotency_key": "campaign-key"},
            headers=headers,
        )

        resp = await client.get(f"/api/v1/campaigns?product_id={product_id}", headers=headers)
        assert resp.status_code == 200
        item = next(i for i in resp.json()["items"] if i["id"] == cid)
        assert item["benefit_count"] == 1
        assert item["stock_total"] == 10
        assert item["stock_used"] == 1
        assert item["claim_count"] == 1

    @pytest.mark.anyio
    async def test_create_campaign_rejects_unknown_product(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        resp = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "不存在产品活动",
                "campaign_type": "coupon",
                "product_id": str(uuid.uuid4()),
                "start_at": "2026-06-01T00:00:00",
                "end_at": "2026-06-30T23:59:59",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Product not found"

    @pytest.mark.anyio
    async def test_campaign_legacy_rules_product_id_is_compatible(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        product_id = await create_product(client, headers, "旧活动产品")
        resp = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "旧规则产品活动",
                "campaign_type": "coupon",
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": {**RULES_JSON, "product_id": product_id},
            },
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["product_id"] == product_id
        assert resp.json()["product_name"] == "旧活动产品"

    @pytest.mark.anyio
    async def test_update_campaign_product_id(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        product_id = await create_product(client, headers, "更新产品")
        campaign = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "待更新活动",
                "campaign_type": "coupon",
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        cid = campaign.json()["id"]

        resp = await client.patch(
            f"/api/v1/campaigns/{cid}",
            json={"product_id": product_id, "rules_json": RULES_JSON},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["product_id"] == product_id
        assert resp.json()["rules_json"]["product_id"] == product_id

    @pytest.mark.anyio
    async def test_campaign_status_change(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        product_id = await create_product(client, headers, "上线产品")
        create = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "状态测试",
                "campaign_type": "coupon",
                "product_id": product_id,
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        cid = create.json()["id"]
        await client.post(
            f"/api/v1/campaigns/{cid}/benefits",
            json={
                "name": "上线权益",
                "benefit_type": "platform_coupon",
                "config_json": {"validity_type": "campaign_period"},
                "stock_total": 10,
            },
            headers=headers,
        )
        resp = await client.post(
            f"/api/v1/campaigns/{cid}/status",
            json={"status": "active"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    @pytest.mark.anyio
    async def test_campaign_status_change_rejects_incomplete_campaign(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        create = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "未闭环活动",
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
        assert resp.status_code == 400
        assert "活动未关联产品" in resp.json()["detail"]
        assert "活动未配置权益" in resp.json()["detail"]

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

    @pytest.mark.anyio
    async def test_get_campaign_detail(self, client: AsyncClient, auth_setup):
        """GET /campaigns/{id} 返回完整详情"""
        _, headers = auth_setup
        resp = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "详情测试",
                "campaign_type": "coupon",
                "start_at": "2025-01-01T00:00:00Z",
                "end_at": "2027-12-31T23:59:59Z",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        cid = resp.json()["id"]
        resp = await client.get(f"/api/v1/campaigns/{cid}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "详情测试"
        assert "computed_status" in data
        assert "benefit_count" in data

    @pytest.mark.anyio
    async def test_get_campaign_not_found(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        resp = await client.get(f"/api/v1/campaigns/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_delete_non_draft_campaign_rejected(self, client: AsyncClient, db_session: AsyncSession, auth_setup):
        """非草稿活动不可删除"""
        _, headers = auth_setup
        resp = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "不可删活动",
                "campaign_type": "coupon",
                "start_at": "2025-01-01T00:00:00Z",
                "end_at": "2027-12-31T23:59:59Z",
                "rules_json": RULES_JSON,
            },
            headers=headers,
        )
        cid = resp.json()["id"]
        # 手动激活
        from sqlalchemy import update as sa_update

        from app.models.campaign import Campaign

        await db_session.execute(
            sa_update(Campaign).where(Campaign.id == uuid.UUID(cid)).values(status="active")
        )
        await db_session.commit()

        resp = await client.delete(f"/api/v1/campaigns/{cid}", headers=headers)
        assert resp.status_code == 400

    @pytest.mark.anyio
    async def test_list_campaigns_filter_by_status(self, client: AsyncClient, auth_setup):
        """按状态过滤活动列表"""
        _, headers = auth_setup
        resp = await client.get("/api/v1/campaigns?status=draft", headers=headers)
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item["status"] == "draft"


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
    async def test_structured_campaign_rules_and_benefit_validity_are_persisted(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        product_id = await create_product(client, headers, "结构化活动产品")
        campaign = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "结构化活动",
                "campaign_type": "lottery",
                "product_id": product_id,
                "start_at": "2026-06-01",
                "end_at": "2026-06-30",
                "rules_json": {
                    **RULES_JSON,
                    "campaign_goal": "lottery",
                    "participation_condition_type": "first_scan",
                    "claim_limit_count": 1,
                },
            },
            headers=headers,
        )
        assert campaign.status_code == 201
        cid = campaign.json()["id"]
        assert campaign.json()["rules_json"]["participation_condition_type"] == "first_scan"
        assert campaign.json()["rules_json"]["claim_limit_count"] == 1

        benefit = await client.post(
            f"/api/v1/campaigns/{cid}/benefits",
            json={
                "name": "结构化奖品",
                "benefit_type": "platform_coupon",
                "config_json": {
                    "campaign_goal": "lottery",
                    "validity_type": "after_claim_days",
                    "validity_days": 7,
                    "validity_period": "领取后 7 天内有效",
                },
                "stock_total": 100,
                "per_person_limit": 1,
            },
            headers=headers,
        )
        assert benefit.status_code == 201
        assert benefit.json()["config_json"]["validity_type"] == "after_claim_days"
        assert benefit.json()["config_json"]["validity_days"] == 7

        detail = await client.get(f"/api/v1/benefits/{benefit.json()['id']}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["config_json"]["validity_period"] == "领取后 7 天内有效"

    @pytest.mark.anyio
    async def test_create_benefit_rejects_invalid_validity_config(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        campaign = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "权益校验活动",
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
                "name": "无效权益",
                "benefit_type": "platform_coupon",
                "config_json": {"validity_type": "after_claim_days", "validity_days": 0},
                "stock_total": 100,
            },
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_create_benefit_rejects_invalid_fixed_validity_range(self, client: AsyncClient, auth_setup):
        _, headers = auth_setup
        campaign = await client.post(
            "/api/v1/campaigns",
            json={
                "name": "权益日期校验活动",
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
                "name": "无效日期权益",
                "benefit_type": "platform_coupon",
                "config_json": {
                    "validity_type": "fixed_range",
                    "validity_start_at": "2026-06-10T00:00:00",
                    "validity_end_at": "2026-06-01T00:00:00",
                },
                "stock_total": 100,
            },
            headers=headers,
        )
        assert resp.status_code == 422

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
