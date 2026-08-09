"""Real PostgreSQL coverage for routes that previously committed mid-request."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db, set_session_tenant_context
from app.main import app
from app.models.tenant import Tenant, TenantPlan
from app.services.entitlement import require_active_plan
from app.utils.auth_rbac import API_KEY_ROLE_PERMISSIONS
from app.utils.security import create_access_token

pytestmark = [pytest.mark.acceptance, pytest.mark.anyio]


@dataclass(frozen=True, slots=True)
class _PlanState:
    plan: TenantPlan
    plan_expires_at: datetime | None


class FaultingSession(AsyncSession):
    """Acceptance-only session that can fail after a selected route flush."""

    async def flush(self, objects=None) -> None:
        await super().flush(objects)
        flush_count = int(self.info.get("route_flush_count", 0)) + 1
        self.info["route_flush_count"] = flush_count
        fail_on = self.info.get("fail_on_route_flush")
        if fail_on is not None and flush_count == fail_on:
            raise RuntimeError("forced post-flush route failure")


async def test_route_writes_keep_one_tenant_scoped_transaction(migrated_pg_url: str, monkeypatch) -> None:
    from app.core import database
    from tests.test_acceptance.conftest import seed_baseline

    summary = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])
    admin_email = summary["baseline_tenant"]["admin_email"]
    brand_name = summary["brand"]["name"]
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(runtime_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=FaultingSession, expire_on_commit=False)
    route_flushed = asyncio.Event()
    allow_finalize = asyncio.Event()

    async with owner_factory() as db:
        tenant = await db.get(Tenant, tenant_id)
        assert tenant is not None
        prior_plan = _PlanState(tenant.plan, tenant.plan_expires_at)

    async with owner_factory() as db, db.begin():
        account = (
            await db.execute(
                text("SELECT id, auth_version FROM accounts WHERE tenant_id=:tenant_id AND email=:email"),
                {"tenant_id": tenant_id, "email": admin_email},
            )
        ).one()
        api_key = f"atomic-erp-{uuid.uuid4()}"
        await db.execute(
            text(
                "INSERT INTO api_keys (id, tenant_id, name, key, role, permissions, revoked) "
                "VALUES (:id, :tenant_id, 'Atomic ERP', :key, 'erp_sync', CAST(:permissions AS jsonb), false)"
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "key": api_key,
                "permissions": json.dumps(API_KEY_ROLE_PERMISSIONS["erp_sync"]),
            },
        )
        authoritative_products = await db.scalar(
            text("SELECT count(*) FROM products WHERE tenant_id=:tenant_id"), {"tenant_id": tenant_id}
        )
        await db.execute(
            text(
                "INSERT INTO tenant_quota_usage (tenant_id, products) VALUES (:tenant_id, :products) "
                "ON CONFLICT (tenant_id) DO UPDATE SET products=EXCLUDED.products"
            ),
            {"tenant_id": tenant_id, "products": authoritative_products},
        )
        await db.execute(
            text("UPDATE tenants SET plan_expires_at=:future WHERE id=:tenant_id"),
            {"future": datetime.now(UTC) + timedelta(days=1), "tenant_id": tenant_id},
        )

    admin_token = create_access_token(
        str(tenant_id),
        str(account.id),
        "admin",
        "brand",
        extra={"auth_version": account.auth_version},
    )
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    api_key_headers = {"X-Api-Key": api_key}

    async def atomic_runtime_db(request: Request):
        async with runtime_factory() as session:
            try:
                request_tenant_id = uuid.UUID(str(request.state.tenant_id))
                await set_session_tenant_context(session, request_tenant_id)
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    await require_active_plan(session, request_tenant_id, lock_tenant=True)
                fail_on_flush = request.headers.get("X-Test-Fail-On-Flush")
                if fail_on_flush:
                    session.info["fail_on_route_flush"] = int(fail_on_flush)
                yield session
                if request.headers.get("X-Test-Pause-Finalize") == "1":
                    route_flushed.set()
                    await allow_finalize.wait()
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)
    app.dependency_overrides[get_db] = atomic_runtime_db
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async def counts(sql: str, params: dict | None = None) -> int:
        async with owner_factory() as db:
            return int((await db.scalar(text(sql), params or {})) or 0)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Writer-first ordering: the route has flushed both rows, but the
            # dependency still owns the transaction and tenant lock.
            apply_task = asyncio.create_task(
                client.post(
                    "/api/v1/industry-templates/0/apply",
                    json={},
                    headers={**admin_headers, "X-Test-Pause-Finalize": "1"},
                )
            )
            await asyncio.wait_for(route_flushed.wait(), timeout=10)

            async def expire_plan() -> None:
                async with owner_factory() as db, db.begin():
                    await db.execute(
                        text("UPDATE tenants SET plan_expires_at=:expired WHERE id=:tenant_id"),
                        {"expired": datetime.now(UTC) - timedelta(seconds=1), "tenant_id": tenant_id},
                    )

            expiry_task = asyncio.create_task(expire_plan())
            await asyncio.sleep(0.15)
            assert not expiry_task.done(), "platform expiry must wait through template and version finalization"
            allow_finalize.set()
            applied = await asyncio.wait_for(apply_task, timeout=10)
            await asyncio.wait_for(expiry_task, timeout=10)
            assert applied.status_code == 201, applied.text
            applied_body = applied.json()
            async with owner_factory() as db:
                atomic_pair = (
                    await db.execute(
                        text(
                            "SELECT t.id, v.id, v.created_by FROM page_templates t "
                            "JOIN page_versions v ON v.page_template_id=t.id AND v.tenant_id=t.tenant_id "
                            "WHERE t.tenant_id=:tenant_id AND t.id=:template_id AND v.id=:version_id"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "template_id": uuid.UUID(applied_body["template_id"]),
                            "version_id": uuid.UUID(applied_body["version_id"]),
                        },
                    )
                ).one()
                assert atomic_pair.created_by == account.id

            # Restore the plan for the remaining atomicity probes.
            async with owner_factory() as db, db.begin():
                await db.execute(
                    text("UPDATE tenants SET plan_expires_at=:future WHERE id=:tenant_id"),
                    {"future": datetime.now(UTC) + timedelta(days=1), "tenant_id": tenant_id},
                )

            template_count = await counts(
                "SELECT count(*) FROM page_templates WHERE tenant_id=:tenant_id", {"tenant_id": tenant_id}
            )
            version_count = await counts(
                "SELECT count(*) FROM page_versions WHERE tenant_id=:tenant_id", {"tenant_id": tenant_id}
            )
            failed_template = await client.post(
                "/api/v1/industry-templates/0/apply",
                json={},
                headers={**admin_headers, "X-Test-Fail-On-Flush": "2"},
            )
            assert failed_template.status_code == 500
            assert (
                await counts("SELECT count(*) FROM page_templates WHERE tenant_id=:tenant_id", {"tenant_id": tenant_id})
                == template_count
            )
            assert (
                await counts("SELECT count(*) FROM page_versions WHERE tenant_id=:tenant_id", {"tenant_id": tenant_id})
                == version_count
            )

            private_name = f"atomic-private-{uuid.uuid4()}"
            failed_private = await client.post(
                "/api/v1/private-domain-configs",
                json={"config_type": "wecom", "name": private_name, "config": {}},
                headers={**admin_headers, "X-Test-Fail-On-Flush": "1"},
            )
            assert failed_private.status_code == 500
            assert (
                await counts(
                    "SELECT count(*) FROM private_domain_configs WHERE tenant_id=:tenant_id AND name=:name",
                    {"tenant_id": tenant_id, "name": private_name},
                )
                == 0
            )
            created_private = await client.post(
                "/api/v1/private-domain-configs",
                json={"config_type": "wecom", "name": private_name, "config": {}},
                headers=admin_headers,
            )
            assert created_private.status_code == 201, created_private.text

            product_name = f"atomic-product-{uuid.uuid4()}"
            product_external_id = f"product-{uuid.uuid4()}"
            initial_usage = await counts(
                "SELECT products FROM tenant_quota_usage WHERE tenant_id=:tenant_id", {"tenant_id": tenant_id}
            )
            failed_product = await client.post(
                "/open/v1/products",
                json={"name": product_name, "brand_name": brand_name, "external_id": product_external_id},
                headers={**api_key_headers, "X-Test-Fail-On-Flush": "2"},
            )
            assert failed_product.status_code == 500
            assert (
                await counts(
                    "SELECT count(*) FROM products WHERE tenant_id=:tenant_id AND external_id=:external_id",
                    {"tenant_id": tenant_id, "external_id": product_external_id},
                )
                == 0
            )
            assert (
                await counts(
                    "SELECT products FROM tenant_quota_usage WHERE tenant_id=:tenant_id", {"tenant_id": tenant_id}
                )
                == initial_usage
            )
            created_product = await client.post(
                "/open/v1/products",
                json={"name": product_name, "brand_name": brand_name, "external_id": product_external_id},
                headers=api_key_headers,
            )
            assert created_product.status_code == 201, created_product.text
            assert (
                await counts("SELECT count(*) FROM products WHERE tenant_id=:tenant_id", {"tenant_id": tenant_id})
                == initial_usage + 1
            )
            assert (
                await counts(
                    "SELECT products FROM tenant_quota_usage WHERE tenant_id=:tenant_id", {"tenant_id": tenant_id}
                )
                == initial_usage + 1
            )

            sku_code = f"SKU-{uuid.uuid4().hex[:12]}"
            sku_external_id = f"sku-{uuid.uuid4()}"
            failed_sku = await client.post(
                "/open/v1/skus",
                json={
                    "product_name": product_name,
                    "code": sku_code,
                    "name": "Atomic SKU",
                    "external_id": sku_external_id,
                },
                headers={**api_key_headers, "X-Test-Fail-On-Flush": "1"},
            )
            assert failed_sku.status_code == 500
            assert (
                await counts(
                    "SELECT count(*) FROM skus WHERE tenant_id=:tenant_id AND external_id=:external_id",
                    {"tenant_id": tenant_id, "external_id": sku_external_id},
                )
                == 0
            )
            created_sku = await client.post(
                "/open/v1/skus",
                json={
                    "product_name": product_name,
                    "code": sku_code,
                    "name": "Atomic SKU",
                    "external_id": sku_external_id,
                },
                headers=api_key_headers,
            )
            assert created_sku.status_code == 201, created_sku.text

            batch_external_id = f"batch-{uuid.uuid4()}"
            batch_body = {
                "sku_code": sku_code,
                "batch_code": f"BATCH-{uuid.uuid4().hex[:10]}",
                "production_date": "2026-08-01",
                "expiry_date": "2027-08-01",
                "external_id": batch_external_id,
            }
            failed_batch = await client.post(
                "/open/v1/batches",
                json=batch_body,
                headers={**api_key_headers, "X-Test-Fail-On-Flush": "1"},
            )
            assert failed_batch.status_code == 500
            assert (
                await counts(
                    "SELECT count(*) FROM production_batches WHERE tenant_id=:tenant_id AND external_id=:external_id",
                    {"tenant_id": tenant_id, "external_id": batch_external_id},
                )
                == 0
            )
            created_batch = await client.post("/open/v1/batches", json=batch_body, headers=api_key_headers)
            assert created_batch.status_code == 201, created_batch.text
    finally:
        allow_finalize.set()
        app.dependency_overrides.clear()
        try:
            async with owner_factory() as db, db.begin():
                tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
                assert tenant is not None
                tenant.plan = prior_plan.plan
                tenant.plan_expires_at = prior_plan.plan_expires_at
        finally:
            await runtime_engine.dispose()
            await owner_engine.dispose()
