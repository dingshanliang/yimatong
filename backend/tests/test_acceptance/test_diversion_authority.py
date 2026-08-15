"""Real PostgreSQL contract for diversion investigation authority."""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from tests.test_acceptance.conftest import ADMIN_DSN, seed_baseline

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

BACKEND_DIR = Path(__file__).resolve().parents[2]
_FUNCTION_SIGNATURE = (
    "record_diversion_observation(uuid,uuid,uuid,timestamp with time zone,text,text,uuid,text,text,text,text,text,"
    "boolean,uuid,uuid,text,text)"
)


def _alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["database_url"] = database_url
    env["migration_database_url"] = database_url
    env["control_database_url"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


async def test_diversion_authority_is_immutable_actor_bound_and_reopenable(migrated_pg_url: str) -> None:
    baseline = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(baseline["baseline_tenant"]["id"])
    batch_id = uuid.UUID(baseline["code_batch"]["id"])
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    try:
        item = await owner.fetchrow(
            "SELECT id,public_id FROM code_items WHERE tenant_id=$1 AND code_batch_id=$2 ORDER BY id LIMIT 1",
            tenant_id,
            batch_id,
        )
        assert item is not None
        item_id = item["id"]
        public_id = item["public_id"]
        account = await owner.fetchrow(
            "SELECT id,auth_version FROM accounts WHERE tenant_id=$1 AND email=$2",
            tenant_id,
            baseline["baseline_tenant"]["admin_email"],
        )
        session_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,"
            "created_at,updated_at) VALUES($1,$2,$3,$4,$5,now()+interval '1 hour',now(),now())",
            session_id,
            tenant_id,
            account["id"],
            account["auth_version"],
            uuid.uuid4().hex,
        )
        scan_id = uuid.uuid4()
        scan_time = await owner.fetchval("SELECT date_trunc('microseconds',now())")
        await owner.execute(
            "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,ip_hash,is_first_scan,is_valid_visit) "
            "VALUES($1,$2,$3,$4,$5,false,true)",
            scan_id,
            tenant_id,
            public_id,
            scan_time,
            "a" * 64,
        )
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
        observation_id = uuid.uuid4()
        args = (
            tenant_id,
            observation_id,
            scan_id,
            scan_time,
            "observation-1",
            public_id,
            item_id,
            "a" * 64,
            "上海",
            "北京",
            "ip_inference",
            "medium",
            None,
            None,
            None,
            "cross_region_ip",
            "medium",
        )
        first = await runtime.fetchrow(
            "SELECT * FROM record_diversion_observation(" + ",".join(f"${i}" for i in range(1, 18)) + ")", *args
        )
        replay = await runtime.fetchrow(
            "SELECT * FROM record_diversion_observation(" + ",".join(f"${i}" for i in range(1, 18)) + ")", *args
        )
        assert first["replayed"] is False and replay["replayed"] is True
        assert first["observation_count"] == 1 and first["clue_version"] == 1
        clue_id = first["clue_id"]

        same_scan_replay = await runtime.fetchrow(
            "SELECT * FROM record_diversion_observation(" + ",".join(f"${i}" for i in range(1, 18)) + ")",
            tenant_id,
            uuid.uuid4(),
            scan_id,
            scan_time,
            "observation-same-scan-new-idem",
            *args[5:],
        )
        assert same_scan_replay["resource_id"] == observation_id
        assert same_scan_replay["replayed"] is True
        with pytest.raises(asyncpg.UniqueViolationError):
            await runtime.fetchrow(
                "SELECT * FROM record_diversion_observation(" + ",".join(f"${i}" for i in range(1, 18)) + ")",
                *args[:8],
                "杭州",
                *args[9:],
            )

        for table in (
            "diversion_clues",
            "diversion_observations",
            "diversion_evidence",
            "diversion_investigation_history",
            "diversion_action_receipts",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with runtime.transaction():
                    await runtime.execute(f"DELETE FROM {table} WHERE tenant_id=$1", tenant_id)

        evidence_args = (
            tenant_id,
            session_id,
            uuid.uuid4(),
            uuid.uuid4(),
            clue_id,
            1,
            "evidence-1",
            "explanation",
            None,
            "渠道说明",
            "b" * 64,
        )
        evidence = await runtime.fetchrow(
            "SELECT * FROM add_diversion_evidence($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)", *evidence_args
        )
        assert evidence["clue_version"] == 2 and evidence["actor_id"] == account["id"]
        evidence_replay = await runtime.fetchrow(
            "SELECT * FROM add_diversion_evidence($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)", *evidence_args
        )
        assert evidence_replay["replayed"] is True and evidence_replay["clue_version"] == 2
        with pytest.raises(asyncpg.UniqueViolationError):
            await runtime.fetchrow(
                "SELECT * FROM add_diversion_evidence($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                *evidence_args[:9],
                "不同说明",
                evidence_args[10],
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await runtime.fetchrow(
                "SELECT * FROM add_diversion_evidence($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                tenant_id,
                session_id,
                uuid.uuid4(),
                uuid.uuid4(),
                clue_id,
                1,
                "evidence-stale",
                "explanation",
                None,
                "陈旧快照",
                "c" * 64,
            )

        transition_args = (
            tenant_id,
            session_id,
            uuid.uuid4(),
            clue_id,
            2,
            "close-1",
            "false_positive",
            "核验为误报",
            "物流单据一致",
        )
        closed = await runtime.fetchrow(
            "SELECT * FROM transition_diversion_clue($1,$2,$3,$4,$5,$6,$7,$8,$9)", *transition_args
        )
        assert closed["resolved"] is True and closed["clue_version"] == 3
        transition_replay = await runtime.fetchrow(
            "SELECT * FROM transition_diversion_clue($1,$2,$3,$4,$5,$6,$7,$8,$9)", *transition_args
        )
        assert transition_replay["replayed"] is True and transition_replay["clue_version"] == 3
        with pytest.raises(asyncpg.UniqueViolationError):
            await runtime.fetchrow(
                "SELECT * FROM transition_diversion_clue($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                *transition_args[:7],
                "不同原因",
                transition_args[8],
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await runtime.fetchrow(
                "SELECT * FROM transition_diversion_clue($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                tenant_id,
                session_id,
                uuid.uuid4(),
                clue_id,
                2,
                "transition-stale",
                "open",
                "陈旧快照",
                None,
            )
        audit_transition = await owner.fetchrow(
            "SELECT details FROM platform_audit_log WHERE target_tenant_id=$1 "
            "AND action='diversion_clue_transitioned' ORDER BY timestamp DESC LIMIT 1",
            str(tenant_id),
        )
        audit_details = json.loads(audit_transition["details"])
        assert audit_details["from_status"] == "open"
        assert audit_details["to_status"] == "false_positive"

        scan_id_2 = uuid.uuid4()
        scan_time_2 = await owner.fetchval("SELECT date_trunc('microseconds',now())")
        await owner.execute(
            "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,ip_hash,is_first_scan,is_valid_visit) "
            "VALUES($1,$2,$3,$4,$5,false,true)",
            scan_id_2,
            tenant_id,
            public_id,
            scan_time_2,
            "c" * 64,
        )
        reopened = await runtime.fetchrow(
            "SELECT * FROM record_diversion_observation(" + ",".join(f"${i}" for i in range(1, 18)) + ")",
            tenant_id,
            uuid.uuid4(),
            scan_id_2,
            scan_time_2,
            "observation-2",
            public_id,
            item_id,
            "c" * 64,
            "深圳",
            "北京",
            "ip_inference",
            "medium",
            None,
            None,
            None,
            "cross_region_ip",
            "medium",
        )
        assert reopened["clue_id"] == clue_id
        assert reopened["investigation_status"] == "pending_evidence"
        assert reopened["resolved"] is False and reopened["observation_count"] == 2
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM diversion_investigation_history WHERE tenant_id=$1 AND clue_id=$2",
                tenant_id,
                clue_id,
            )
            == 2
        )

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.fetchrow(
                "SELECT * FROM record_diversion_observation(" + ",".join(f"${i}" for i in range(1, 18)) + ")",
                uuid.uuid4(),
                uuid.uuid4(),
                scan_id_2,
                scan_time_2,
                "cross-tenant",
                public_id,
                item_id,
                "c" * 64,
                "深圳",
                "北京",
                "ip_inference",
                "medium",
                None,
                None,
                None,
                "cross_region_ip",
                "medium",
            )

        await owner.execute("UPDATE auth_sessions SET revoked_at=now() WHERE id=$1", session_id)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.fetchrow(
                "SELECT * FROM add_diversion_evidence($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                tenant_id,
                session_id,
                uuid.uuid4(),
                uuid.uuid4(),
                clue_id,
                4,
                "revoked-session",
                "explanation",
                None,
                "已撤销会话",
                "d" * 64,
            )
        await owner.execute("UPDATE auth_sessions SET revoked_at=NULL WHERE id=$1", session_id)

        concurrent_scan_time = await owner.fetchval("SELECT date_trunc('microseconds',now())")
        concurrent_scan_ids = (uuid.uuid4(), uuid.uuid4())
        for concurrent_scan_id, ip_hash in zip(concurrent_scan_ids, ("e" * 64, "f" * 64), strict=True):
            await owner.execute(
                "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,ip_hash,is_first_scan,is_valid_visit) "
                "VALUES($1,$2,$3,$4,$5,false,true)",
                concurrent_scan_id,
                tenant_id,
                public_id,
                concurrent_scan_time,
                ip_hash,
            )
        concurrent_runtimes = [await asyncpg.connect(runtime_dsn), await asyncpg.connect(runtime_dsn)]
        try:
            for connection in concurrent_runtimes:
                await connection.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))

            async def record_concurrently(position: int) -> asyncpg.Record:
                return await concurrent_runtimes[position].fetchrow(
                    "SELECT * FROM record_diversion_observation(" + ",".join(f"${i}" for i in range(1, 18)) + ")",
                    tenant_id,
                    uuid.uuid4(),
                    concurrent_scan_ids[position],
                    concurrent_scan_time,
                    f"concurrent-observation-{position}",
                    public_id,
                    item_id,
                    ("e" if position == 0 else "f") * 64,
                    "广州" if position == 0 else "成都",
                    "北京",
                    "ip_inference",
                    "medium",
                    None,
                    None,
                    None,
                    "cross_region_browser",
                    "medium",
                )

            concurrent_observations = await asyncio.gather(record_concurrently(0), record_concurrently(1))
            concurrent_clue_ids = {row["clue_id"] for row in concurrent_observations}
            assert len(concurrent_clue_ids) == 1
            concurrent_clue_id = concurrent_clue_ids.pop()
            assert {row["clue_version"] for row in concurrent_observations} == {1, 2}
            assert (
                await owner.fetchval(
                    "SELECT observation_count FROM diversion_clues WHERE tenant_id=$1 AND id=$2",
                    tenant_id,
                    concurrent_clue_id,
                )
                == 2
            )

            async def conclude_concurrently(position: int) -> asyncpg.Record:
                return await concurrent_runtimes[position].fetchrow(
                    "SELECT * FROM transition_diversion_clue($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                    tenant_id,
                    session_id,
                    uuid.uuid4(),
                    concurrent_clue_id,
                    2,
                    f"concurrent-transition-{position}",
                    "false_positive" if position == 0 else "normal_transfer",
                    f"并发结论 {position}",
                    None,
                )

            concurrent_transitions = await asyncio.gather(
                conclude_concurrently(0),
                conclude_concurrently(1),
                return_exceptions=True,
            )
            assert sum(isinstance(result, asyncpg.Record) for result in concurrent_transitions) == 1
            assert sum(isinstance(result, asyncpg.CheckViolationError) for result in concurrent_transitions) == 1
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM diversion_investigation_history WHERE tenant_id=$1 AND clue_id=$2",
                    tenant_id,
                    concurrent_clue_id,
                )
                == 1
            )
            assert (
                await owner.fetchval(
                    "SELECT version FROM diversion_clues WHERE tenant_id=$1 AND id=$2",
                    tenant_id,
                    concurrent_clue_id,
                )
                == 3
            )
        finally:
            for connection in concurrent_runtimes:
                await connection.close()

        permission_link = await owner.fetchrow(
            "SELECT rp.role_id,rp.permission_id FROM role_permissions rp "
            "JOIN account_roles ar ON ar.tenant_id=rp.tenant_id AND ar.role_id=rp.role_id "
            "JOIN permissions p ON p.tenant_id=rp.tenant_id AND p.id=rp.permission_id "
            "WHERE rp.tenant_id=$1 AND ar.account_id=$2 AND p.code='channel:manage' LIMIT 1",
            tenant_id,
            account["id"],
        )
        assert permission_link is not None
        await owner.execute(
            "DELETE FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3",
            tenant_id,
            permission_link["role_id"],
            permission_link["permission_id"],
        )
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime.fetchrow(
                    "SELECT * FROM transition_diversion_clue($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                    tenant_id,
                    session_id,
                    uuid.uuid4(),
                    clue_id,
                    4,
                    "missing-permission",
                    "false_positive",
                    "无权限",
                    None,
                )
        finally:
            await owner.execute(
                "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
                tenant_id,
                permission_link["role_id"],
                permission_link["permission_id"],
            )

        downgrade = _alembic(migrated_pg_url, "downgrade", "u7b0c1d2e3f4")
        assert downgrade.returncode != 0
        assert "immutable diversion investigation facts exist" in downgrade.stderr
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u7b1d2e3f4a5"
    finally:
        await runtime.close()
        await owner.close()


async def test_diversion_clean_one_step_downgrade_round_trip(migrated_pg_url: str) -> None:
    database_name = f"yimatong_acceptance_u7brt_{uuid.uuid4().hex[:12]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    admin = await asyncpg.connect(ADMIN_DSN)
    try:
        await admin.execute(f'CREATE DATABASE "{database_name}" OWNER yimatong')
    finally:
        await admin.close()
    try:
        upgraded = _alembic(database_url, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr
        schema_check = _alembic(database_url, "-x", "baseline_legacy_timestamp_nullability=true", "check")
        assert schema_check.returncode == 0, schema_check.stderr
        downgraded = _alembic(database_url, "downgrade", "u7b0c1d2e3f4")
        assert downgraded.returncode == 0, downgraded.stderr
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u7b0c1d2e3f4"
        finally:
            await owner.close()
        restored = _alembic(database_url, "upgrade", "head")
        assert restored.returncode == 0, restored.stderr
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u7b1d2e3f4a5"
            assert await owner.fetchval("SELECT to_regprocedure($1)", _FUNCTION_SIGNATURE) is not None
        finally:
            await owner.close()
    finally:
        admin = await asyncpg.connect(ADMIN_DSN)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid<>pg_backend_pid()",
                database_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await admin.close()


async def test_u7b0_downgrade_restores_exact_u7a3_diversion_policies(migrated_pg_url: str) -> None:
    tables = ("diversion_clues", "diversion_evidence", "diversion_investigation_history")
    database_name = f"yimatong_acceptance_u7brls_{uuid.uuid4().hex[:12]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    admin = await asyncpg.connect(ADMIN_DSN)
    try:
        await admin.execute(f'CREATE DATABASE "{database_name}" OWNER yimatong')
    finally:
        await admin.close()

    async def policy_catalog() -> dict[str, tuple]:
        owner = await asyncpg.connect(owner_dsn)
        try:
            rows = await owner.fetch(
                "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,p.polname,p.polpermissive,p.polcmd,"
                "p.polroles,pg_get_expr(p.polqual,p.polrelid) AS qual,"
                "pg_get_expr(p.polwithcheck,p.polrelid) AS with_check "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_policy p ON p.polrelid=c.oid "
                "WHERE n.nspname='public' AND c.relname=ANY($1::text[]) ORDER BY c.relname,p.polname",
                list(tables),
            )
            return {
                row["relname"]: (
                    row["relrowsecurity"],
                    row["relforcerowsecurity"],
                    row["polname"],
                    row["polpermissive"],
                    row["polcmd"],
                    tuple(row["polroles"]),
                    row["qual"],
                    row["with_check"],
                )
                for row in rows
            }
        finally:
            await owner.close()

    try:
        baseline_upgrade = _alembic(database_url, "upgrade", "u7a3f4a5b6c7")
        assert baseline_upgrade.returncode == 0, baseline_upgrade.stderr
        baseline = await policy_catalog()
        assert set(baseline) == set(tables)
        for policy in baseline.values():
            assert policy[0] is True and policy[1] is True
            assert "has_parameter_privilege" in policy[6]
            assert policy[6] == policy[7]

        cutover = _alembic(database_url, "upgrade", "u7b0c1d2e3f4")
        assert cutover.returncode == 0, cutover.stderr
        cutover_catalog = await policy_catalog()
        for policy in cutover_catalog.values():
            assert "has_parameter_privilege" not in policy[6]
            assert "current_setting" not in policy[6]
            assert policy[6] == policy[7]

        downgraded = _alembic(database_url, "downgrade", "u7a3f4a5b6c7")
        assert downgraded.returncode == 0, downgraded.stderr
        assert await policy_catalog() == baseline

        restored = _alembic(database_url, "upgrade", "head")
        assert restored.returncode == 0, restored.stderr
    finally:
        admin = await asyncpg.connect(ADMIN_DSN)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid<>pg_backend_pid()",
                database_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await admin.close()
