"""Real PostgreSQL proof for fixed-role normalization and reversal."""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url

from tests.test_acceptance.conftest import (
    ADMIN_DSN,
    BACKEND_DIR,
    AcceptanceDatabaseLease,
    _create_owned_database,
    _drop_database_with_retry,
    run_owned_migrations_with_snapshot_retry,
)

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


def _alembic(database_url: str, *args: str) -> None:
    env = os.environ.copy()
    env["database_url"] = database_url
    env["migration_database_url"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"


async def _migration_heads(database_url: str) -> tuple[str, ...]:
    conn = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        rows = await conn.fetch("SELECT version_num FROM alembic_version ORDER BY version_num")
        return tuple(row["version_num"] for row in rows)
    finally:
        await conn.close()


async def _resolve_test_owned_recovery_markers(database_url: str) -> None:
    """Acknowledge only the deterministic markers created inside this leased empty database."""

    expected_markers = {
        ("0006", "distributors", "contact_phone"),
        ("0006", "kyc_records", "real_name"),
        ("0006", "kyc_records", "id_number"),
        ("0006", "kyc_records", "phone"),
    }
    conn = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        rows = await conn.fetch(
            "SELECT source_revision,source_table,source_column,state FROM legacy_pii_recovery_markers"
        )
        actual_markers = {(row["source_revision"], row["source_table"], row["source_column"]) for row in rows}
        assert actual_markers == expected_markers
        assert all(row["state"] == "legacy_unknown" for row in rows)
        result = await conn.execute(
            "UPDATE legacy_pii_recovery_markers "
            "SET state='operator_recovered', note=note||'; isolated fixed-role roundtrip acknowledgement' "
            "WHERE source_revision='0006' AND state='legacy_unknown'"
        )
        assert result == f"UPDATE {len(expected_markers)}"
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def fixed_role_pg_url(migrated_pg_url: str) -> AsyncIterator[str]:
    """Lease a database so deep migration round trips never mutate the shared acceptance DB."""

    database_name = f"yimatong_acceptance_fixedrole_{uuid.uuid4().hex[:12]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(
        database_name=database_name,
        database_dsn=database_url,
        owner_token=uuid.uuid4().hex,
    )
    initial_heads: tuple[str, ...] = ()
    primary_error: BaseException | None = None
    cleanup_errors: list[BaseException] = []
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        initial_heads = await _migration_heads(database_url)
        assert initial_heads, "fresh leased database has no Alembic head"
        await _resolve_test_owned_recovery_markers(database_url)
        yield database_url
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if lease.created:
            if initial_heads:
                try:
                    _alembic(database_url, "upgrade", "heads")
                    assert await _migration_heads(database_url) == initial_heads
                except BaseException as exc:
                    cleanup_errors.append(exc)
            try:
                await _drop_database_with_retry(
                    lease.database_name,
                    ADMIN_DSN,
                    expected_owner_marker=lease.owner_marker,
                    allow_unmarked_created=lease.created and not lease.marker_written,
                )
            except BaseException as exc:
                cleanup_errors.append(exc)

        if cleanup_errors:
            if primary_error is not None:
                for cleanup_error in cleanup_errors:
                    primary_error.add_note(f"fixed-role isolated database cleanup also failed: {cleanup_error!r}")
            else:
                raise cleanup_errors[0]


async def test_fixed_role_drift_is_normalized_and_restored_on_downgrade(fixed_role_pg_url: str):
    _alembic(fixed_role_pg_url, "downgrade", "d052bb312c44")
    dsn = fixed_role_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    tenant_id, role_id, permission_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, status, plan, tenant_type, created_at, updated_at) "
            "VALUES ($1, 'migration tenant', $2, 'active', 'free', 'brand', now(), now())",
            tenant_id,
            f"migration-{tenant_id.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO roles (id, tenant_id, name, description) VALUES ($1, $2, 'viewer', 'legacy viewer')",
            role_id,
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO permissions (id, tenant_id, code, description) "
            "VALUES ($1, $2, 'tenant:manage', 'legacy elevated permission')",
            permission_id,
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", role_id, permission_id
        )
    finally:
        await conn.close()

    _alembic(fixed_role_pg_url, "upgrade", "e163ca423d55")
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT count(*) FROM role_permissions WHERE role_id = $1", role_id) == 0
        assert (
            await conn.fetchval(
                "SELECT prior_permission_ids::jsonb @> $1::jsonb FROM role_template_backups WHERE role_id = $2",
                f'["{permission_id}"]',
                role_id,
            )
            is True
        )
        assert await conn.fetchval("SELECT description FROM roles WHERE id = $1", role_id) == "无业务操作权限成员"
    finally:
        await conn.close()

    _alembic(fixed_role_pg_url, "downgrade", "d052bb312c44")
    conn = await asyncpg.connect(dsn)
    try:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM role_permissions WHERE role_id = $1 AND permission_id = $2",
                role_id,
                permission_id,
            )
            == 1
        )
        assert await conn.fetchval("SELECT description FROM roles WHERE id = $1", role_id) == "legacy viewer"
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM role_permissions WHERE role_id = $1 AND permission_id = $2",
                role_id,
                permission_id,
            )
            await conn.execute("DELETE FROM permissions WHERE id = $1", permission_id)
            await conn.execute("DELETE FROM roles WHERE id = $1", role_id)
            await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
    finally:
        await conn.close()
    _alembic(fixed_role_pg_url, "upgrade", "head")


async def test_tenant_platform_admin_assignment_is_removed_and_reversible(fixed_role_pg_url: str):
    _alembic(fixed_role_pg_url, "downgrade", "g385ec645f77")
    dsn = fixed_role_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    tenant_id, organization_id, account_id, role_id = (uuid.uuid4() for _ in range(4))
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, status, plan, tenant_type, created_at, updated_at) "
            "VALUES ($1, 'legacy tenant platform role', $2, 'active', 'free', 'brand', now(), now())",
            tenant_id,
            f"legacy-platform-{tenant_id.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO organizations (id, tenant_id, name, created_at, updated_at) "
            "VALUES ($1, $2, '总部', now(), now())",
            organization_id,
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO accounts "
            "(id, tenant_id, organization_id, email, hashed_password, name, is_active, auth_version, "
            "must_change_password, failed_login_attempts, created_at, updated_at) "
            "VALUES ($1, $2, $3, $4, 'unused', '历史账号', true, 4, false, 0, now(), now())",
            account_id,
            tenant_id,
            organization_id,
            f"legacy-{account_id.hex[:8]}@example.com",
        )
        await conn.execute(
            "INSERT INTO roles (id, tenant_id, name, description) VALUES ($1, $2, 'platform_admin', '历史错误角色')",
            role_id,
            tenant_id,
        )
        await conn.execute("INSERT INTO account_roles (account_id, role_id) VALUES ($1, $2)", account_id, role_id)
    finally:
        await conn.close()

    _alembic(fixed_role_pg_url, "upgrade", "h496fd756a88")
    conn = await asyncpg.connect(dsn)
    try:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM account_roles WHERE account_id = $1 AND role_id = $2", account_id, role_id
            )
            == 0
        )
        assert await conn.fetchval("SELECT auth_version FROM accounts WHERE id = $1", account_id) == 5
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM tenant_platform_role_assignment_backups WHERE account_id = $1 AND role_id = $2",
                account_id,
                role_id,
            )
            == 1
        )
    finally:
        await conn.close()

    _alembic(fixed_role_pg_url, "downgrade", "g385ec645f77")
    conn = await asyncpg.connect(dsn)
    try:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM account_roles WHERE account_id = $1 AND role_id = $2", account_id, role_id
            )
            == 1
        )
        assert await conn.fetchval("SELECT auth_version FROM accounts WHERE id = $1", account_id) == 4
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM account_roles WHERE account_id = $1 AND role_id = $2",
                account_id,
                role_id,
            )
            await conn.execute("DELETE FROM accounts WHERE id = $1", account_id)
            await conn.execute("DELETE FROM organizations WHERE id = $1", organization_id)
            await conn.execute("DELETE FROM roles WHERE id = $1", role_id)
            await conn.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        assert await conn.fetchval("SELECT count(*) FROM tenants WHERE id = $1", tenant_id) == 0
    finally:
        await conn.close()
    _alembic(fixed_role_pg_url, "upgrade", "head")


async def test_cross_tenant_organization_parent_is_repaired_and_reversible(fixed_role_pg_url: str):
    _alembic(fixed_role_pg_url, "downgrade", "e163ca423d55")
    dsn = fixed_role_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    tenant_a, tenant_b, parent_id, child_id = (uuid.uuid4() for _ in range(4))
    conn = await asyncpg.connect(dsn)
    try:
        for tenant_id, slug in ((tenant_a, "parent"), (tenant_b, "child")):
            await conn.execute(
                "INSERT INTO tenants (id, name, slug, status, plan, tenant_type, created_at, updated_at) "
                "VALUES ($1, $2, $3, 'active', 'free', 'brand', now(), now())",
                tenant_id,
                f"{slug} tenant",
                f"migration-{slug}-{tenant_id.hex[:8]}",
            )
        await conn.execute(
            "INSERT INTO organizations (id, tenant_id, name, created_at, updated_at) "
            "VALUES ($1, $2, 'Parent', now(), now())",
            parent_id,
            tenant_a,
        )
        await conn.execute(
            "INSERT INTO organizations (id, tenant_id, name, parent_id, created_at, updated_at) "
            "VALUES ($1, $2, 'Cross Child', $3, now(), now())",
            child_id,
            tenant_b,
            parent_id,
        )
    finally:
        await conn.close()

    _alembic(fixed_role_pg_url, "upgrade", "f274db534e66")
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT parent_id FROM organizations WHERE id = $1", child_id) is None
        assert (
            await conn.fetchval(
                "SELECT prior_parent_id FROM organization_parent_repair_backups WHERE organization_id = $1", child_id
            )
            == parent_id
        )
    finally:
        await conn.close()

    _alembic(fixed_role_pg_url, "downgrade", "e163ca423d55")
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT parent_id FROM organizations WHERE id = $1", child_id) == parent_id
        async with conn.transaction():
            await conn.execute("DELETE FROM organizations WHERE id = $1", child_id)
            await conn.execute("DELETE FROM organizations WHERE id = $1", parent_id)
            await conn.execute("DELETE FROM tenants WHERE id = ANY($1::uuid[])", [tenant_a, tenant_b])
    finally:
        await conn.close()
    _alembic(fixed_role_pg_url, "upgrade", "head")


async def test_operator_campaign_manage_grants_are_backfilled_and_reversed_safely(fixed_role_pg_url: str):
    _alembic(fixed_role_pg_url, "downgrade", "i507ae867b99")
    dsn = fixed_role_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    tenant_new, tenant_existing, tenant_mapped = (uuid.uuid4() for _ in range(3))
    role_new, role_new_duplicate, role_existing, role_mapped = (uuid.uuid4() for _ in range(4))
    permission_existing, permission_mapped = uuid.uuid4(), uuid.uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        for tenant_id, suffix in (
            (tenant_new, "new"),
            (tenant_existing, "existing"),
            (tenant_mapped, "mapped"),
        ):
            await conn.execute(
                "INSERT INTO tenants (id, name, slug, status, plan, tenant_type, created_at, updated_at) "
                "VALUES ($1, $2, $3, 'active', 'free', 'brand', now(), now())",
                tenant_id,
                f"operator campaign manage {suffix}",
                f"operator-campaign-{suffix}-{tenant_id.hex[:8]}",
            )
        for role_id, tenant_id in (
            (role_new, tenant_new),
            (role_new_duplicate, tenant_new),
            (role_existing, tenant_existing),
            (role_mapped, tenant_mapped),
        ):
            await conn.execute(
                "INSERT INTO roles (id, tenant_id, name, description) VALUES ($1, $2, 'operator', '运营人员')",
                role_id,
                tenant_id,
            )
        for permission_id, tenant_id in (
            (permission_existing, tenant_existing),
            (permission_mapped, tenant_mapped),
        ):
            await conn.execute(
                "INSERT INTO permissions (id, tenant_id, code, description) "
                "VALUES ($1, $2, 'campaign:manage', 'existing permission')",
                permission_id,
                tenant_id,
            )
        await conn.execute(
            "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)", role_mapped, permission_mapped
        )
    finally:
        await conn.close()

    _alembic(fixed_role_pg_url, "upgrade", "937c5e56d157")
    conn = await asyncpg.connect(dsn)
    try:
        created_permission = await conn.fetchval(
            "SELECT p.id FROM permissions p JOIN role_permissions rp ON rp.permission_id = p.id "
            "WHERE rp.role_id = $1 AND p.code = 'campaign:manage'",
            role_new,
        )
        assert created_permission is not None
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM role_permissions WHERE role_id IN ($1, $2) AND permission_id = $3",
                role_new,
                role_new_duplicate,
                created_permission,
            )
            == 2
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM role_permissions WHERE role_id = $1 AND permission_id = $2",
                role_existing,
                permission_existing,
            )
            == 1
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM operator_campaign_manage_grants WHERE role_id IN ($1, $2, $3)",
                role_new,
                role_new_duplicate,
                role_existing,
            )
            == 3
        )
        assert (
            await conn.fetchval("SELECT count(*) FROM operator_campaign_manage_grants WHERE role_id = $1", role_mapped)
            == 0
        )
        assert (
            await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE relname = 'operator_campaign_manage_grants'")
            is True
        )
        policy = await conn.fetchrow(
            "SELECT qual, with_check FROM pg_policies "
            "WHERE tablename = 'operator_campaign_manage_grants' AND policyname = 'tenant_isolation'"
        )
        assert policy is not None
        assert "current_tenant_id()" in policy["qual"]
        assert "app.bypass_rls" in policy["qual"]
        assert "current_tenant_id()" in policy["with_check"]
        assert "app.bypass_rls" in policy["with_check"]
        await conn.execute("ALTER TABLE operator_campaign_manage_grants FORCE ROW LEVEL SECURITY")
        await conn.execute("GRANT SELECT ON operator_campaign_manage_grants TO acceptance_tester")
    finally:
        await conn.close()

    tester_dsn = dsn.replace("yimatong:yimatong@", "acceptance_tester:tester_pwd@")
    conn = await asyncpg.connect(tester_dsn)
    try:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_existing}'")
            await conn.execute("SET LOCAL app.bypass_rls = 'false'")
            visible_tenants = await conn.fetch(
                "SELECT DISTINCT tenant_id FROM operator_campaign_manage_grants ORDER BY tenant_id"
            )
            assert [row["tenant_id"] for row in visible_tenants] == [tenant_existing]

        async with conn.transaction():
            await conn.execute("SET LOCAL app.tenant_id = ''")
            await conn.execute("SET LOCAL app.bypass_rls = 'true'")
            assert await conn.fetchval("SELECT count(*) FROM operator_campaign_manage_grants") >= 3
    finally:
        await conn.close()

    _alembic(fixed_role_pg_url, "downgrade", "i507ae867b99")
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT count(*) FROM permissions WHERE id = $1", created_permission) == 0
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM role_permissions WHERE role_id = $1 AND permission_id = $2",
                role_existing,
                permission_existing,
            )
            == 0
        )
        assert await conn.fetchval("SELECT count(*) FROM permissions WHERE id = $1", permission_existing) == 1
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM role_permissions WHERE role_id = $1 AND permission_id = $2",
                role_mapped,
                permission_mapped,
            )
            == 1
        )
        # All rows belong to this leased database and this test. Remove the
        # historical fixtures after reversal so later authority migrations are
        # checked against a clean catalog rather than synthetic role drift.
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM role_permissions WHERE role_id = ANY($1::uuid[])",
                [role_new, role_new_duplicate, role_existing, role_mapped],
            )
            await conn.execute(
                "DELETE FROM roles WHERE id = ANY($1::uuid[])",
                [role_new, role_new_duplicate, role_existing, role_mapped],
            )
            await conn.execute(
                "DELETE FROM permissions WHERE id = ANY($1::uuid[])",
                [permission_existing, permission_mapped],
            )
            await conn.execute(
                "DELETE FROM tenants WHERE id = ANY($1::uuid[])",
                [tenant_new, tenant_existing, tenant_mapped],
            )
    finally:
        await conn.close()
    _alembic(fixed_role_pg_url, "upgrade", "head")
