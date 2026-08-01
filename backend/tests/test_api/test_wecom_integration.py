"""Enterprise WeChat integration and claim gate tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.services.scan_token import create_scan_token
from app.utils.client_ip import compute_ip_hash
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
            "name": "企微测试",
            "admin_email": "wecom@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tenant_id = resp.json()["id"]
    token = create_access_token(tenant_id, "00000000-0000-0000-0000-000000000001", "admin")
    return tenant_id, {"Authorization": f"Bearer {token}"}


def _scan_token(tenant_id: str, public_id: str) -> str:
    return create_scan_token(public_id, compute_ip_hash("127.0.0.1"), tenant_id=tenant_id)


async def create_product(client: AsyncClient, headers: dict[str, str]) -> str:
    brand = await client.post("/api/v1/brands", json={"name": "企微品牌"}, headers=headers)
    product = await client.post(
        "/api/v1/products",
        json={"brand_id": brand.json()["id"], "name": "企微活动产品", "category": "大米"},
        headers=headers,
    )
    return product.json()["id"]


@pytest.mark.anyio
async def test_wecom_required_claim_waits_for_callback_then_allows_claim(client: AsyncClient, auth_setup):
    tenant_id, headers = auth_setup
    product_id = await create_product(client, headers)

    config = await client.post(
        "/api/v1/integrations/wecom",
        json={
            "corp_id": "ww-test",
            "secret": "test-secret",
            "customer_service_user_ids": ["member-a"],
            "mock_mode": True,
        },
        headers=headers,
    )
    assert config.status_code == 200
    verify = await client.post("/api/v1/integrations/wecom/verify", headers=headers)
    assert verify.status_code == 200
    assert verify.json()["connected"] is True

    campaign = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "加企微后领取",
            "campaign_type": "coupon",
            "product_id": product_id,
            "start_at": "2026-06-01T00:00:00",
            "end_at": "2026-06-30T23:59:59",
            "rules_json": {**RULES_JSON, "wecom_mode": "required"},
        },
        headers=headers,
    )
    assert campaign.status_code == 201
    benefit = await client.post(
        f"/api/v1/campaigns/{campaign.json()['id']}/benefits",
        json={
            "name": "企微领取券",
            "benefit_type": "platform_coupon",
            "config_json": {"validity_type": "campaign_period"},
            "stock_total": 10,
        },
        headers=headers,
    )
    benefit_id = benefit.json()["id"]
    scan_token = _scan_token(tenant_id, "PUBLIC12345")

    blocked = await client.post(
        "/api/v1/benefit-claims",
        json={"benefit_id": benefit_id, "scan_token": scan_token},
    )
    assert blocked.status_code == 403
    detail = blocked.json()["detail"]
    assert detail["code"] == "require_wecom_contact"
    assert detail["qr_code"]

    added = await client.get(f"/api/v1/integrations/wecom/mock-added?state={detail['state']}")
    assert added.status_code == 200

    claimed = await client.post(
        "/api/v1/benefit-claims",
        json={"benefit_id": benefit_id, "scan_token": scan_token},
    )
    assert claimed.status_code == 201
    assert claimed.json()["status"] == "claimed"


@pytest.mark.anyio
async def test_wecom_guide_mode_does_not_block_claim(client: AsyncClient, auth_setup):
    tenant_id, headers = auth_setup
    product_id = await create_product(client, headers)
    campaign = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "引导添加企微",
            "campaign_type": "coupon",
            "product_id": product_id,
            "start_at": "2026-06-01T00:00:00",
            "end_at": "2026-06-30T23:59:59",
            "rules_json": {**RULES_JSON, "wecom_mode": "guide"},
        },
        headers=headers,
    )
    benefit = await client.post(
        f"/api/v1/campaigns/{campaign.json()['id']}/benefits",
        json={
            "name": "直接领取券",
            "benefit_type": "platform_coupon",
            "config_json": {"validity_type": "campaign_period"},
            "stock_total": 10,
        },
        headers=headers,
    )
    claimed = await client.post(
        "/api/v1/benefit-claims",
        json={"benefit_id": benefit.json()["id"], "scan_token": _scan_token(tenant_id, "PUBLIC67890")},
    )
    assert claimed.status_code == 201
    assert claimed.json()["status"] == "claimed"
