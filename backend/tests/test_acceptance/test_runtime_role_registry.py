"""Acceptance gates for the reviewed runtime relation registry and repaired RLS gaps."""

from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.audit import write_audit_log
from tests.test_acceptance.conftest import seed_baseline

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

BUSINESS_GAP_TABLES = (
    "account_roles",
    "ai_generations",
    "code_allocations",
    "coupon_codes",
    "diversion_evidence",
    "diversion_investigation_history",
    "gmv_daily_stats",
    "pilot_milestone_corrections",
    "pilot_milestones",
    "regional_code_rules",
    "regional_templates",
    "retrospectives",
    "risk_notifications",
    "role_permissions",
    "sync_mappings",
    "tenant_domains",
    "tenant_quota_usage",
    "whitelabel_configs",
)

CONTROL_TABLES = (
    "auth_sessions",
    "consumed_refresh_tokens",
    "invite_registration_receipts",
    "operator_campaign_manage_grants",
    "organization_parent_repair_backups",
    "platform_auth_sessions",
    "platform_configs",
    "platform_tenant_openings",
    "role_template_backups",
    "tenant_invite_codes",
    "tenant_platform_role_assignment_backups",
)

APPEND_ONLY_RUNTIME_TABLES = ("platform_audit_log",)
READ_ONLY_GLOBAL_TABLES = ("quota_rollout_state",)


async def _assert_insert_denied(conn: asyncpg.Connection, query: str, *args: object) -> None:
    savepoint = conn.transaction()
    await savepoint.start()
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="row-level security"):
        await conn.execute(query, *args)
    await savepoint.rollback()


async def _assert_association_insert_denied(conn: asyncpg.Connection, query: str, *args: object) -> None:
    savepoint = conn.transaction()
    await savepoint.start()
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await conn.execute(query, *args)
    await savepoint.rollback()


async def _assert_statement_privilege_denied(conn: asyncpg.Connection, query: str) -> None:
    savepoint = conn.transaction()
    await savepoint.start()
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.execute(query)
    await savepoint.rollback()


async def test_registry_catalog_acl_and_control_boundary(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
    control_pg_conn: asyncpg.Connection,
) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        states = await owner.fetch(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, count(p.polname) AS policies "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "LEFT JOIN pg_policy p ON p.polrelid=c.oid "
            "WHERE n.nspname='public' AND c.relname=ANY($1::text[]) "
            "GROUP BY c.relname,c.relrowsecurity,c.relforcerowsecurity ORDER BY c.relname",
            list((*BUSINESS_GAP_TABLES, *APPEND_ONLY_RUNTIME_TABLES)),
        )
        assert len(states) == len(BUSINESS_GAP_TABLES) + len(APPEND_ONLY_RUNTIME_TABLES)
        assert all(row["relrowsecurity"] and row["relforcerowsecurity"] and row["policies"] for row in states)

        orm_root_count = await owner.fetchval(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind IN ('r','p') "
            "AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid=c.oid) "
            "AND c.relname NOT IN "
            "('alembic_version','rls_force_remediation_backups','runtime_privilege_remediation_backup')"
        )
        assert orm_root_count == 97
    finally:
        await owner.close()

    for table in BUSINESS_GAP_TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert await runtime_pg_conn.fetchval(
                "SELECT has_table_privilege('yimatong_app', $1, $2)", f"public.{table}", privilege
            )
    for table in CONTROL_TABLES:
        assert not await runtime_pg_conn.fetchval(
            "SELECT has_table_privilege('yimatong_app', $1, 'SELECT')", f"public.{table}"
        )
    for table in APPEND_ONLY_RUNTIME_TABLES:
        for privilege in ("SELECT", "INSERT"):
            assert await runtime_pg_conn.fetchval(
                "SELECT has_table_privilege('yimatong_app', $1, $2)", f"public.{table}", privilege
            )
        for privilege in ("UPDATE", "DELETE"):
            assert not await runtime_pg_conn.fetchval(
                "SELECT has_table_privilege('yimatong_app', $1, $2)", f"public.{table}", privilege
            )
    for table in READ_ONLY_GLOBAL_TABLES:
        assert await runtime_pg_conn.fetchval(
            "SELECT has_table_privilege('yimatong_app', $1, 'SELECT')", f"public.{table}"
        )
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not await runtime_pg_conn.fetchval(
                "SELECT has_table_privilege('yimatong_app', $1, $2)", f"public.{table}", privilege
            )

    await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', '', true)")
    await runtime_pg_conn.execute("SELECT set_config('app.bypass_rls', 'true', true)")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await runtime_pg_conn.fetchval("SELECT count(*) FROM platform_configs")

    key = f"acceptance-{uuid.uuid4()}"
    await control_pg_conn.execute(
        "INSERT INTO platform_configs (id,key,value,updated_at) VALUES ($1,$2,'{}'::json,now())",
        uuid.uuid4(),
        key,
    )
    assert await control_pg_conn.fetchval("SELECT count(*) FROM platform_configs WHERE key=$1", key) == 1


async def test_audit_ledger_runtime_read_append_and_agency_boundary(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
    control_pg_conn: asyncpg.Connection,
) -> None:
    summary = await seed_baseline(migrated_pg_url)
    agency_id = uuid.UUID(summary["baseline_tenant"]["id"])
    client_id = uuid.UUID(summary["control_tenant"]["id"])
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await owner.execute(
            "INSERT INTO agency_authorizations "
            "(id,agency_tenant_id,client_tenant_id,scope,status,granted_at,expires_at,created_at,updated_at) "
            "VALUES ($1,$2,$3,'[]'::json,'active',now(),now()+interval '1 hour',now(),now())",
            uuid.uuid4(),
            agency_id,
            client_id,
        )
        await owner.executemany(
            "INSERT INTO platform_audit_log "
            "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
            "VALUES ($1,'owner',$2,$3,'acceptance',now(),now(),now())",
            [
                (uuid.uuid4(), str(agency_id), "agency-existing"),
                (uuid.uuid4(), str(client_id), "client-existing"),
            ],
        )
    finally:
        await owner.close()

    await runtime_pg_conn.execute("SELECT set_config('app.bypass_rls', 'false', true)")
    await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(agency_id))
    assert await runtime_pg_conn.fetchval("SELECT count(*) FROM platform_audit_log WHERE action='agency-existing'") == 1
    assert await runtime_pg_conn.fetchval("SELECT count(*) FROM platform_audit_log WHERE action='client-existing'") == 0

    await runtime_pg_conn.execute(
        "INSERT INTO platform_audit_log "
        "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
        "VALUES ($1,'agency',$2,'agency-own','acceptance',now(),now(),now())",
        uuid.uuid4(),
        str(agency_id),
    )
    await runtime_pg_conn.execute(
        "INSERT INTO platform_audit_log "
        "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
        "VALUES ($1,'agency',$2,'agency-client','acceptance',now(),now(),now())",
        uuid.uuid4(),
        str(client_id),
    )
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    runtime_engine = create_async_engine(runtime_url)
    runtime_session = async_sessionmaker(runtime_engine, expire_on_commit=False)
    try:
        async with runtime_session.begin() as db:
            await db.execute(text("SELECT set_config('app.bypass_rls', 'false', true)"))
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(agency_id)},
            )
            await write_audit_log(
                db,
                operator_id="agency-orm",
                target_tenant_id=str(client_id),
                action="agency-client-orm",
                resource="acceptance",
            )
    finally:
        await runtime_engine.dispose()
    assert await runtime_pg_conn.fetchval("SELECT count(*) FROM platform_audit_log WHERE action='agency-client'") == 0
    assert (
        await runtime_pg_conn.fetchval("SELECT count(*) FROM platform_audit_log WHERE action='agency-client-orm'") == 0
    )
    await _assert_insert_denied(
        runtime_pg_conn,
        "INSERT INTO platform_audit_log "
        "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
        "VALUES ($1,'agency',$2,'unauthorized','acceptance',now(),now(),now())",
        uuid.uuid4(),
        str(uuid.uuid4()),
    )
    await _assert_statement_privilege_denied(runtime_pg_conn, "UPDATE platform_audit_log SET resource='tampered'")
    await _assert_statement_privilege_denied(runtime_pg_conn, "DELETE FROM platform_audit_log")
    await _assert_statement_privilege_denied(runtime_pg_conn, "TRUNCATE platform_audit_log")

    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await owner.execute(
            "UPDATE agency_authorizations SET expires_at=now()-interval '1 minute' "
            "WHERE agency_tenant_id=$1 AND client_tenant_id=$2",
            agency_id,
            client_id,
        )
    finally:
        await owner.close()
    await _assert_insert_denied(
        runtime_pg_conn,
        "INSERT INTO platform_audit_log "
        "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
        "VALUES ($1,'agency',$2,'expired-auth','acceptance',now(),now(),now())",
        uuid.uuid4(),
        str(client_id),
    )
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await owner.execute(
            "UPDATE agency_authorizations SET status='revoked', expires_at=now()+interval '1 hour' "
            "WHERE agency_tenant_id=$1 AND client_tenant_id=$2",
            agency_id,
            client_id,
        )
    finally:
        await owner.close()
    await _assert_insert_denied(
        runtime_pg_conn,
        "INSERT INTO platform_audit_log "
        "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
        "VALUES ($1,'agency',$2,'revoked-auth','acceptance',now(),now(),now())",
        uuid.uuid4(),
        str(client_id),
    )

    await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(client_id))
    assert (
        await runtime_pg_conn.fetchval(
            "SELECT count(*) FROM platform_audit_log WHERE action IN ('client-existing','agency-client')"
        )
        == 2
    )
    assert (
        await runtime_pg_conn.fetchval("SELECT count(*) FROM platform_audit_log WHERE action='agency-client-orm'") == 1
    )
    control_log_id = uuid.uuid4()
    await control_pg_conn.execute(
        "INSERT INTO platform_audit_log "
        "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
        "VALUES ($1,'platform-admin','platform','control-write','acceptance',now(),now(),now())",
        control_log_id,
    )
    assert await control_pg_conn.fetchval("SELECT count(*) FROM platform_audit_log WHERE id=$1", control_log_id) == 1
    assert (
        await control_pg_conn.fetchval(
            "SELECT count(*) FROM platform_audit_log WHERE action IN ('agency-existing','client-existing')"
        )
        == 2
    )

    await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', '', true)")
    await runtime_pg_conn.execute("SELECT set_config('app.bypass_rls', 'true', true)")
    assert await runtime_pg_conn.fetchval("SELECT count(*) FROM platform_audit_log") == 0
    await _assert_insert_denied(
        runtime_pg_conn,
        "INSERT INTO platform_audit_log "
        "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
        "VALUES ($1,'runtime','platform','forged-bypass','acceptance',now(),now(),now())",
        uuid.uuid4(),
    )


async def test_pilot_relations_runtime_crud_and_isolation(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
) -> None:
    summary = await seed_baseline(migrated_pg_url)
    tenant_a = uuid.UUID(summary["baseline_tenant"]["id"])
    tenant_b = uuid.UUID(summary["control_tenant"]["id"])
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))

    foreign_milestone_id = uuid.uuid4()
    foreign_retrospective_id = uuid.uuid4()
    foreign_correction_id = uuid.uuid4()
    try:
        await owner.execute(
            "INSERT INTO pilot_milestones "
            "(id,tenant_id,milestone_type,achieved_at,source,created_at,updated_at) "
            "VALUES ($1,$2,'acceptance-foreign',now(),'foreign',now(),now())",
            foreign_milestone_id,
            tenant_b,
        )
        await owner.execute(
            "INSERT INTO retrospectives "
            "(id,tenant_id,period_day,window_start,window_end,next_review_date,status,"
            "scorecard_snapshot,actions,created_at,updated_at) "
            "VALUES ($1,$2,7,now(),now(),current_date,'pending','{}'::json,'[]'::json,now(),now())",
            foreign_retrospective_id,
            tenant_b,
        )
        await owner.execute(
            "INSERT INTO pilot_milestone_corrections "
            "(id,tenant_id,milestone_id,milestone_type,corrected_at,source,reason,created_at) "
            "VALUES ($1,$2,$3,'acceptance-foreign',now(),'foreign','foreign',now())",
            foreign_correction_id,
            tenant_b,
            foreign_milestone_id,
        )
    finally:
        await owner.close()

    for bypass_value in ("false", "true"):
        await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', '', true)")
        await runtime_pg_conn.execute("SELECT set_config('app.bypass_rls', $1, true)", bypass_value)
        for table in ("pilot_milestones", "retrospectives", "pilot_milestone_corrections"):
            assert await runtime_pg_conn.fetchval(f"SELECT count(*) FROM {table}") == 0

    await runtime_pg_conn.execute("SELECT set_config('app.bypass_rls', 'false', true)")
    await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_a))

    own_milestone_id = uuid.uuid4()
    own_retrospective_id = uuid.uuid4()
    own_correction_id = uuid.uuid4()
    await runtime_pg_conn.execute(
        "INSERT INTO pilot_milestones "
        "(id,tenant_id,milestone_type,achieved_at,source,created_at,updated_at) "
        "VALUES ($1,$2,'acceptance-own',now(),'own',now(),now())",
        own_milestone_id,
        tenant_a,
    )
    await runtime_pg_conn.execute(
        "INSERT INTO retrospectives "
        "(id,tenant_id,period_day,window_start,window_end,next_review_date,status,"
        "scorecard_snapshot,actions,created_at,updated_at) "
        "VALUES ($1,$2,7,now(),now(),current_date,'pending','{}'::json,'[]'::json,now(),now())",
        own_retrospective_id,
        tenant_a,
    )
    await runtime_pg_conn.execute(
        "INSERT INTO pilot_milestone_corrections "
        "(id,tenant_id,milestone_id,milestone_type,corrected_at,source,reason,created_at) "
        "VALUES ($1,$2,$3,'acceptance-own',now(),'own','own',now())",
        own_correction_id,
        tenant_a,
        own_milestone_id,
    )

    cross_parent_savepoint = runtime_pg_conn.transaction()
    await cross_parent_savepoint.start()
    with pytest.raises(
        asyncpg.ForeignKeyViolationError,
        match="fk_pilot_milestone_corrections_tenant_milestone",
    ):
        await runtime_pg_conn.execute(
            "INSERT INTO pilot_milestone_corrections "
            "(id,tenant_id,milestone_id,milestone_type,corrected_at,source,reason,created_at) "
            "VALUES ($1,$2,$3,'acceptance-cross-parent',now(),'cross','cross-parent',now())",
            uuid.uuid4(),
            tenant_a,
            foreign_milestone_id,
        )
    await cross_parent_savepoint.rollback()

    await _assert_insert_denied(
        runtime_pg_conn,
        "INSERT INTO pilot_milestones "
        "(id,tenant_id,milestone_type,achieved_at,source,created_at,updated_at) "
        "VALUES ($1,$2,'acceptance-cross',now(),'cross',now(),now())",
        uuid.uuid4(),
        tenant_b,
    )
    await _assert_insert_denied(
        runtime_pg_conn,
        "INSERT INTO retrospectives "
        "(id,tenant_id,period_day,window_start,window_end,next_review_date,status,"
        "scorecard_snapshot,actions,created_at,updated_at) "
        "VALUES ($1,$2,14,now(),now(),current_date,'pending','{}'::json,'[]'::json,now(),now())",
        uuid.uuid4(),
        tenant_b,
    )
    await _assert_insert_denied(
        runtime_pg_conn,
        "INSERT INTO pilot_milestone_corrections "
        "(id,tenant_id,milestone_id,milestone_type,corrected_at,source,reason,created_at) "
        "VALUES ($1,$2,$3,'acceptance-cross',now(),'cross','cross',now())",
        uuid.uuid4(),
        tenant_b,
        foreign_milestone_id,
    )

    cases = (
        ("pilot_milestones", own_milestone_id, foreign_milestone_id, "source='updated'"),
        ("retrospectives", own_retrospective_id, foreign_retrospective_id, "issues='updated'"),
        (
            "pilot_milestone_corrections",
            own_correction_id,
            foreign_correction_id,
            "reason='updated'",
        ),
    )
    for table, own_id, foreign_id, update_clause in cases:
        assert await runtime_pg_conn.fetchval(f"SELECT count(*) FROM {table} WHERE id=$1", own_id) == 1
        assert await runtime_pg_conn.fetchval(f"SELECT count(*) FROM {table} WHERE id=$1", foreign_id) == 0
        assert await runtime_pg_conn.execute(f"UPDATE {table} SET {update_clause} WHERE id=$1", own_id) == "UPDATE 1"
        assert (
            await runtime_pg_conn.execute(f"UPDATE {table} SET {update_clause} WHERE id=$1", foreign_id) == "UPDATE 0"
        )
        assert await runtime_pg_conn.execute(f"DELETE FROM {table} WHERE id=$1", foreign_id) == "DELETE 0"

    assert (
        await runtime_pg_conn.execute("DELETE FROM pilot_milestone_corrections WHERE id=$1", own_correction_id)
        == "DELETE 1"
    )
    assert await runtime_pg_conn.execute("DELETE FROM retrospectives WHERE id=$1", own_retrospective_id) == "DELETE 1"
    assert await runtime_pg_conn.execute("DELETE FROM pilot_milestones WHERE id=$1", own_milestone_id) == "DELETE 1"


async def test_repaired_business_relations_runtime_crud_and_isolation(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
) -> None:
    summary = await seed_baseline(migrated_pg_url)
    tenant_a = uuid.UUID(summary["baseline_tenant"]["id"])
    tenant_b = uuid.UUID(summary["control_tenant"]["id"])
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))

    def ids() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        return uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    try:
        org_a, org_b1, org_b2 = ids()
        account_a, account_b1, account_b2 = ids()
        role_a, role_b1, role_b2 = ids()
        permission_a, permission_b1, permission_b2 = ids()
        pool_a, pool_b1, _pool_b2 = ids()
        regional_a, regional_b1, regional_b2 = ids()
        clue_a, clue_b1, _clue_b2 = ids()
        for org_id, tenant_id, label in (
            (org_a, tenant_a, "a"),
            (org_b1, tenant_b, "b1"),
            (org_b2, tenant_b, "b2"),
        ):
            await owner.execute(
                "INSERT INTO organizations (id,tenant_id,name,created_at,updated_at) VALUES ($1,$2,$3,now(),now())",
                org_id,
                tenant_id,
                f"registry-{label}",
            )
        for account_id, tenant_id, org_id, label in (
            (account_a, tenant_a, org_a, "a"),
            (account_b1, tenant_b, org_b1, "b1"),
            (account_b2, tenant_b, org_b2, "b2"),
        ):
            await owner.execute(
                "INSERT INTO accounts (id,tenant_id,organization_id,email,hashed_password,name,"
                "failed_login_attempts,created_at,updated_at) VALUES ($1,$2,$3,$4,'x',$5,0,now(),now())",
                account_id,
                tenant_id,
                org_id,
                f"registry-{label}-{account_id.hex[:8]}@test.local",
                label,
            )
        for role_id, permission_id, tenant_id, label in (
            (role_a, permission_a, tenant_a, "a"),
            (role_b1, permission_b1, tenant_b, "b1"),
            (role_b2, permission_b2, tenant_b, "b2"),
        ):
            await owner.execute(
                "INSERT INTO roles (id,tenant_id,name,created_at,updated_at) VALUES ($1,$2,$3,now(),now())",
                role_id,
                tenant_id,
                f"registry-{label}-{role_id.hex[:6]}",
            )
            await owner.execute(
                "INSERT INTO permissions (id,tenant_id,code,created_at,updated_at) VALUES ($1,$2,$3,now(),now())",
                permission_id,
                tenant_id,
                f"registry:{label}:{permission_id.hex[:6]}",
            )
        for pool_id, tenant_id, label in ((pool_a, tenant_a, "a"), (pool_b1, tenant_b, "b")):
            await owner.execute(
                "INSERT INTO coupon_pools (id,tenant_id,name,total_codes,remaining,created_at,updated_at) "
                "VALUES ($1,$2,$3,10,10,now(),now())",
                pool_id,
                tenant_id,
                label,
            )
        for regional_id, tenant_id, label in (
            (regional_a, tenant_a, "a"),
            (regional_b1, tenant_b, "b1"),
            (regional_b2, tenant_b, "b2"),
        ):
            await owner.execute(
                "INSERT INTO regional_orgs (id,tenant_id,name,org_type,config,created_at,updated_at) "
                "VALUES ($1,$2,$3,'association','{}'::json,now(),now())",
                regional_id,
                tenant_id,
                label,
            )
        for clue_id, tenant_id, label in ((clue_a, tenant_a, "a"), (clue_b1, tenant_b, "b")):
            await owner.execute(
                "INSERT INTO diversion_clues (id,tenant_id,public_id,resolved,pending_review,observation_count,"
                "investigation_status,created_at,updated_at) VALUES ($1,$2,$3,false,false,1,'open',now(),now())",
                clue_id,
                tenant_id,
                f"R{label}{clue_id.hex[:8]}",
            )
        batch_a = await owner.fetchval("SELECT id FROM code_batches WHERE tenant_id=$1 ORDER BY id LIMIT 1", tenant_a)
        batch_b = await owner.fetchval("SELECT id FROM code_batches WHERE tenant_id=$1 ORDER BY id LIMIT 1", tenant_b)
        assert batch_a and batch_b

        cases = [
            (
                "ai_generations",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO ai_generations (id,tenant_id,type,input_snapshot) VALUES ($1,$2,'copywriting','{}'::jsonb)",
                (uuid.uuid4(), tenant_a),
                (uuid.uuid4(), tenant_b),
                "status='accepted'",
            ),
            (
                "code_allocations",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO code_allocations (id,tenant_id,batch_id,quantity,version) VALUES ($1,$2,$3,1,1)",
                (uuid.uuid4(), tenant_a, batch_a),
                (uuid.uuid4(), tenant_b, batch_b),
                "quantity=2",
            ),
            (
                "coupon_codes",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO coupon_codes (id,pool_id,code,distributed) VALUES ($1,$2,$3,false)",
                (uuid.uuid4(), pool_a, f"A-{uuid.uuid4()}"),
                (uuid.uuid4(), pool_b1, f"B-{uuid.uuid4()}"),
                "distributed=true",
            ),
            (
                "diversion_evidence",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO diversion_evidence (id,tenant_id,clue_id,evidence_type,source) VALUES ($1,$2,$3,'other','system')",
                (uuid.uuid4(), tenant_a, clue_a),
                (uuid.uuid4(), tenant_b, clue_b1),
                "description='updated'",
            ),
            (
                "diversion_investigation_history",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO diversion_investigation_history (id,tenant_id,clue_id,to_status) VALUES ($1,$2,$3,'open')",
                (uuid.uuid4(), tenant_a, clue_a),
                (uuid.uuid4(), tenant_b, clue_b1),
                "reason='updated'",
            ),
            (
                "gmv_daily_stats",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO gmv_daily_stats (id,tenant_id,stat_date,attributed_gmv,attributed_orders,scan_count,scan_uv) "
                "VALUES ($1,$2,now(),0,0,0,0)",
                (uuid.uuid4(), tenant_a),
                (uuid.uuid4(), tenant_b),
                "scan_count=2",
            ),
            (
                "regional_code_rules",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO regional_code_rules (id,org_id,rule_name,pattern,prefix) VALUES ($1,$2,$3,'*','')",
                (uuid.uuid4(), regional_a, f"a-{uuid.uuid4()}"),
                (uuid.uuid4(), regional_b1, f"b-{uuid.uuid4()}"),
                "prefix='U'",
            ),
            (
                "regional_templates",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO regional_templates (id,org_id,name,config) VALUES ($1,$2,$3,'{}'::json)",
                (uuid.uuid4(), regional_a, f"a-{uuid.uuid4()}"),
                (uuid.uuid4(), regional_b1, f"b-{uuid.uuid4()}"),
                "name='updated'",
            ),
            (
                "risk_notifications",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO risk_notifications (id,tenant_id,notification_type,title,detail,read) "
                "VALUES ($1,$2,'risk','title','detail',false)",
                (uuid.uuid4(), tenant_a),
                (uuid.uuid4(), tenant_b),
                "read=true",
            ),
            (
                "sync_mappings",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO sync_mappings (id,tenant_id,local_entity_type,local_entity_id,source_system,external_id,sync_direction) "
                "VALUES ($1,$2,'consumer_profile',$3,'test',$4,'push_only')",
                (uuid.uuid4(), tenant_a, uuid.uuid4(), f"a-{uuid.uuid4()}"),
                (uuid.uuid4(), tenant_b, uuid.uuid4(), f"b-{uuid.uuid4()}"),
                "external_phone_hash='updated'",
            ),
            (
                "tenant_domains",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO tenant_domains (id,tenant_id,domain,ssl_status,verified,cname_target) "
                "VALUES ($1,$2,$3,'pending',false,'cname.test')",
                (uuid.uuid4(), tenant_a, f"a-{uuid.uuid4()}.test"),
                (uuid.uuid4(), tenant_b, f"b-{uuid.uuid4()}.test"),
                "verified=true",
            ),
        ]

        # Seed one foreign row per simple relation as the control owner.
        foreign_keys: dict[str, uuid.UUID] = {}
        for table, _key_col, _unused, insert_sql, _own_args, foreign_args, _update in cases:
            await owner.execute(insert_sql, *foreign_args)
            foreign_keys[table] = foreign_args[0]

        await runtime_pg_conn.execute("SELECT set_config('app.bypass_rls', 'false', true)")
        await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_a))
        for table, key_col, _unused, insert_sql, own_args, foreign_args, update_clause in cases:
            foreign_id = foreign_keys[table]
            assert await runtime_pg_conn.fetchval(f"SELECT count(*) FROM {table} WHERE {key_col}=$1", foreign_id) == 0
            cross_args = (uuid.uuid4(), *foreign_args[1:])
            await _assert_insert_denied(runtime_pg_conn, insert_sql, *cross_args)
            await runtime_pg_conn.execute(insert_sql, *own_args)
            own_id = own_args[0]
            assert (
                await runtime_pg_conn.execute(f"UPDATE {table} SET {update_clause} WHERE {key_col}=$1", own_id)
                == "UPDATE 1"
            )
            assert (
                await runtime_pg_conn.execute(f"UPDATE {table} SET {update_clause} WHERE {key_col}=$1", foreign_id)
                == "UPDATE 0"
            )
            assert await runtime_pg_conn.execute(f"DELETE FROM {table} WHERE {key_col}=$1", foreign_id) == "DELETE 0"
            assert await runtime_pg_conn.execute(f"DELETE FROM {table} WHERE {key_col}=$1", own_id) == "DELETE 1"

        association_cases = (
            (
                "account_roles",
                "account_id",
                "role_id",
                (account_a, role_a),
                (account_b1, role_b1),
                (account_b2, role_b2),
            ),
            (
                "role_permissions",
                "role_id",
                "permission_id",
                (role_a, permission_a),
                (role_b1, permission_b1),
                (role_b2, permission_b2),
            ),
        )
        for table, left, right, own_pair, foreign_pair, cross_pair in association_cases:
            await owner.execute(f"INSERT INTO {table} ({left},{right}) VALUES ($1,$2)", *foreign_pair)
            assert (
                await runtime_pg_conn.fetchval(
                    f"SELECT count(*) FROM {table} WHERE {left}=$1 AND {right}=$2", *foreign_pair
                )
                == 0
            )
            await _assert_association_insert_denied(
                runtime_pg_conn, f"INSERT INTO {table} ({left},{right}) VALUES ($1,$2)", *cross_pair
            )
            await runtime_pg_conn.execute(f"INSERT INTO {table} ({left},{right}) VALUES ($1,$2)", *own_pair)
            assert (
                await runtime_pg_conn.execute(
                    f"UPDATE {table} SET {right}={right} WHERE {left}=$1 AND {right}=$2", *own_pair
                )
                == "UPDATE 1"
            )
            assert (
                await runtime_pg_conn.execute(f"DELETE FROM {table} WHERE {left}=$1 AND {right}=$2", *foreign_pair)
                == "DELETE 0"
            )
            assert (
                await runtime_pg_conn.execute(f"DELETE FROM {table} WHERE {left}=$1 AND {right}=$2", *own_pair)
                == "DELETE 1"
            )

        whitelabel_foreign = uuid.uuid4()
        await owner.execute(
            "INSERT INTO whitelabel_configs (id,org_id,brand_name,hide_yimatong,primary_color,font_family) "
            "VALUES ($1,$2,'foreign',false,'#000000','')",
            whitelabel_foreign,
            regional_b1,
        )
        assert (
            await runtime_pg_conn.fetchval("SELECT count(*) FROM whitelabel_configs WHERE id=$1", whitelabel_foreign)
            == 0
        )
        await _assert_insert_denied(
            runtime_pg_conn,
            "INSERT INTO whitelabel_configs (id,org_id,brand_name,hide_yimatong,primary_color,font_family) "
            "VALUES ($1,$2,'cross',false,'#000000','')",
            uuid.uuid4(),
            regional_b2,
        )
        whitelabel_own = uuid.uuid4()
        await runtime_pg_conn.execute(
            "INSERT INTO whitelabel_configs (id,org_id,brand_name,hide_yimatong,primary_color,font_family) "
            "VALUES ($1,$2,'own',false,'#000000','')",
            whitelabel_own,
            regional_a,
        )
        assert (
            await runtime_pg_conn.execute(
                "UPDATE whitelabel_configs SET brand_name='updated' WHERE id=$1", whitelabel_own
            )
            == "UPDATE 1"
        )
        assert (
            await runtime_pg_conn.execute("DELETE FROM whitelabel_configs WHERE id=$1", whitelabel_foreign)
            == "DELETE 0"
        )
        assert await runtime_pg_conn.execute("DELETE FROM whitelabel_configs WHERE id=$1", whitelabel_own) == "DELETE 1"
    finally:
        await owner.close()


async def test_scan_partition_lifecycle_and_replay_keep_child_acl_closed(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    partition_name = "scan_events_2099_01"
    try:
        assert (
            await owner.fetchval("SELECT public.create_secure_scan_events_partition('2099-01-01'::date)")
            == partition_name
        )
        assert not await owner.fetchval(
            "SELECT has_table_privilege('yimatong_app', $1, 'SELECT')", f"public.{partition_name}"
        )
        init_sql = (Path(__file__).resolve().parents[2] / "scripts" / "init_runtime_role.sql").read_text()
        await owner.execute(init_sql)
        assert not await owner.fetchval(
            "SELECT has_table_privilege('yimatong_app', $1, 'SELECT')", f"public.{partition_name}"
        )
        state = await owner.fetchrow(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=to_regclass($1)",
            f"public.{partition_name}",
        )
        assert state["relrowsecurity"] and state["relforcerowsecurity"]
    finally:
        await owner.close()

    tenant_id = uuid.uuid4()
    # Parent access remains the only runtime contract and routes normally.
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await owner.execute(
            "INSERT INTO tenants (id,name,slug,status,plan,tenant_type,created_at,updated_at) "
            "VALUES ($1,'partition tenant',$2,'active','free','brand',now(),now())",
            tenant_id,
            f"partition-{tenant_id.hex[:8]}",
        )
    finally:
        await owner.close()
    await runtime_pg_conn.execute("SELECT set_config('app.bypass_rls', 'false', true)")
    await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
    event_id = uuid.uuid4()
    await runtime_pg_conn.execute(
        "INSERT INTO scan_events (id,tenant_id,public_id,scan_time,is_first_scan,is_valid_visit) "
        "VALUES ($1,$2,'PARTITION-TEST','2099-01-15T00:00:00Z',false,false)",
        event_id,
        tenant_id,
    )
    assert await runtime_pg_conn.fetchval("SELECT count(*) FROM scan_events WHERE id=$1", event_id) == 1
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await runtime_pg_conn.fetchval(f"SELECT count(*) FROM public.{partition_name} WHERE id=$1", event_id)
