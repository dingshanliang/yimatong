"""Focused PostgreSQL gates for U09 immutable pilot facts."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from uuid6 import uuid7

from app.services.auth import _prune_expired_auth_sessions
from app.services.platform_auth import prune_expired_platform_sessions
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


def _dsn(url: str, *, runtime: bool = False) -> str:
    value = url.replace("postgresql+asyncpg://", "postgresql://")
    return value.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@") if runtime else value


async def _session(owner: asyncpg.Connection, tenant_id: uuid.UUID, account_id: uuid.UUID) -> uuid.UUID:
    session_id = uuid7()
    auth_version = await owner.fetchval("SELECT auth_version FROM accounts WHERE id=$1", account_id)
    await owner.execute(
        "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,"
        "created_at,updated_at) VALUES($1,$2,$3,$4,$5,now()+interval '1 hour',now(),now())",
        session_id,
        tenant_id,
        account_id,
        auth_version,
        uuid.uuid4().hex,
    )
    return session_id


async def test_retrospective_action_owner_is_tenant_bound_and_atomic(migrated_pg_url: str) -> None:
    tenant_id, other_tenant_id = uuid7(), uuid7()
    owner = await asyncpg.connect(_dsn(migrated_pg_url))
    runtime = await asyncpg.connect(_dsn(migrated_pg_url, runtime=True))
    retro_id, task_id = uuid7(), uuid7()
    try:
        actor_id, foreign_id = uuid7(), uuid7()
        for index, (current_tenant, account_id) in enumerate(
            ((tenant_id, actor_id), (other_tenant_id, foreign_id)), start=1
        ):
            seed_tx = owner.transaction()
            await seed_tx.start()
            org_id, role_id, permission_id = uuid7(), uuid7(), uuid7()
            await owner.execute(
                "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
                "VALUES($1,$2,$3,'active','free','brand',now(),now())",
                current_tenant,
                f"U09 tenant {index}",
                f"u09-{current_tenant.hex[-12:]}",
            )
            await owner.execute(
                "INSERT INTO organizations(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'U09 org',now(),now())",
                org_id,
                current_tenant,
            )
            await owner.execute(
                "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,is_active,auth_version,"
                "must_change_password,failed_login_attempts,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,'hash','U09 admin',true,0,false,0,now(),now())",
                account_id,
                current_tenant,
                org_id,
                f"u09-{account_id}@example.test",
            )
            await owner.execute(
                "INSERT INTO roles(id,tenant_id,name,description,created_at,updated_at) "
                "VALUES($1,$2,'admin','U09 admin',now(),now())",
                role_id,
                current_tenant,
            )
            await owner.execute(
                "INSERT INTO permissions(id,tenant_id,code,description) VALUES($1,$2,'campaign:manage','U09 gate')",
                permission_id,
                current_tenant,
            )
            await owner.execute(
                "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
                current_tenant,
                account_id,
                role_id,
            )
            await owner.execute(
                "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
                current_tenant,
                role_id,
                permission_id,
            )
            await seed_tx.commit()
        session_id = await _session(owner, tenant_id, actor_id)
        launched_at = datetime.now(UTC) - timedelta(days=10)
        await owner.execute(
            "INSERT INTO ops_tasks(id,tenant_id,assigned_to,title,status,priority,due_date,created_at,updated_at) "
            "VALUES($1,$2,$3,'U09 gate','pending','medium',now(),now(),now())",
            task_id,
            tenant_id,
            actor_id,
        )
        await owner.execute(
            "INSERT INTO retrospectives(id,tenant_id,period_day,window_start,window_end,next_review_date,status,"
            "scorecard_snapshot,snapshot_digest,version,authority_version,actions,ops_task_id,created_at,updated_at) "
            "VALUES($1,$2,7,$3,$4,$5,'pending','{}'::jsonb,"
            "encode(digest(convert_to('{}'::jsonb::text,'UTF8'),'sha256'),'hex'),1,1,'[]'::jsonb,$6,now(),now())",
            retro_id,
            tenant_id,
            launched_at,
            launched_at + timedelta(days=7),
            (launched_at + timedelta(days=14)).date(),
            task_id,
        )
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))

        bad_request = uuid7()
        with pytest.raises(asyncpg.PostgresError) as denied:
            async with runtime.transaction():
                await runtime.fetchrow(
                    "SELECT * FROM complete_retrospective_authority($1,$2,$3,$4,1,NULL,NULL,$5::jsonb,NULL,NULL,$6,$7)",
                    tenant_id,
                    session_id,
                    retro_id,
                    bad_request,
                    json.dumps([{"content": "leak", "owner_id": str(foreign_id)}]),
                    "bad-owner",
                    "0" * 64,
                )
        assert denied.value.sqlstate == "23514"
        assert await owner.fetchval("SELECT version FROM retrospectives WHERE id=$1", retro_id) == 1
        assert await owner.fetchval("SELECT count(*) FROM pilot_authority_receipts WHERE id=$1", bad_request) == 0

        good_request = uuid7()
        async with runtime.transaction():
            result = await runtime.fetchrow(
                "SELECT * FROM complete_retrospective_authority($1,$2,$3,$4,1,NULL,NULL,$5::jsonb,NULL,NULL,$6,$7)",
                tenant_id,
                session_id,
                retro_id,
                good_request,
                json.dumps([{"content": "owned", "owner_id": str(actor_id)}]),
                "good-owner",
                "1" * 64,
            )
        assert result["version"] == 2 and not result["replayed"]
        assert await owner.fetchval("SELECT status::text FROM retrospectives WHERE id=$1", retro_id) == "completed"
        assert await owner.fetchval("SELECT count(*) FROM pilot_authority_receipts WHERE id=$1", good_request) == 1
        task_evidence = await owner.fetchval("SELECT result FROM pilot_authority_receipts WHERE id=$1", good_request)
        if isinstance(task_evidence, str):
            task_evidence = json.loads(task_evidence)
        assert task_evidence["ops_task_id"] == str(task_id)
        assert task_evidence["ops_task_before_status"] == "pending"
        assert task_evidence["ops_task_after_status"] == "completed"
        assert task_evidence["ops_task_changed"] is True

        agency_tenant_id, agency_actor_id = uuid7(), uuid7()
        agency_org_id, agency_role_id, agency_permission_id = uuid7(), uuid7(), uuid7()
        agency_seed = owner.transaction()
        await agency_seed.start()
        await owner.execute(
            "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
            "VALUES($1,'U09 agency',$2,'active','free','agency',now(),now())",
            agency_tenant_id,
            f"u09-agency-{agency_tenant_id.hex[-12:]}",
        )
        await owner.execute(
            "INSERT INTO organizations(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'Agency org',now(),now())",
            agency_org_id,
            agency_tenant_id,
        )
        await owner.execute(
            "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,is_active,auth_version,"
            "must_change_password,failed_login_attempts,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,'hash','Agency actor',true,0,false,0,now(),now())",
            agency_actor_id,
            agency_tenant_id,
            agency_org_id,
            f"u09-{agency_actor_id}@example.test",
        )
        await owner.execute(
            "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'admin',now(),now())",
            agency_role_id,
            agency_tenant_id,
        )
        await owner.execute(
            "INSERT INTO permissions(id,tenant_id,code,description) VALUES($1,$2,'campaign:manage','U09 acting')",
            agency_permission_id,
            agency_tenant_id,
        )
        await owner.execute(
            "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
            agency_tenant_id,
            agency_actor_id,
            agency_role_id,
        )
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            agency_tenant_id,
            agency_role_id,
            agency_permission_id,
        )
        await agency_seed.commit()
        agency_session_id = await _session(owner, agency_tenant_id, agency_actor_id)
        authz_id = uuid7()
        await owner.execute(
            "INSERT INTO agency_authorizations(id,agency_tenant_id,client_tenant_id,scope,status,granted_by,"
            "granted_at,created_at,updated_at) VALUES($1,$2,$3,'[\"campaigns\"]'::jsonb,'active',$4,now(),now(),now())",
            authz_id,
            agency_tenant_id,
            tenant_id,
            actor_id,
        )
        acting_retro_id, acting_task_id = uuid7(), uuid7()
        await owner.execute(
            "INSERT INTO ops_tasks(id,tenant_id,assigned_to,title,status,priority,due_date,created_at,updated_at) "
            "VALUES($1,$2,$3,'U09 acting','pending','medium',now(),now(),now())",
            acting_task_id,
            tenant_id,
            agency_actor_id,
        )
        await owner.execute(
            "INSERT INTO retrospectives(id,tenant_id,period_day,window_start,window_end,next_review_date,status,"
            "scorecard_snapshot,snapshot_digest,version,authority_version,actions,ops_task_id,created_at,updated_at) "
            "VALUES($1,$2,14,$3,$4,$5,'pending','{}'::jsonb,$6,1,1,'[]'::jsonb,$7,now(),now())",
            acting_retro_id,
            tenant_id,
            launched_at,
            launched_at + timedelta(days=14),
            (launched_at + timedelta(days=30)).date(),
            "0" * 64,
            acting_task_id,
        )
        async with runtime.transaction():
            acting_result = await runtime.fetchrow(
                "SELECT * FROM update_pending_retrospective_authority($1,$2,$3,$4,1,NULL,NULL,$5::jsonb,NULL,$6,$7)",
                tenant_id,
                agency_session_id,
                acting_retro_id,
                uuid7(),
                json.dumps([{"content": "self", "owner_id": str(agency_actor_id)}]),
                "acting-self",
                "3" * 64,
            )
        assert acting_result["version"] == 2
        with pytest.raises(asyncpg.PostgresError) as other_owner_denied:
            async with runtime.transaction():
                await runtime.fetchrow(
                    "SELECT * FROM update_pending_retrospective_authority($1,$2,$3,$4,2,NULL,NULL,$5::jsonb,NULL,$6,$7)",
                    tenant_id,
                    agency_session_id,
                    acting_retro_id,
                    uuid7(),
                    json.dumps([{"content": "other", "owner_id": str(foreign_id)}]),
                    "acting-other",
                    "4" * 64,
                )
        assert other_owner_denied.value.sqlstate == "23514"
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(other_tenant_id))
        with pytest.raises(asyncpg.PostgresError) as cross_target_denied:
            async with runtime.transaction():
                await runtime.fetchrow(
                    "SELECT * FROM update_pending_retrospective_authority($1,$2,$3,$4,2,NULL,NULL,NULL,NULL,$5,$6)",
                    tenant_id,
                    agency_session_id,
                    acting_retro_id,
                    uuid7(),
                    "cross-target",
                    "5" * 64,
                )
        assert cross_target_denied.value.sqlstate == "42501"
        assert await owner.fetchval("SELECT version FROM retrospectives WHERE id=$1", acting_retro_id) == 2

        analytics_authz_id, analytics_retro_id, analytics_task_id = uuid7(), uuid7(), uuid7()
        await owner.execute(
            "INSERT INTO agency_authorizations(id,agency_tenant_id,client_tenant_id,scope,status,granted_by,"
            "granted_at,created_at,updated_at) VALUES($1,$2,$3,'[\"analytics\"]'::jsonb,'active',$4,now(),now(),now())",
            analytics_authz_id,
            agency_tenant_id,
            other_tenant_id,
            foreign_id,
        )
        await owner.execute(
            "INSERT INTO ops_tasks(id,tenant_id,assigned_to,title,status,priority,due_date,created_at,updated_at) "
            "VALUES($1,$2,$3,'U09 analytics only','pending','medium',now(),now(),now())",
            analytics_task_id,
            other_tenant_id,
            agency_actor_id,
        )
        await owner.execute(
            "INSERT INTO retrospectives(id,tenant_id,period_day,window_start,window_end,next_review_date,status,"
            "scorecard_snapshot,snapshot_digest,version,authority_version,actions,ops_task_id,created_at,updated_at) "
            "VALUES($1,$2,7,$3,$4,$5,'pending','{}'::jsonb,$6,1,1,'[]'::jsonb,$7,now(),now())",
            analytics_retro_id,
            other_tenant_id,
            launched_at,
            launched_at + timedelta(days=7),
            (launched_at + timedelta(days=14)).date(),
            "0" * 64,
            analytics_task_id,
        )
        with pytest.raises(asyncpg.PostgresError) as scope_denied:
            async with runtime.transaction():
                await runtime.fetchrow(
                    "SELECT * FROM update_pending_retrospective_authority($1,$2,$3,$4,1,NULL,NULL,$5::jsonb,NULL,$6,$7)",
                    other_tenant_id,
                    agency_session_id,
                    analytics_retro_id,
                    uuid7(),
                    json.dumps([{"content": "self", "owner_id": str(agency_actor_id)}]),
                    "analytics-only",
                    "2" * 64,
                )
        assert scope_denied.value.sqlstate == "42501"
        assert await owner.fetchval("SELECT version FROM retrospectives WHERE id=$1", analytics_retro_id) == 1

        expired_unreferenced, other_expired = uuid7(), uuid7()
        await owner.execute("UPDATE auth_sessions SET expires_at=now()-interval '1 hour' WHERE id=$1", session_id)
        for expired_id, expired_tenant, expired_actor in (
            (expired_unreferenced, tenant_id, actor_id),
            (other_expired, other_tenant_id, foreign_id),
        ):
            await owner.execute(
                "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,"
                "created_at,updated_at) VALUES($1,$2,$3,0,$4,now()-interval '1 hour',now(),now())",
                expired_id,
                expired_tenant,
                expired_actor,
                uuid.uuid4().hex,
            )
        control_url = migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
        control_engine = create_async_engine(control_url)
        control_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)
        async with control_factory() as cleanup_db:
            await cleanup_db.execute(text("SELECT set_config('app.tenant_id','',false)"))
            await cleanup_db.execute(text("SELECT set_config('app.bypass_rls','true',false)"))
            await _prune_expired_auth_sessions(cleanup_db)
            await cleanup_db.commit()
        assert await owner.fetchval("SELECT count(*) FROM auth_sessions WHERE id=$1", session_id) == 1
        assert await owner.fetchval("SELECT count(*) FROM auth_sessions WHERE id=$1", expired_unreferenced) == 0
        assert await owner.fetchval("SELECT count(*) FROM auth_sessions WHERE id=$1", other_expired) == 0
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
        with pytest.raises(asyncpg.PostgresError) as expired_denied:
            async with runtime.transaction():
                await runtime.fetchrow(
                    "SELECT * FROM update_pending_retrospective_authority($1,$2,$3,$4,2,NULL,NULL,NULL,NULL,$5,$6)",
                    tenant_id,
                    session_id,
                    acting_retro_id,
                    uuid7(),
                    "expired-session",
                    "6" * 64,
                )
        assert expired_denied.value.sqlstate == "42501"
        assert await _session(owner, tenant_id, actor_id) is not None

        platform_referenced, platform_unreferenced = uuid7(), uuid7()
        await owner.executemany(
            "INSERT INTO platform_auth_sessions(id,principal,expires_at,created_at) "
            "VALUES($1,'platform-admin',now()-interval '1 hour',now())",
            [(platform_referenced,), (platform_unreferenced,)],
        )
        platform_receipt = uuid7()
        await owner.execute(
            "INSERT INTO pilot_authority_receipts(id,tenant_id,operation,idempotency_key,payload_digest,resource_id,"
            "actor_type,platform_auth_session_id,result) VALUES($1,$2,'append_milestone_correction',$3,$4,$1,"
            "'platform',$5,'{}'::jsonb)",
            platform_receipt,
            tenant_id,
            f"platform:{platform_receipt}",
            "7" * 64,
            platform_referenced,
        )
        async with control_factory() as cleanup_db:
            await cleanup_db.execute(text("SELECT set_config('app.tenant_id','',false)"))
            await cleanup_db.execute(text("SELECT set_config('app.bypass_rls','true',false)"))
            await prune_expired_platform_sessions(cleanup_db)
            await cleanup_db.commit()
        await control_engine.dispose()
        assert await owner.fetchval("SELECT count(*) FROM platform_auth_sessions WHERE id=$1", platform_referenced) == 1
        assert (
            await owner.fetchval("SELECT count(*) FROM platform_auth_sessions WHERE id=$1", platform_unreferenced) == 0
        )
        await owner.execute(
            "INSERT INTO platform_auth_sessions(id,principal,expires_at,created_at) "
            "VALUES($1,'platform-admin',now()+interval '1 hour',now())",
            uuid7(),
        )
    finally:
        await runtime.close()
        await owner.close()


async def test_u09_downgrade_blocks_immutable_fact_before_head_or_catalog_drift(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_dsn(migrated_pg_url))
    tenant_id, receipt_id = uuid7(), uuid7()
    try:
        await owner.execute(
            "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
            "VALUES($1,'U09 blocker',$2,'active','free','brand',now(),now())",
            tenant_id,
            f"u09-block-{tenant_id.hex[-12:]}",
        )
        await owner.execute(
            "INSERT INTO pilot_authority_receipts(id,tenant_id,operation,idempotency_key,payload_digest,resource_id,"
            "actor_type,result) VALUES($1,$2,'test_fact',$3,$4,$1,'brand','{}'::jsonb)",
            receipt_id,
            tenant_id,
            f"block:{receipt_id}",
            "8" * 64,
        )
        before = await owner.fetchrow(
            "SELECT (SELECT version_num FROM alembic_version) head,"
            "md5(row_to_json(receipt)::text) digest,receipt.xmin::text xmin "
            "FROM pilot_authority_receipts receipt WHERE id=$1",
            receipt_id,
        )
        function_before = await owner.fetchval(
            "SELECT md5(pg_get_functiondef(to_regprocedure($1)))",
            "public.complete_retrospective_authority(uuid,uuid,uuid,uuid,bigint,text,text,jsonb,date,text,text,text)",
        )
    finally:
        await owner.close()

    blocked = _alembic(migrated_pg_url, "downgrade", "u8d4c5d6e7f8", succeeds=False)
    assert "immutable pilot authority facts exist" in f"{blocked.stdout}\n{blocked.stderr}"
    owner = await asyncpg.connect(_dsn(migrated_pg_url))
    try:
        after = await owner.fetchrow(
            "SELECT (SELECT version_num FROM alembic_version) head,"
            "md5(row_to_json(receipt)::text) digest,receipt.xmin::text xmin "
            "FROM pilot_authority_receipts receipt WHERE id=$1",
            receipt_id,
        )
        assert after == before
        assert (
            await owner.fetchval(
                "SELECT md5(pg_get_functiondef(to_regprocedure($1)))",
                "public.complete_retrospective_authority(uuid,uuid,uuid,uuid,bigint,text,text,jsonb,date,text,text,text)",
            )
            == function_before
        )
    finally:
        await owner.close()


async def test_retrospective_materialization_concurrency_is_retry_safe(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_dsn(migrated_pg_url))
    control_dsn = _dsn(migrated_pg_url).replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
    first = await asyncpg.connect(control_dsn)
    second = await asyncpg.connect(control_dsn)
    first_retro, first_task, losing_retro, losing_task = uuid7(), uuid7(), uuid7(), uuid7()
    try:
        tenant_id = uuid7()
        await owner.execute(
            "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
            "VALUES($1,'U09 race',$2,'active','free','brand',now(),now())",
            tenant_id,
            f"u09-race-{tenant_id.hex[-12:]}",
        )
        launched_at = datetime.now(UTC) - timedelta(days=10)
        release_id = uuid7()
        await owner.execute("SET session_replication_role='replica'")
        await owner.execute(
            "INSERT INTO launch_releases(id,tenant_id,page_template_id,page_version_id,campaign_id,code_batch_id,"
            "status,readiness_snapshot,readiness_manifest,content_digest,created_by,created_by_tenant_id,"
            "brand_confirmed_by,brand_confirmed_by_tenant_id,brand_confirmed_at,brand_confirmation_digest,"
            "launched_by,launched_by_tenant_id,launched_at,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,$6,'live','{}'::jsonb,'{}'::jsonb,$7,$8,$2,$8,$2,$9,$7,$8,$2,$9,now(),now())",
            release_id,
            tenant_id,
            uuid7(),
            uuid7(),
            uuid7(),
            uuid7(),
            "9" * 64,
            uuid7(),
            launched_at,
        )
        await owner.execute("SET session_replication_role='origin'")
        await owner.execute(
            "GRANT EXECUTE ON FUNCTION materialize_due_retrospective("
            "uuid,uuid,uuid,integer,timestamptz,timestamptz,date,jsonb,text,uuid) TO acceptance_control"
        )
        snapshot = json.dumps({"version": 1, "confirmed": True})
        digest = await owner.fetchval(
            "SELECT encode(digest(convert_to($1::jsonb::text,'UTF8'),'sha256'),'hex')", snapshot
        )
        call = "SELECT * FROM materialize_due_retrospective($1,$2,$3,7,$4,$5,$6,$7::jsonb,$8,NULL)"
        for connection in (first, second):
            await connection.execute("SELECT set_config('app.tenant_id','',false)")
            await connection.execute("SELECT set_config('app.bypass_rls','true',false)")
        first_tx = first.transaction()
        await first_tx.start()
        first_result = await first.fetchrow(
            call,
            tenant_id,
            first_retro,
            first_task,
            launched_at,
            launched_at + timedelta(days=7),
            (launched_at + timedelta(days=14)).date(),
            snapshot,
            digest,
        )
        assert first_result["retrospective_id"] == first_retro and first_result["created"] is True
        second_tx = second.transaction()
        await second_tx.start()
        with pytest.raises(asyncpg.PostgresError) as busy:
            await second.fetchrow(
                call,
                tenant_id,
                losing_retro,
                losing_task,
                launched_at,
                launched_at + timedelta(days=7),
                (launched_at + timedelta(days=14)).date(),
                snapshot,
                digest,
            )
        assert busy.value.sqlstate == "55P03"
        await second_tx.rollback()
        await first_tx.commit()

        async with second.transaction():
            replay = await second.fetchrow(
                call,
                tenant_id,
                losing_retro,
                losing_task,
                launched_at,
                launched_at + timedelta(days=7),
                (launched_at + timedelta(days=14)).date(),
                snapshot,
                digest,
            )
        assert replay["retrospective_id"] == first_retro and replay["created"] is False
        assert (
            await owner.fetchval("SELECT count(*) FROM retrospectives WHERE tenant_id=$1 AND period_day=7", tenant_id)
            == 1
        )
        assert (
            await owner.fetchval("SELECT count(*) FROM ops_tasks WHERE id=ANY($1::uuid[])", [first_task, losing_task])
            == 1
        )
        conflict_snapshot = json.dumps({"version": 2, "confirmed": False})
        conflict_digest = await owner.fetchval(
            "SELECT encode(digest(convert_to($1::jsonb::text,'UTF8'),'sha256'),'hex')", conflict_snapshot
        )
        with pytest.raises(asyncpg.PostgresError) as conflict:
            async with second.transaction():
                await second.fetchrow(
                    call,
                    tenant_id,
                    uuid7(),
                    uuid7(),
                    launched_at,
                    launched_at + timedelta(days=7),
                    (launched_at + timedelta(days=14)).date(),
                    conflict_snapshot,
                    conflict_digest,
                )
        assert conflict.value.sqlstate == "23505"
        assert await owner.fetchval("SELECT count(*) FROM retrospectives WHERE tenant_id=$1", tenant_id) == 1
    finally:
        if not owner.is_closed():
            await owner.execute("SET session_replication_role='origin'")
        await second.close()
        await first.close()
        await owner.close()
