"""PostgreSQL gates for tenant-safe takeover routing and durable worker authority."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import uuid

import asyncpg
import pytest

from tests.test_acceptance.conftest import BACKEND_DIR

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

HEAD_REVISION = "t3c3d2e6f7a8"
CONTRACT_REVISION = "t3b2c1d5e6f7"
BELOW_TAKEOVER_REVISION = "r9a0b1c2d3e4"


def _owner_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


def _runtime_dsn(database_url: str) -> str:
    return _owner_dsn(database_url).replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")


def _alembic(database_url: str, *args: str, succeeds: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["database_url"] = database_url
    env["migration_database_url"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert (result.returncode == 0) is succeeds, f"{result.stdout}\n{result.stderr}"
    return result


async def _sqlstate(conn: asyncpg.Connection, sql: str, *args: object) -> str | None:
    try:
        async with conn.transaction():
            await conn.execute(sql, *args)
    except asyncpg.PostgresError as exc:
        return exc.sqlstate
    return None


async def _seed_actor_project(
    conn: asyncpg.Connection,
    *,
    label: str,
    domain: str,
    mode: str = "cname",
) -> dict[str, uuid.UUID]:
    ids = {name: uuid.uuid4() for name in ("tenant", "organization", "account", "role", "session", "project", "route")}
    ids["verification_token"] = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
        "VALUES($1,$2,$3,'active','free','brand',now(),now())",
        ids["tenant"],
        f"takeover authority {label}",
        f"takeover-authority-{label}-{ids['tenant'].hex[:8]}",
    )
    await conn.execute(
        "INSERT INTO organizations(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
        ids["organization"],
        ids["tenant"],
        f"takeover authority {label}",
    )
    await conn.execute(
        "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,failed_login_attempts,"
        "is_active,auth_version,must_change_password,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'x',$5,0,true,0,false,now(),now())",
        ids["account"],
        ids["tenant"],
        ids["organization"],
        f"takeover-{label}-{ids['account'].hex[:8]}@test.local",
        f"takeover {label}",
    )
    await conn.execute(
        "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
        ids["role"],
        ids["tenant"],
        "admin",
    )
    await conn.execute(
        "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
        ids["tenant"],
        ids["account"],
        ids["role"],
    )
    for code in ("takeover:prepare", "takeover:approve", "takeover:execute", "takeover:rollback"):
        permission_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO permissions(id,tenant_id,code,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
            permission_id,
            ids["tenant"],
            code,
        )
        await conn.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            ids["tenant"],
            ids["role"],
            permission_id,
        )
    await conn.execute(
        "INSERT INTO auth_sessions "
        "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
        "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
        ids["session"],
        ids["account"],
        ids["tenant"],
        uuid.uuid4().hex,
    )
    project_domain_column = "consumer_domain" if mode == "cname" else "source_domain"
    await conn.execute(
        f"INSERT INTO takeover_projects "
        f"(id,tenant_id,name,source_system,mode,{project_domain_column},expected_cname,domain_verification_token,"
        "sample_url,url_rule,code_scope,"
        "control_facts,responsible_person,rollback_contact,fallback_url,status,assessment,readiness_snapshot,"
        "readiness_digest,configuration_version,created_by,created_at,updated_at) "
        "VALUES($1,$2,$3,'legacy',$4,$5,'cname.yimatong.cn',$6,$7,'{}'::json,'{}'::json,'{}'::json,"
        "'owner','rollback owner',$8,'ready_for_confirmation','{}'::json,'{\"ready\":true}'::json,$9,1,$10,now(),now())",
        ids["project"],
        ids["tenant"],
        f"takeover {label}",
        mode,
        domain,
        ids["verification_token"],
        f"https://{domain}/OLD-001",
        f"https://fallback.test/{label}",
        "a" * 64,
        ids["account"],
    )
    await conn.execute(
        "INSERT INTO takeover_route_versions "
        "(id,tenant_id,project_id,version,mode,domain,source_url,target_url,extraction_rule,sample_codes,status,"
        "content_digest,readiness_snapshot,created_by,created_at) "
        "VALUES($1,$2,$3,1,$4,$5,$6,$7,'{}'::json,'[\"OLD-001\",\"OLD-002\"]'::json,"
        "'candidate',$8,'{}'::json,$9,now())",
        ids["route"],
        ids["tenant"],
        ids["project"],
        mode,
        domain,
        f"https://{domain}/OLD-001",
        f"https://new-chain.test/{label}",
        "b" * 64,
        ids["account"],
    )
    return ids


async def _set_runtime(conn: asyncpg.Connection, tenant_id: uuid.UUID) -> None:
    await conn.execute("SET LOCAL ROLE yimatong_app")
    await conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))


async def _transition(
    conn: asyncpg.Connection,
    ids: dict[str, uuid.UUID],
    action: str,
    key: str,
    reason: str | None = None,
) -> asyncpg.Record:
    return await conn.fetchrow(
        "SELECT * FROM transition_takeover_route($1,$2,$3,$4,$5,$6,$7,$8)",
        ids["tenant"],
        ids["session"],
        uuid.uuid4(),
        ids["project"],
        ids["route"],
        action,
        key,
        reason,
    )


async def _record_probe(
    conn: asyncpg.Connection,
    ids: dict[str, uuid.UUID],
    *,
    event_id: uuid.UUID,
    purpose: str,
    checked_url: str,
    observed_target: str,
    expected_target: str,
    status_code: int = 200,
) -> asyncpg.Record:
    facts = {
        "evidence_source": "server_probe",
        "evidence_purpose": purpose,
        "transition_event_id": str(event_id),
        "status_code": status_code,
        "checked_url": checked_url,
        "observed_target": observed_target,
        "expected_target": expected_target,
        "target_match": observed_target.rstrip("/") == expected_target.rstrip("/"),
    }
    digest = hashlib.sha256(
        json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    metrics = {**facts, "evidence_digest": digest}
    return await conn.fetchrow(
        "SELECT * FROM record_takeover_server_probe($1,$2,$3,$4,$5,$6,$7,$8,$9,10,$10::jsonb,$11)",
        ids["tenant"],
        ids["session"],
        uuid.uuid4(),
        ids["project"],
        ids["route"],
        event_id,
        checked_url,
        observed_target,
        "passed" if 200 <= status_code < 400 and facts["target_match"] else "failed",
        json.dumps(metrics),
        digest,
    )


async def _record_domain_check(
    conn: asyncpg.Connection,
    ids: dict[str, object],
    *,
    domain: str,
    token: str,
    certificate_offset_seconds: int = 3600,
) -> asyncpg.Record:
    return await conn.fetchrow(
        "SELECT * FROM record_takeover_domain_check($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,"
        "60,'active',now()+make_interval(secs=>$9),'passed',NULL,'{}'::jsonb)",
        ids["tenant"],
        ids["session"],
        uuid.uuid4(),
        ids["project"],
        domain,
        json.dumps(["cname.yimatong.cn"]),
        json.dumps([]),
        json.dumps([f"yimatong-verification={token}"]),
        certificate_offset_seconds,
    )


async def test_catalog_tenant_contract_acl_and_runtime_interfaces(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == HEAD_REVISION
        assert await owner.fetchval(
            "SELECT count(*)=1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname='takeover_domain_claims' AND NOT c.relrowsecurity"
        )
        invalid = await owner.fetchval(
            "SELECT count(*) FROM pg_constraint WHERE conname LIKE 'fk_takeover_%_tenant_%' AND NOT convalidated"
        )
        assert invalid == 0
        for table in (
            "takeover_projects",
            "takeover_import_jobs",
            "takeover_import_errors",
            "takeover_aliases",
            "takeover_domain_checks",
            "takeover_route_versions",
            "takeover_cutover_events",
            "takeover_observations",
        ):
            assert await owner.fetchval(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=to_regclass('public.'||$1)",
                table,
            )
        for table in ("takeover_aliases", "takeover_route_versions", "takeover_import_jobs"):
            assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,'UPDATE')", table)
            assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,'DELETE')", table)
        for table in ("takeover_cutover_events", "takeover_observations"):
            assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,'INSERT')", table)
        assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app','takeover_domain_claims','SELECT')")
        for signature in (
            "enqueue_takeover_import_job(uuid,uuid)",
            "claim_takeover_import_job(uuid,uuid,uuid)",
            "fail_takeover_import_job(uuid,uuid,uuid,text)",
            "complete_takeover_import_job(uuid,uuid,uuid,text,jsonb)",
            "record_takeover_server_probe(uuid,uuid,uuid,uuid,uuid,uuid,text,text,text,double precision,jsonb,text)",
            "record_takeover_domain_check(uuid,uuid,uuid,uuid,text,jsonb,jsonb,jsonb,integer,text,timestamp with time zone,text,text,jsonb)",
            "transition_takeover_route(uuid,uuid,uuid,uuid,uuid,text,text,text)",
            "resolve_takeover_public_route(text)",
        ):
            assert await owner.fetchval("SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", signature)
            assert not await owner.fetchval("SELECT has_function_privilege('public',$1,'EXECUTE')", signature)
        assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app','takeover_domain_checks','INSERT')")
    finally:
        await owner.close()


async def test_concurrent_index_stage_rejects_valid_wrong_definition_and_recovers(migrated_pg_url: str) -> None:
    _alembic(migrated_pg_url, "downgrade", "t3a1c0d4e5f6")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        await owner.execute("CREATE INDEX ix_takeover_import_jobs_pending_poll ON takeover_import_jobs(created_at)")
    finally:
        await owner.close()
    failed = _alembic(migrated_pg_url, "upgrade", "t3a2d0e4f6a7", succeeds=False)
    assert "unexpected definition" in failed.stderr
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "t3a1c0d4e5f6"
        assert "(created_at)" in await owner.fetchval(
            "SELECT pg_get_indexdef(to_regclass('public.ix_takeover_import_jobs_pending_poll'))"
        )
        await owner.execute("DROP INDEX ix_takeover_import_jobs_pending_poll")
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "head")
    _alembic(migrated_pg_url, "-x", "baseline_legacy_timestamp_nullability=true", "check")


async def test_domain_verification_token_expands_nullable_then_backfills_in_batches(migrated_pg_url: str) -> None:
    _alembic(migrated_pg_url, "downgrade", "d297eb0518da")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()
    try:
        await owner.execute("SET session_replication_role=replica")
        await owner.execute(
            "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
            "VALUES($1,'expand','expand-'||$2,'active','free','brand',now(),now())",
            tenant_id,
            tenant_id.hex[:8],
        )
        await owner.execute(
            "INSERT INTO takeover_projects "
            "(id,tenant_id,name,source_system,mode,expected_cname,sample_url,url_rule,code_scope,control_facts,"
            "responsible_person,rollback_contact,fallback_url,status,assessment,readiness_snapshot,configuration_version,"
            "created_by,created_at,updated_at) VALUES($1,$2,'expand','legacy','cname','cname.yimatong.cn',"
            "'https://old.test/OLD-1','{}'::json,'{}'::json,'{}'::json,'owner','owner',"
            "'https://fallback.test','draft','{}'::json,'{}'::json,1,$3,now(),now())",
            project_id,
            tenant_id,
            uuid.uuid4(),
        )
        await owner.execute("SET session_replication_role=origin")
    finally:
        await owner.close()

    _alembic(migrated_pg_url, "upgrade", "t3a1c0d4e5f6")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval(
            "SELECT domain_verification_token IS NULL FROM takeover_projects WHERE id=$1", project_id
        )
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "t3a2d0e4f6a7")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval(
            "SELECT domain_verification_token IS NOT NULL FROM takeover_projects WHERE id=$1", project_id
        )
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "head")
    cleanup = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        await cleanup.execute("SET session_replication_role=replica")
        await cleanup.execute("DELETE FROM takeover_projects WHERE id=$1", project_id)
        await cleanup.execute("DELETE FROM tenants WHERE id=$1", tenant_id)
        await cleanup.execute("SET session_replication_role=origin")
    finally:
        await cleanup.close()


async def test_cname_claim_freshness_two_tenant_contention_and_rollback_fallback(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    transaction = owner.transaction()
    await transaction.start()
    domain = f"takeover-{uuid.uuid4().hex[:10]}.test"
    first = await _seed_actor_project(owner, label="first", domain=domain)
    second = await _seed_actor_project(owner, label="second", domain=domain)
    try:
        await _set_runtime(owner, first["tenant"])
        await _transition(owner, first, "confirm", "confirm")
        assert (
            await _sqlstate(
                owner,
                "INSERT INTO takeover_domain_checks "
                "(id,tenant_id,project_id,domain,expected_cname,observed_cnames,observed_ips,"
                "observed_ownership_tokens,ownership_verified,tls_status,status,raw_observation) "
                "VALUES($1,$2,$3,$4,'cname.yimatong.cn','[]'::json,'[]'::json,'[]'::json,true,"
                "'active','passed','{}'::json)",
                uuid.uuid4(),
                first["tenant"],
                first["project"],
                domain,
            )
            == "42501"
        )
        expired_check = await _record_domain_check(
            owner,
            first,
            domain=domain,
            token=str(first["verification_token"]),
            certificate_offset_seconds=-60,
        )
        assert expired_check["status"] == "failed"
        assert (
            await _sqlstate(
                owner,
                "SELECT * FROM transition_takeover_route($1,$2,$3,$4,$5,'cutover','expired-cert',NULL)",
                first["tenant"],
                first["session"],
                uuid.uuid4(),
                first["project"],
                first["route"],
            )
            == "23514"
        )
        valid_check = await _record_domain_check(
            owner,
            first,
            domain=domain,
            token=str(first["verification_token"]),
        )
        assert valid_check["status"] == "passed"
        assert valid_check["ownership_verified"] is True
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM platform_audit_log WHERE target_tenant_id=$1::text "
                "AND action='takeover_domain_checked' AND operator_id=$2::text",
                str(first["tenant"]),
                str(first["account"]),
            )
            == 2
        )
        cutover = await _transition(owner, first, "cutover", "cutover")
        await _record_probe(
            owner,
            first,
            event_id=cutover["current_event_id"],
            purpose="cutover",
            checked_url=f"https://{domain}/OLD-001",
            observed_target="https://new-chain.test/first",
            expected_target="https://new-chain.test/first",
        )
        await _transition(owner, first, "complete", "complete")
        rollback = await _transition(owner, first, "begin_rollback", "rollback", "operator rollback")
        await _record_probe(
            owner,
            first,
            event_id=rollback["current_event_id"],
            purpose="rollback",
            checked_url=f"https://{domain}/OLD-001",
            observed_target="https://fallback.test/first",
            expected_target="https://fallback.test/first",
        )
        await _transition(owner, first, "finish_rollback", "rollback-finish")
        authority = await owner.fetchrow("SELECT * FROM resolve_takeover_public_route($1)", domain)
        assert authority and authority["tenant_id"] == first["tenant"]
        await owner.execute("RESET ROLE")
        await owner.execute(
            "UPDATE takeover_projects SET status='completed' WHERE id=$1",
            first["project"],
        )
        assert await owner.fetchrow("SELECT * FROM resolve_takeover_public_route($1)", domain) is None
        await owner.execute(
            "UPDATE takeover_projects SET status='rolled_back' WHERE id=$1",
            first["project"],
        )
        await _set_runtime(owner, first["tenant"])
        assert (
            await _sqlstate(
                owner,
                "UPDATE takeover_aliases SET status='disabled' WHERE tenant_id=$1",
                first["tenant"],
            )
            == "42501"
        )

        await owner.execute("RESET ROLE")
        await _set_runtime(owner, second["tenant"])
        await _transition(owner, second, "confirm", "confirm")
        forged_check = await _record_domain_check(
            owner,
            second,
            domain=domain,
            token="forged",
        )
        assert forged_check["status"] == "failed"
        assert forged_check["ownership_verified"] is False
        assert (
            await _sqlstate(
                owner,
                "SELECT * FROM transition_takeover_route($1,$2,$3,$4,$5,'cutover','forged-owner',NULL)",
                second["tenant"],
                second["session"],
                uuid.uuid4(),
                second["project"],
                second["route"],
            )
            == "23514"
        )
        second_valid_check = await _record_domain_check(
            owner,
            second,
            domain=domain,
            token=str(second["verification_token"]),
        )
        assert second_valid_check["status"] == "passed"
        assert (
            await _sqlstate(
                owner,
                "SELECT * FROM transition_takeover_route($1,$2,$3,$4,$5,'cutover','claim-race',NULL)",
                second["tenant"],
                second["session"],
                uuid.uuid4(),
                second["project"],
                second["route"],
            )
            == "23505"
        )
    finally:
        await transaction.rollback()
        await owner.close()


async def test_legacy_requires_confirmed_external_epoch_and_fresh_server_probe(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    transaction = owner.transaction()
    await transaction.start()
    domain = f"legacy-{uuid.uuid4().hex[:10]}.test"
    ids = await _seed_actor_project(owner, label="legacy", domain=domain, mode="legacy_redirect")
    try:
        await _set_runtime(owner, ids["tenant"])
        assert (
            await _sqlstate(
                owner,
                "SELECT * FROM transition_takeover_route($1,$2,$3,$4,$5,'record_external_execution','early','ref')",
                ids["tenant"],
                ids["session"],
                uuid.uuid4(),
                ids["project"],
                ids["route"],
            )
            == "23514"
        )
        await _transition(owner, ids, "confirm", "confirm")
        external = await _transition(owner, ids, "record_external_execution", "external", "ticket-123")
        assert (
            await _sqlstate(
                owner,
                "SELECT * FROM transition_takeover_route($1,$2,$3,$4,$5,'record_external_execution','external','ticket-999')",
                ids["tenant"],
                ids["session"],
                uuid.uuid4(),
                ids["project"],
                ids["route"],
            )
            == "22023"
        )
        assert (
            await _sqlstate(
                owner,
                "INSERT INTO takeover_cutover_events(id,tenant_id,project_id,route_version_id,action,state,actor_id,details) "
                "VALUES($1,$2,$3,$4,'cutover','forged',$5,'{}'::json)",
                uuid.uuid4(),
                ids["tenant"],
                ids["project"],
                ids["route"],
                ids["account"],
            )
            == "42501"
        )
        await owner.execute("RESET ROLE")
        await owner.execute(
            "INSERT INTO takeover_observations "
            "(id,tenant_id,project_id,route_version_id,transition_event_id,checked_url,observed_target_url,"
            "evidence_source,evidence_purpose,evidence_digest,status,success_rate,error_rate,h5_reach_rate,"
            "target_match,metrics,recommendation,created_at) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,'server_probe','pre_cutover',$8,'passed',1,0,1,true,'{}'::json,"
            "'continue',now()-interval '16 minutes')",
            uuid.uuid4(),
            ids["tenant"],
            ids["project"],
            ids["route"],
            external["current_event_id"],
            f"https://{domain}/OLD-001",
            "https://new-chain.test/legacy",
            "e" * 64,
        )
        await _set_runtime(owner, ids["tenant"])
        assert (
            await _sqlstate(
                owner,
                "SELECT * FROM transition_takeover_route($1,$2,$3,$4,$5,'cutover','stale-probe',NULL)",
                ids["tenant"],
                ids["session"],
                uuid.uuid4(),
                ids["project"],
                ids["route"],
            )
            == "23514"
        )
        canonical_facts = {
            "evidence_source": "server_probe",
            "evidence_purpose": "pre_cutover",
            "transition_event_id": str(external["current_event_id"]),
            "status_code": 200,
            "checked_url": f"https://{domain}/OLD-001",
            "observed_target": "https://new-chain.test/legacy",
            "expected_target": "https://new-chain.test/legacy",
            "target_match": True,
        }
        canonical_digest = hashlib.sha256(
            json.dumps(canonical_facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        stale_metrics = json.dumps({**canonical_facts, "evidence_digest": canonical_digest})
        assert (
            await _sqlstate(
                owner,
                "SELECT * FROM record_takeover_server_probe($1,$2,$3,$4,$5,$6,$7,$8,'failed',10,$9::jsonb,$10)",
                ids["tenant"],
                ids["session"],
                uuid.uuid4(),
                ids["project"],
                ids["route"],
                external["current_event_id"],
                f"https://{domain}/OLD-001",
                "https://new-chain.test/tampered",
                stale_metrics,
                canonical_digest,
            )
            == "22023"
        )
        await _record_probe(
            owner,
            ids,
            event_id=external["current_event_id"],
            purpose="pre_cutover",
            checked_url=f"https://{domain}/OLD-001",
            observed_target="https://new-chain.test/legacy",
            expected_target="https://new-chain.test/legacy",
        )
        assert (await _transition(owner, ids, "cutover", "fresh-probe"))["route_status"] == "active"
    finally:
        await transaction.rollback()
        await owner.close()


async def test_same_key_concurrency_replays_and_queue_reclaims_to_terminal_attempt(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    domain = f"concurrent-{uuid.uuid4().hex[:10]}.test"
    async with owner.transaction():
        ids = await _seed_actor_project(owner, label="concurrent", domain=domain)
    job_id = uuid.uuid4()
    renewal_job_id = uuid.uuid4()
    backoff_job_id = uuid.uuid4()
    await owner.execute(
        "INSERT INTO takeover_import_jobs "
        "(id,tenant_id,project_id,file_name,file_sha256,status,source_rows,counts,attempt_count,created_by,created_at) "
        "VALUES($1,$2,$3,'codes.csv',$4,'dry_run','[]'::json,'{\"failed\":0}'::json,0,$5,now())",
        job_id,
        ids["tenant"],
        ids["project"],
        "1" * 64,
        ids["account"],
    )
    await owner.executemany(
        "INSERT INTO takeover_import_jobs "
        "(id,tenant_id,project_id,file_name,file_sha256,status,source_rows,counts,attempt_count,last_error_code,"
        "created_by,created_at) VALUES($1,$2,$3,'codes.csv',$4,'failed','[]'::json,'{\"failed\":0}'::json,1,$5,$6,now())",
        [
            (renewal_job_id, ids["tenant"], ids["project"], "2" * 64, "tenant_plan_expired", ids["account"]),
            (backoff_job_id, ids["tenant"], ids["project"], "3" * 64, "transient_network", ids["account"]),
        ],
    )
    first = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    second = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    try:
        for conn in (first, second):
            await conn.execute("SELECT set_config('app.tenant_id',$1,false)", str(ids["tenant"]))
        start = asyncio.Event()

        async def confirm(conn: asyncpg.Connection) -> asyncpg.Record:
            await start.wait()
            return await conn.fetchrow(
                "SELECT * FROM transition_takeover_route($1,$2,$3,$4,$5,'confirm','same-key',NULL)",
                ids["tenant"],
                ids["session"],
                uuid.uuid4(),
                ids["project"],
                ids["route"],
            )

        tasks = [asyncio.create_task(confirm(first)), asyncio.create_task(confirm(second))]
        start.set()
        results = await asyncio.gather(*tasks)
        assert sorted(result["replayed"] for result in results) == [False, True]
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM takeover_cutover_events WHERE project_id=$1 AND idempotency_key='same-key'",
                ids["project"],
            )
            == 1
        )

        assert await first.fetchval("SELECT enqueue_takeover_import_job($1,$2)", ids["tenant"], job_id) == "pending"
        token = uuid.uuid4()
        assert (
            await first.fetchrow("SELECT * FROM claim_takeover_import_job($1,$2,$3)", ids["tenant"], job_id, token)
        )["attempt_count"] == 1
        await owner.execute("UPDATE takeover_import_jobs SET claimed_at=now()-interval '6 minutes' WHERE id=$1", job_id)
        token = uuid.uuid4()
        assert (
            await first.fetchrow("SELECT * FROM claim_takeover_import_job($1,$2,$3)", ids["tenant"], job_id, token)
        )["attempt_count"] == 2
        while True:
            outcome = await first.fetchval(
                "SELECT fail_takeover_import_job($1,$2,$3,'transient_network')",
                ids["tenant"],
                job_id,
                token,
            )
            attempt = await owner.fetchval("SELECT attempt_count FROM takeover_import_jobs WHERE id=$1", job_id)
            if outcome == "dead_letter":
                assert attempt == 20
                break
            await owner.execute(
                "UPDATE takeover_import_jobs SET next_attempt_at=now()-interval '1 second' WHERE id=$1", job_id
            )
            token = uuid.uuid4()
            claimed = await first.fetchrow(
                "SELECT * FROM claim_takeover_import_job($1,$2,$3)", ids["tenant"], job_id, token
            )
            assert claimed["attempt_count"] == attempt + 1
        assert await owner.fetchval("SELECT status FROM takeover_import_jobs WHERE id=$1", job_id) == "dead_letter"
        assert (
            await _sqlstate(first, "UPDATE takeover_import_jobs SET status='completed' WHERE id=$1", job_id) == "42501"
        )
        assert (
            await first.fetchval("SELECT enqueue_takeover_import_job($1,$2)", ids["tenant"], renewal_job_id)
            == "pending"
        )
        assert (
            await _sqlstate(
                first,
                "SELECT enqueue_takeover_import_job($1,$2)",
                ids["tenant"],
                backoff_job_id,
            )
            == "22023"
        )
    finally:
        await first.close()
        await second.close()
        await owner.execute("SET session_replication_role=replica")
        for table in (
            "takeover_domain_claims",
            "takeover_observations",
            "takeover_cutover_events",
            "takeover_aliases",
            "takeover_domain_checks",
            "takeover_import_errors",
            "takeover_import_jobs",
            "takeover_route_versions",
            "takeover_projects",
        ):
            await owner.execute(f"DELETE FROM {table} WHERE tenant_id=$1", ids["tenant"])
        await owner.execute("DELETE FROM auth_sessions WHERE tenant_id=$1", ids["tenant"])
        await owner.execute("DELETE FROM role_permissions WHERE tenant_id=$1", ids["tenant"])
        await owner.execute("DELETE FROM account_roles WHERE tenant_id=$1", ids["tenant"])
        await owner.execute("DELETE FROM permissions WHERE tenant_id=$1", ids["tenant"])
        await owner.execute("DELETE FROM roles WHERE tenant_id=$1", ids["tenant"])
        await owner.execute("DELETE FROM accounts WHERE tenant_id=$1", ids["tenant"])
        await owner.execute("DELETE FROM organizations WHERE tenant_id=$1", ids["tenant"])
        await owner.execute("DELETE FROM tenants WHERE id=$1", ids["tenant"])
        await owner.execute("SET session_replication_role=origin")
        await owner.close()


async def test_two_command_downgrade_preflight_is_zero_drift(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    role_id = uuid.uuid4()
    project_id = uuid.uuid4()
    job_id = uuid.uuid4()
    seed_transaction = owner.transaction()
    await seed_transaction.start()
    await owner.execute(
        "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
        "VALUES($1,'downgrade','downgrade-'||$2,'active','free','brand',now(),now())",
        tenant_id,
        tenant_id.hex[:8],
    )
    await owner.execute(
        "INSERT INTO organizations(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'downgrade',now(),now())",
        organization_id,
        tenant_id,
    )
    await owner.execute(
        "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,failed_login_attempts,is_active,"
        "auth_version,must_change_password,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'x','downgrade',0,true,0,false,now(),now())",
        account_id,
        tenant_id,
        organization_id,
        f"downgrade-{account_id.hex[:8]}@test.local",
    )
    await owner.execute(
        "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'admin',now(),now())",
        role_id,
        tenant_id,
    )
    await owner.execute(
        "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
        tenant_id,
        account_id,
        role_id,
    )
    await owner.execute(
        "INSERT INTO takeover_projects "
        "(id,tenant_id,name,source_system,mode,expected_cname,domain_verification_token,sample_url,url_rule,code_scope,control_facts,"
        "responsible_person,rollback_contact,fallback_url,status,assessment,readiness_snapshot,configuration_version,"
        "created_by,created_at,updated_at) VALUES($1,$2,'downgrade','legacy','cname','cname.test',$3,'https://old.test/x',"
        "'{}'::json,'{}'::json,'{}'::json,'owner','owner','https://fallback.test','draft','{}'::json,'{}'::json,1,$4,now(),now())",
        project_id,
        tenant_id,
        str(uuid.uuid4()),
        account_id,
    )
    await owner.execute(
        "INSERT INTO takeover_import_jobs "
        "(id,tenant_id,project_id,file_name,file_sha256,status,source_rows,counts,attempt_count,next_attempt_at,"
        "created_by,created_at) VALUES($1,$2,$3,'scheduled.csv',$4,'pending','[]'::json,'{}'::json,0,"
        "now()+interval '5 minutes',$5,now())",
        job_id,
        tenant_id,
        project_id,
        "f" * 64,
        account_id,
    )
    await seed_transaction.commit()
    await owner.close()
    try:
        scheduled_failed = _alembic(migrated_pg_url, "downgrade", CONTRACT_REVISION, succeeds=False)
        assert "durable worker state" in scheduled_failed.stderr
        assert "domain ownership verification" in scheduled_failed.stderr
        scheduled = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        assert await scheduled.fetchval("SELECT version_num FROM alembic_version") == HEAD_REVISION
        await scheduled.execute("DELETE FROM takeover_import_jobs WHERE id=$1", job_id)
        await scheduled.execute("DELETE FROM takeover_projects WHERE id=$1", project_id)
        await scheduled.close()
        _alembic(migrated_pg_url, "downgrade", CONTRACT_REVISION)
        contract = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        await contract.execute(
            "INSERT INTO takeover_projects "
            "(id,tenant_id,name,source_system,mode,expected_cname,domain_verification_token,sample_url,url_rule,"
            "code_scope,control_facts,responsible_person,rollback_contact,fallback_url,status,assessment,"
            "readiness_snapshot,configuration_version,created_by,created_at,updated_at) VALUES($1,$2,'downgrade',"
            "'legacy','cname','cname.test',$3,'https://old.test/x','{}'::json,'{}'::json,'{}'::json,'owner',"
            "'owner','https://fallback.test','draft','{}'::json,'{}'::json,1,$4,now(),now())",
            project_id,
            tenant_id,
            str(uuid.uuid4()),
            account_id,
        )
        await contract.close()
        before = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        before_version = await before.fetchval("SELECT version_num FROM alembic_version")
        before_columns = await before.fetchval(
            "SELECT string_agg(column_name,',' ORDER BY ordinal_position) FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='takeover_import_jobs'"
        )
        await before.close()
        failed = _alembic(migrated_pg_url, "downgrade", BELOW_TAKEOVER_REVISION, succeeds=False)
        assert "durable takeover data exists" in failed.stderr
        after = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        assert await after.fetchval("SELECT version_num FROM alembic_version") == before_version
        assert (
            await after.fetchval(
                "SELECT string_agg(column_name,',' ORDER BY ordinal_position) FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='takeover_import_jobs'"
            )
            == before_columns
        )
        await after.close()
    finally:
        cleanup = await asyncpg.connect(_owner_dsn(migrated_pg_url))
        await cleanup.execute("SET session_replication_role=replica")
        await cleanup.execute("DELETE FROM takeover_projects WHERE id=$1", project_id)
        await cleanup.execute("DELETE FROM account_roles WHERE tenant_id=$1", tenant_id)
        await cleanup.execute("DELETE FROM roles WHERE id=$1", role_id)
        await cleanup.execute("DELETE FROM accounts WHERE id=$1", account_id)
        await cleanup.execute("DELETE FROM organizations WHERE id=$1", organization_id)
        await cleanup.execute("DELETE FROM tenants WHERE id=$1", tenant_id)
        await cleanup.execute("SET session_replication_role=origin")
        await cleanup.close()
        _alembic(migrated_pg_url, "upgrade", "head")
        _alembic(migrated_pg_url, "-x", "baseline_legacy_timestamp_nullability=true", "check")
