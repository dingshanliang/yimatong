"""W11: 渠道流向绑定测试"""

from collections.abc import AsyncGenerator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.tenant import Account, Organization
from app.utils.security import create_access_token, hash_password
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
    production_batch = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": product.json()["id"],
            "sku_id": sku.json()["id"],
            "batch_code": "CH-PB-001",
            "production_date": "2026-05-31",
            "expiry_date": "2027-05-31",
        },
        headers=headers,
    )
    return tid, headers, product.json()["id"], sku.json()["id"], production_batch.json()["id"]


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

    @pytest.mark.anyio
    async def test_patch_and_filter_distributors(self, client: AsyncClient, setup_tenant):
        _tid, headers, *_ = setup_tenant
        created = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "华南经销商", "code": "DIST-SOUTH", "contact_name": "李四"},
            headers=headers,
        )
        distributor_id = created.json()["id"]

        patch_resp = await client.patch(
            f"/api/v1/channels/distributors/{distributor_id}",
            json={"name": "华南核心经销商", "status": "inactive"},
            headers=headers,
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["name"] == "华南核心经销商"
        assert patch_resp.json()["status"] == "inactive"

        list_resp = await client.get(
            "/api/v1/channels/distributors",
            params={"q": "核心", "status": "inactive"},
            headers=headers,
        )
        assert list_resp.status_code == 200
        item = list_resp.json()["items"][0]
        assert item["id"] == distributor_id
        assert item["region_count"] == 0
        assert item["store_count"] == 0
        assert "allocated_quantity" in item


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

    @pytest.mark.anyio
    async def test_region_list_returns_distributor_name_and_store_count(self, client: AsyncClient, setup_tenant):
        _tid, headers, *_ = setup_tenant
        dist = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "华东经销商", "code": "DIST-EAST"},
            headers=headers,
        )
        region = await client.post(
            "/api/v1/channels/regions",
            json={
                "name": "上海市区",
                "code": "REG-SH-CITY",
                "city": "上海",
                "distributor_id": dist.json()["id"],
            },
            headers=headers,
        )
        await client.post(
            "/api/v1/channels/stores",
            json={"name": "南京东路店", "code": "STORE-NJDL", "region_id": region.json()["id"]},
            headers=headers,
        )

        resp = await client.get(
            "/api/v1/channels/regions",
            params={"distributor_id": dist.json()["id"], "q": "上海"},
            headers=headers,
        )
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["distributor_name"] == "华东经销商"
        assert item["store_count"] == 1


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

    @pytest.mark.anyio
    async def test_patch_store_and_filter_by_region(self, client: AsyncClient, setup_tenant):
        _tid, headers, *_ = setup_tenant
        region = await client.post(
            "/api/v1/channels/regions",
            json={"name": "上海区域", "code": "REG-STORE", "city": "上海"},
            headers=headers,
        )
        store = await client.post(
            "/api/v1/channels/stores",
            json={"name": "旧门店", "code": "STORE-PATCH", "region_id": region.json()["id"]},
            headers=headers,
        )
        patch_resp = await client.patch(
            f"/api/v1/channels/stores/{store.json()['id']}",
            json={"name": "新门店", "address": "上海市黄浦区"},
            headers=headers,
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["name"] == "新门店"

        list_resp = await client.get(
            "/api/v1/channels/stores",
            params={"region_id": region.json()["id"], "q": "新门店"},
            headers=headers,
        )
        assert list_resp.status_code == 200
        item = list_resp.json()["items"][0]
        assert item["region_name"] == "上海区域"
        assert item["allocated_quantity"] == 0


class TestBatchAssignment:
    """W11-003: 码批次分配给经销商/区域"""

    @pytest.mark.anyio
    async def test_assign_batch_to_region(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, headers, product_id, sku_id, production_batch_id = setup_tenant

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
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 10,
            },
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

    @pytest.mark.anyio
    async def test_store_allocation_rejects_quantity_above_remaining(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        _tid, headers, product_id, sku_id, production_batch_id = setup_tenant
        store = await client.post(
            "/api/v1/channels/stores",
            json={"name": "限量门店", "code": "STORE-LIMIT"},
            headers=headers,
        )
        batch = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 10,
            },
            headers=headers,
        )

        first = await client.post(
            "/api/v1/channels/code-allocations",
            json={"batch_id": batch.json()["id"], "store_id": store.json()["id"], "quantity": 6},
            headers=headers,
        )
        assert first.status_code == 201

        over = await client.post(
            "/api/v1/channels/code-allocations",
            json={"batch_id": batch.json()["id"], "store_id": store.json()["id"], "quantity": 5},
            headers=headers,
        )
        assert over.status_code == 400
        assert "剩余" in over.json()["detail"]

        list_resp = await client.get("/api/v1/channels/code-allocations", headers=headers)
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["store_name"] == "限量门店"
        assert item["batch_code"] == batch.json()["batch_code"]
        assert item["remaining_quantity"] == 4


class TestChannelAccountScopes:
    @pytest.mark.anyio
    async def test_account_scope_limits_distributor_portal_summary(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, product_id, sku_id, production_batch_id = setup_tenant
        dist = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "授权经销商", "code": "DIST-SCOPED"},
            headers=headers,
        )
        region = await client.post(
            "/api/v1/channels/regions",
            json={"name": "授权区域", "code": "REG-SCOPED", "city": "上海", "distributor_id": dist.json()["id"]},
            headers=headers,
        )
        store = await client.post(
            "/api/v1/channels/stores",
            json={
                "name": "授权门店",
                "code": "STORE-SCOPED",
                "distributor_id": dist.json()["id"],
                "region_id": region.json()["id"],
            },
            headers=headers,
        )
        batch = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 8,
            },
            headers=headers,
        )
        await client.post(
            "/api/v1/channels/code-allocations",
            json={"batch_id": batch.json()["id"], "store_id": store.json()["id"], "quantity": 8},
            headers=headers,
        )

        org = Organization(tenant_id=UUID(tid), name="渠道组织")
        db_session.add(org)
        await db_session.flush()
        account = Account(
            tenant_id=UUID(tid),
            organization_id=org.id,
            email="dist@test.com",
            name="经销商账号",
            hashed_password=hash_password("Pass1234"),
        )
        db_session.add(account)
        await db_session.flush()
        await db_session.refresh(account)

        scope_resp = await client.post(
            "/api/v1/channels/account-scopes",
            json={
                "account_id": str(account.id),
                "scope_type": "distributor",
                "distributor_id": dist.json()["id"],
            },
            headers=headers,
        )
        assert scope_resp.status_code == 201

        scoped_token = create_access_token(tid, str(account.id), "distributor")
        scoped_headers = {"Authorization": f"Bearer {scoped_token}"}
        summary = await client.get("/api/v1/channels/portal/distributor/summary", headers=scoped_headers)
        assert summary.status_code == 200
        data = summary.json()
        assert data["scope"]["name"] == "授权经销商"
        assert data["allocated_quantity"] == 8
        assert data["store_count"] == 1

    @pytest.mark.anyio
    async def test_account_scope_limits_store_portal_summary(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, *_ = setup_tenant
        store = await client.post(
            "/api/v1/channels/stores",
            json={"name": "门店入口店", "code": "STORE-PORTAL"},
            headers=headers,
        )

        org = Organization(tenant_id=UUID(tid), name="门店组织")
        db_session.add(org)
        await db_session.flush()
        account = Account(
            tenant_id=UUID(tid),
            organization_id=org.id,
            email="store@test.com",
            name="门店账号",
            hashed_password=hash_password("Pass1234"),
        )
        db_session.add(account)
        await db_session.flush()
        await db_session.refresh(account)

        scope_resp = await client.post(
            "/api/v1/channels/account-scopes",
            json={"account_id": str(account.id), "scope_type": "store", "store_id": store.json()["id"]},
            headers=headers,
        )
        assert scope_resp.status_code == 201

        scoped_token = create_access_token(tid, str(account.id), "store_guide")
        summary = await client.get(
            "/api/v1/channels/portal/store/summary",
            headers={"Authorization": f"Bearer {scoped_token}"},
        )
        assert summary.status_code == 200
        assert summary.json()["scope"]["name"] == "门店入口店"


class TestChannelOverview:
    @pytest.mark.anyio
    async def test_overview_returns_channel_metrics(self, client: AsyncClient, setup_tenant):
        _tid, headers, *_ = setup_tenant
        await client.post("/api/v1/channels/distributors", json={"name": "经销商", "code": "OV-D"}, headers=headers)
        await client.post("/api/v1/channels/regions", json={"name": "区域", "code": "OV-R"}, headers=headers)
        await client.post("/api/v1/channels/stores", json={"name": "门店", "code": "OV-S"}, headers=headers)

        resp = await client.get("/api/v1/channels/overview", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["distributor_count"] == 1
        assert data["region_count"] == 1
        assert data["store_count"] == 1
        assert "pending_diversion_count" in data


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
        tid, headers, product_id, sku_id, production_batch_id = setup_tenant

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
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 5,
            },
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
        tid, headers, product_id, sku_id, production_batch_id = setup_tenant

        region = await client.post(
            "/api/v1/channels/regions",
            json={"name": "上海区域", "code": "REG-SAME", "city": "上海"},
            headers=headers,
        )
        region_id = region.json()["id"]

        batch = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": product_id,
                "sku_id": sku_id,
                "production_batch_id": production_batch_id,
                "quantity": 5,
            },
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
