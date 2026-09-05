"""W11: 渠道流向绑定测试"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.code import CodeItem
from app.models.tenant import Account, Organization
from app.utils.security import create_access_token, hash_password
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
async def setup_tenant(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "渠道测试租户",
            "admin_email": "channel@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "018f0f65-7ad4-7cc4-b874-57d93b10ab12",
    }
    # enabled_features 必须走平台 control API；品牌方不能自助开付费 feature。
    feature_resp = await client.patch(
        f"/api/v1/tenants/{tid}",
        json={"enabled_features": {"channel_portal": True}},
        headers=_platform_admin_headers(),
    )
    assert feature_resp.status_code == 200

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


@pytest.mark.anyio
async def test_channel_management_and_analytics_require_the_paid_feature(client: AsyncClient):
    created = await client.post(
        "/api/v1/tenants",
        json={
            "name": "未购渠道能力租户",
            "admin_email": "no-channel@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = created.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    management = await client.get("/api/v1/channels/distributors", headers=headers)
    analytics = await client.get("/api/v1/channel-analytics/health-scores", headers=headers)

    for response in (management, analytics):
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "TENANT_FEATURE_DISABLED"
        assert response.json()["detail"]["feature"] == "channel_portal"


@pytest.mark.anyio
async def test_channel_role_matrix_denies_viewer_and_scope_for_operator_before_service(
    client: AsyncClient, setup_tenant, monkeypatch
):
    tid, _headers, *_ = setup_tenant
    from app.api.v1 import channels as channel_api

    service_spy = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(channel_api, "list_distributors", service_spy)
    viewer = create_access_token(tid, str(uuid4()), "viewer")
    viewer_response = await client.get("/api/v1/channels/distributors", headers={"Authorization": f"Bearer {viewer}"})
    assert viewer_response.status_code == 403
    service_spy.assert_not_awaited()

    operator = create_access_token(tid, str(uuid4()), "operator")
    operator_headers = {
        "Authorization": f"Bearer {operator}",
        "Idempotency-Key": str(uuid4()),
    }
    operator_read = await client.get("/api/v1/channels/distributors", headers=operator_headers)
    assert operator_read.status_code == 200
    service_spy.assert_awaited_once()
    operator_manage = await client.post(
        "/api/v1/channels/distributors",
        json={"name": "运营员经销商"},
        headers=operator_headers,
    )
    assert operator_manage.status_code == 201
    operator_scope = await client.get("/api/v1/channels/account-scopes", headers=operator_headers)
    assert operator_scope.status_code == 403


def test_channel_search_query_contract_is_bounded_at_runtime():
    expected_paths = {
        "/api/v1/channels/distributors",
        "/api/v1/channels/regions",
        "/api/v1/channels/stores",
        "/api/v1/channels/diversion-clues",
    }
    routes = {route.path: route for route in app.routes if getattr(route, "path", None) in expected_paths}

    assert routes.keys() == expected_paths
    for route in routes.values():
        query_param = next(param for param in route.dependant.query_params if param.name == "q")
        string_schema = next(
            item for item in query_param._type_adapter.json_schema()["anyOf"] if item.get("type") == "string"
        )
        assert string_schema["minLength"] == 1
        assert string_schema["maxLength"] == 100


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "service_name"),
    [
        ("/api/v1/channels/distributors", "list_distributors"),
        ("/api/v1/channels/regions", "list_regions"),
        ("/api/v1/channels/stores", "list_stores"),
        ("/api/v1/channels/diversion-clues", "list_diversion_clues"),
    ],
)
async def test_channel_search_query_is_normalized_and_rejected_before_service(
    client: AsyncClient,
    setup_tenant,
    monkeypatch,
    path: str,
    service_name: str,
):
    _tid, headers, *_ = setup_tenant
    from app.api.v1 import channels as channel_api

    service_spy = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(channel_api, service_name, service_spy)

    oversized = await client.get(path, params={"q": "x" * 101}, headers=headers)
    blank = await client.get(path, params={"q": "   "}, headers=headers)

    assert oversized.status_code == 422
    assert blank.status_code == 422
    service_spy.assert_not_awaited()

    valid = await client.get(path, params={"q": "  needle  "}, headers=headers)

    assert valid.status_code == 200
    assert service_spy.await_args.kwargs["q"] == "needle"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "service_name"),
    [
        ("/api/v1/channels/distributors", "list_distributors"),
        ("/api/v1/channels/regions", "list_regions"),
        ("/api/v1/channels/stores", "list_stores"),
    ],
)
async def test_channel_status_query_is_canonical_before_service(
    client: AsyncClient,
    setup_tenant,
    monkeypatch,
    path: str,
    service_name: str,
):
    _tid, headers, *_ = setup_tenant
    from app.api.v1 import channels as channel_api

    service_spy = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(channel_api, service_name, service_spy)

    for status in ("unknown", " active ", "archived"):
        response = await client.get(path, params={"status": status}, headers=headers)
        assert response.status_code == 422
    service_spy.assert_not_awaited()

    valid = await client.get(path, params={"status": "inactive"}, headers=headers)

    assert valid.status_code == 200
    assert service_spy.await_args.kwargs["status"] == "inactive"


@pytest.mark.anyio
async def test_diversion_severity_query_is_canonical_before_service(
    client: AsyncClient,
    setup_tenant,
    monkeypatch,
):
    _tid, headers, *_ = setup_tenant
    from app.api.v1 import channels as channel_api

    service_spy = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(channel_api, "list_diversion_clues", service_spy)

    for severity in ("unknown", " high "):
        response = await client.get("/api/v1/channels/diversion-clues", params={"severity": severity}, headers=headers)
        assert response.status_code == 422
    service_spy.assert_not_awaited()

    valid = await client.get("/api/v1/channels/diversion-clues", params={"severity": "high"}, headers=headers)

    assert valid.status_code == 200
    assert service_spy.await_args.kwargs["severity"] == "high"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "service_name", "service_result"),
    [
        ("/api/v1/channel-analytics/scan-by-channel", "get_scan_by_channel", ([], 0)),
        ("/api/v1/channel-analytics/health-scores", "get_channel_health_scores", []),
        ("/api/v1/channel-analytics/conversion-comparison", "get_conversion_comparison", []),
    ],
)
async def test_channel_analytics_dimension_is_canonical_before_service(
    client: AsyncClient,
    setup_tenant,
    monkeypatch,
    path: str,
    service_name: str,
    service_result,
):
    _tid, headers, *_ = setup_tenant
    from app.api.v1 import channel_analytics as channel_analytics_api

    service_spy = AsyncMock(return_value=service_result)
    monkeypatch.setattr(channel_analytics_api, service_name, service_spy)

    for dimension in ("unknown", " store "):
        response = await client.get(path, params={"dimension": dimension}, headers=headers)
        assert response.status_code == 422
    service_spy.assert_not_awaited()

    valid = await client.get(path, params={"dimension": "store"}, headers=headers)

    assert valid.status_code == 200
    assert service_spy.await_args.kwargs["dimension"] == "store"


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
    async def test_create_distributor_generates_code_when_omitted(self, client: AsyncClient, setup_tenant):
        _tid, headers, *_ = setup_tenant
        first = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "自动编码经销商", "contact_name": "张三", "contact_phone": "13800138000"},
            headers=headers,
        )
        second = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "第二个经销商", "status": "inactive"},
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )

        assert first.status_code == 201
        assert first.json()["code"].startswith("DIST-")
        assert first.json()["contact_name"] == "张三"
        assert first.json()["contact_phone_masked"] == "138****8000"
        assert second.status_code == 201
        assert second.json()["code"].startswith("DIST-")
        assert second.json()["code"] != first.json()["code"]
        assert second.json()["status"] == "inactive"

    @pytest.mark.anyio
    async def test_distributor_idempotency_uses_business_phone_hash_not_random_ciphertext(
        self, client: AsyncClient, setup_tenant
    ):
        _tid, headers, *_ = setup_tenant
        create_headers = {**headers, "Idempotency-Key": str(uuid4())}
        body = {"name": "幂等经销商", "contact_name": "张三", "contact_phone": "13800138000"}

        first = await client.post("/api/v1/channels/distributors", json=body, headers=create_headers)
        replay = await client.post("/api/v1/channels/distributors", json=body, headers=create_headers)
        conflict = await client.post(
            "/api/v1/channels/distributors",
            json={**body, "contact_phone": "13900139000"},
            headers=create_headers,
        )

        assert first.status_code == replay.status_code == 201
        assert replay.json() == first.json()
        assert first.json()["contact_phone_masked"] == "138****8000"
        assert conflict.status_code == 409

        update_headers = {**headers, "Idempotency-Key": str(uuid4())}
        update = {"expected_version": 1, "contact_phone": "13700137000"}
        updated = await client.patch(
            f"/api/v1/channels/distributors/{first.json()['id']}", json=update, headers=update_headers
        )
        update_replay = await client.patch(
            f"/api/v1/channels/distributors/{first.json()['id']}", json=update, headers=update_headers
        )
        update_conflict = await client.patch(
            f"/api/v1/channels/distributors/{first.json()['id']}",
            json={"expected_version": 1, "contact_phone": "13600136000"},
            headers=update_headers,
        )

        assert updated.status_code == update_replay.status_code == 200
        assert update_replay.json() == updated.json()
        assert updated.json()["version"] == 2
        assert updated.json()["contact_phone_masked"] == "137****7000"
        assert update_conflict.status_code == 409

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
            json={
                "name": "华南核心经销商",
                "contact_name": None,
                "contact_phone": "13900139000",
                "status": "inactive",
                "expected_version": created.json()["version"],
            },
            headers=headers,
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["name"] == "华南核心经销商"
        assert patch_resp.json()["status"] == "inactive"
        assert patch_resp.json()["contact_name"] is None
        assert patch_resp.json()["contact_phone_masked"] == "139****9000"

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
    async def test_create_region_generates_code_and_requires_no_store(self, client: AsyncClient, setup_tenant):
        _tid, headers, *_ = setup_tenant
        dist = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "区域所属经销商"},
            headers=headers,
        )
        resp = await client.post(
            "/api/v1/channels/regions",
            json={
                "name": "上海区域",
                "province": "上海",
                "city": "上海",
                "distributor_id": dist.json()["id"],
                "status": "active",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["code"].startswith("REG-")
        assert resp.json()["city"] == "上海"
        assert resp.json()["distributor_id"] == dist.json()["id"]
        assert resp.json()["status"] == "active"
        assert "store_id" not in resp.json()

    @pytest.mark.anyio
    async def test_create_region_supports_province_and_multi_province_coverage(self, client: AsyncClient, setup_tenant):
        _tid, headers, *_ = setup_tenant
        dist = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "片区经销商"},
            headers=headers,
        )

        province_region = await client.post(
            "/api/v1/channels/regions",
            json={
                "name": "四川省区",
                "coverage_type": "province",
                "province": "四川",
                "distributor_id": dist.json()["id"],
            },
            headers=headers,
        )
        assert province_region.status_code == 201
        assert province_region.json()["coverage_type"] == "province"
        assert province_region.json()["city"] is None
        assert province_region.json()["coverage_label"] == "四川"
        assert province_region.json()["coverage_areas"] == [{"province": "四川", "city": None}]

        multi_region = await client.post(
            "/api/v1/channels/regions",
            json={
                "name": "华东大区",
                "coverage_type": "multi_province",
                "coverage_areas": [{"province": "上海"}, {"province": "江苏"}, {"province": "浙江"}],
                "distributor_id": dist.json()["id"],
            },
            headers=headers,
        )
        assert multi_region.status_code == 201
        assert multi_region.json()["coverage_type"] == "multi_province"
        assert multi_region.json()["coverage_label"] == "上海、江苏、浙江"
        assert multi_region.json()["province"] == "上海"
        assert multi_region.json()["city"] is None

    @pytest.mark.anyio
    async def test_create_region_rejects_missing_or_inactive_distributor(self, client: AsyncClient, setup_tenant):
        _tid, headers, *_ = setup_tenant
        missing = await client.post(
            "/api/v1/channels/regions",
            json={"name": "无主区域", "province": "上海", "city": "上海"},
            headers=headers,
        )
        assert missing.status_code == 400
        assert "经销商" in missing.json()["detail"]

        dist = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "停用经销商", "status": "inactive"},
            headers=headers,
        )
        inactive = await client.post(
            "/api/v1/channels/regions",
            json={"name": "停用经销商区域", "province": "上海", "city": "上海", "distributor_id": dist.json()["id"]},
            headers=headers,
        )
        assert inactive.status_code == 400
        assert "启用" in inactive.json()["detail"]

    @pytest.mark.anyio
    async def test_list_regions(self, client: AsyncClient, setup_tenant):
        _tid, headers, *_ = setup_tenant
        dist = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "北京经销商", "code": "DIST-BJ"},
            headers=headers,
        )
        await client.post(
            "/api/v1/channels/regions",
            json={"name": "北京区域", "code": "REG-BJ", "city": "北京", "distributor_id": dist.json()["id"]},
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
        assert "allocated_quantity" in item


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
        dist = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "门店经销商", "code": "DIST-STORE"},
            headers=headers,
        )
        region = await client.post(
            "/api/v1/channels/regions",
            json={"name": "上海区域", "code": "REG-STORE", "city": "上海", "distributor_id": dist.json()["id"]},
            headers=headers,
        )
        store = await client.post(
            "/api/v1/channels/stores",
            json={"name": "旧门店", "code": "STORE-PATCH", "region_id": region.json()["id"]},
            headers=headers,
        )
        patch_resp = await client.patch(
            f"/api/v1/channels/stores/{store.json()['id']}",
            json={"name": "新门店", "address": "上海市黄浦区", "expected_version": store.json()["version"]},
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
        _tid, headers, product_id, sku_id, production_batch_id = setup_tenant

        # 创建区域
        dist = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "分配经销商", "code": "DIST-ASSIGN"},
            headers=headers,
        )
        region = await client.post(
            "/api/v1/channels/regions",
            json={"name": "上海区域", "code": "REG-TEST", "city": "上海", "distributor_id": dist.json()["id"]},
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
    async def test_code_allocation_targets_region_without_store(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        _tid, headers, product_id, sku_id, production_batch_id = setup_tenant
        dist = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "区域分配经销商", "code": "DIST-REG-ALLOC"},
            headers=headers,
        )
        region = await client.post(
            "/api/v1/channels/regions",
            json={
                "name": "区域分配上海",
                "code": "REG-ALLOC",
                "province": "上海",
                "city": "上海",
                "distributor_id": dist.json()["id"],
            },
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

        allocation = await client.post(
            "/api/v1/channels/code-allocations",
            json={
                "batch_id": batch.json()["id"],
                "target_type": "region",
                "region_id": region.json()["id"],
                "quantity": 6,
                "reason": "initial regional allocation",
            },
            headers=headers,
        )
        assert allocation.status_code == 201
        data = allocation.json()
        assert data["store_id"] is None
        assert data["region_id"] == region.json()["id"]
        assert data["region_name"] == "区域分配上海"
        assert data["distributor_id"] == dist.json()["id"]
        assert data["remaining_quantity"] == 4

        list_resp = await client.get(
            "/api/v1/channels/code-allocations",
            params={"region_id": region.json()["id"]},
            headers=headers,
        )
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] == 1

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
            json={
                "batch_id": batch.json()["id"],
                "target_type": "store",
                "store_id": store.json()["id"],
                "quantity": 6,
                "reason": "initial store allocation",
            },
            headers=headers,
        )
        assert first.status_code == 201

        over = await client.post(
            "/api/v1/channels/code-allocations",
            json={
                "batch_id": batch.json()["id"],
                "target_type": "store",
                "store_id": store.json()["id"],
                "quantity": 5,
                "reason": "exceeds remaining capacity",
            },
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

    @pytest.mark.anyio
    async def test_reassign_and_archive_preserve_allocation_history(
        self, client: AsyncClient, setup_tenant, db_session: AsyncSession
    ):
        tid, headers, product_id, sku_id, production_batch_id = setup_tenant
        first_store = await client.post(
            "/api/v1/channels/stores", json={"name": "原门店", "code": "STORE-HISTORY-A"}, headers=headers
        )
        second_store = await client.post(
            "/api/v1/channels/stores", json={"name": "新门店", "code": "STORE-HISTORY-B"}, headers=headers
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
        initial = await client.post(
            "/api/v1/channels/code-allocations",
            json={
                "batch_id": batch.json()["id"],
                "target_type": "store",
                "store_id": first_store.json()["id"],
                "quantity": 8,
                "reason": "initial shipment",
            },
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert initial.status_code == 201

        reassigned = await client.post(
            f"/api/v1/channels/code-allocations/{initial.json()['id']}/reassign",
            json={
                "expected_version": initial.json()["version"],
                "target_type": "store",
                "store_id": second_store.json()["id"],
                "quantity": 7,
                "reason": "route correction",
            },
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert reassigned.status_code == 200
        assert reassigned.json()["version"] == 2
        assert reassigned.json()["action"] == "reassign"

        stale = await client.post(
            f"/api/v1/channels/code-allocations/{initial.json()['id']}/archive",
            json={"expected_version": 1, "reason": "stale archive"},
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert stale.status_code == 409

        archived = await client.post(
            f"/api/v1/channels/code-allocations/{reassigned.json()['id']}/archive",
            json={"expected_version": 2, "reason": "shipment cancelled"},
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"
        assert archived.json()["version"] == 3
        assert archived.json()["allocated_quantity"] == 0
        assert archived.json()["remaining_quantity"] == 10

        current = await client.get("/api/v1/channels/code-allocations", headers=headers)
        history = await client.get(
            "/api/v1/channels/code-allocations", params={"include_history": True}, headers=headers
        )
        assert current.json()["total"] == 0
        assert current.json()["items"] == []
        assert history.json()["total"] == 3
        assert [item["version"] for item in history.json()["items"]] == [3, 2, 1]

        reassign_tombstone = await client.post(
            f"/api/v1/channels/code-allocations/{archived.json()['id']}/reassign",
            json={
                "expected_version": 3,
                "target_type": "store",
                "store_id": first_store.json()["id"],
                "quantity": 1,
                "reason": "tombstone must remain terminal",
            },
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        rearchive_tombstone = await client.post(
            f"/api/v1/channels/code-allocations/{archived.json()['id']}/archive",
            json={"expected_version": 3, "reason": "tombstone must not be archived twice"},
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert reassign_tombstone.status_code == 409
        assert rearchive_tombstone.status_code == 409
        history_after_terminal_rejections = await client.get(
            "/api/v1/channels/code-allocations", params={"include_history": True}, headers=headers
        )
        assert history_after_terminal_rejections.json()["total"] == 3

        overview = await client.get("/api/v1/channels/overview", headers=headers)
        assert overview.status_code == 200
        assert overview.json()["allocated_quantity"] == 0

        code_item = await db_session.scalar(
            select(CodeItem).where(CodeItem.tenant_id == UUID(tid), CodeItem.code_batch_id == UUID(batch.json()["id"]))
        )
        assert code_item is not None
        resolved = await client.get(f"/api/v1/channels/code-items/{code_item.public_id}/store", headers=headers)
        assert resolved.status_code == 200
        assert resolved.json() == {"matched": False}

        replacement = await client.post(
            "/api/v1/channels/code-allocations",
            json={
                "batch_id": batch.json()["id"],
                "target_type": "store",
                "store_id": first_store.json()["id"],
                "quantity": 10,
                "reason": "capacity restored after archive",
            },
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert replacement.status_code == 201
        assert replacement.json()["allocation_root_id"] != archived.json()["allocation_root_id"]
        assert replacement.json()["allocated_quantity"] == 10
        assert replacement.json()["remaining_quantity"] == 0
        current_after_replacement = await client.get("/api/v1/channels/code-allocations", headers=headers)
        assert current_after_replacement.json()["total"] == 1
        assert (
            current_after_replacement.json()["items"][0]["allocation_root_id"]
            == replacement.json()["allocation_root_id"]
        )


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
            json={
                "batch_id": batch.json()["id"],
                "target_type": "store",
                "store_id": store.json()["id"],
                "quantity": 8,
                "reason": "portal scope fixture",
            },
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
        assert data["regions"][0]["name"] == "授权区域"

    @pytest.mark.anyio
    async def test_region_scope_limits_distributor_portal_summary_without_store(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, product_id, sku_id, production_batch_id = setup_tenant
        dist = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "区域账号经销商", "code": "DIST-REGION-SCOPE"},
            headers=headers,
        )
        region = await client.post(
            "/api/v1/channels/regions",
            json={
                "name": "区域账号上海",
                "code": "REG-SCOPE",
                "city": "上海",
                "distributor_id": dist.json()["id"],
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
            json={
                "batch_id": batch.json()["id"],
                "target_type": "region",
                "region_id": region.json()["id"],
                "quantity": 8,
                "reason": "region portal fixture",
            },
            headers=headers,
        )

        org = Organization(tenant_id=UUID(tid), name="区域渠道组织")
        db_session.add(org)
        await db_session.flush()
        account = Account(
            tenant_id=UUID(tid),
            organization_id=org.id,
            email="region@test.com",
            name="区域账号",
            hashed_password=hash_password("Pass1234"),
        )
        db_session.add(account)
        await db_session.flush()
        await db_session.refresh(account)

        scope_resp = await client.post(
            "/api/v1/channels/account-scopes",
            json={"account_id": str(account.id), "scope_type": "region", "region_id": region.json()["id"]},
            headers=headers,
        )
        assert scope_resp.status_code == 201

        scoped_token = create_access_token(tid, str(account.id), "distributor")
        summary = await client.get(
            "/api/v1/channels/portal/distributor/summary",
            headers={"Authorization": f"Bearer {scoped_token}"},
        )
        assert summary.status_code == 200
        data = summary.json()
        assert data["scope"]["type"] == "region"
        assert data["scope"]["name"] == "区域账号上海"
        assert data["allocated_quantity"] == 8
        assert data["store_count"] == 0
        assert data["recent_allocations"][0]["store_id"] is None

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


class TestDistributorPortalDiversionAlerts:
    """渠道门户身份没有 risk:read，窜货预警必须走 portal 专用端点并按账号范围过滤。"""

    @pytest.mark.anyio
    async def test_distributor_portal_alerts_scoped_to_account_scope(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, *_ = setup_tenant
        own = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "预警经销商", "code": "DIST-ALERT-OWN"},
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        other = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "无关经销商", "code": "DIST-ALERT-OTHER"},
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        own_distributor_id = UUID(own.json()["id"])
        other_distributor_id = UUID(other.json()["id"])

        own_item_id, other_item_id = uuid4(), uuid4()
        from app.models.channel import DiversionClue
        from app.models.risk import RiskNotification

        db_session.add_all(
            [
                DiversionClue(
                    tenant_id=UUID(tid),
                    public_id="ALERTOWN01",
                    code_item_id=own_item_id,
                    distributor_id=own_distributor_id,
                    expected_region="上海",
                    detected_city="北京",
                    ip_hash="ip-alert-own",
                    resolved=False,
                ),
                DiversionClue(
                    tenant_id=UUID(tid),
                    public_id="ALERTOTH01",
                    code_item_id=other_item_id,
                    distributor_id=other_distributor_id,
                    expected_region="上海",
                    detected_city="北京",
                    ip_hash="ip-alert-other",
                    resolved=False,
                ),
                RiskNotification(
                    tenant_id=UUID(tid),
                    notification_type="diversion_alert",
                    title="疑似窜货：ALERTOWN01",
                    detail="预期区域：上海，实际扫码城市：北京",
                    code_item_id=own_item_id,
                    read=False,
                ),
                RiskNotification(
                    tenant_id=UUID(tid),
                    notification_type="diversion_alert",
                    title="疑似窜货：ALERTOTH01",
                    detail="其他经销商范围的通知",
                    code_item_id=other_item_id,
                    read=False,
                ),
                # 非窜货类型通知即使落在同一批码上也不应出现在门户预警里
                RiskNotification(
                    tenant_id=UUID(tid),
                    notification_type="rule_hit",
                    title="风控规则命中",
                    detail="",
                    code_item_id=own_item_id,
                    read=False,
                ),
            ]
        )
        await db_session.commit()

        org = Organization(tenant_id=UUID(tid), name="预警组织")
        db_session.add(org)
        await db_session.flush()
        account = Account(
            tenant_id=UUID(tid),
            organization_id=org.id,
            email="alert-dist@test.com",
            name="预警经销商账号",
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
                "distributor_id": str(own_distributor_id),
            },
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert scope_resp.status_code == 201

        scoped_token = create_access_token(tid, str(account.id), "distributor")
        resp = await client.get(
            "/api/v1/channels/portal/distributor/diversion-alerts",
            headers={"Authorization": f"Bearer {scoped_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["title"] == "疑似窜货：ALERTOWN01"
        assert data["items"][0]["read"] is False

    @pytest.mark.anyio
    async def test_region_scope_falls_back_for_distributor_portal_alerts(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, *_ = setup_tenant
        dist = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "区域预警经销商", "code": "DIST-ALERT-REGION"},
            headers=headers,
        )
        region = await client.post(
            "/api/v1/channels/regions",
            json={"name": "预警区域", "code": "REG-ALERT", "city": "上海", "distributor_id": dist.json()["id"]},
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        region_id = UUID(region.json()["id"])
        item_id = uuid4()

        from app.models.channel import DiversionClue
        from app.models.risk import RiskNotification

        notification = RiskNotification(
            tenant_id=UUID(tid),
            notification_type="diversion_alert",
            title="疑似窜货：区域预警",
            detail="预期区域：上海，实际扫码城市：北京",
            code_item_id=item_id,
            read=False,
        )
        db_session.add_all(
            [
                DiversionClue(
                    tenant_id=UUID(tid),
                    public_id="ALERTREG01",
                    code_item_id=item_id,
                    region_id=region_id,
                    expected_region="上海",
                    detected_city="北京",
                    ip_hash="ip-alert-region",
                    resolved=False,
                ),
                notification,
            ]
        )
        await db_session.commit()

        org = Organization(tenant_id=UUID(tid), name="区域预警组织")
        db_session.add(org)
        await db_session.flush()
        account = Account(
            tenant_id=UUID(tid),
            organization_id=org.id,
            email="alert-region@test.com",
            name="区域预警账号",
            hashed_password=hash_password("Pass1234"),
        )
        db_session.add(account)
        await db_session.flush()
        await db_session.refresh(account)

        scope_resp = await client.post(
            "/api/v1/channels/account-scopes",
            json={"account_id": str(account.id), "scope_type": "region", "region_id": str(region_id)},
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert scope_resp.status_code == 201

        scoped_token = create_access_token(tid, str(account.id), "distributor")
        resp = await client.get(
            "/api/v1/channels/portal/distributor/diversion-alerts",
            headers={"Authorization": f"Bearer {scoped_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "疑似窜货：区域预警"

    @pytest.mark.anyio
    async def test_portal_alerts_reject_non_portal_principals(self, client: AsyncClient, setup_tenant):
        tid, _headers, *_ = setup_tenant
        admin_token = create_access_token(tid, str(uuid4()), "admin")
        resp = await client.get(
            "/api/v1/channels/portal/distributor/diversion-alerts",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Distributor portal access required"

    @pytest.mark.anyio
    async def test_portal_alerts_404_when_account_has_no_scope(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, *_ = setup_tenant
        org = Organization(tenant_id=UUID(tid), name="无范围组织")
        db_session.add(org)
        await db_session.flush()
        account = Account(
            tenant_id=UUID(tid),
            organization_id=org.id,
            email="no-scope@test.com",
            name="无范围账号",
            hashed_password=hash_password("Pass1234"),
        )
        db_session.add(account)
        await db_session.flush()
        await db_session.refresh(account)

        scoped_token = create_access_token(tid, str(account.id), "distributor")
        resp = await client.get(
            "/api/v1/channels/portal/distributor/diversion-alerts",
            headers={"Authorization": f"Bearer {scoped_token}"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Distributor scope not found"


class TestChannelOverview:
    @pytest.mark.anyio
    async def test_overview_returns_channel_metrics(self, client: AsyncClient, setup_tenant):
        _tid, headers, *_ = setup_tenant
        dist = await client.post(
            "/api/v1/channels/distributors", json={"name": "经销商", "code": "OV-D"}, headers=headers
        )
        await client.post(
            "/api/v1/channels/regions",
            json={"name": "区域", "code": "OV-R", "distributor_id": dist.json()["id"]},
            headers=headers,
        )
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

        assert resolve_ip_to_city("8.8.8.8") == "未知位置"


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
        dist = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "窜货经销商", "code": "DIST-DIV"},
            headers=headers,
        )
        region = await client.post(
            "/api/v1/channels/regions",
            json={"name": "上海区域", "code": "REG-DIV", "city": "上海", "distributor_id": dist.json()["id"]},
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
        assert str(clue.region_id) == region_id
        assert clue.resolved is False

        filtered = await client.get(
            "/api/v1/channels/diversion-clues",
            params={"region_id": region_id, "resolved": False},
            headers=headers,
        )
        assert filtered.status_code == 200
        item = filtered.json()["items"][0]
        assert item["region_id"] == region_id
        assert item["region_name"] == "上海区域"
        assert item["distributor_name"] == "窜货经销商"
        assert item["batch_code"] == batch.json()["batch_code"]
        assert item["product_name"]
        assert item["sku_name"]
        assert item["severity"] == "high"
        assert item["resolution_action"] is None

    @pytest.mark.anyio
    async def test_no_diversion_same_city(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, product_id, sku_id, production_batch_id = setup_tenant

        dist = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "同城经销商", "code": "DIST-SAME"},
            headers=headers,
        )
        region = await client.post(
            "/api/v1/channels/regions",
            json={"name": "上海区域", "code": "REG-SAME", "city": "上海", "distributor_id": dist.json()["id"]},
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
    async def test_diversion_respects_province_and_multi_province_coverage(
        self,
        client: AsyncClient,
        setup_tenant,
        db_session: AsyncSession,
    ):
        tid, headers, product_id, sku_id, production_batch_id = setup_tenant
        dist = await client.post(
            "/api/v1/channels/distributors",
            json={"name": "大区窜货经销商", "code": "DIST-COVERAGE"},
            headers=headers,
        )
        region = await client.post(
            "/api/v1/channels/regions",
            json={
                "name": "沪粤大区",
                "code": "REG-COVERAGE",
                "coverage_type": "multi_province",
                "coverage_areas": [{"province": "上海"}, {"province": "广东"}],
                "distributor_id": dist.json()["id"],
            },
            headers=headers,
        )
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
            json={"region_id": region.json()["id"]},
            headers=headers,
        )
        await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)
        items_resp = await client.get(f"/api/v1/code-items?code_batch_id={batch_id}", headers=headers)
        public_id = items_resp.json()["items"][0]["public_id"]

        from app.services.channel import check_diversion

        assert await check_diversion(db_session, UUID(tid), public_id, "113.1.2.3") is None

        clue = await check_diversion(db_session, UUID(tid), public_id, "110.1.2.3")
        assert clue is not None
        assert clue.expected_region == "沪粤大区（上海、广东）"
        assert clue.detected_city == "北京"
        assert str(clue.region_id) == region.json()["id"]

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
