"""验证 resolver JSON 响应结构完整性"""

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


@pytest.fixture
async def db_session():
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
async def traceability_setup(client: AsyncClient):
    """创建带溯源数据的完整链路"""
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "溯源测试",
            "admin_email": "trace@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    assert resp.status_code in (200, 201)
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "溯源品牌"}, headers=headers)
    assert brand.status_code in (200, 201)

    prod = await client.post(
        "/api/v1/products",
        json={
            "brand_id": brand.json()["id"],
            "name": "溯源大米",
            "description": "测试溯源",
            "origin": "黑龙江五常",
            "image_url": "https://example.com/rice.jpg",
        },
        headers=headers,
    )
    assert prod.status_code in (200, 201)

    sku = await client.post(
        "/api/v1/skus",
        json={
            "product_id": prod.json()["id"],
            "code": "TRACE-SKU",
            "name": "5kg装",
        },
        headers=headers,
    )
    assert sku.status_code in (200, 201)

    pb = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": prod.json()["id"],
            "sku_id": sku.json()["id"],
            "batch_code": "PB-TRACE-001",
            "production_date": "2026-03-01",
            "expiry_date": "2027-03-01",
            "origin": "黑龙江省五常市",
        },
        headers=headers,
    )
    assert pb.status_code in (200, 201)

    batch = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": prod.json()["id"],
            "sku_id": sku.json()["id"],
            "production_batch_id": pb.json()["id"],
            "batch_code": "CB-TRACE-001",
            "quantity": 2,
        },
        headers=headers,
    )
    assert batch.status_code in (200, 201)

    await client.post(f"/api/v1/code-batches/{batch.json()['id']}/activate", headers=headers)

    items = await client.get(
        f"/api/v1/code-items?code_batch_id={batch.json()['id']}",
        headers=headers,
    )
    return items.json()["items"][0]["public_id"]


class TestResolverJsonResponse:
    @pytest.mark.anyio
    async def test_json_has_product_image_url(self, client, traceability_setup):
        """产品图片应该用 image_url 而非空 images 数组"""
        resp = await client.get(
            f"/c/{traceability_setup}",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        product = data["code_data"]["product"]
        assert "image_url" in product, "缺少 image_url 字段"
        assert product["image_url"] == "https://example.com/rice.jpg"

    @pytest.mark.anyio
    async def test_json_has_traceability_data(self, client, traceability_setup):
        """响应应包含溯源信息（产地、生产日期、保质期、批次号）"""
        resp = await client.get(
            f"/c/{traceability_setup}",
            headers={"Accept": "application/json"},
        )
        data = resp.json()
        batch = data.get("batch") or data.get("code_data", {}).get("batch")
        assert batch is not None, "缺少溯源/批次信息"
        assert batch.get("batch_code") == "PB-TRACE-001"
        assert batch.get("origin") == "黑龙江省五常市"
        assert batch.get("production_date") is not None
        assert batch.get("expiry_date") is not None

    @pytest.mark.anyio
    async def test_first_scan_count(self, client, traceability_setup):
        """yimatong-zgb1.4：scan_count 改为 post-insert 语义（含本次），
        首次查验 = 1（旧 0 语义已废弃）。``verification_count`` 是新契约字段，
        与 scan_count 同值（scan_count 兼容别名）。
        """
        resp = await client.get(
            f"/c/{traceability_setup}",
            headers={"Accept": "application/json"},
        )
        data = resp.json()
        assert data["scan_info"]["is_first_scan"] is True
        # post-insert 语义：含本次，首次 = 1
        assert data["scan_info"]["scan_count"] == 1, "首次查验 scan_count 应为 1（含本次）"
        # 新契约字段，与 scan_count 同值
        assert data["scan_info"]["verification_count"] == 1
        # 新增首查时间字段，读自 code_items.first_scanned_at
        assert data["scan_info"]["first_scan_time"] is not None
        assert data["scan_info"]["verification_time"] is not None
