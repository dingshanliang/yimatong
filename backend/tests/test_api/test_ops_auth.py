"""代运营工作台 API 认证测试"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def transport():
    return ASGITransport(app=app)


@pytest.mark.asyncio
async def test_ops_workbench_requires_auth(transport):
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/ops/workbench")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ops_create_task_requires_auth(transport):
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ops/tasks",
            json={"tenant_id": "00000000-0000-0000-0000-000000000000", "title": "test"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ops_overview_requires_auth(transport):
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/ops/overview")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ops_list_tasks_requires_auth(transport):
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/ops/tasks")
        assert resp.status_code == 401
