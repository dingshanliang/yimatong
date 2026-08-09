"""Real PostgreSQL gates for cumulative quota reservation state."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.core import database
from app.core.context import reset_request_tenant_id, set_request_tenant_id
from app.core.exceptions import ForbiddenError
from app.models.plan import QuotaRolloutPhase, QuotaRolloutState, TenantQuotaUsage
from app.models.product import Brand, Product
from app.models.tenant import Organization, Tenant
from app.services.entitlement import TenantPlanExpiredError
from app.services.product import delete_product
from app.services.quota import (
    QUOTA_RECONCILIATION_SOURCE_REVISION,
    CumulativeQuotaKey,
    QuotaExceededError,
    reserve_quota,
)

pytestmark = [pytest.mark.acceptance, pytest.mark.anyio]

BACKEND_DIR = Path(__file__).resolve().parents[2]
PARENT_REVISION = "b9e2c3d4f5a6"


def _alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["database_url"] = database_url
    env["migration_database_url"] = database_url
    env["control_database_url"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


async def _set_current_epoch_active(db: AsyncSession) -> None:
    state = await db.get(QuotaRolloutState, 1, with_for_update=True)
    assert state is not None
    now = datetime.now(UTC)
    state.source_revision = QUOTA_RECONCILIATION_SOURCE_REVISION
    state.phase = QuotaRolloutPhase.active
    state.drained_at = now
    state.drained_by = "acceptance"
    state.activated_at = now
    state.activated_by = "acceptance"


async def test_runtime_reservations_serialize_and_rollback(migrated_pg_url: str) -> None:
    owner_url = migrated_pg_url
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner_engine = create_async_engine(owner_url)
    runtime_engine = create_async_engine(runtime_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    concurrent_tenant = uuid.uuid4()
    rollback_tenant = uuid.uuid4()

    async with owner_factory() as db, db.begin():
        await _set_current_epoch_active(db)
        db.add_all(
            [
                Tenant(
                    id=concurrent_tenant,
                    name="并发配额租户",
                    slug=f"quota-concurrent-{concurrent_tenant.hex[:8]}",
                    quota={"max_products": 1},
                ),
                Tenant(
                    id=rollback_tenant,
                    name="回滚配额租户",
                    slug=f"quota-rollback-{rollback_tenant.hex[:8]}",
                    quota={"max_codes": 1},
                ),
            ]
        )
        await db.flush()
        db.add_all(
            [
                TenantQuotaUsage(
                    tenant_id=tenant_id,
                    reconciled_at=datetime.now(UTC),
                    source_revision=QUOTA_RECONCILIATION_SOURCE_REVISION,
                    enforcement_ready=True,
                )
                for tenant_id in (concurrent_tenant, rollback_tenant)
            ]
        )

    first_reserved = asyncio.Event()

    async def first() -> str:
        async with runtime_factory() as db, db.begin():
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(concurrent_tenant)}
            )
            await reserve_quota(db, concurrent_tenant, CumulativeQuotaKey.MAX_PRODUCTS)
            first_reserved.set()
            await asyncio.sleep(0.1)
        return "accepted"

    async def second() -> str:
        await first_reserved.wait()
        async with runtime_factory() as db, db.begin():
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(concurrent_tenant)}
            )
            try:
                await reserve_quota(db, concurrent_tenant, CumulativeQuotaKey.MAX_PRODUCTS)
            except QuotaExceededError:
                return "rejected"
        return "accepted"

    try:
        assert await asyncio.gather(first(), second()) == ["accepted", "rejected"]

        async with runtime_factory() as db:
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(rollback_tenant)}
            )
            await reserve_quota(db, rollback_tenant, CumulativeQuotaKey.MAX_CODES)
            await db.rollback()

        async with runtime_factory() as db, db.begin():
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(rollback_tenant)}
            )
            usage = await reserve_quota(db, rollback_tenant, CumulativeQuotaKey.MAX_CODES)
            assert usage.codes == 1

        async with owner_factory() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT tenant_id, codes, products FROM tenant_quota_usage "
                        "WHERE tenant_id IN (:concurrent_tenant, :rollback_tenant) ORDER BY tenant_id"
                    ),
                    {
                        "concurrent_tenant": concurrent_tenant,
                        "rollback_tenant": rollback_tenant,
                    },
                )
            ).mappings()
            by_tenant = {row["tenant_id"]: row for row in rows}
            assert by_tenant[concurrent_tenant]["products"] == 1
            assert by_tenant[rollback_tenant]["codes"] == 1
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_plan_expiry_assignment_linearizes_before_reservation(migrated_pg_url: str) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id = uuid.uuid4()

    async with owner_factory() as db, db.begin():
        await _set_current_epoch_active(db)
        db.add(
            Tenant(
                id=tenant_id,
                name="套餐到期竞态租户",
                slug=f"quota-expiry-{tenant_id.hex[:8]}",
                quota={"max_products": 1},
                plan_expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.flush()
        db.add(
            TenantQuotaUsage(
                tenant_id=tenant_id,
                reconciled_at=datetime.now(UTC),
                source_revision=QUOTA_RECONCILIATION_SOURCE_REVISION,
                enforcement_ready=True,
            )
        )

    expiry_locked = asyncio.Event()
    reservation_started = asyncio.Event()

    async def expire_plan() -> None:
        async with owner_factory() as db, db.begin():
            await db.execute(
                text("UPDATE tenants SET plan_expires_at=:expired WHERE id=:tenant_id"),
                {"expired": datetime.now(UTC) - timedelta(seconds=1), "tenant_id": tenant_id},
            )
            expiry_locked.set()
            await reservation_started.wait()
            # Keep the tenant row lock long enough to prove reserve_quota waits
            # for the expiry assignment rather than reading the previous plan.
            await asyncio.sleep(0.1)

    async def reserve_after_expiry_lock() -> ForbiddenError | None:
        await expiry_locked.wait()
        async with runtime_factory() as db, db.begin():
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            reservation_started.set()
            try:
                await reserve_quota(db, tenant_id, CumulativeQuotaKey.MAX_PRODUCTS)
            except ForbiddenError as exc:
                return exc
        return None

    try:
        _, error = await asyncio.gather(expire_plan(), reserve_after_expiry_lock())
        assert error is not None
        assert error.status_code == 403
        assert error.error_code == "TENANT_PLAN_EXPIRED"

        async with owner_factory() as db:
            products = await db.scalar(
                text("SELECT products FROM tenant_quota_usage WHERE tenant_id=:tenant_id"),
                {"tenant_id": tenant_id},
            )
            assert products == 0
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_request_db_lock_linearizes_non_quota_business_writes(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id = uuid.uuid4()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/organizations",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("acceptance", 80),
            "client": ("127.0.0.1", 1),
        }
    )

    async with owner_factory() as db, db.begin():
        db.add(
            Tenant(
                id=tenant_id,
                name="非配额写竞态租户",
                slug=f"non-quota-expiry-{tenant_id.hex[:8]}",
                plan_expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )

    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "_is_pg", True)
    writer_locked = asyncio.Event()
    allow_writer_commit = asyncio.Event()

    async def write_before_expiry() -> None:
        context_token = set_request_tenant_id(str(tenant_id))
        dependency = database.get_db(request)
        try:
            db = await anext(dependency)
            db.add(Organization(tenant_id=tenant_id, name="锁内组织"))
            await db.flush()
            writer_locked.set()
            await allow_writer_commit.wait()
            with pytest.raises(StopAsyncIteration):
                await anext(dependency)
        finally:
            await dependency.aclose()
            reset_request_tenant_id(context_token)

    async def expire_after_writer_lock() -> None:
        await writer_locked.wait()
        async with owner_factory() as db, db.begin():
            await db.execute(
                text("UPDATE tenants SET plan_expires_at=:expired WHERE id=:tenant_id"),
                {"expired": datetime.now(UTC) - timedelta(seconds=1), "tenant_id": tenant_id},
            )

    try:
        writer_task = asyncio.create_task(write_before_expiry())
        await writer_locked.wait()
        expiry_task = asyncio.create_task(expire_after_writer_lock())
        await asyncio.sleep(0.1)
        assert not expiry_task.done()
        allow_writer_commit.set()
        await asyncio.gather(writer_task, expiry_task)

        async with owner_factory() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM organizations WHERE tenant_id=:tenant_id"),
                    {"tenant_id": tenant_id},
                )
                == 1
            )

        context_token = set_request_tenant_id(str(tenant_id))
        dependency = database.get_db(request)
        try:
            with pytest.raises(TenantPlanExpiredError):
                await anext(dependency)
        finally:
            await dependency.aclose()
            reset_request_tenant_id(context_token)
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_concurrent_product_delete_releases_usage_once(migrated_pg_url: str) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id = uuid.uuid4()
    brand_id = uuid.uuid4()
    product_id = uuid.uuid4()

    async with owner_factory() as db, db.begin():
        await _set_current_epoch_active(db)
        db.add(
            Tenant(
                id=tenant_id,
                name="并发删除配额租户",
                slug=f"quota-delete-{tenant_id.hex[:8]}",
                quota={"max_products": 1},
            )
        )
        await db.flush()
        db.add(Brand(id=brand_id, tenant_id=tenant_id, name="并发删除品牌"))
        await db.flush()
        db.add(Product(id=product_id, tenant_id=tenant_id, brand_id=brand_id, name="并发删除产品"))
        db.add(
            TenantQuotaUsage(
                tenant_id=tenant_id,
                products=1,
                reconciled_at=datetime.now(UTC),
                source_revision=QUOTA_RECONCILIATION_SOURCE_REVISION,
                enforcement_ready=True,
            )
        )

    first_deleted = asyncio.Event()

    async def first() -> bool:
        async with runtime_factory() as db, db.begin():
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            deleted, conflict = await delete_product(db, tenant_id, product_id)
            assert conflict is None
            first_deleted.set()
            await asyncio.sleep(0.1)
            return deleted

    async def second() -> bool:
        await first_deleted.wait()
        async with runtime_factory() as db, db.begin():
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            deleted, conflict = await delete_product(db, tenant_id, product_id)
            assert conflict is None
            return deleted

    try:
        assert await asyncio.gather(first(), second()) == [True, False]
        async with owner_factory() as db:
            products = await db.scalar(
                text("SELECT products FROM tenant_quota_usage WHERE tenant_id=:tenant_id"),
                {"tenant_id": tenant_id},
            )
            assert products == 0
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_migration_backfills_authoritative_usage_and_normalizes_feature_alias(
    migrated_pg_url: str,
) -> None:
    tenant_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    account_id = uuid.uuid4()
    brand_id = uuid.uuid4()
    product_id = uuid.uuid4()
    sku_id = uuid.uuid4()
    code_batch_id = uuid.uuid4()
    sync_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")

    downgrade = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    assert downgrade.returncode == 0, downgrade.stderr
    conn = await asyncpg.connect(sync_dsn)
    try:
        async with conn.transaction():
            public_id = f"Q{tenant_id.hex[:12].upper()}"
            await conn.execute(
                "INSERT INTO tenants (id, name, slug, status, plan, tenant_type, enabled_features) "
                "VALUES ($1, '配额回填租户', $2, 'active', 'free', 'brand', '{\"channel_store\": true}'::json)",
                tenant_id,
                f"quota-backfill-{tenant_id.hex[:8]}",
            )
            await conn.execute(
                "INSERT INTO organizations (id, tenant_id, name) VALUES ($1, $2, '默认组织')",
                organization_id,
                tenant_id,
            )
            await conn.execute(
                "INSERT INTO accounts "
                "(id, tenant_id, organization_id, email, hashed_password, name, failed_login_attempts) "
                "VALUES ($1, $2, $3, $4, 'not-a-real-password', '管理员', 0)",
                account_id,
                tenant_id,
                organization_id,
                f"quota-{tenant_id.hex[:8]}@example.com",
            )
            await conn.execute(
                "INSERT INTO brands (id, tenant_id, name, status) VALUES ($1, $2, '回填品牌', 'active')",
                brand_id,
                tenant_id,
            )
            await conn.execute(
                "INSERT INTO products (id, tenant_id, brand_id, name, status) "
                "VALUES ($1, $2, $3, '回填产品', 'active')",
                product_id,
                tenant_id,
                brand_id,
            )
            await conn.execute(
                "INSERT INTO skus (id, tenant_id, product_id, code, name, status) "
                "VALUES ($1, $2, $3, 'BACKFILL-SKU', '回填规格', 'active')",
                sku_id,
                tenant_id,
                product_id,
            )
            await conn.execute(
                "INSERT INTO code_batches "
                "(id, tenant_id, product_id, sku_id, batch_code, quantity, status, "
                "code_type, generation_mode, created_by) "
                "VALUES ($1, $2, $3, $4, 'BACKFILL-BATCH', 1, 'pending', 'single', 'item_level', $5)",
                code_batch_id,
                tenant_id,
                product_id,
                sku_id,
                account_id,
            )
            await conn.execute(
                "INSERT INTO code_items (id, tenant_id, code_batch_id, public_id, status, code_type) "
                "VALUES ($1, $2, $3, $4, 'created', 'single')",
                uuid.uuid4(),
                tenant_id,
                code_batch_id,
                public_id,
            )
            await conn.execute(
                "INSERT INTO campaigns "
                "(id, tenant_id, name, campaign_type, status, start_at, end_at, rules_json) "
                "VALUES ($1, $2, '回填活动', 'coupon', 'draft', '2026-01-01', '2027-01-01', '{}'::json)",
                uuid.uuid4(),
                tenant_id,
            )
            await conn.execute(
                "INSERT INTO scan_events (id, tenant_id, public_id, scan_time, is_first_scan, is_valid_visit) "
                "VALUES ($1, $2, $3, now(), true, true)",
                uuid.uuid4(),
                tenant_id,
                public_id,
            )
    finally:
        await conn.close()

    try:
        upgrade = _alembic(migrated_pg_url, "upgrade", "head")
        assert upgrade.returncode == 0, upgrade.stderr
        conn = await asyncpg.connect(sync_dsn)
        try:
            usage = await conn.fetchrow(
                "SELECT codes, scans, campaigns, products, accounts, enforcement_ready, "
                "reconciled_at, source_revision FROM tenant_quota_usage WHERE tenant_id=$1",
                tenant_id,
            )
            assert tuple(usage) == (1, 1, 1, 1, 1, False, None, None)
            rollout = await conn.fetchrow(
                "SELECT source_revision, phase, drained_at, activated_at FROM quota_rollout_state WHERE id=1"
            )
            assert tuple(rollout) == (QUOTA_RECONCILIATION_SOURCE_REVISION, "bridge", None, None)
            normalized = await conn.fetchval(
                "SELECT enabled_features::jsonb ->> 'channel_portal' = 'true' "
                "AND NOT (enabled_features::jsonb ? 'channel_store') FROM tenants WHERE id=$1",
                tenant_id,
            )
            assert normalized is True
        finally:
            await conn.close()

        downgrade = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
        assert downgrade.returncode == 0, downgrade.stderr
        conn = await asyncpg.connect(sync_dsn)
        try:
            assert await conn.fetchval("SELECT to_regclass('public.tenant_quota_usage')") is None
            legacy_restored = await conn.fetchval(
                "SELECT enabled_features::jsonb ? 'channel_store' FROM tenants WHERE id=$1", tenant_id
            )
            assert legacy_restored is True
        finally:
            await conn.close()
    finally:
        restore = _alembic(migrated_pg_url, "upgrade", "head")
        assert restore.returncode == 0, restore.stderr
        conn = await asyncpg.connect(sync_dsn)
        try:
            await conn.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_quota_usage TO acceptance_control, acceptance_tester"
            )
        finally:
            await conn.close()
