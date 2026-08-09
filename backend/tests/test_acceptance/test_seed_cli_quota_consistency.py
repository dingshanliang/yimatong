"""Real PostgreSQL proof that trusted seed CLIs keep active quota counters exact."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.tenant import Tenant
from app.services.quota import (
    QUOTA_RECONCILIATION_SOURCE_REVISION,
    CumulativeQuotaKey,
    QuotaExceededError,
    activate_current_quota_rollout_epoch,
    begin_current_quota_rollout_epoch,
    reconcile_quota_usage_from_authoritative_rows,
    reserve_quota,
)
from app.utils.security import verify_password

pytestmark = [pytest.mark.acceptance, pytest.mark.anyio]

BACKEND_DIR = Path(__file__).resolve().parents[2]
BASELINE_SLUGS = ("baseline-base", "baseline-control")


def _runtime_url(owner_url: str) -> str:
    return (
        make_url(owner_url).set(username="yimatong_app", password="yimatong_app").render_as_string(hide_password=False)
    )


def _run_cli(
    runtime_url: str,
    control_url: str,
    *args: str,
    succeeds: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["database_url"] = runtime_url
    env["migration_database_url"] = control_url
    env["control_database_url"] = control_url
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if succeeds:
        assert result.returncode == 0, f"CLI {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    else:
        assert result.returncode != 0, f"CLI {' '.join(args)} unexpectedly succeeded"
    return result


async def _activate_current_epoch(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as db, db.begin():
        await begin_current_quota_rollout_epoch(
            db,
            old_writers_drained=True,
            operator="seed-cli-acceptance",
        )

    async with factory() as db:
        tenant_ids = tuple(await db.scalars(select(Tenant.id).order_by(Tenant.id)))
    for tenant_id in tenant_ids:
        async with factory() as db, db.begin():
            await reconcile_quota_usage_from_authoritative_rows(
                db,
                tenant_id,
                old_writers_drained=True,
            )

    async with factory() as db, db.begin():
        readiness = await activate_current_quota_rollout_epoch(db, operator="seed-cli-acceptance")
        assert readiness.ready


async def _install_refresh_failure_trigger(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION cycle9_fail_campaign_quota_refresh()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.campaigns > OLD.campaigns THEN
                RAISE EXCEPTION 'cycle9 injected quota refresh failure';
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER cycle9_fail_campaign_quota_refresh
        BEFORE UPDATE ON tenant_quota_usage
        FOR EACH ROW EXECUTE FUNCTION cycle9_fail_campaign_quota_refresh();
        """
    )


async def _drop_refresh_failure_trigger(conn: asyncpg.Connection) -> None:
    await conn.execute("DROP TRIGGER IF EXISTS cycle9_fail_campaign_quota_refresh ON tenant_quota_usage")
    await conn.execute("DROP FUNCTION IF EXISTS cycle9_fail_campaign_quota_refresh()")


async def _install_admin_repair_failure_trigger(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION cycle11_fail_after_account_repair()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.name = 'admin'
               AND NEW.tenant_id = (SELECT id FROM tenants WHERE slug = 'baseline-base') THEN
                RAISE EXCEPTION 'cycle11 injected failure after account repair';
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER cycle11_fail_after_account_repair
        BEFORE INSERT ON roles
        FOR EACH ROW EXECUTE FUNCTION cycle11_fail_after_account_repair();
        """
    )


async def _drop_admin_repair_failure_trigger(conn: asyncpg.Connection) -> None:
    await conn.execute("DROP TRIGGER IF EXISTS cycle11_fail_after_account_repair ON roles")
    await conn.execute("DROP FUNCTION IF EXISTS cycle11_fail_after_account_repair()")


async def _tenant_id(conn: asyncpg.Connection, slug: str) -> uuid.UUID | None:
    return await conn.fetchval("SELECT id FROM tenants WHERE slug=$1", slug)


async def _insert_expired_baseline_identity(conn: asyncpg.Connection) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    inserted = await conn.fetchval(
        """
        INSERT INTO tenants (
            id, name, slug, status, plan, plan_expires_at, tenant_type, industry,
            quota, enabled_features, categories, created_at, updated_at
        )
        SELECT
            $1, '基准租户', 'baseline-base', 'active', 'free', now() - interval '1 minute',
            'brand', '食品', quota_defaults, feature_flags, '[]'::json, now(), now()
        FROM plan_definitions
        WHERE name = 'free' AND is_active IS TRUE
        RETURNING id
        """,
        tenant_id,
    )
    assert inserted == tenant_id
    await conn.execute(
        """
        INSERT INTO tenant_quota_usage (
            tenant_id, codes, scans, campaigns, products, accounts,
            reconciled_at, source_revision, enforcement_ready
        ) VALUES ($1, 0, 0, 0, 0, 0, now(), $2, true)
        """,
        tenant_id,
        QUOTA_RECONCILIATION_SOURCE_REVISION,
    )
    return tenant_id


async def _assert_no_admin_graph(conn: asyncpg.Connection, tenant_id: uuid.UUID) -> None:
    row = await conn.fetchrow(
        """
        SELECT
            (SELECT count(*) FROM organizations WHERE tenant_id=$1) AS organizations,
            (SELECT count(*) FROM accounts WHERE tenant_id=$1) AS accounts,
            (SELECT count(*) FROM roles WHERE tenant_id=$1) AS roles,
            (SELECT count(*) FROM account_roles ar JOIN accounts a ON a.id=ar.account_id WHERE a.tenant_id=$1)
                AS account_roles
        """,
        tenant_id,
    )
    assert row is not None
    assert dict(row) == {"organizations": 0, "accounts": 0, "roles": 0, "account_roles": 0}


async def _assert_exact_usage(conn: asyncpg.Connection, tenant_id: uuid.UUID) -> dict[str, int]:
    row = await conn.fetchrow(
        """
        SELECT
            usage.codes,
            usage.scans,
            usage.campaigns,
            usage.products,
            usage.accounts,
            usage.enforcement_ready,
            usage.reconciled_at,
            usage.source_revision,
            (SELECT count(*) FROM code_items WHERE tenant_id=$1) AS fact_codes,
            (SELECT count(*) FROM scan_events WHERE tenant_id=$1) AS fact_scans,
            (SELECT count(*) FROM campaigns WHERE tenant_id=$1) AS fact_campaigns,
            (SELECT count(*) FROM products WHERE tenant_id=$1) AS fact_products,
            (SELECT count(*) FROM accounts WHERE tenant_id=$1) AS fact_accounts
        FROM tenant_quota_usage AS usage
        WHERE usage.tenant_id=$1
        """,
        tenant_id,
    )
    assert row is not None
    counters = {key: int(row[key]) for key in ("codes", "scans", "campaigns", "products", "accounts")}
    facts = {key: int(row[f"fact_{key}"]) for key in ("codes", "scans", "campaigns", "products", "accounts")}
    assert counters == facts
    assert row["enforcement_ready"] is True
    assert row["reconciled_at"] is not None
    assert row["source_revision"] == QUOTA_RECONCILIATION_SOURCE_REVISION
    return counters


async def _assert_next_reservation_rejected(
    migrated_pg_url: str,
    owner: asyncpg.Connection,
    tenant_id: uuid.UUID,
    key: CumulativeQuotaKey,
    current_usage: int,
) -> None:
    await owner.execute(
        "UPDATE tenants SET quota=$2::json WHERE id=$1",
        tenant_id,
        f'{{"{key.value}": {current_usage}}}',
    )
    runtime_url = _runtime_url(migrated_pg_url)
    runtime_engine = create_async_engine(runtime_url)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with runtime_factory() as db:
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            with pytest.raises(QuotaExceededError) as exc_info:
                await reserve_quota(db, tenant_id, key)
            assert exc_info.value.status_code == 429
            await db.rollback()
    finally:
        await runtime_engine.dispose()


async def test_seed_all_and_baseline_refresh_active_quota_usage_atomically(migrated_pg_url: str) -> None:
    owner_engine = create_async_engine(migrated_pg_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(dsn)
    runtime_url = _runtime_url(migrated_pg_url)
    seed_slug = f"cycle9-seed-{uuid.uuid4().hex[:8]}"
    seed_email = f"{seed_slug}@example.com"
    refresh_trigger_installed = False
    admin_trigger_installed = False

    try:
        await _activate_current_epoch(owner_factory)
        await _install_refresh_failure_trigger(owner)
        refresh_trigger_installed = True

        _run_cli(
            runtime_url,
            migrated_pg_url,
            "all",
            "--name",
            "Cycle 9 Seed",
            "--slug",
            seed_slug,
            "--admin-email",
            seed_email,
            "--admin-password",
            "StrongPass123",
            succeeds=False,
        )
        seed_tenant_id = await _tenant_id(owner, seed_slug)
        assert seed_tenant_id is not None
        # Tenant initialization committed first, but every business row and
        # counter mutation from the failed final-refresh transaction rolled back.
        failed_seed_usage = await _assert_exact_usage(owner, seed_tenant_id)
        assert failed_seed_usage == {"codes": 0, "scans": 0, "campaigns": 0, "products": 0, "accounts": 1}

        await _drop_refresh_failure_trigger(owner)
        refresh_trigger_installed = False

        baseline_tenant_id = await _tenant_id(owner, BASELINE_SLUGS[0])
        if baseline_tenant_id is None:
            baseline_tenant_id = await _insert_expired_baseline_identity(owner)
            expired = _run_cli(runtime_url, migrated_pg_url, "baseline", "build", "--json", succeeds=False)
            assert "plan is expired" in expired.stderr
            assert "renew it and rerun baseline build" in expired.stderr
            assert await _tenant_id(owner, BASELINE_SLUGS[0]) == baseline_tenant_id
            control_tenant_id = await _tenant_id(owner, BASELINE_SLUGS[1])
            assert control_tenant_id is not None
            await _assert_no_admin_graph(owner, baseline_tenant_id)
            await _assert_no_admin_graph(owner, control_tenant_id)
            assert await _assert_exact_usage(owner, baseline_tenant_id) == {
                "codes": 0,
                "scans": 0,
                "campaigns": 0,
                "products": 0,
                "accounts": 0,
            }
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM tenant_quota_usage WHERE tenant_id=$1",
                    control_tenant_id,
                )
                == 0
            )

            await owner.execute("UPDATE tenants SET plan_expires_at=NULL WHERE id=$1", baseline_tenant_id)
            await _install_admin_repair_failure_trigger(owner)
            admin_trigger_installed = True
            repaired_then_failed = _run_cli(
                runtime_url,
                migrated_pg_url,
                "baseline",
                "build",
                "--json",
                succeeds=False,
            )
            assert "injected failure after account repair" in repaired_then_failed.stderr
            await _assert_no_admin_graph(owner, baseline_tenant_id)
            assert await _assert_exact_usage(owner, baseline_tenant_id) == {
                "codes": 0,
                "scans": 0,
                "campaigns": 0,
                "products": 0,
                "accounts": 0,
            }
            await _drop_admin_repair_failure_trigger(owner)
            admin_trigger_installed = False
        else:
            # Order-sensitive regression: takeover acceptance must restore the
            # exact shared baseline plan state rather than leaving it expired.
            assert (
                await owner.fetchval(
                    "SELECT plan_expires_at FROM tenants WHERE id=$1",
                    baseline_tenant_id,
                )
                is None
            )
            control_tenant_id = await _tenant_id(owner, BASELINE_SLUGS[1])
            assert control_tenant_id is not None

        _run_cli(
            runtime_url,
            migrated_pg_url,
            "all",
            "--name",
            "Cycle 9 Seed",
            "--slug",
            seed_slug,
            "--admin-email",
            seed_email,
            "--admin-password",
            "StrongPass123",
        )
        first_baseline = _run_cli(runtime_url, migrated_pg_url, "baseline", "build", "--json")
        first_summary = json.loads(first_baseline.stdout)
        second_baseline = _run_cli(runtime_url, migrated_pg_url, "baseline", "build", "--json")
        second_summary = json.loads(second_baseline.stdout)
        assert second_summary == first_summary
        verified = _run_cli(runtime_url, migrated_pg_url, "baseline", "verify", "--json")
        assert json.loads(verified.stdout)["status"] == "passed"

        seed_usage = await _assert_exact_usage(owner, seed_tenant_id)
        assert await _tenant_id(owner, BASELINE_SLUGS[0]) == baseline_tenant_id
        assert await _tenant_id(owner, BASELINE_SLUGS[1]) == control_tenant_id
        baseline_usage = await _assert_exact_usage(owner, baseline_tenant_id)
        control_usage = await _assert_exact_usage(owner, control_tenant_id)
        baseline_hash = await owner.fetchval(
            "SELECT hashed_password FROM accounts WHERE tenant_id=$1 AND email='admin@baseline.local'",
            baseline_tenant_id,
        )
        assert baseline_hash is not None and verify_password("Baseline1234", baseline_hash)

        runtime_dsn = runtime_url.replace("postgresql+asyncpg://", "postgresql://")
        runtime = await asyncpg.connect(runtime_dsn)
        try:
            assert (
                await runtime.fetchval("SELECT has_parameter_privilege(current_user, 'app.bypass_rls', 'SET')") is False
            )
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id', $1, true)", str(baseline_tenant_id))
                assert (
                    await runtime.fetchval("SELECT count(*) FROM products WHERE tenant_id=$1", baseline_tenant_id)
                    == baseline_usage["products"]
                )
                assert (
                    await runtime.fetchval("SELECT count(*) FROM products WHERE tenant_id=$1", control_tenant_id) == 0
                )
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id', $1, true)", str(control_tenant_id))
                assert (
                    await runtime.fetchval("SELECT count(*) FROM products WHERE tenant_id=$1", control_tenant_id)
                    == control_usage["products"]
                )
                assert (
                    await runtime.fetchval("SELECT count(*) FROM products WHERE tenant_id=$1", baseline_tenant_id) == 0
                )
        finally:
            await runtime.close()

        await _assert_next_reservation_rejected(
            migrated_pg_url,
            owner,
            seed_tenant_id,
            CumulativeQuotaKey.MAX_PRODUCTS,
            seed_usage["products"],
        )
        await _assert_next_reservation_rejected(
            migrated_pg_url,
            owner,
            baseline_tenant_id,
            CumulativeQuotaKey.MAX_CAMPAIGNS,
            baseline_usage["campaigns"],
        )
    finally:
        if refresh_trigger_installed:
            await _drop_refresh_failure_trigger(owner)
        if admin_trigger_installed:
            await _drop_admin_repair_failure_trigger(owner)
        # The session-scoped acceptance fixture owns and drops this uniquely
        # leased database. Do not partially delete the committed CLI graph:
        # tenant FKs are deliberately restrictive and a partial cleanup would
        # provide weaker evidence than the fixture's verified database drop.
        await owner.close()
        await owner_engine.dispose()
