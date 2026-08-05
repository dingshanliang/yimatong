"""A3-004: ProductionBatch 数据模型与 CRUD API 验收测试"""

import io
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
async def tenant_with_auth(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "批次测试",
            "admin_email": "batch@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return tid, {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def sku_id(client: AsyncClient, tenant_with_auth):
    _, headers = tenant_with_auth
    brand_resp = await client.post("/api/v1/brands", json={"name": "批次品牌"}, headers=headers)
    brand_id = brand_resp.json()["id"]
    prod_resp = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": "批次产品"},
        headers=headers,
    )
    product_id = prod_resp.json()["id"]
    sku_resp = await client.post(
        "/api/v1/skus",
        json={"product_id": product_id, "code": "BATCH-SKU", "name": "批次SKU"},
        headers=headers,
    )
    return product_id, sku_resp.json()["id"]


class TestProductionBatchCRUD:
    @pytest.mark.anyio
    async def test_create_batch(self, client: AsyncClient, tenant_with_auth, sku_id):
        tid, headers = tenant_with_auth
        product_id, sid = sku_id
        resp = await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product_id,
                "sku_id": sid,
                "batch_code": "BATCH-2026-001",
                "production_date": "2026-01-15",
                "expiry_date": "2027-01-15",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["batch_code"] == "BATCH-2026-001"
        assert data["tenant_id"] == tid
        assert data["product_id"] == product_id
        assert data["sku_id"] == sid
        assert data["production_date"] == "2026-01-15"
        assert data["expiry_date"] == "2027-01-15"

    @pytest.mark.anyio
    async def test_list_batches_paginated(self, client: AsyncClient, tenant_with_auth, sku_id):
        product_id, sid = sku_id
        _, headers = tenant_with_auth
        for i in range(3):
            await client.post(
                "/api/v1/production-batches",
                json={
                    "product_id": product_id,
                    "sku_id": sid,
                    "batch_code": f"BATCH-P{i:03d}",
                    "production_date": "2026-01-01",
                    "expiry_date": "2027-01-01",
                },
                headers=headers,
            )

        resp = await client.get("/api/v1/production-batches?page=1&page_size=2", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 3
        assert len(data["items"]) <= 2

    @pytest.mark.anyio
    async def test_list_batches_filter_by_product(self, client: AsyncClient, tenant_with_auth, sku_id):
        product_id, sid = sku_id
        _, headers = tenant_with_auth
        # Create second product + sku
        brand_resp = await client.post("/api/v1/brands", json={"name": "品牌2"}, headers=headers)
        brand2_id = brand_resp.json()["id"]
        prod2_resp = await client.post(
            "/api/v1/products",
            json={"brand_id": brand2_id, "name": "产品2"},
            headers=headers,
        )
        product2_id = prod2_resp.json()["id"]
        sku2_resp = await client.post(
            "/api/v1/skus",
            json={"product_id": product2_id, "code": "SKU-2", "name": "SKU2"},
            headers=headers,
        )
        sku2_id = sku2_resp.json()["id"]

        await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product_id,
                "sku_id": sid,
                "batch_code": "BATCH-A",
                "production_date": "2026-01-01",
                "expiry_date": "2027-01-01",
            },
            headers=headers,
        )
        await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product2_id,
                "sku_id": sku2_id,
                "batch_code": "BATCH-B",
                "production_date": "2026-02-01",
                "expiry_date": "2027-02-01",
            },
            headers=headers,
        )

        resp = await client.get(f"/api/v1/production-batches?product_id={product_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["product_id"] == product_id for item in data["items"])

    @pytest.mark.anyio
    async def test_batch_code_unique_per_tenant(self, client: AsyncClient, tenant_with_auth, sku_id):
        product_id, sid = sku_id
        _, headers = tenant_with_auth
        await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product_id,
                "sku_id": sid,
                "batch_code": "DUP-BATCH",
                "production_date": "2026-01-01",
                "expiry_date": "2027-01-01",
            },
            headers=headers,
        )
        resp = await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product_id,
                "sku_id": sid,
                "batch_code": "DUP-BATCH",
                "production_date": "2026-02-01",
                "expiry_date": "2027-02-01",
            },
            headers=headers,
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"].lower()

    @pytest.mark.anyio
    async def test_create_batch_rejects_sku_from_another_product(self, client: AsyncClient, tenant_with_auth, sku_id):
        product_id, _ = sku_id
        _, headers = tenant_with_auth
        brand_resp = await client.post("/api/v1/brands", json={"name": "错配品牌"}, headers=headers)
        other_product_resp = await client.post(
            "/api/v1/products",
            json={"brand_id": brand_resp.json()["id"], "name": "错配产品"},
            headers=headers,
        )
        other_sku_resp = await client.post(
            "/api/v1/skus",
            json={"product_id": other_product_resp.json()["id"], "code": "OTHER-SKU", "name": "错配 SKU"},
            headers=headers,
        )

        resp = await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product_id,
                "sku_id": other_sku_resp.json()["id"],
                "batch_code": "WRONG-SKU-BATCH",
                "production_date": "2026-01-01",
                "expiry_date": "2027-01-01",
            },
            headers=headers,
        )

        assert resp.status_code == 400
        assert "does not belong" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_batch_date_validation_and_origin_clear(self, client: AsyncClient, tenant_with_auth, sku_id):
        product_id, sid = sku_id
        _, headers = tenant_with_auth
        invalid_resp = await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product_id,
                "sku_id": sid,
                "batch_code": "INVALID-DATE-BATCH",
                "production_date": "2026-02-01",
                "expiry_date": "2026-01-01",
            },
            headers=headers,
        )
        assert invalid_resp.status_code == 400
        assert "earlier than production date" in invalid_resp.json()["detail"]

        create_resp = await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product_id,
                "sku_id": sid,
                "batch_code": "CLEAR-ORIGIN-BATCH",
                "production_date": "2026-01-01",
                "expiry_date": "2027-01-01",
                "origin": "黑龙江省哈尔滨市五常市",
            },
            headers=headers,
        )
        assert create_resp.status_code == 201

        patch_resp = await client.patch(
            f"/api/v1/production-batches/{create_resp.json()['id']}",
            json={"origin": None},
            headers=headers,
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["origin"] is None

    @pytest.mark.anyio
    async def test_csv_import(self, client: AsyncClient, tenant_with_auth, sku_id):
        product_id, sid = sku_id
        _, headers = tenant_with_auth
        csv_content = (
            "batch_code,production_date,expiry_date\nCSV-001,2026-03-01,2027-03-01\nCSV-002,2026-04-01,2027-04-01\n"
        )
        files = {"file": ("batches.csv", io.BytesIO(csv_content.encode()), "text/csv")}
        resp = await client.post(
            "/api/v1/production-batches/import-csv",
            files=files,
            data={"product_id": str(product_id), "sku_id": str(sid)},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2
        assert len(data["errors"]) == 0

    @pytest.mark.anyio
    async def test_delete_batch_success(self, client: AsyncClient, tenant_with_auth, sku_id):
        product_id, sid = sku_id
        _, headers = tenant_with_auth
        resp = await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product_id,
                "sku_id": sid,
                "batch_code": "DEL-BATCH-001",
                "production_date": "2026-01-01",
                "expiry_date": "2027-01-01",
            },
            headers=headers,
        )
        batch_id = resp.json()["id"]

        del_resp = await client.delete(f"/api/v1/production-batches/{batch_id}", headers=headers)
        assert del_resp.status_code == 204

        list_resp = await client.get("/api/v1/production-batches", headers=headers)
        assert list_resp.status_code == 200
        assert not any(b["id"] == batch_id for b in list_resp.json()["items"])

    @pytest.mark.anyio
    async def test_delete_batch_not_found(self, client: AsyncClient, tenant_with_auth):
        _, headers = tenant_with_auth
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = await client.delete(f"/api/v1/production-batches/{fake_id}", headers=headers)
        assert resp.status_code == 404
