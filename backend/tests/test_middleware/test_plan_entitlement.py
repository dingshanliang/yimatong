"""套餐到期状态在 Open API 认证边界的行为。"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.middleware.tenant import TenantScopeMiddleware
from app.models.tenant import Tenant
from app.models.webhook import ApiKey
from tests.conftest import TestSessionLocal


@pytest.mark.anyio
async def test_expired_api_key_tenant_is_read_only_and_renewal_restores_writes(monkeypatch):
    import app.core.database as database

    monkeypatch.setattr(database, "async_session_factory", TestSessionLocal)
    tenant_id = uuid.uuid4()
    api_key = f"test-key-{uuid.uuid4()}"
    async with TestSessionLocal() as db:
        db.add_all(
            [
                Tenant(
                    id=tenant_id,
                    name="Open API 到期租户",
                    slug=f"open-expired-{tenant_id.hex[:8]}",
                    plan_expires_at=datetime.now(UTC) - timedelta(seconds=1),
                ),
                ApiKey(
                    tenant_id=tenant_id,
                    name="外部集成",
                    key=api_key,
                    permissions=["scan:list", "coupon:issue"],
                ),
            ]
        )
        await db.commit()

    test_app = FastAPI()
    test_app.add_middleware(TenantScopeMiddleware)

    @test_app.get("/open/v1/test")
    async def read_endpoint():
        return {"status": "ok"}

    @test_app.post("/open/v1/test")
    async def write_endpoint():
        return {"status": "written"}

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-Api-Key": api_key}
        assert (await client.get("/open/v1/test", headers=headers)).status_code == 200

        blocked = await client.post("/open/v1/test", headers=headers)
        assert blocked.status_code == 403
        assert blocked.json()["code"] == "TENANT_PLAN_EXPIRED"

        async with TestSessionLocal() as db:
            tenant = await db.get(Tenant, tenant_id)
            tenant.plan_expires_at = datetime.now(UTC) + timedelta(days=1)
            await db.commit()

        restored = await client.post("/open/v1/test", headers=headers)
        assert restored.status_code == 200
        assert restored.json() == {"status": "written"}
