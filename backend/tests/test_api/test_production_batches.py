"""A3-004: ProductionBatch 数据模型与 CRUD API 验收测试"""

import io
import uuid
from collections.abc import AsyncGenerator
from datetime import date, timedelta

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.audit import PlatformAuditLog
from app.models.product import BatchStatus, ProductionBatch
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
    async def test_cross_tenant_recall_returns_not_found_without_state_or_audit_change(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        tenant_with_auth,
    ):
        tenant_a_id, tenant_a_headers = tenant_with_auth
        tenant_b_response = await client.post(
            "/api/v1/tenants",
            json={
                "name": "跨租户召回对照",
                "admin_email": "batch-isolation@test.com",
                "admin_name": "Admin",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        assert tenant_b_response.status_code == 201
        tenant_b_id = tenant_b_response.json()["id"]
        tenant_b_headers = {"Authorization": f"Bearer {create_access_token(tenant_b_id, str(uuid.uuid4()), 'admin')}"}
        brand = await client.post("/api/v1/brands", json={"name": "租户 B 品牌"}, headers=tenant_b_headers)
        assert brand.status_code == 201
        product = await client.post(
            "/api/v1/products",
            json={"brand_id": brand.json()["id"], "name": "租户 B 产品"},
            headers=tenant_b_headers,
        )
        assert product.status_code == 201
        sku = await client.post(
            "/api/v1/skus",
            json={"product_id": product.json()["id"], "code": "TENANT-B-SKU", "name": "租户 B SKU"},
            headers=tenant_b_headers,
        )
        assert sku.status_code == 201
        created = await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product.json()["id"],
                "sku_id": sku.json()["id"],
                "batch_code": "TENANT-B-RECALL-TARGET",
                "production_date": "2026-08-01",
                "expiry_date": "2099-08-01",
            },
            headers=tenant_b_headers,
        )
        assert created.status_code == 201
        batch_id = uuid.UUID(created.json()["id"])
        await db_session.commit()

        recall_route = next(
            route
            for route in app.routes
            if isinstance(route, APIRoute)
            and route.path == "/api/v1/production-batches/{batch_id}/recall"
            and "POST" in route.methods
        )
        db_dependency = next(
            dependency for dependency in recall_route.dependant.dependencies if dependency.call is get_db
        )
        assert db_dependency.scope == "function"
        before_audit_count = await db_session.scalar(
            select(func.count())
            .select_from(PlatformAuditLog)
            .where(PlatformAuditLog.action == "production_batch_recalled")
        )
        assert before_audit_count == 0

        response = await client.post(
            f"/api/v1/production-batches/{batch_id}/recall",
            json={"reason": "tenant A must not mutate tenant B", "confirm": "recall"},
            headers=tenant_a_headers,
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Batch not found"
        assert tenant_a_id != tenant_b_id
        persisted = await db_session.get(ProductionBatch, batch_id)
        await db_session.refresh(persisted)
        assert persisted.tenant_id == uuid.UUID(tenant_b_id)
        assert persisted.status == BatchStatus.active
        assert persisted.recall_reason is None
        assert persisted.recalled_at is None
        assert persisted.recalled_by is None
        after_audit_count = await db_session.scalar(
            select(func.count())
            .select_from(PlatformAuditLog)
            .where(PlatformAuditLog.action == "production_batch_recalled")
        )
        assert after_audit_count == before_audit_count

    @pytest.mark.anyio
    async def test_responses_expose_effective_status_with_recall_priority(
        self,
        client,
        db_session,
        tenant_with_auth,
        sku_id,
    ):
        product_id, sid = sku_id
        _, headers = tenant_with_auth
        expired = await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product_id,
                "sku_id": sid,
                "batch_code": "EFFECTIVE-EXPIRED",
                "production_date": str(date.today() - timedelta(days=30)),
                "expiry_date": str(date.today() - timedelta(days=1)),
            },
            headers=headers,
        )
        assert expired.status_code == 201
        assert expired.json()["status"] == "active"
        assert expired.json()["effective_status"] == "expired"

        recalled = await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product_id,
                "sku_id": sid,
                "batch_code": "EFFECTIVE-RECALLED",
                "production_date": str(date.today() - timedelta(days=30)),
                "expiry_date": str(date.today() + timedelta(days=30)),
            },
            headers=headers,
        )
        recalled_id = recalled.json()["id"]
        recall_response = await client.post(
            f"/api/v1/production-batches/{recalled_id}/recall",
            json={"reason": "safety recall", "confirm": "recall"},
            headers=headers,
        )
        assert recall_response.status_code == 200
        recalled_batch = await db_session.get(ProductionBatch, uuid.UUID(recalled_id))
        recalled_batch.expiry_date = date.today() - timedelta(days=1)
        await db_session.flush()

        listed = await client.get("/api/v1/production-batches", headers=headers)
        by_code = {item["batch_code"]: item for item in listed.json()["items"]}
        assert by_code["EFFECTIVE-EXPIRED"]["status"] == "active"
        assert by_code["EFFECTIVE-EXPIRED"]["effective_status"] == "expired"
        assert by_code["EFFECTIVE-RECALLED"]["status"] == "recalled"
        assert by_code["EFFECTIVE-RECALLED"]["effective_status"] == "recalled"

    @pytest.mark.anyio
    async def test_status_is_not_patchable_and_recall_is_explicit(self, client, tenant_with_auth, sku_id):
        product_id, sid = sku_id
        _, headers = tenant_with_auth
        created = await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product_id,
                "sku_id": sid,
                "batch_code": "RECALL-001",
                "production_date": "2026-01-01",
                "expiry_date": "2027-01-01",
            },
            headers=headers,
        )
        batch_id = created.json()["id"]

        patched = await client.patch(
            f"/api/v1/production-batches/{batch_id}", json={"status": "recalled"}, headers=headers
        )
        assert patched.status_code == 422

        invalid_confirm = await client.post(
            f"/api/v1/production-batches/{batch_id}/recall",
            json={"reason": " quality incident ", "confirm": "yes"},
            headers=headers,
        )
        assert invalid_confirm.status_code == 422

        recalled = await client.post(
            f"/api/v1/production-batches/{batch_id}/recall",
            json={"reason": " quality incident ", "confirm": "recall"},
            headers=headers,
        )
        assert recalled.status_code == 200
        assert recalled.json()["status"] == "recalled"
        assert recalled.json()["recall_reason"] == "quality incident"
        assert recalled.json()["recalled_at"]
        assert recalled.json()["recalled_by"]

        immutable_update = await client.patch(
            f"/api/v1/production-batches/{batch_id}",
            json={"origin": "must not change"},
            headers=headers,
        )
        assert immutable_update.status_code == 409

        repeated = await client.post(
            f"/api/v1/production-batches/{batch_id}/recall",
            json={"reason": "again", "confirm": "recall"},
            headers=headers,
        )
        assert repeated.status_code == 409

    @pytest.mark.anyio
    async def test_recall_audit_failure_aborts_the_state_change(
        self, client, db_session, tenant_with_auth, sku_id, monkeypatch
    ):
        product_id, sid = sku_id
        _, headers = tenant_with_auth
        created = await client.post(
            "/api/v1/production-batches",
            json={
                "product_id": product_id,
                "sku_id": sid,
                "batch_code": "RECALL-AUDIT-ROLLBACK",
                "production_date": "2026-01-01",
                "expiry_date": "2027-01-01",
            },
            headers=headers,
        )
        await db_session.commit()

        async def fail_audit(*_args, **_kwargs):
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr("app.services.audit.write_audit_log", fail_audit)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await client.post(
                f"/api/v1/production-batches/{created.json()['id']}/recall",
                json={"reason": "safety recall", "confirm": "recall"},
                headers=headers,
            )

        await db_session.rollback()
        persisted = await db_session.scalar(
            select(ProductionBatch).where(ProductionBatch.id == uuid.UUID(created.json()["id"]))
        )
        assert persisted.status == BatchStatus.active

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
        assert invalid_resp.status_code == 422

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


class TestImportWriteAuthorization:
    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("path", "filename", "content_type"),
        [
            (
                "/api/v1/imports/excel",
                "catalog.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            ("/api/v1/imports/products", "products.csv", "text/csv"),
            ("/api/v1/imports/existing-codes", "codes.csv", "text/csv"),
        ],
    )
    async def test_viewer_cannot_import(self, client, tenant_with_auth, path, filename, content_type):
        tenant_id, _ = tenant_with_auth
        viewer_token = create_access_token(tenant_id, "00000000-0000-0000-0000-000000000003", "viewer")

        response = await client.post(
            path,
            files={"file": (filename, b"header\n", content_type)},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )

        assert response.status_code == 403
