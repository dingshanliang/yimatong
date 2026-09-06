"""A6-010: 端到端扫码流程后端测试"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.campaign import Campaign, CampaignStatus
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
async def e2e_setup(client: AsyncClient, db_session: AsyncSession):
    """完整链路：租户→品牌→产品→SKU→码批次→模板→发布"""
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "E2E扫码",
            "admin_email": "e2e@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "E2E品牌"}, headers=headers)
    prod = await client.post(
        "/api/v1/products",
        json={"brand_id": brand.json()["id"], "name": "E2E产品"},
        headers=headers,
    )
    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": prod.json()["id"], "code": "E2E-SKU", "name": "E2E SKU"},
        headers=headers,
    )
    production_batch = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": prod.json()["id"],
            "sku_id": sku.json()["id"],
            "batch_code": "E2E-PB-001",
            "production_date": "2026-07-01",
            "expiry_date": "2027-07-01",
        },
        headers=headers,
    )
    batch = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": prod.json()["id"],
            "sku_id": sku.json()["id"],
            "production_batch_id": production_batch.json()["id"],
            "batch_code": "E2E-001",
            "quantity": 3,
        },
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    batch_id = batch.json()["id"]

    # 创建并发布模板
    tmpl = await client.post(
        "/api/v1/page-templates",
        json={
            "name": "E2E模板",
            "template_type": "product_info",
            "product_id": prod.json()["id"],
        },
        headers=headers,
    )
    ver = await client.post(
        f"/api/v1/page-templates/{tmpl.json()['id']}/versions",
        json={"config_json": {"brand_name": "E2E品牌", "product_name": "E2E产品"}},
        headers=headers,
    )
    await client.post(
        f"/api/v1/page-versions/{ver.json()['id']}/publish",
        headers=headers,
    )

    # 激活码（同步生成的批次为 completed 状态，需先走 导出→印刷→交付 生命周期）
    exported = await client.post(
        f"/api/v1/code-batches/{batch_id}/export",
        json={"reason": "E2E测试导出"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert exported.status_code in (200, 201), f"导出码批次失败: {exported.text}"
    printing = await client.post(f"/api/v1/code-batches/{batch_id}/mark-printing", headers=headers)
    assert printing.status_code in (200, 201), f"标记印刷失败: {printing.text}"
    delivered = await client.post(
        f"/api/v1/code-batches/{batch_id}/mark-delivered",
        json={"reason": "E2E测试交付", "recipient": "测试收货人", "confirm": "deliver"},
        headers=headers,
    )
    assert delivered.status_code in (200, 201), f"标记交付失败: {delivered.text}"
    await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)

    # 创建并激活活动（resolver 需要 live LaunchRelease 才渲染模板页）
    campaign = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "E2E首扫活动",
            "campaign_type": "coupon",
            "product_id": prod.json()["id"],
            "start_at": "2026-01-01T00:00:00",
            "end_at": "2026-12-31T23:59:59",
            "rules_json": {
                "campaign_goal": "first_scan_coupon",
                "participation_condition_type": "first_scan",
                "participation_conditions": "首次扫码用户可参与",
                "claim_limits": {"per_user": 1, "per_day": 1},
                "validity_period": {"type": "campaign_period"},
                "disclaimer": "本活动最终解释权归品牌方所有",
                "minor_notice": "未成年人请在监护人陪同下参与",
                "customer_service_contact": "400-000-0000",
            },
        },
        headers=headers,
    )
    assert campaign.status_code in (200, 201), f"创建活动失败: {campaign.text}"
    # 激活活动（直接在 DB 中设置状态，绕过业务 blocker 检查）
    await db_session.execute(
        sa_update(Campaign).where(Campaign.id == uuid.UUID(campaign.json()["id"])).values(status=CampaignStatus.ACTIVE)
    )
    await db_session.commit()

    # 创建、确认并上线扫码页发布
    release = await client.post(
        "/api/v1/launch-releases",
        json={
            "page_version_id": ver.json()["id"],
            "campaign_id": campaign.json()["id"],
            "code_batch_id": batch_id,
            "idempotency_key": str(uuid.uuid4()),
        },
        headers=headers,
    )
    assert release.status_code in (200, 201), f"创建上线发布失败: {release.text}"
    confirmed_release = await client.post(
        f"/api/v1/launch-releases/{release.json()['id']}/confirm",
        json={"idempotency_key": str(uuid.uuid4())},
        headers=headers,
    )
    assert confirmed_release.status_code == 200, f"确认上线发布失败: {confirmed_release.text}"
    launched = await client.post(
        f"/api/v1/launch-releases/{release.json()['id']}/launch",
        json={"idempotency_key": str(uuid.uuid4())},
        headers=headers,
    )
    assert launched.status_code == 200, f"上线发布失败: {launched.text}"

    items = await client.get(
        f"/api/v1/code-items?code_batch_id={batch_id}",
        headers=headers,
    )
    return tid, headers, [i["public_id"] for i in items.json()["items"]]


class TestE2EScanFlow:
    @pytest.mark.anyio
    async def test_scan_returns_page_content(self, client: AsyncClient, e2e_setup):
        _, _, public_ids = e2e_setup
        # 落地页渲染按微信场景设计；非微信 UA 返回引导页（见 resolver）
        resp = await client.get(
            f"/c/{public_ids[0]}",
            headers={"User-Agent": "Mozilla/5.0 (iPhone) MicroMessenger/8.0"},
        )
        assert resp.status_code == 200
        assert "E2E品牌" in resp.text
        assert "E2E产品" in resp.text

    @pytest.mark.anyio
    async def test_scan_with_wechat_ua(self, client: AsyncClient, e2e_setup):
        _, _, public_ids = e2e_setup
        resp = await client.get(
            f"/c/{public_ids[0]}",
            headers={"User-Agent": "MicroMessenger/8.0.38"},
        )
        assert resp.status_code == 200
        assert "E2E品牌" in resp.text

    @pytest.mark.anyio
    async def test_repeated_scan_works(self, client: AsyncClient, e2e_setup):
        _, _, public_ids = e2e_setup
        # 多次扫码
        for _ in range(3):
            resp = await client.get(f"/c/{public_ids[0]}")
            assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_different_codes_resolve_independently(self, client: AsyncClient, e2e_setup):
        _, _, public_ids = e2e_setup
        r1 = await client.get(f"/c/{public_ids[0]}")
        r2 = await client.get(f"/c/{public_ids[1]}")
        assert r1.status_code == 200
        assert r2.status_code == 200
