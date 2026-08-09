"""Real PostgreSQL gates for quota rollout readiness and audit index DDL."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.campaign import Campaign
from app.models.plan import QuotaRolloutPhase, QuotaRolloutState, TenantQuotaUsage
from app.models.tenant import Tenant
from app.modules.brand_tenant_initialization import (
    BrandTenantInitialization,
    InitializeBrandTenant,
    TrustedAutomationOpening,
)
from app.services.quota import (
    QUOTA_RECONCILIATION_SOURCE_REVISION,
    CumulativeQuotaKey,
    QuotaExceededError,
    QuotaReconciliationGateError,
    activate_current_quota_rollout_epoch,
    begin_current_quota_rollout_epoch,
    get_quota_enforcement_readiness,
    lock_quota_rollout_state,
    reconcile_quota_usage_from_authoritative_rows,
    reserve_quota,
)

pytestmark = [pytest.mark.acceptance, pytest.mark.anyio]

BACKEND_DIR = Path(__file__).resolve().parents[2]
QUOTA_READINESS_REVISION = "02c17094ef47"
AUDIT_INDEX = "ix_platform_audit_target_timeline"
GLOBAL_EPOCH_PARENT_REVISION = "a38ad952599f"


def _alembic(database_url: str, *args: str, succeeds: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["database_url"] = database_url
    env["migration_database_url"] = database_url
    env["control_database_url"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if succeeds:
        assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    else:
        assert result.returncode != 0, f"alembic {' '.join(args)} unexpectedly succeeded"
    return result


def _quota_cli(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["database_url"] = database_url
    env["migration_database_url"] = database_url
    env["control_database_url"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "app.cli", "quota", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _quota_cli_process(database_url: str, *args: str) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["database_url"] = database_url
    env["migration_database_url"] = database_url
    env["control_database_url"] = database_url
    return subprocess.Popen(
        [sys.executable, "-m", "app.cli", "quota", *args],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _campaign(tenant_id: uuid.UUID, name: str) -> Campaign:
    return Campaign(
        tenant_id=tenant_id,
        name=name,
        campaign_type="coupon",
        start_at="2026-08-01T00:00:00Z",
        end_at="2026-08-31T00:00:00Z",
        rules_json={},
    )


async def _runtime_context(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    await db.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )


async def _reset_rollout_bridge(
    factory: async_sessionmaker[AsyncSession], *, source_revision: str | None = None
) -> None:
    async with factory() as db, db.begin():
        state = await db.get(QuotaRolloutState, 1, with_for_update=True)
        assert state is not None
        state.source_revision = source_revision or QUOTA_RECONCILIATION_SOURCE_REVISION
        state.phase = QuotaRolloutPhase.bridge
        state.drained_at = None
        state.drained_by = None
        state.activated_at = None
        state.activated_by = None


async def _begin_drained(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as db, db.begin():
        await begin_current_quota_rollout_epoch(
            db,
            old_writers_drained=True,
            operator="acceptance",
        )


async def _reconcile_all_current_tenants(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as db:
        tenant_ids = tuple(await db.scalars(select(Tenant.id).order_by(Tenant.id)))
    for tenant_id in tenant_ids:
        async with factory() as db, db.begin():
            await reconcile_quota_usage_from_authoritative_rows(
                db,
                tenant_id,
                old_writers_drained=True,
            )


async def _activate_all_current_tenants(factory: async_sessionmaker[AsyncSession]) -> None:
    await _begin_drained(factory)
    await _reconcile_all_current_tenants(factory)
    async with factory() as db, db.begin():
        readiness = await activate_current_quota_rollout_epoch(db, operator="acceptance")
        assert readiness.ready


def _initialization_command(tenant_key: str) -> InitializeBrandTenant:
    return InitializeBrandTenant(
        name=f"Rollout {tenant_key}",
        admin_name="Rollout Admin",
        admin_email=f"{tenant_key}@example.com",
        opening=TrustedAutomationOpening(
            actor="acceptance",
            chosen_password="StrongPass123",
            stable_tenant_key=tenant_key,
        ),
    )


async def test_stale_global_revision_never_enforces_or_reports_ready(migrated_pg_url: str) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id = uuid.uuid4()

    try:
        async with owner_factory() as db, db.begin():
            state = await db.get(QuotaRolloutState, 1, with_for_update=True)
            assert state is not None
            now = datetime.now(UTC)
            state.source_revision = "stale-arbitrary-revision"
            state.phase = QuotaRolloutPhase.active
            state.drained_at = now
            state.drained_by = "stale-deployment"
            state.activated_at = now
            state.activated_by = "stale-deployment"
            db.add(
                Tenant(
                    id=tenant_id,
                    name="过期 epoch 租户",
                    slug=f"quota-stale-epoch-{tenant_id.hex[:8]}",
                    quota={"max_campaigns": 0},
                )
            )
            await db.flush()
            db.add(
                TenantQuotaUsage(
                    tenant_id=tenant_id,
                    reconciled_at=now,
                    source_revision=QUOTA_RECONCILIATION_SOURCE_REVISION,
                    enforcement_ready=True,
                )
            )

        async with runtime_factory() as db:
            await _runtime_context(db, tenant_id)
            usage = await reserve_quota(db, tenant_id, CumulativeQuotaKey.MAX_CAMPAIGNS)
            assert usage.campaigns == 1
            await db.rollback()

        async with owner_factory() as db:
            state = await get_quota_enforcement_readiness(db)
            assert state.global_active_current is False
            assert state.ready is False
            await db.rollback()

        async with owner_factory() as db, db.begin():
            with pytest.raises(QuotaReconciliationGateError, match="durably marked drained"):
                await reconcile_quota_usage_from_authoritative_rows(
                    db,
                    tenant_id,
                    old_writers_drained=True,
                )
            with pytest.raises(QuotaReconciliationGateError, match="does not match"):
                await reconcile_quota_usage_from_authoritative_rows(
                    db,
                    tenant_id,
                    old_writers_drained=True,
                    source_revision="stale-arbitrary-revision",
                )
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_partial_reconciliation_remains_globally_non_enforcing_and_resumable(migrated_pg_url: str) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_ids = (uuid.uuid4(), uuid.uuid4())

    try:
        await _reset_rollout_bridge(owner_factory)
        async with owner_factory() as db, db.begin():
            for index, tenant_id in enumerate(tenant_ids):
                db.add(
                    Tenant(
                        id=tenant_id,
                        name=f"部分对账租户{index}",
                        slug=f"quota-partial-{tenant_id.hex[:8]}",
                        quota={"max_campaigns": 0},
                    )
                )
            await db.flush()
            db.add_all(TenantQuotaUsage(tenant_id=tenant_id) for tenant_id in tenant_ids)

        await _begin_drained(owner_factory)
        async with owner_factory() as db, db.begin():
            await reconcile_quota_usage_from_authoritative_rows(db, tenant_ids[0], old_writers_drained=True)
            readiness = await activate_current_quota_rollout_epoch(db, operator="acceptance")
            assert readiness.ready is False
            assert readiness.global_phase is QuotaRolloutPhase.drained

        # Even the reconciled tenant cannot enforce while any current tenant
        # keeps the durable global epoch from becoming active.
        async with runtime_factory() as db:
            await _runtime_context(db, tenant_ids[0])
            usage = await reserve_quota(db, tenant_ids[0], CumulativeQuotaKey.MAX_CAMPAIGNS)
            assert usage.campaigns == 1
            await db.rollback()

        await _reconcile_all_current_tenants(owner_factory)
        async with owner_factory() as db, db.begin():
            readiness = await activate_current_quota_rollout_epoch(db, operator="acceptance-resume")
            assert readiness.ready is True

        async with runtime_factory() as db, db.begin():
            await _runtime_context(db, tenant_ids[0])
            with pytest.raises(QuotaExceededError, match="max_campaigns"):
                await reserve_quota(db, tenant_ids[0], CumulativeQuotaKey.MAX_CAMPAIGNS)
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_old_writer_drift_is_not_enforced_until_authoritative_reconciliation(
    migrated_pg_url: str,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id = uuid.uuid4()

    try:
        await _reset_rollout_bridge(owner_factory)
        async with owner_factory() as db, db.begin():
            db.add(
                Tenant(
                    id=tenant_id,
                    name="旧写进程漂移租户",
                    slug=f"quota-drift-{tenant_id.hex[:8]}",
                    quota={"max_campaigns": 1},
                )
            )
            await db.flush()
            db.add(TenantQuotaUsage(tenant_id=tenant_id))
            db.add(_campaign(tenant_id, "旧写进程漏记活动"))

        # The stale zero must not reject during the bridge. The increment is
        # still transactional and disappears with the business rollback.
        async with runtime_factory() as db:
            await _runtime_context(db, tenant_id)
            usage = await reserve_quota(db, tenant_id, CumulativeQuotaKey.MAX_CAMPAIGNS)
            assert usage.campaigns == 1
            await db.rollback()

        await _begin_drained(owner_factory)
        async with owner_factory() as db, db.begin():
            usage = await reconcile_quota_usage_from_authoritative_rows(
                db,
                tenant_id,
                old_writers_drained=True,
            )
            assert usage.campaigns == 1
            assert usage.enforcement_ready is True

        # A tenant marker alone is not enough: while the durable global epoch
        # is merely drained, the runtime continues double-writing without
        # rejecting. Activation after every current tenant is exact flips the
        # single global enforcement switch.
        async with runtime_factory() as db:
            await _runtime_context(db, tenant_id)
            usage = await reserve_quota(db, tenant_id, CumulativeQuotaKey.MAX_CAMPAIGNS)
            assert usage.campaigns == 2
            await db.rollback()

        await _activate_all_current_tenants(owner_factory)
        async with runtime_factory() as db, db.begin():
            await _runtime_context(db, tenant_id)
            with pytest.raises(QuotaExceededError, match="max_campaigns"):
                await reserve_quota(db, tenant_id, CumulativeQuotaKey.MAX_CAMPAIGNS)

        async with owner_factory() as db:
            usage = await db.get(TenantQuotaUsage, tenant_id)
            assert usage is not None
            assert usage.campaigns == 1
            assert usage.source_revision == QUOTA_RECONCILIATION_SOURCE_REVISION
            assert usage.reconciled_at is not None
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_readiness_stays_false_until_every_tenant_has_current_provenance(migrated_pg_url: str) -> None:
    engine = create_async_engine(migrated_pg_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    tenant_ids = (uuid.uuid4(), uuid.uuid4())

    try:
        await _reset_rollout_bridge(factory)
        async with factory() as db, db.begin():
            for index, tenant_id in enumerate(tenant_ids):
                db.add(
                    Tenant(
                        id=tenant_id,
                        name=f"门禁租户{index}",
                        slug=f"quota-readiness-{tenant_id.hex[:8]}",
                    )
                )
            await db.flush()
            for tenant_id in tenant_ids:
                db.add(TenantQuotaUsage(tenant_id=tenant_id))

        await _begin_drained(factory)
        async with factory() as db, db.begin():
            await reconcile_quota_usage_from_authoritative_rows(
                db,
                tenant_ids[0],
                old_writers_drained=True,
            )
            state = await get_quota_enforcement_readiness(db)
            assert state.ready is False

        # Reconcile every remaining tenant so the global deployment gate is
        # deterministic even when this test follows other acceptance cases.
        async with factory() as db:
            pending_ids = tuple(
                await db.scalars(
                    select(Tenant.id)
                    .outerjoin(TenantQuotaUsage, TenantQuotaUsage.tenant_id == Tenant.id)
                    .where(
                        (TenantQuotaUsage.tenant_id.is_(None))
                        | (TenantQuotaUsage.enforcement_ready.is_(False))
                        | (TenantQuotaUsage.source_revision != QUOTA_RECONCILIATION_SOURCE_REVISION)
                        | (TenantQuotaUsage.source_revision.is_(None))
                    )
                )
            )
            await db.rollback()
        for tenant_id in pending_ids:
            async with factory() as db, db.begin():
                await reconcile_quota_usage_from_authoritative_rows(db, tenant_id, old_writers_drained=True)

        async with factory() as db, db.begin():
            state = await activate_current_quota_rollout_epoch(db, operator="acceptance")
            assert state.ready is True
            assert state.ready_tenants == state.total_tenants
    finally:
        await engine.dispose()


async def test_operator_cli_fails_closed_then_reconciles_all_tenants(migrated_pg_url: str) -> None:
    tenant_id = uuid.uuid4()
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "UPDATE quota_rollout_state SET source_revision=$1, phase='bridge', "
            "drained_at=NULL, drained_by=NULL, activated_at=NULL, activated_by=NULL WHERE id=1",
            QUOTA_RECONCILIATION_SOURCE_REVISION,
        )
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, status, plan, tenant_type) "
            "VALUES ($1, 'CLI门禁租户', $2, 'active', 'free', 'brand')",
            tenant_id,
            f"quota-cli-{tenant_id.hex[:8]}",
        )
        await conn.execute("INSERT INTO tenant_quota_usage (tenant_id) VALUES ($1)", tenant_id)
    finally:
        await conn.close()

    blocked = _quota_cli(migrated_pg_url, "reconcile")
    assert blocked.returncode == 2
    assert "--old-writers-drained" in f"{blocked.stdout}\n{blocked.stderr}"
    pending = _quota_cli(migrated_pg_url, "readiness")
    assert pending.returncode == 2
    assert "quota_enforcement=NOT_READY" in pending.stdout

    conn = await asyncpg.connect(dsn)
    try:
        assert (
            await conn.fetchval("SELECT enforcement_ready FROM tenant_quota_usage WHERE tenant_id=$1", tenant_id)
            is False
        )
    finally:
        await conn.close()

    # Crash after the durable drain transition but before the tenant lock can
    # be acquired. The current epoch must remain globally non-enforcing and a
    # subsequent CLI invocation must resume to completion.
    blocker = await asyncpg.connect(dsn)
    blocker_tx = blocker.transaction()
    await blocker_tx.start()
    await blocker.execute("SELECT id FROM tenants WHERE id=$1 FOR UPDATE", tenant_id)
    interrupted = _quota_cli_process(migrated_pg_url, "reconcile", "--old-writers-drained")
    observer = await asyncpg.connect(dsn)
    try:
        for _ in range(100):
            if await observer.fetchval("SELECT phase='drained' FROM quota_rollout_state WHERE id=1"):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("CLI did not durably enter the drained epoch")
        assert interrupted.poll() is None
        interrupted.terminate()
        await asyncio.to_thread(interrupted.wait, 10)
        assert interrupted.returncode != 0
        assert await observer.fetchval("SELECT phase FROM quota_rollout_state WHERE id=1") == "drained"
        assert (
            await observer.fetchval("SELECT enforcement_ready FROM tenant_quota_usage WHERE tenant_id=$1", tenant_id)
            is False
        )
    finally:
        if interrupted.poll() is None:
            interrupted.kill()
            await asyncio.to_thread(interrupted.wait, 10)
        await observer.close()
        await blocker_tx.rollback()
        await blocker.close()

    reconciled = _quota_cli(migrated_pg_url, "reconcile", "--old-writers-drained")
    assert reconciled.returncode == 0, f"{reconciled.stdout}\n{reconciled.stderr}"
    assert "quota_enforcement=READY" in reconciled.stdout

    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT enforcement_ready, reconciled_at, source_revision FROM tenant_quota_usage WHERE tenant_id=$1",
            tenant_id,
        )
        assert row is not None
        assert row["enforcement_ready"] is True
        assert row["reconciled_at"] is not None
        assert row["source_revision"] == QUOTA_RECONCILIATION_SOURCE_REVISION
    finally:
        await conn.close()


@pytest.mark.parametrize("reconcile_first", [True, False], ids=["reconcile-first", "writer-first"])
async def test_reconciliation_and_new_writer_linearize_to_exact_counter(
    migrated_pg_url: str,
    reconcile_first: bool,
) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id = uuid.uuid4()
    first_locked = asyncio.Event()
    second_started = asyncio.Event()
    allow_first_commit = asyncio.Event()

    await _reset_rollout_bridge(owner_factory)
    await _begin_drained(owner_factory)
    async with owner_factory() as db, db.begin():
        db.add(
            Tenant(
                id=tenant_id,
                name=f"重算写入竞态-{reconcile_first}",
                slug=f"quota-reconcile-race-{tenant_id.hex[:8]}",
                quota={"max_campaigns": 100},
            )
        )
        await db.flush()
        db.add(TenantQuotaUsage(tenant_id=tenant_id))

    async def reconcile() -> None:
        async with owner_factory() as db, db.begin():
            if not reconcile_first:
                second_started.set()
            await reconcile_quota_usage_from_authoritative_rows(db, tenant_id, old_writers_drained=True)
            if reconcile_first:
                first_locked.set()
                await allow_first_commit.wait()

    async def write() -> None:
        async with runtime_factory() as db, db.begin():
            await _runtime_context(db, tenant_id)
            if reconcile_first:
                second_started.set()
            await reserve_quota(db, tenant_id, CumulativeQuotaKey.MAX_CAMPAIGNS)
            db.add(_campaign(tenant_id, f"竞态活动-{reconcile_first}"))
            await db.flush()
            if not reconcile_first:
                first_locked.set()
                await allow_first_commit.wait()

    try:
        if reconcile_first:
            first_task = asyncio.create_task(reconcile())
            await first_locked.wait()
            second_task = asyncio.create_task(write())
        else:
            first_task = asyncio.create_task(write())
            await first_locked.wait()
            second_task = asyncio.create_task(reconcile())
        await second_started.wait()
        await asyncio.sleep(0.1)
        assert second_task.done() is False
        allow_first_commit.set()
        await asyncio.gather(first_task, second_task)

        async with owner_factory() as db:
            usage = await db.get(TenantQuotaUsage, tenant_id)
            facts = await db.scalar(select(func.count()).select_from(Campaign).where(Campaign.tenant_id == tenant_id))
            assert usage is not None
            assert usage.enforcement_ready is True
            assert usage.campaigns == facts == 1
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_ready_reservation_rollback_preserves_marker_and_counter(migrated_pg_url: str) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_engine = create_async_engine(migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id = uuid.uuid4()

    try:
        await _reset_rollout_bridge(owner_factory)
        await _activate_all_current_tenants(owner_factory)
        async with owner_factory() as db, db.begin():
            db.add(
                Tenant(
                    id=tenant_id,
                    name="就绪回滚租户",
                    slug=f"quota-ready-rollback-{tenant_id.hex[:8]}",
                    quota={"max_campaigns": 1},
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

        async with runtime_factory() as db:
            await _runtime_context(db, tenant_id)
            usage = await reserve_quota(db, tenant_id, CumulativeQuotaKey.MAX_CAMPAIGNS)
            assert usage.campaigns == 1
            await db.rollback()

        async with owner_factory() as db:
            usage = await db.get(TenantQuotaUsage, tenant_id)
            assert usage is not None
            assert usage.campaigns == 0
            assert usage.enforcement_ready is True
            assert usage.source_revision == QUOTA_RECONCILIATION_SOURCE_REVISION
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_tenant_creation_first_blocks_activation_and_is_born_pending(migrated_pg_url: str) -> None:
    engine = create_async_engine(migrated_pg_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    tenant_key = f"rollout-create-first-{uuid.uuid4().hex[:8]}"
    tenant_holds_epoch = asyncio.Event()
    activation_started = asyncio.Event()
    tenant_initialized = asyncio.Event()
    allow_tenant_commit = asyncio.Event()

    try:
        await _reset_rollout_bridge(factory)
        await _begin_drained(factory)
        await _reconcile_all_current_tenants(factory)

        async def create_tenant():
            async with factory() as db, db.begin():
                # The production initializer takes this same shared lock. Take
                # it explicitly first only to expose a deterministic sync point.
                await lock_quota_rollout_state(db)
                tenant_holds_epoch.set()
                await activation_started.wait()
                receipt = await BrandTenantInitialization(db).initialize(_initialization_command(tenant_key))
                tenant_initialized.set()
                await allow_tenant_commit.wait()
                return receipt

        async def activate():
            await tenant_holds_epoch.wait()
            async with factory() as db, db.begin():
                activation_started.set()
                return await activate_current_quota_rollout_epoch(db, operator="acceptance-race")

        create_task = asyncio.create_task(create_tenant())
        await tenant_holds_epoch.wait()
        activate_task = asyncio.create_task(activate())
        await activation_started.wait()
        await tenant_initialized.wait()
        await asyncio.sleep(0.1)
        assert activate_task.done() is False
        allow_tenant_commit.set()
        receipt, readiness = await asyncio.gather(create_task, activate_task)

        assert readiness.ready is False
        assert readiness.global_phase is QuotaRolloutPhase.drained
        async with factory() as db:
            usage = await db.get(TenantQuotaUsage, receipt.tenant_id)
            state = await db.get(QuotaRolloutState, 1)
            assert usage is not None and usage.enforcement_ready is False
            assert usage.reconciled_at is None and usage.source_revision is None
            assert state is not None and state.phase is QuotaRolloutPhase.drained
    finally:
        await engine.dispose()


async def test_activation_first_blocks_tenant_creation_then_births_it_ready(migrated_pg_url: str) -> None:
    engine = create_async_engine(migrated_pg_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    tenant_key = f"rollout-active-first-{uuid.uuid4().hex[:8]}"
    activation_holds_epoch = asyncio.Event()
    initialization_started = asyncio.Event()
    allow_activation = asyncio.Event()

    try:
        await _reset_rollout_bridge(factory)
        await _begin_drained(factory)
        await _reconcile_all_current_tenants(factory)

        async def activate():
            async with factory() as db, db.begin():
                # Deterministic sync point on the same exclusive lock used by
                # the production activation function.
                await lock_quota_rollout_state(db, write=True)
                activation_holds_epoch.set()
                await allow_activation.wait()
                return await activate_current_quota_rollout_epoch(db, operator="acceptance-race")

        async def create_tenant():
            await activation_holds_epoch.wait()
            async with factory() as db, db.begin():
                initialization_started.set()
                return await BrandTenantInitialization(db).initialize(_initialization_command(tenant_key))

        activate_task = asyncio.create_task(activate())
        await activation_holds_epoch.wait()
        create_task = asyncio.create_task(create_tenant())
        await initialization_started.wait()
        await asyncio.sleep(0.1)
        assert create_task.done() is False
        allow_activation.set()
        readiness, receipt = await asyncio.gather(activate_task, create_task)

        assert readiness.ready is True
        async with factory() as db:
            usage = await db.get(TenantQuotaUsage, receipt.tenant_id)
            state = await db.get(QuotaRolloutState, 1)
            assert state is not None and state.phase is QuotaRolloutPhase.active
            assert usage is not None and usage.enforcement_ready is True
            assert usage.reconciled_at is not None
            assert usage.source_revision == QUOTA_RECONCILIATION_SOURCE_REVISION
    finally:
        await engine.dispose()


async def test_global_epoch_migration_singleton_acl_and_roundtrip(migrated_pg_url: str) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")

    _alembic(migrated_pg_url, "downgrade", GLOBAL_EPOCH_PARENT_REVISION)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT to_regclass('public.quota_rollout_state')") is None
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == GLOBAL_EPOCH_PARENT_REVISION
    finally:
        await conn.close()

    _alembic(migrated_pg_url, "upgrade", "head")
    conn = await asyncpg.connect(dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT id, source_revision, phase, drained_at, activated_at, drained_by, activated_by "
            "FROM quota_rollout_state"
        )
        assert row is not None
        assert dict(row) == {
            "id": 1,
            "source_revision": QUOTA_RECONCILIATION_SOURCE_REVISION,
            "phase": "bridge",
            "drained_at": None,
            "activated_at": None,
            "drained_by": None,
            "activated_by": None,
        }
        assert await runtime.fetchval("SELECT source_revision FROM quota_rollout_state WHERE id=1") == (
            QUOTA_RECONCILIATION_SOURCE_REVISION
        )
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not await conn.fetchval(
                "SELECT has_table_privilege('yimatong_app', 'public.quota_rollout_state', $1)",
                privilege,
            )
    finally:
        await runtime.close()
        await conn.close()


async def test_audit_index_catalog_guard_invalid_cleanup_and_roundtrip(migrated_pg_url: str) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    _alembic(migrated_pg_url, "downgrade", QUOTA_READINESS_REVISION)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT to_regclass('public.ix_platform_audit_target_timeline')") is None
        await conn.execute(f"CREATE INDEX {AUDIT_INDEX} ON public.platform_audit_log (target_tenant_id)")
    finally:
        await conn.close()

    try:
        failed = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
        assert "Refusing to reuse valid index" in f"{failed.stdout}\n{failed.stderr}"
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetchval("SELECT version_num FROM alembic_version") == QUOTA_READINESS_REVISION
            await conn.execute(f"DROP INDEX public.{AUDIT_INDEX}")
            await conn.execute(
                f"CREATE INDEX CONCURRENTLY {AUDIT_INDEX} ON public.platform_audit_log "
                "(target_tenant_id, timestamp DESC, id DESC)"
            )
            await conn.execute(
                "UPDATE pg_index SET indisvalid=false "
                "WHERE indexrelid='public.ix_platform_audit_target_timeline'::regclass"
            )
        finally:
            await conn.close()

        _alembic(migrated_pg_url, "upgrade", "head")
        conn = await asyncpg.connect(dsn)
        try:
            catalog = await conn.fetchrow(
                """
                SELECT i.indisvalid, i.indisready, i.indislive,
                       ARRAY(
                           SELECT attr.attname
                           FROM unnest(i.indkey::smallint[]) WITH ORDINALITY AS key(attnum, ordinality)
                           JOIN pg_attribute AS attr
                             ON attr.attrelid=i.indrelid AND attr.attnum=key.attnum
                           WHERE key.ordinality <= i.indnkeyatts ORDER BY key.ordinality
                       ) AS columns,
                       i.indoption::smallint[] AS options
                FROM pg_index AS i
                WHERE i.indexrelid='public.ix_platform_audit_target_timeline'::regclass
                """
            )
            assert catalog is not None
            assert (catalog["indisvalid"], catalog["indisready"], catalog["indislive"]) == (True, True, True)
            assert tuple(catalog["columns"]) == ("target_tenant_id", "timestamp", "id")
            assert tuple(catalog["options"]) == (0, 3, 3)
        finally:
            await conn.close()

        _alembic(migrated_pg_url, "downgrade", QUOTA_READINESS_REVISION)
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetchval("SELECT to_regclass('public.ix_platform_audit_target_timeline')") is None
        finally:
            await conn.close()
    finally:
        _alembic(migrated_pg_url, "upgrade", "head")
