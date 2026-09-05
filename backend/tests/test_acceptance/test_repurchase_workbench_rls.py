"""Real PostgreSQL proof for the repurchase operations workbench authority."""

import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from tests.test_acceptance.conftest import seed_baseline

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLES = ("repurchase_work_items", "repurchase_work_item_events")


async def test_workbench_authority_is_tenant_scoped_audited_and_downgrade_safe(migrated_pg_url: str) -> None:
    summary = await seed_baseline(migrated_pg_url)
    tenant_a = uuid.UUID(str(summary["baseline_tenant"]["id"]))
    tenant_b = uuid.UUID(str(summary["control_tenant"]["id"]))
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    try:
        actor = await owner.fetchrow(
            "SELECT account.id AS actor_id,account_role.role_id FROM accounts account "
            "JOIN account_roles account_role ON account_role.tenant_id=account.tenant_id "
            "AND account_role.account_id=account.id WHERE account.tenant_id=$1 LIMIT 1",
            tenant_a,
        )
        assert actor is not None
        actor_id = actor["actor_id"]
        permission_id = await owner.fetchval(
            "SELECT id FROM permissions WHERE tenant_id=$1 AND code='campaign:manage'",
            tenant_a,
        )
        if permission_id is None:
            permission_id = uuid.uuid4()
            await owner.execute(
                "INSERT INTO permissions(id,tenant_id,code,description) VALUES($1,$2,'campaign:manage',$3)",
                permission_id,
                tenant_a,
                "复购运营验收权限",
            )
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
            tenant_a,
            actor["role_id"],
            permission_id,
        )
        auth_session_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at) "
            "SELECT $1,$2,$3,auth_version,$4,statement_timestamp()+interval '1 hour' FROM accounts "
            "WHERE tenant_id=$2 AND id=$3",
            auth_session_id,
            tenant_a,
            actor_id,
            f"workbench-{auth_session_id.hex}",
        )
        for table in TABLES:
            security = await owner.fetchrow(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                "WHERE relnamespace='public'::regnamespace AND relname=$1",
                table,
            )
            assert security and security["relrowsecurity"] and security["relforcerowsecurity"]
            assert await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,'SELECT')", table)
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,$2)", table, privilege)
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',"
            "'mutate_repurchase_work_item_authority(uuid,jsonb)','EXECUTE')"
        )
    finally:
        await owner.close()

    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    runtime = await asyncpg.connect(runtime_dsn)
    item_id = uuid.uuid4()
    try:
        base = {
            "actor_account_id": str(actor_id),
            "auth_session_id": str(auth_session_id),
            "work_item_id": str(item_id),
        }
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
            created = await runtime.fetchval(
                "SELECT mutate_repurchase_work_item_authority($1,$2::jsonb)",
                tenant_a,
                json.dumps(
                    {
                        **base,
                        "action": "create",
                        "event_id": str(uuid.uuid4()),
                        "category": "refund_unsynced",
                        "business_ref": "REFUND-ACCEPTANCE-001",
                        "title": "退款未同步",
                        "impact_summary": "净销售额可能被高估",
                        "priority": "high",
                        "owner_account_id": str(actor_id),
                        "due_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                        "reason": "验收创建",
                        "evidence": {"refund_ref": "REFUND-001"},
                    }
                ),
            )
            assert created == item_id
            await runtime.fetchval(
                "SELECT mutate_repurchase_work_item_authority($1,$2::jsonb)",
                tenant_a,
                json.dumps(
                    {
                        **base,
                        "action": "transition",
                        "event_id": str(uuid.uuid4()),
                        "status": "in_progress",
                        "reason": "已开始核对",
                        "evidence": {"ticket": "OPS-001"},
                    }
                ),
            )
            await runtime.fetchval(
                "SELECT mutate_repurchase_work_item_authority($1,$2::jsonb)",
                tenant_a,
                json.dumps(
                    {
                        **base,
                        "action": "correction",
                        "event_id": str(uuid.uuid4()),
                        "reason": "来源退款事实已确认",
                        "before": {"refund_status": "missing"},
                        "after": {"refund_status": "confirmed"},
                        "evidence": {"refund_ref": "REFUND-001"},
                    }
                ),
            )
            event = await runtime.fetchrow(
                "SELECT before_snapshot,after_snapshot FROM repurchase_work_item_events "
                "WHERE tenant_id=$1 AND work_item_id=$2 AND action='correction_submitted'",
                tenant_a,
                item_id,
            )
            assert event["before_snapshot"] == '{"refund_status": "missing"}'
            assert event["after_snapshot"] == '{"refund_status": "confirmed"}'

        for statement in (
            "INSERT INTO repurchase_work_items DEFAULT VALUES",
            "UPDATE repurchase_work_items SET status='resolved'",
            "DELETE FROM repurchase_work_items",
            "INSERT INTO repurchase_work_item_events DEFAULT VALUES",
            "UPDATE repurchase_work_item_events SET reason='forged'",
            "DELETE FROM repurchase_work_item_events",
        ):
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await runtime.execute(statement)

        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_b))
            assert await runtime.fetchval("SELECT count(*) FROM repurchase_work_items") == 0
            assert await runtime.fetchval("SELECT count(*) FROM repurchase_work_item_events") == 0
    finally:
        await runtime.close()

    env = os.environ.copy()
    env["database_url"] = migrated_pg_url
    env["migration_database_url"] = migrated_pg_url
    downgrade = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "downgrade",
        "c36405c62488",
        cwd=BACKEND_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await downgrade.communicate()
    assert downgrade.returncode != 0
    assert b"repurchase workbench facts exist; archive before downgrade" in stdout + stderr
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "26336e5a1635"
    finally:
        await owner.close()
