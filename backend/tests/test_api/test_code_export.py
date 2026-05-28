"""A4-005: 码包导出 CSV API 验收测试"""

from collections.abc import AsyncGenerator
from unittest.mock import patch

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
async def batch_with_codes(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "导出测试",
            "admin_email": "export@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "导出品牌"}, headers=headers)
    brand_id = brand.json()["id"]
    prod = await client.post(
        "/api/v1/products",
        json={"brand_id": brand_id, "name": "导出产品"},
        headers=headers,
    )
    product_id = prod.json()["id"]
    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": product_id, "code": "EXP-SKU", "name": "EXP SKU"},
        headers=headers,
    )
    sku_id = sku.json()["id"]

    batch = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": product_id, "sku_id": sku_id,
            "batch_code": "EXP-001", "quantity": 5,
        },
        headers=headers,
    )
    batch_id = batch.json()["id"]
    return tid, headers, batch_id


class TestCodeExport:
    @pytest.mark.anyio
    async def test_trigger_export_returns_task_id(
        self, client: AsyncClient, batch_with_codes
    ):
        _, headers, batch_id = batch_with_codes
        with patch("app.api.v1.code_batches.enqueue_export_task") as mock_enqueue:
            mock_enqueue.return_value = "task-export-001"
            resp = await client.post(
                f"/api/v1/code-batches/{batch_id}/export", headers=headers,
            )
            assert resp.status_code == 202
            data = resp.json()
            assert data["task_id"] == "task-export-001"

    @pytest.mark.anyio
    async def test_query_task_status(
        self, client: AsyncClient, batch_with_codes
    ):
        _, headers, _ = batch_with_codes
        with patch("app.api.v1.tasks.get_task_status") as mock_status:
            mock_status.return_value = {
                "task_id": "task-export-001",
                "status": "completed",
                "download_url": "https://minio.example.com/export.csv",
            }
            resp = await client.get(
                "/api/v1/tasks/task-export-001", headers=headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "completed"
            assert "download_url" in data
