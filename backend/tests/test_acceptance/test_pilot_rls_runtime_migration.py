"""Runtime-faithful upgrade/downgrade proof for the pilot RLS remediation."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

import asyncpg
import pytest

from app.models.pilot_milestone import PilotMilestone, PilotMilestoneCorrection
from app.models.retrospective import Retrospective
from tests.test_acceptance.conftest import BACKEND_DIR, seed_baseline

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "p4e5f6a7b8c9"
HEAD_REVISION = "fec8dda0b399"
PILOT_TABLES = ("pilot_milestones", "retrospectives", "pilot_milestone_corrections")
PILOT_MODELS = (PilotMilestone, Retrospective, PilotMilestoneCorrection)
PARENT_UNIQUE = "uq_pilot_milestones_tenant_id_id"
OLD_MILESTONE_FK = "pilot_milestone_corrections_milestone_id_fkey"
TENANT_MILESTONE_FK = "fk_pilot_milestone_corrections_tenant_milestone"


def _run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
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


def _alembic(database_url: str, *args: str) -> None:
    result = _run_alembic(database_url, *args)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


async def _security_state(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT cls.relname,cls.relrowsecurity,cls.relforcerowsecurity,"
        "string_agg(COALESCE(pg_get_expr(pol.polqual,pol.polrelid),'') || ' ' || "
        "COALESCE(pg_get_expr(pol.polwithcheck,pol.polrelid),''),' ') AS expressions "
        "FROM pg_class cls JOIN pg_namespace ns ON ns.oid=cls.relnamespace "
        "LEFT JOIN pg_policy pol ON pol.polrelid=cls.oid "
        "WHERE ns.nspname='public' AND cls.relname=ANY($1::text[]) "
        "GROUP BY cls.relname,cls.relrowsecurity,cls.relforcerowsecurity ORDER BY cls.relname",
        list(PILOT_TABLES),
    )


async def _constraint_names(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch(
        "SELECT con.conname FROM pg_constraint con "
        "JOIN pg_class cls ON cls.oid=con.conrelid "
        "JOIN pg_namespace ns ON ns.oid=cls.relnamespace "
        "WHERE ns.nspname='public' "
        "AND cls.relname=ANY($1::text[])",
        ["pilot_milestones", "pilot_milestone_corrections"],
    )
    return {row["conname"] for row in rows}


async def test_pilot_rls_remediation_downgrade_upgrade_round_trip(migrated_pg_url: str) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    summary = await seed_baseline(migrated_pg_url)
    tenant_a = summary["baseline_tenant"]["id"]
    tenant_b = summary["control_tenant"]["id"]

    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == PARENT_REVISION
        downgraded = await _security_state(conn)
        assert len(downgraded) == 3
        assert all(row["relrowsecurity"] and not row["relforcerowsecurity"] for row in downgraded)
        assert all("has_parameter_privilege" not in row["expressions"] for row in downgraded)
        downgraded_constraints = await _constraint_names(conn)
        assert OLD_MILESTONE_FK in downgraded_constraints
        assert TENANT_MILESTONE_FK not in downgraded_constraints
        assert PARENT_UNIQUE not in downgraded_constraints
        for table in PILOT_TABLES:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert not await conn.fetchval(
                    "SELECT has_table_privilege('yimatong_app', $1, $2)",
                    f"public.{table}",
                    privilege,
                )

        mismatched_milestone_id = uuid.uuid4()
        mismatched_correction_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO pilot_milestones "
            "(id,tenant_id,milestone_type,achieved_at,source,created_at,updated_at) "
            "VALUES ($1,$2,'migration-preflight',now(),'preflight',now(),now())",
            mismatched_milestone_id,
            tenant_b,
        )
        await conn.execute(
            "INSERT INTO pilot_milestone_corrections "
            "(id,tenant_id,milestone_id,milestone_type,corrected_at,source,reason,created_at) "
            "VALUES ($1,$2,$3,'migration-preflight',now(),'preflight','cross-tenant',now())",
            mismatched_correction_id,
            tenant_a,
            mismatched_milestone_id,
        )
    finally:
        await conn.close()

    rejected_upgrade = _run_alembic(migrated_pg_url, "upgrade", "head")
    assert rejected_upgrade.returncode != 0
    assert "contain cross-tenant parent links" in rejected_upgrade.stderr

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == PARENT_REVISION
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM pilot_milestone_corrections WHERE id=$1",
                mismatched_correction_id,
            )
            == 1
        )
        await conn.execute("DELETE FROM pilot_milestone_corrections WHERE id=$1", mismatched_correction_id)
        await conn.execute("DELETE FROM pilot_milestones WHERE id=$1", mismatched_milestone_id)
    finally:
        await conn.close()

    _alembic(migrated_pg_url, "upgrade", "head")
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == HEAD_REVISION
        upgraded = await _security_state(conn)
        assert len(upgraded) == 3
        assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in upgraded)
        assert all("has_parameter_privilege" in row["expressions"] for row in upgraded)
        upgraded_constraints = await _constraint_names(conn)
        assert OLD_MILESTONE_FK not in upgraded_constraints
        assert TENANT_MILESTONE_FK in upgraded_constraints
        assert PARENT_UNIQUE in upgraded_constraints
        for model in PILOT_MODELS:
            database_columns = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=$1",
                model.__tablename__,
            )
            database_column_names = {row["column_name"] for row in database_columns}
            assert database_column_names == set(model.__table__.columns.keys())
        for table in PILOT_TABLES:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert await conn.fetchval(
                    "SELECT has_table_privilege('yimatong_app', $1, $2)",
                    f"public.{table}",
                    privilege,
                )
    finally:
        await conn.close()
