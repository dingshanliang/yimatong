"""Real PostgreSQL contract for actor-bound page mutation authority."""

from __future__ import annotations

import asyncio
import time
import uuid

import asyncpg
import pytest

from tests.test_acceptance.test_cli_identity_reconciliation import (
    BASELINE_ADMIN_EMAIL,
    BASELINE_TENANT_SLUG,
    _run_cli,
)
from tests.test_acceptance.test_code_batch_delivery_contract import _purge_owned_delivery_fixture, _seed_catalog
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic, _assert_sqlstate, _runtime_call

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "u4c3a4b5c6d7"


async def _grant_page_permissions(conn: asyncpg.Connection, ids: dict[str, uuid.UUID]) -> uuid.UUID:
    for code in ("page:create", "page:publish"):
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
            ids["admin_role"],
            permission_id,
        )
    session_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO auth_sessions "
        "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
        "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
        session_id,
        ids["account"],
        ids["tenant"],
        uuid.uuid4().hex,
    )
    return session_id


async def _legacy_runtime_execute(
    conn: asyncpg.Connection,
    tenant_id: uuid.UUID,
    auth_session_id: uuid.UUID | str | None,
    sql: str,
    *args: object,
) -> str:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))
        if auth_session_id is not None:
            await conn.execute("SELECT set_config('app.auth_session_id',$1,true)", str(auth_session_id))
        return await conn.execute(sql, *args)


async def _assert_official_seed_page_authority(
    owner: asyncpg.Connection,
    tenant_slug: str,
    admin_email: str,
) -> None:
    tenant_id = await owner.fetchval("SELECT id FROM tenants WHERE slug=$1", tenant_slug)
    admin_id = await owner.fetchval("SELECT id FROM accounts WHERE tenant_id=$1 AND email=$2", tenant_id, admin_email)
    assert tenant_id is not None and admin_id is not None
    assert await owner.fetchval("SELECT count(*) FROM page_templates WHERE tenant_id=$1", tenant_id) >= 1
    assert await owner.fetchval("SELECT count(*) FROM page_versions WHERE tenant_id=$1", tenant_id) >= 1
    assert not await owner.fetchval(
        "SELECT EXISTS(SELECT 1 FROM page_versions WHERE tenant_id=$1 "
        "AND (created_by_tenant_id<>$1 OR created_by<>$2))",
        tenant_id,
        admin_id,
    )
    assert not await owner.fetchval(
        "SELECT EXISTS(SELECT 1 FROM page_versions WHERE tenant_id=$1 "
        "GROUP BY page_template_id HAVING count(*) FILTER (WHERE status='published')<>1)",
        tenant_id,
    )
    assert await owner.fetchval(
        "SELECT count(*)>=3 FROM platform_audit_log WHERE target_tenant_id=$1::text "
        "AND operator_id=$2::text AND action IN "
        "('page_template_created','page_version_created','page_version_published')",
        str(tenant_id),
        str(admin_id),
    )
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM auth_sessions WHERE tenant_id=$1 AND current_refresh_jti LIKE 'cli-%'",
            tenant_id,
        )
        == 0
    )


async def _assert_legacy_creator_tenant_rollout(migrated_pg_url: str) -> None:
    await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", PARENT_REVISION)
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    target = await _seed_catalog(owner, "page-legacy-target")
    agency = await _seed_catalog(owner, "page-legacy-agency")
    other = await _seed_catalog(owner, "page-legacy-other")
    await owner.execute("UPDATE tenants SET tenant_type='agency' WHERE id=$1", agency["tenant"])
    agency_session = await _grant_page_permissions(owner, agency)
    target_session = await _grant_page_permissions(owner, target)
    revoked_session = uuid.uuid4()
    await owner.execute(
        "INSERT INTO auth_sessions "
        "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,revoked_at,created_at,updated_at) "
        "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now(),now())",
        revoked_session,
        agency["account"],
        agency["tenant"],
        uuid.uuid4().hex,
    )
    authorization_id = uuid.uuid4()
    await owner.execute(
        "INSERT INTO agency_authorizations "
        "(id,agency_tenant_id,client_tenant_id,scope,status,granted_by,granted_at,created_at,updated_at) "
        "VALUES($1,$2,$3,'[\"pages\"]','active',$4,now(),now(),now())",
        authorization_id,
        agency["tenant"],
        target["tenant"],
        target["account"],
    )
    template_id = uuid.uuid4()
    legacy_valid = uuid.uuid4()
    legacy_missing = uuid.uuid4()
    missing_actor = uuid.uuid4()
    expand_insert = uuid.uuid4()
    expand_update = uuid.uuid4()
    rejected_ids: list[uuid.UUID] = []
    try:
        await owner.execute(
            "INSERT INTO page_templates(id,tenant_id,product_id,name,template_type,status,created_at,updated_at) "
            "VALUES($1,$2,$3,'Legacy','traceability','active',now(),now())",
            template_id,
            target["tenant"],
            target["product"],
        )
        await owner.executemany(
            "INSERT INTO page_versions(id,tenant_id,page_template_id,version,config_json,status,created_by,"
            "created_at,updated_at) VALUES($1,$2,$3,$4,'{}','draft',$5,now(),now())",
            [
                (legacy_valid, target["tenant"], template_id, 1, agency["account"]),
                (legacy_missing, target["tenant"], template_id, 2, missing_actor),
            ],
        )
        await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "u5a0b1c2d3e4")
        expand_compat_contract = await owner.fetchrow(
            "SELECT prosecdef,proconfig,regexp_replace(prosrc,'\\s+','','g') AS normalized_source "
            "FROM pg_proc WHERE oid=to_regprocedure($1)",
            "public.populate_page_version_creator_tenant()",
        )
        assert await owner.fetchval(
            "SELECT prosecdef FROM pg_proc WHERE oid=to_regprocedure($1)",
            "public.populate_page_version_creator_tenant()",
        )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app','public.populate_page_version_creator_tenant()','EXECUTE')"
        )
        await _legacy_runtime_execute(
            runtime,
            target["tenant"],
            agency_session,
            "INSERT INTO page_versions(id,tenant_id,page_template_id,version,config_json,status,created_by,"
            "created_at,updated_at) VALUES($1,$2,$3,3,'{}','draft',$4,now(),now())",
            expand_insert,
            target["tenant"],
            template_id,
            agency["account"],
        )
        await _legacy_runtime_execute(
            runtime,
            target["tenant"],
            target_session,
            "INSERT INTO page_versions(id,tenant_id,page_template_id,version,config_json,status,created_by,"
            "created_at,updated_at) VALUES($1,$2,$3,4,'{}','draft',$4,now(),now())",
            expand_update,
            target["tenant"],
            template_id,
            target["account"],
        )
        await _legacy_runtime_execute(
            runtime,
            target["tenant"],
            agency_session,
            "UPDATE page_versions SET created_by=$2 WHERE id=$1",
            expand_update,
            agency["account"],
        )
        assert await owner.fetchval(
            "SELECT bool_and(created_by_tenant_id=$2) FROM page_versions WHERE id=ANY($1::uuid[])",
            [expand_insert, expand_update],
            agency["tenant"],
        )

        for session_context, creator_id in (
            (None, agency["account"]),
            (uuid.uuid4(), agency["account"]),
            (revoked_session, agency["account"]),
            (agency_session, target["account"]),
        ):
            rejected_id = uuid.uuid4()
            rejected_ids.append(rejected_id)
            await _assert_sqlstate(
                _legacy_runtime_execute(
                    runtime,
                    target["tenant"],
                    session_context,
                    "INSERT INTO page_versions(id,tenant_id,page_template_id,version,config_json,status,created_by,"
                    "created_at,updated_at) VALUES($1,$2,$3,50,'{}','draft',$4,now(),now())",
                    rejected_id,
                    target["tenant"],
                    template_id,
                    creator_id,
                ),
                "42501",
            )
        rejected_other_tenant = uuid.uuid4()
        rejected_ids.append(rejected_other_tenant)
        await _assert_sqlstate(
            _legacy_runtime_execute(
                runtime,
                target["tenant"],
                agency_session,
                "INSERT INTO page_versions(id,tenant_id,page_template_id,version,config_json,status,created_by,"
                "created_at,updated_at) VALUES($1,$2,$3,52,'{}','draft',$4,now(),now())",
                rejected_other_tenant,
                other["tenant"],
                template_id,
                agency["account"],
            ),
            "42501",
        )
        await owner.execute(
            "UPDATE agency_authorizations SET status='revoked',revoked_at=now(),updated_at=now() WHERE id=$1",
            authorization_id,
        )
        await owner.execute("DELETE FROM agency_authorizations WHERE id=$1", authorization_id)
        authorization_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO agency_authorizations "
            "(id,agency_tenant_id,client_tenant_id,scope,status,granted_by,granted_at,created_at,updated_at) "
            "VALUES($1,$2,$3,'[\"analytics\"]','active',$4,now(),now(),now())",
            authorization_id,
            agency["tenant"],
            target["tenant"],
            target["account"],
        )
        rejected_scope = uuid.uuid4()
        rejected_ids.append(rejected_scope)
        await _assert_sqlstate(
            _legacy_runtime_execute(
                runtime,
                target["tenant"],
                agency_session,
                "INSERT INTO page_versions(id,tenant_id,page_template_id,version,config_json,status,created_by,"
                "created_at,updated_at) VALUES($1,$2,$3,51,'{}','draft',$4,now(),now())",
                rejected_scope,
                target["tenant"],
                template_id,
                agency["account"],
            ),
            "42501",
        )
        await owner.execute(
            "UPDATE agency_authorizations SET status='revoked',revoked_at=now(),updated_at=now() WHERE id=$1",
            authorization_id,
        )
        await owner.execute("DELETE FROM agency_authorizations WHERE id=$1", authorization_id)
        authorization_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO agency_authorizations "
            "(id,agency_tenant_id,client_tenant_id,scope,status,granted_by,granted_at,created_at,updated_at) "
            "VALUES($1,$2,$3,'[\"pages\"]','active',$4,now(),now(),now())",
            authorization_id,
            agency["tenant"],
            target["tenant"],
            target["account"],
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM page_versions WHERE id=ANY($1::uuid[]))", rejected_ids
        )

        failed = await asyncio.to_thread(
            _alembic,
            migrated_pg_url,
            "upgrade",
            "head",
            succeeds=False,
        )
        assert "requires exactly one authoritative account" in f"{failed.stdout}\n{failed.stderr}"
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u5a1b2c3d4e5"
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='fk_page_versions_tenant_creator')"
        )
        assert await owner.fetchval(
            "SELECT bool_and(created_by_tenant_id IS NULL) FROM page_versions WHERE id=ANY($1::uuid[])",
            [legacy_valid, legacy_missing],
        )

        await owner.execute("DELETE FROM page_versions WHERE id=$1", legacy_missing)
        await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")
        assert await owner.fetchval(
            "SELECT count(*)=3 FROM page_versions WHERE id=ANY($1::uuid[]) AND created_by_tenant_id=$2",
            [legacy_valid, expand_insert, expand_update],
            agency["tenant"],
        )
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u5a3d4e5f6a7"

        # u5a3 downgrade must restore the identical safe compatibility trigger.
        downgraded = await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", "u5a2c3d4e5f6")
        assert "u5a3d4e5f6a7 -> u5a2c3d4e5f6" in f"{downgraded.stdout}\n{downgraded.stderr}"
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u5a2c3d4e5f6"
        assert (
            await owner.fetchrow(
                "SELECT prosecdef,proconfig,regexp_replace(prosrc,'\\s+','','g') AS normalized_source "
                "FROM pg_proc WHERE oid=to_regprocedure($1)",
                "public.populate_page_version_creator_tenant()",
            )
            == expand_compat_contract
        )
        assert await owner.fetchval(
            "SELECT prosecdef FROM pg_proc WHERE oid=to_regprocedure($1)",
            "public.populate_page_version_creator_tenant()",
        )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app','public.populate_page_version_creator_tenant()','EXECUTE')"
        )
        downgrade_insert = uuid.uuid4()
        await _legacy_runtime_execute(
            runtime,
            target["tenant"],
            agency_session,
            "INSERT INTO page_versions(id,tenant_id,page_template_id,version,config_json,status,created_by,"
            "created_at,updated_at,created_by_tenant_id) VALUES($1,$2,$3,60,'{}','draft',$4,now(),now(),$2)",
            downgrade_insert,
            target["tenant"],
            template_id,
            agency["account"],
        )
        assert await owner.fetchval(
            "SELECT created_by_tenant_id=$2 FROM page_versions WHERE id=$1", downgrade_insert, agency["tenant"]
        )
        same_tenant_downgrade = uuid.uuid4()
        await _legacy_runtime_execute(
            runtime,
            target["tenant"],
            target_session,
            "INSERT INTO page_versions(id,tenant_id,page_template_id,version,config_json,status,created_by,"
            "created_at,updated_at,created_by_tenant_id) VALUES($1,$2,$3,62,'{}','draft',$4,now(),now(),$2)",
            same_tenant_downgrade,
            target["tenant"],
            template_id,
            target["account"],
        )
        assert await owner.fetchval(
            "SELECT created_by_tenant_id=$2 FROM page_versions WHERE id=$1",
            same_tenant_downgrade,
            target["tenant"],
        )
        for session_context, creator_id in (
            (None, agency["account"]),
            (uuid.uuid4(), agency["account"]),
            (revoked_session, agency["account"]),
            (agency_session, target["account"]),
        ):
            rejected_downgrade_context = uuid.uuid4()
            await _assert_sqlstate(
                _legacy_runtime_execute(
                    runtime,
                    target["tenant"],
                    session_context,
                    "INSERT INTO page_versions(id,tenant_id,page_template_id,version,config_json,status,created_by,"
                    "created_at,updated_at,created_by_tenant_id) VALUES($1,$2,$3,63,'{}','draft',$4,now(),now(),$2)",
                    rejected_downgrade_context,
                    target["tenant"],
                    template_id,
                    creator_id,
                ),
                "42501",
            )
            assert not await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM page_versions WHERE id=$1)", rejected_downgrade_context
            )
        await owner.execute(
            "UPDATE agency_authorizations SET status='revoked',revoked_at=now() WHERE id=$1", authorization_id
        )
        rejected_downgrade = uuid.uuid4()
        await _assert_sqlstate(
            _legacy_runtime_execute(
                runtime,
                target["tenant"],
                agency_session,
                "INSERT INTO page_versions(id,tenant_id,page_template_id,version,config_json,status,created_by,"
                "created_at,updated_at,created_by_tenant_id) VALUES($1,$2,$3,61,'{}','draft',$4,now(),now(),$2)",
                rejected_downgrade,
                target["tenant"],
                template_id,
                agency["account"],
            ),
            "42501",
        )
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM page_versions WHERE id=$1)", rejected_downgrade)
        await owner.execute("DELETE FROM agency_authorizations WHERE id=$1", authorization_id)
        authorization_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO agency_authorizations "
            "(id,agency_tenant_id,client_tenant_id,scope,status,granted_by,granted_at,created_at,updated_at) "
            "VALUES($1,$2,$3,'[\"analytics\"]','active',$4,now(),now(),now())",
            authorization_id,
            agency["tenant"],
            target["tenant"],
            target["account"],
        )
        rejected_downgrade_scope = uuid.uuid4()
        await _assert_sqlstate(
            _legacy_runtime_execute(
                runtime,
                target["tenant"],
                agency_session,
                "INSERT INTO page_versions(id,tenant_id,page_template_id,version,config_json,status,created_by,"
                "created_at,updated_at,created_by_tenant_id) VALUES($1,$2,$3,64,'{}','draft',$4,now(),now(),$2)",
                rejected_downgrade_scope,
                target["tenant"],
                template_id,
                agency["account"],
            ),
            "42501",
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM page_versions WHERE id=$1)", rejected_downgrade_scope
        )
        await owner.execute(
            "UPDATE agency_authorizations SET status='revoked',revoked_at=now(),updated_at=now() WHERE id=$1",
            authorization_id,
        )
        await owner.execute("DELETE FROM agency_authorizations WHERE id=$1", authorization_id)
    finally:
        if await owner.fetchval("SELECT to_regclass('public.page_versions') IS NOT NULL"):
            await owner.execute("DELETE FROM page_versions WHERE page_template_id=$1", template_id)
            await owner.execute("DELETE FROM page_templates WHERE id=$1", template_id)
        await _purge_owned_delivery_fixture(owner, agency["tenant"])
        await _purge_owned_delivery_fixture(owner, target["tenant"])
        await _purge_owned_delivery_fixture(owner, other["tenant"])
        await runtime.close()
        await owner.close()
        await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")


async def test_official_split_role_seeds_use_page_authority_and_leave_no_session(migrated_pg_url: str) -> None:
    await asyncio.to_thread(_run_cli, migrated_pg_url, "all")
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    try:
        await _assert_official_seed_page_authority(owner, "demo", "admin@demo.com")
        await asyncio.to_thread(_run_cli, migrated_pg_url, "baseline", "build", "--target", BASELINE_TENANT_SLUG)
        await _assert_official_seed_page_authority(owner, BASELINE_TENANT_SLUG, BASELINE_ADMIN_EMAIL)
    finally:
        await owner.close()


async def test_page_authority_is_tenant_bound_atomic_audited_and_function_only(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    ids = await _seed_catalog(owner, "page-authority")
    other = await _seed_catalog(owner, "page-authority-other")
    session_id = await _grant_page_permissions(owner, ids)
    template_id = uuid.uuid4()
    version_ids = [uuid.uuid4() for _ in range(5)]
    audit_ids: list[uuid.UUID] = []
    try:
        for table in ("page_templates", "page_versions"):
            assert await owner.fetchval(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=$1::regclass",
                table,
            )
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,$2)", table, privilege)
            assert await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,'SELECT')", table)

        audit_ids.append(uuid.uuid4())
        template = await _runtime_call(
            runtime,
            ids["tenant"],
            "SELECT * FROM public.mutate_page_template($1,$2,$3,'create',$4,$5,$6,$7,$8)",
            ids["tenant"],
            session_id,
            audit_ids[-1],
            template_id,
            ids["product"],
            "Authority page",
            "traceability",
            "acceptance",
        )
        assert template["page_template_id"] == template_id
        assert template["status"] == "active"

        audit_ids.append(uuid.uuid4())
        await _runtime_call(
            runtime,
            ids["tenant"],
            "SELECT * FROM public.mutate_page_template($1,$2,$3,'update',$4,NULL,$5,NULL,NULL)",
            ids["tenant"],
            session_id,
            audit_ids[-1],
            template_id,
            "Authority page renamed",
        )
        audit_ids.append(uuid.uuid4())
        await _runtime_call(
            runtime,
            ids["tenant"],
            "SELECT * FROM public.mutate_page_template($1,$2,$3,'update',$4,NULL,NULL,NULL,$5)",
            ids["tenant"],
            session_id,
            audit_ids[-1],
            template_id,
            "description patched independently",
        )
        merged_template = await owner.fetchrow(
            "SELECT name,description,product_id,template_type FROM page_templates WHERE id=$1",
            template_id,
        )
        assert dict(merged_template) == {
            "name": "Authority page renamed",
            "description": "description patched independently",
            "product_id": ids["product"],
            "template_type": "traceability",
        }

        await _assert_sqlstate(
            _runtime_call(
                runtime,
                ids["tenant"],
                "SELECT * FROM public.mutate_page_template($1,$2,$3,'create',$4,$5,$6,$7,$8)",
                ids["tenant"],
                session_id,
                uuid.uuid4(),
                uuid.uuid4(),
                ids["product"],
                "Duplicate active",
                "traceability",
                None,
            ),
            "23505",
        )

        for position, version_id in enumerate(version_ids[:2], start=1):
            audit_ids.append(uuid.uuid4())
            created = await _runtime_call(
                runtime,
                ids["tenant"],
                "SELECT * FROM public.create_page_version($1,$2,$3,$4,$5,$6::jsonb)",
                ids["tenant"],
                session_id,
                audit_ids[-1],
                version_id,
                template_id,
                f'{{"modules":[],"marker":{position}}}',
            )
            assert created["version_number"] == position
            assert created["current_status"] == "draft"

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
                await runtime.execute(
                    "UPDATE page_versions SET status='published',published_at=now() WHERE id=$1",
                    version_ids[0],
                )

        audit_ids.append(uuid.uuid4())
        first_published = await _runtime_call(
            runtime,
            ids["tenant"],
            "SELECT * FROM public.publish_page_version($1,$2,$3,$4)",
            ids["tenant"],
            session_id,
            audit_ids[-1],
            version_ids[0],
        )
        assert first_published["current_status"] == "published"
        await _assert_sqlstate(
            _runtime_call(
                runtime,
                ids["tenant"],
                "SELECT * FROM public.update_page_version($1,$2,$3,$4,$5::jsonb)",
                ids["tenant"],
                session_id,
                uuid.uuid4(),
                version_ids[0],
                '{"modules":[],"forbidden":true}',
            ),
            "23514",
        )

        audit_ids.append(uuid.uuid4())
        second_published = await _runtime_call(
            runtime,
            ids["tenant"],
            "SELECT * FROM public.publish_page_version($1,$2,$3,$4)",
            ids["tenant"],
            session_id,
            audit_ids[-1],
            version_ids[1],
        )
        assert second_published["current_status"] == "published"
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM page_versions WHERE tenant_id=$1 AND page_template_id=$2 AND status='published'",
                ids["tenant"],
                template_id,
            )
            == 1
        )
        assert await owner.fetchval("SELECT status FROM page_versions WHERE id=$1", version_ids[0]) == "archived"

        await _assert_sqlstate(
            owner.execute(
                "INSERT INTO page_versions(id,tenant_id,page_template_id,version,config_json,status,"
                "created_by_tenant_id,created_by) VALUES($1,$2,$3,99,'{}','draft',$2,$4)",
                uuid.uuid4(),
                other["tenant"],
                template_id,
                other["account"],
            ),
            "23503",
        )
        await _assert_sqlstate(
            _runtime_call(
                runtime,
                other["tenant"],
                "SELECT * FROM public.create_page_version($1,$2,$3,$4,$5,$6::jsonb)",
                other["tenant"],
                session_id,
                uuid.uuid4(),
                uuid.uuid4(),
                template_id,
                "{}",
            ),
            "42501",
        )

        audit_ids.append(uuid.uuid4())
        rolled_back = await _runtime_call(
            runtime,
            ids["tenant"],
            "SELECT * FROM public.rollback_page_version($1,$2,$3,$4,$5,$6)",
            ids["tenant"],
            session_id,
            audit_ids[-1],
            version_ids[2],
            template_id,
            version_ids[0],
        )
        assert rolled_back["source_version_id"] == version_ids[0]
        assert rolled_back["version_number"] == 3
        assert rolled_back["current_status"] == "draft"
        assert await owner.fetchval(
            'SELECT config_json::jsonb = \'{"modules":[],"marker":1}\'::jsonb FROM page_versions WHERE id=$1',
            version_ids[2],
        )
        assert await owner.fetchval(
            "SELECT count(*) FROM platform_audit_log WHERE id=ANY($1::uuid[])", audit_ids
        ) == len(audit_ids)
    finally:
        await runtime.close()
        await owner.execute("DELETE FROM launch_releases WHERE tenant_id IN ($1,$2)", ids["tenant"], other["tenant"])
        await owner.execute("DELETE FROM page_versions WHERE tenant_id IN ($1,$2)", ids["tenant"], other["tenant"])
        await owner.execute("DELETE FROM page_templates WHERE tenant_id IN ($1,$2)", ids["tenant"], other["tenant"])
        await owner.execute("DELETE FROM platform_audit_log WHERE id=ANY($1::uuid[])", audit_ids)
        await owner.execute("DELETE FROM auth_sessions WHERE id=$1", session_id)
        await _purge_owned_delivery_fixture(owner, other["tenant"])
        await _purge_owned_delivery_fixture(owner, ids["tenant"])
        await owner.close()


async def test_acting_agency_page_authority_records_principal_creator_and_target_audit(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    target = await _seed_catalog(owner, "page-agency-target")
    agency = await _seed_catalog(owner, "page-agency-principal")
    await owner.execute("UPDATE tenants SET tenant_type='agency' WHERE id=$1", agency["tenant"])
    session_id = await _grant_page_permissions(owner, agency)
    authorization_id = uuid.uuid4()
    template_id = uuid.uuid4()
    version_id = uuid.uuid4()
    rollback_id = uuid.uuid4()
    audit_ids = [uuid.uuid4() for _ in range(6)]
    rejected_audits: list[uuid.UUID] = []
    try:
        await owner.execute(
            "INSERT INTO agency_authorizations "
            "(id,agency_tenant_id,client_tenant_id,scope,status,granted_by,granted_at,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,'[\"pages\"]'::jsonb,'active',$4,now(),now()+interval '1 hour',now(),now())",
            authorization_id,
            agency["tenant"],
            target["tenant"],
            target["account"],
        )
        await _runtime_call(
            runtime,
            target["tenant"],
            "SELECT * FROM mutate_page_template($1,$2,$3,'create',$4,$5,'Agency page','traceability',NULL)",
            target["tenant"],
            session_id,
            audit_ids[0],
            template_id,
            target["product"],
        )
        await _runtime_call(
            runtime,
            target["tenant"],
            "SELECT * FROM mutate_page_template($1,$2,$3,'update',$4,NULL,'Agency renamed',NULL,NULL)",
            target["tenant"],
            session_id,
            audit_ids[1],
            template_id,
        )
        await _runtime_call(
            runtime,
            target["tenant"],
            "SELECT * FROM create_page_version($1,$2,$3,$4,$5,'{}'::jsonb)",
            target["tenant"],
            session_id,
            audit_ids[2],
            version_id,
            template_id,
        )
        await _runtime_call(
            runtime,
            target["tenant"],
            "SELECT * FROM update_page_version($1,$2,$3,$4,'{\"modules\":[]}'::jsonb)",
            target["tenant"],
            session_id,
            audit_ids[3],
            version_id,
        )
        await _runtime_call(
            runtime,
            target["tenant"],
            "SELECT * FROM publish_page_version($1,$2,$3,$4)",
            target["tenant"],
            session_id,
            audit_ids[4],
            version_id,
        )
        await _runtime_call(
            runtime,
            target["tenant"],
            "SELECT * FROM archive_page_version($1,$2,$3,$4)",
            target["tenant"],
            session_id,
            audit_ids[5],
            version_id,
        )
        rollback_audit = uuid.uuid4()
        audit_ids.append(rollback_audit)
        await _runtime_call(
            runtime,
            target["tenant"],
            "SELECT * FROM rollback_page_version($1,$2,$3,$4,$5,$6)",
            target["tenant"],
            session_id,
            rollback_audit,
            rollback_id,
            template_id,
            version_id,
        )
        creators = await owner.fetch(
            "SELECT created_by_tenant_id,created_by FROM page_versions WHERE id=ANY($1::uuid[]) ORDER BY id",
            [version_id, rollback_id],
        )
        assert len(creators) == 2
        assert all(row["created_by_tenant_id"] == agency["tenant"] for row in creators)
        assert all(row["created_by"] == agency["account"] for row in creators)
        audit_rows = await owner.fetch(
            "SELECT operator_id,target_tenant_id FROM platform_audit_log WHERE id=ANY($1::uuid[])", audit_ids
        )
        assert len(audit_rows) == len(audit_ids)
        assert all(row["operator_id"] == str(agency["account"]) for row in audit_rows)
        assert all(row["target_tenant_id"] == str(target["tenant"]) for row in audit_rows)

        before_versions = await owner.fetchval(
            "SELECT count(*) FROM page_versions WHERE tenant_id=$1", target["tenant"]
        )
        await owner.execute(
            "UPDATE agency_authorizations SET status='revoked',revoked_at=now(),updated_at=now() WHERE id=$1",
            authorization_id,
        )
        for scope_sql in (None, '["analytics"]'):
            if scope_sql is not None:
                await owner.execute("DELETE FROM agency_authorizations WHERE id=$1", authorization_id)
                authorization_id = uuid.uuid4()
                await owner.execute(
                    "INSERT INTO agency_authorizations "
                    "(id,agency_tenant_id,client_tenant_id,scope,status,granted_by,granted_at,expires_at,"
                    "created_at,updated_at) VALUES($1,$2,$3,$4::jsonb,'active',$5,now(),"
                    "now()+interval '1 hour',now(),now())",
                    authorization_id,
                    agency["tenant"],
                    target["tenant"],
                    scope_sql,
                    target["account"],
                )
            rejected_audit = uuid.uuid4()
            rejected_audits.append(rejected_audit)
            await _assert_sqlstate(
                _runtime_call(
                    runtime,
                    target["tenant"],
                    "SELECT * FROM create_page_version($1,$2,$3,$4,$5,'{}'::jsonb)",
                    target["tenant"],
                    session_id,
                    rejected_audit,
                    uuid.uuid4(),
                    template_id,
                ),
                "42501",
            )
            assert (
                await owner.fetchval("SELECT count(*) FROM page_versions WHERE tenant_id=$1", target["tenant"])
                == before_versions
            )
            assert not await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$1)", rejected_audit
            )

        await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", "u5a2c3d4e5f6")
        blocked = await asyncio.to_thread(
            _alembic,
            migrated_pg_url,
            "downgrade",
            "u5ab2c3d4e5f",
            succeeds=False,
        )
        assert "acting-agency creator facts exist" in f"{blocked.stdout}\n{blocked.stderr}"
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u5a2c3d4e5f6"
        assert await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='fk_page_versions_tenant_creator')"
        )
        await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")
    finally:
        await runtime.close()
        await owner.execute("DELETE FROM agency_authorizations WHERE id=$1", authorization_id)
        await owner.execute("DELETE FROM page_versions WHERE tenant_id=$1", target["tenant"])
        await owner.execute("DELETE FROM page_templates WHERE tenant_id=$1", target["tenant"])
        await owner.execute("DELETE FROM platform_audit_log WHERE id=ANY($1::uuid[])", audit_ids + rejected_audits)
        await owner.execute("DELETE FROM auth_sessions WHERE id=$1", session_id)
        await _purge_owned_delivery_fixture(owner, agency["tenant"])
        await _purge_owned_delivery_fixture(owner, target["tenant"])
        await owner.close()


async def test_page_authority_publish_contention_and_roundtrip(migrated_pg_url: str) -> None:
    await _assert_legacy_creator_tenant_rollout(migrated_pg_url)
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    blocker = await asyncpg.connect(owner_dsn)
    runtime_a = await asyncpg.connect(runtime_dsn)
    runtime_b = await asyncpg.connect(runtime_dsn)
    ids = await _seed_catalog(owner, "page-contention")
    session_id = await _grant_page_permissions(owner, ids)
    template_id = uuid.uuid4()
    versions = [uuid.uuid4(), uuid.uuid4()]
    audits = [uuid.uuid4() for _ in range(5)]
    blocker_tx: asyncpg.Transaction | None = None
    role_timeout_set = False
    try:
        await _runtime_call(
            runtime_a,
            ids["tenant"],
            "SELECT * FROM public.mutate_page_template($1,$2,$3,'create',$4,$5,'Concurrent','traceability',NULL)",
            ids["tenant"],
            session_id,
            audits[0],
            template_id,
            ids["product"],
        )
        for offset, version_id in enumerate(versions):
            await _runtime_call(
                runtime_a,
                ids["tenant"],
                "SELECT * FROM public.create_page_version($1,$2,$3,$4,$5,'{}'::jsonb)",
                ids["tenant"],
                session_id,
                audits[offset + 1],
                version_id,
                template_id,
            )
        await owner.execute(
            "CREATE FUNCTION acceptance_slow_page_publish() RETURNS trigger LANGUAGE plpgsql AS "
            "$$BEGIN IF NEW.status='published' AND OLD.status='draft' THEN PERFORM pg_sleep(0.5); END IF; RETURN NEW; END$$"
        )
        await owner.execute(
            "CREATE TRIGGER acceptance_slow_page_publish BEFORE UPDATE ON page_versions "
            "FOR EACH ROW EXECUTE FUNCTION acceptance_slow_page_publish()"
        )

        async def publish(conn: asyncpg.Connection, version_id: uuid.UUID, audit_id: uuid.UUID):
            try:
                row = await _runtime_call(
                    conn,
                    ids["tenant"],
                    "SELECT * FROM public.publish_page_version($1,$2,$3,$4)",
                    ids["tenant"],
                    session_id,
                    audit_id,
                    version_id,
                )
                return row["current_status"]
            except asyncpg.PostgresError as exc:
                return exc.sqlstate

        outcomes = await asyncio.wait_for(
            asyncio.gather(
                publish(runtime_a, versions[0], audits[3]),
                publish(runtime_b, versions[1], audits[4]),
            ),
            timeout=10,
        )
        assert sorted(outcomes) == ["55P03", "published"]
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM page_versions WHERE tenant_id=$1 AND page_template_id=$2 AND status='published'",
                ids["tenant"],
                template_id,
            )
            == 1
        )

        other_template = uuid.uuid4()
        other_version = uuid.uuid4()
        other_template_audit = uuid.uuid4()
        other_create_audit = uuid.uuid4()
        audits.extend((other_template_audit, other_create_audit))
        await _runtime_call(
            runtime_a,
            ids["tenant"],
            "SELECT * FROM public.mutate_page_template($1,$2,$3,'create',$4,NULL,'Other','traceability',NULL)",
            ids["tenant"],
            session_id,
            other_template_audit,
            other_template,
        )
        await _runtime_call(
            runtime_a,
            ids["tenant"],
            "SELECT * FROM public.create_page_version($1,$2,$3,$4,$5,'{}'::jsonb)",
            ids["tenant"],
            session_id,
            other_create_audit,
            other_version,
            other_template,
        )
        losing_version = await owner.fetchval(
            "SELECT id FROM page_versions WHERE id=ANY($1::uuid[]) AND status='draft'", versions
        )
        assert losing_version in versions
        parallel_audits = [uuid.uuid4(), uuid.uuid4()]
        audits.extend(parallel_audits)
        parallel_started = time.perf_counter()
        parallel_outcomes = await asyncio.wait_for(
            asyncio.gather(
                publish(runtime_a, losing_version, parallel_audits[0]),
                publish(runtime_b, other_version, parallel_audits[1]),
            ),
            timeout=10,
        )
        parallel_elapsed = time.perf_counter() - parallel_started
        assert parallel_outcomes == ["published", "published"]
        assert parallel_elapsed < 0.9
        await owner.execute("DROP TRIGGER acceptance_slow_page_publish ON page_versions")
        await owner.execute("DROP FUNCTION acceptance_slow_page_publish()")

        # A real concurrent-build timeout must not leave an invalid shell or
        # advance/drop any u5a2 catalog contract; the same downgrade then retries.
        await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", "u5a2c3d4e5f6")
        blocker_tx = blocker.transaction()
        await blocker_tx.start()
        await blocker.execute("UPDATE page_templates SET updated_at=updated_at WHERE id=$1", template_id)
        await owner.execute("ALTER ROLE yimatong SET statement_timeout='3s'")
        role_timeout_set = True
        failed_index = await asyncio.to_thread(
            _alembic,
            migrated_pg_url,
            "downgrade",
            "u5ab2c3d4e5f",
            succeeds=False,
        )
        assert "statement timeout" in f"{failed_index.stdout}\n{failed_index.stderr}".lower()
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u5a2c3d4e5f6"
        assert await owner.fetchval(
            "SELECT count(*)=2 FROM pg_constraint WHERE conname IN "
            "('uq_page_versions_tenant_template_id','fk_page_versions_tenant_creator')"
        )
        temporary_definitions = {
            "uq_page_versions_tenant_template_id_downgrade": (
                "CREATE UNIQUE INDEX uq_page_versions_tenant_template_id_downgrade ON public.page_versions "
                "USING btree (tenant_id, page_template_id, id)"
            ),
            "uq_page_templates_tenant_id_id_downgrade": (
                "CREATE UNIQUE INDEX uq_page_templates_tenant_id_id_downgrade ON public.page_templates "
                "USING btree (tenant_id, id)"
            ),
        }
        for index_name, expected_definition in temporary_definitions.items():
            shell = await owner.fetchrow(
                "SELECT indisvalid,pg_get_indexdef(indexrelid) FROM pg_index "
                "WHERE indexrelid=to_regclass('public.' || $1)",
                index_name,
            )
            if shell is not None:
                assert shell["indisvalid"] is False
                assert shell["pg_get_indexdef"] == expected_definition
        await blocker_tx.rollback()
        blocker_tx = None
        await owner.execute("ALTER ROLE yimatong RESET statement_timeout")
        role_timeout_set = False
        await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", "u5ab2c3d4e5f")
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u5ab2c3d4e5f"
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_index WHERE NOT indisvalid AND indexrelid IN "
            "(to_regclass('public.uq_page_versions_tenant_template_id'),"
            "to_regclass('public.uq_page_templates_tenant_id_id')))"
        )
        for index_name in temporary_definitions:
            assert not await owner.fetchval("SELECT to_regclass('public.' || $1) IS NOT NULL", index_name)
        await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")

        # Valid online replacements may remain prepared, but all constraint
        # changes below them must roll back together when short DDL cannot lock.
        await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", "u5a2c3d4e5f6")
        await owner.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY uq_page_versions_tenant_template_id_downgrade "
            "ON page_versions(tenant_id,page_template_id,id)"
        )
        await owner.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY uq_page_templates_tenant_id_id_downgrade ON page_templates(tenant_id,id)"
        )
        blocker_tx = blocker.transaction()
        await blocker_tx.start()
        await blocker.execute("LOCK TABLE page_versions IN ROW EXCLUSIVE MODE")
        failed_short_ddl = await asyncio.to_thread(
            _alembic,
            migrated_pg_url,
            "downgrade",
            "u5ab2c3d4e5f",
            succeeds=False,
        )
        assert "lock timeout" in f"{failed_short_ddl.stdout}\n{failed_short_ddl.stderr}".lower()
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u5a2c3d4e5f6"
        assert await owner.fetchval(
            "SELECT count(*)=2 FROM pg_constraint WHERE conname IN "
            "('uq_page_versions_tenant_template_id','fk_page_versions_tenant_creator')"
        )
        assert await owner.fetchval(
            "SELECT bool_and(indisvalid) FROM pg_index WHERE indexrelid IN "
            "('uq_page_versions_tenant_template_id_downgrade'::regclass,"
            "'uq_page_templates_tenant_id_id_downgrade'::regclass)"
        )
        await blocker_tx.rollback()
        blocker_tx = None
        await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", "u5ab2c3d4e5f")
        await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")

        await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", PARENT_REVISION)
        assert await owner.fetchval("SELECT has_table_privilege('yimatong_app','page_versions','UPDATE')")
        assert not await owner.fetchval(
            "SELECT to_regprocedure('public.publish_page_version(uuid,uuid,uuid,uuid)') IS NOT NULL"
        )
        await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")
        assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app','page_versions','UPDATE')")
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app','public.publish_page_version(uuid,uuid,uuid,uuid)','EXECUTE')"
        )
        await asyncio.to_thread(_alembic, migrated_pg_url, "check")
    finally:
        if blocker_tx is not None:
            await blocker_tx.rollback()
        if role_timeout_set:
            await owner.execute("ALTER ROLE yimatong RESET statement_timeout")
        await blocker.close()
        await runtime_a.close()
        await runtime_b.close()
        if await owner.fetchval("SELECT to_regclass('public.page_versions') IS NOT NULL"):
            await owner.execute("DROP TRIGGER IF EXISTS acceptance_slow_page_publish ON page_versions")
            await owner.execute("DROP FUNCTION IF EXISTS acceptance_slow_page_publish()")
            await owner.execute("DELETE FROM page_versions WHERE tenant_id=$1", ids["tenant"])
            await owner.execute("DELETE FROM page_templates WHERE tenant_id=$1", ids["tenant"])
            await owner.execute("DELETE FROM platform_audit_log WHERE id=ANY($1::uuid[])", audits)
            await owner.execute("DELETE FROM auth_sessions WHERE id=$1", session_id)
            await _purge_owned_delivery_fixture(owner, ids["tenant"])
        await owner.close()
