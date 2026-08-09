"""Real PostgreSQL proof for durable Platform control-plane sessions."""

import os
import subprocess
import sys
import uuid

import asyncpg
import pytest

from tests.test_acceptance.conftest import BACKEND_DIR

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "a8d1c4e7f2b6"


def _alembic(database_url: str, *args: str, succeeds: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "database_url": database_url,
            "migration_database_url": database_url,
            "control_database_url": database_url,
        }
    )
    arguments = [sys.executable, "-m", "alembic"]
    if args == ("check",):
        arguments.extend(["-x", "baseline_legacy_timestamp_nullability=true"])
    arguments.extend(args)
    result = subprocess.run(
        arguments,
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


async def test_platform_session_acl_and_safe_downgrade(migrated_pg_url: str) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    session_id = uuid.uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        assert not await conn.fetchval(
            "SELECT has_table_privilege('yimatong_app', 'platform_auth_sessions', 'SELECT,INSERT,UPDATE,DELETE')"
        )
        await conn.execute(
            "INSERT INTO platform_auth_sessions (id, principal, expires_at) "
            "VALUES ($1, 'platform-admin', now() + interval '15 minutes')",
            session_id,
        )
    finally:
        await conn.close()

    blocked = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
    assert "Cannot downgrade while Platform tokens may still be valid" in f"{blocked.stdout}\n{blocked.stderr}"

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == "b9e2c3d4f5a6"
        assert await conn.fetchval("SELECT to_regclass('public.platform_auth_sessions') IS NOT NULL")
        await conn.execute(
            "UPDATE platform_auth_sessions SET expires_at=now() - interval '1 second' WHERE id=$1",
            session_id,
        )
    finally:
        await conn.close()

    grace_blocked = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
    assert (
        "Cannot downgrade while Platform tokens may still be valid" in f"{grace_blocked.stdout}\n{grace_blocked.stderr}"
    )

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == "b9e2c3d4f5a6"
        await conn.execute(
            "UPDATE platform_auth_sessions SET expires_at=now() - interval '6 seconds' WHERE id=$1",
            session_id,
        )
    finally:
        await conn.close()

    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT to_regclass('public.platform_auth_sessions') IS NULL")
    finally:
        await conn.close()

    _alembic(migrated_pg_url, "upgrade", "head")
    _alembic(migrated_pg_url, "check")
