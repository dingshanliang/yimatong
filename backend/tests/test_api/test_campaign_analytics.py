"""B4-004: 活动看板 API 测试"""

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
    "participation_conditions": "扫码参与",
    "claim_limits": "限1次",
    "validity_period": "7天",
    "disclaimer": "品牌方保留解释权",
    "minor_notice": "需监护人陪同",
    "customer_service_contact": "400-000-0000",
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
async def setup_campaigns(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "看板测试",
            "admin_email": "dashboard2@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    # 创建两个活动
    c1 = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "活动A",
            "campaign_type": "coupon",
            "start_at": "2026-06-01",
            "end_at": "2026-06-30",
            "rules_json": RULES_JSON,
        },
        headers=headers,
    )
    c2 = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "活动B",
            "campaign_type": "coupon",
            "start_at": "2026-06-01",
            "end_at": "2026-06-30",
            "rules_json": RULES_JSON,
        },
        headers=headers,
    )
    return tid, headers, c1.json()["id"], c2.json()["id"]


class TestCampaignAnalytics:
    @pytest.mark.anyio
    async def test_campaign_funnel(self, client: AsyncClient, setup_campaigns):
        _, headers, c1_id, _ = setup_campaigns
        resp = await client.get(
            f"/api/v1/campaigns/analytics/funnel?campaign_id={c1_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["campaign_id"] == c1_id
        assert len(data["steps"]) >= 2

    @pytest.mark.anyio
    async def test_campaign_funnel_not_found(self, client: AsyncClient, setup_campaigns):
        _, headers, _, _ = setup_campaigns
        resp = await client.get(
            "/api/v1/campaigns/analytics/funnel?campaign_id=00000000-0000-0000-0000-000000000099",
            headers=headers,
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_campaign_comparison(self, client: AsyncClient, setup_campaigns):
        _, headers, c1_id, c2_id = setup_campaigns
        resp = await client.get(
            f"/api/v1/campaigns/analytics/comparison?campaign_ids={c1_id},{c2_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = [d["campaign_name"] for d in data]
        assert "活动A" in names
        assert "活动B" in names
