"""W11: 渠道流向绑定测试"""

from collections.abc import AsyncGenerator
from uuid import UUID

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
            "name": "渠道测试租户",
            "admin_email": "channel@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "测试品牌"}, headers=headers)
    product = await client.post(
        "/api/v1/products",
        json={"brand_id": brand.json()["id"], "name": "测试产品"},
        headers=headers,
    )
    sku = await client.post(
        "/api/v1/skus",
        json={
            "product_id": product.json()["id"],
            "code": "SKU-CH",
            "name": "默认规格",
            "specifications": {},
        },
        headers=headers,
    )
    return tid, headers, product.json()["id"], sku.json()["id"]


class TestDistributorCRUD:
    """W11-002: 经销商 CRUD"""

    @pytest.mark.anyio
    async def test_create_distributor(self, client: AsyncClient, setup_tenant):
        tid, headers, *_ = setup_tenant
        resp = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "华东经销商", "code": "DIST-001"},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "华东经销商"
        assert resp.json()["code"] == "DIST-001"

    @pytest.mark.anyio
    async def test_list_distributors(self, client: AsyncClient, setup_tenant):
        tid, headers, *_ = setup_tenant
        await client.post(
            "/api/v1/channels/distributors",
            json={"name": "经销商A", "code": "DIST-A"},
            headers=headers,
        )
        resp = await client.get("/api/v1/channels/distributors", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1


class TestRegionCRUD:
    """W11-002: 区域 CRUD"""

    @pytest.mark.anyio
    async def test_create_region(self, client: AsyncClient, setup_tenant):
        tid, headers, *_ = setup_tenant
        resp = await client.post(
            "/api/v1/channels/regions",
            json={"name": "上海区域", "code": "REG-SH", "province": "上海", "city": "上海"},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["city"] == "上海"

    @pytest.mark.anyio
    async def test_list_regions(self, client: AsyncClient, setup_tenant):
        tid, headers, *_ = setup_tenant
        await client.post(
            "/api/v1/channels/regions",
            json={"name": "北京区域", "code": "REG-BJ", "city": "北京"},
            headers=headers,
        )
        resp = await client.get("/api/v1/channels/regions", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1


class TestStoreCRUD:
    """W11-002: 门店 CRUD"""

    @pytest.mark.anyio
    async def test_create_store(self, client: AsyncClient, setup_tenant):
        tid, headers, *_ = setup_tenant
        resp = await client.post(
            "/api/v1/channels/stores",
            json={"name": "旗舰店", "code": "STORE-001", "address": "上海市中心"},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "旗舰店"


class TestBatchAssignment:
    """W11-003: 码批次分配给经销商/区域"""

    @pytest.mark.anyio
    async def test_assign_batch_to_region(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers, product_id, sku_id = setup_tenant

        # 创建区域
        region = await client.post(
            "/api/v1/channels/regions",
            json={"name": "上海区域", "code": "REG-TEST", "city": "上海"},
            headers=headers,
        )
        region_id = region.json()["id"]

        # 创建码批次
        batch = await client.post(
            "/api/v1/code-batches",
            json={"product_id": product_id, "sku_id": sku_id, "batch_code": "CH-BATCH", "quantity": 10},
            headers=headers,
        )
        batch_id = batch.json()["id"]

        # 分配
        resp = await client.post(
            f"/api/v1/channels/code-batches/{batch_id}/assign",
            json={"region_id": region_id},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["region_id"] == region_id


class TestIPResolution:
    """W11-004: IP → 城市解析"""

    @pytest.mark.anyio
    async def test_resolve_known_ip(self):
        from app.services.geoip import resolve_ip_to_city

        assert resolve_ip_to_city("110.1.2.3") == "北京"
        assert resolve_ip_to_city("120.1.2.3") == "上海"
        assert resolve_ip_to_city("113.1.2.3") == "广东"

    @pytest.mark.anyio
    async def test_resolve_unknown_ip(self):
        from app.services.geoip import resolve_ip_to_city

        assert resolve_ip_to_city("8.8.8.8") is None


class TestDiversionDetection:
    """W11-005: 跨区扫码线索检测"""

    @pytest.mark.anyio
    async def test_diversion_creates_clue(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, product_id, sku_id = setup_tenant

        # 创建上海区域
        region = await client.post(
            "/api/v1/channels/regions",
            json={"name": "上海区域", "code": "REG-DIV", "city": "上海"},
            headers=headers,
        )
        region_id = region.json()["id"]

        # 创建并分配码批次
        batch = await client.post(
            "/api/v1/code-batches",
            json={"product_id": product_id, "sku_id": sku_id, "batch_code": "DIV-BATCH", "quantity": 5},
            headers=headers,
        )
        batch_id = batch.json()["id"]
        await client.post(
            f"/api/v1/channels/code-batches/{batch_id}/assign",
            json={"region_id": region_id},
            headers=headers,
        )
        await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)

        # 获取一个码
        items_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}",
            headers=headers,
        )
        public_id = items_resp.json()["items"][0]["public_id"]

        # 用北京 IP 检测窜货
        from app.services.channel import check_diversion

        clue = await check_diversion(db_session, UUID(tid), public_id, "110.1.2.3")
        assert clue is not None
        assert clue.expected_region == "上海"
        assert clue.detected_city == "北京"
        assert clue.resolved is False

    @pytest.mark.anyio
    async def test_no_diversion_same_city(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, product_id, sku_id = setup_tenant

        region = await client.post(
            "/api/v1/channels/regions",
            json={"name": "上海区域", "code": "REG-SAME", "city": "上海"},
            headers=headers,
        )
        region_id = region.json()["id"]

        batch = await client.post(
            "/api/v1/code-batches",
            json={"product_id": product_id, "sku_id": sku_id, "batch_code": "SAME-BATCH", "quantity": 5},
            headers=headers,
        )
        batch_id = batch.json()["id"]
        await client.post(
            f"/api/v1/channels/code-batches/{batch_id}/assign",
            json={"region_id": region_id},
            headers=headers,
        )
        await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)

        items_resp = await client.get(
            f"/api/v1/code-items?code_batch_id={batch_id}",
            headers=headers,
        )
        public_id = items_resp.json()["items"][0]["public_id"]

        from app.services.channel import check_diversion

        clue = await check_diversion(db_session, UUID(tid), public_id, "120.1.2.3")
        assert clue is None  # 上海 IP → 上海区域，不触发

    @pytest.mark.anyio
    async def test_list_diversion_clues(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers, *_ = setup_tenant
        resp = await client.get(
            "/api/v1/channels/diversion-clues",
            headers=headers,
        )
        assert resp.status_code == 200
        assert "items" in resp.json()
        assert "total" in resp.json()
