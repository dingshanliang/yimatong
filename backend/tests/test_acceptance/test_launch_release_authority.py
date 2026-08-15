"""Real PostgreSQL contract for the current launch-release authority."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from uuid6 import uuid7

from app.services.public_id import generate_public_id
from tests.test_acceptance.conftest import (
    ADMIN_DSN,
    BACKEND_DIR,
    AcceptanceDatabaseLease,
    _create_owned_database,
    _drop_database_with_retry,
    run_owned_migrations_with_snapshot_retry,
)
from tests.test_acceptance.test_code_batch_delivery_contract import (
    _insert_batch,
    _insert_items,
    _insert_manifest_and_deliver,
    _insert_receipt,
    _seed_catalog,
)
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic
from tests.test_acceptance.test_page_authority_contract import _grant_page_permissions

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

_PUBLIC_SIGNATURES = (
    "create_launch_release(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text)",
    "confirm_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)",
    "launch_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)",
    "suspend_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)",
    "resume_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)",
    "invalidate_launch_release(uuid,uuid,uuid,uuid,uuid,text,text,text)",
    "resolve_current_launch_release(uuid,text)",
)
_OBSERVATION_SIGNATURE = "record_launch_release_valid_scan(uuid,uuid,uuid,timestamp with time zone)"
_BOUND_CLAIM_SIGNATURE = "claim_campaign_benefit(uuid,uuid,uuid,uuid,uuid,text,text,text,uuid,uuid,text)"
_LEGACY_CLAIM_SIGNATURE = "claim_campaign_benefit(uuid,uuid,uuid,uuid,uuid,text,text,text)"
_LAUNCH_AUTHORITY_REVISION = "u5b5b6c7d8e9"


def _as_json(value: object) -> dict | list:
    decoded = json.loads(value) if isinstance(value, str) else value
    assert isinstance(decoded, (dict, list))
    return decoded


def _sole_head_with_launch_ancestry() -> str:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1, f"expected one migration head, got {heads}"
    head = heads[0]
    assert script.get_revision(_LAUNCH_AUTHORITY_REVISION) is not None
    if head != _LAUNCH_AUTHORITY_REVISION:
        descendants = list(script.iterate_revisions(head, _LAUNCH_AUTHORITY_REVISION))
        assert descendants, f"{_LAUNCH_AUTHORITY_REVISION} is not an ancestor of {head}"
        assert all(len(revision._normalized_down_revisions) == 1 for revision in descendants)
        assert descendants[-1]._normalized_down_revisions == (_LAUNCH_AUTHORITY_REVISION,)
    return head


async def _resolve_owned_launch_roundtrip_markers(database_url: str) -> None:
    expected = {
        ("0006", "distributors", "contact_phone"),
        ("0006", "kyc_records", "real_name"),
        ("0006", "kyc_records", "id_number"),
        ("0006", "kyc_records", "phone"),
    }
    owner = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        rows = await owner.fetch(
            "SELECT source_revision,source_table,source_column,state FROM legacy_pii_recovery_markers"
        )
        assert {(row["source_revision"], row["source_table"], row["source_column"]) for row in rows} == expected
        assert all(row["state"] == "legacy_unknown" for row in rows)
        assert (
            await owner.execute(
                "UPDATE legacy_pii_recovery_markers SET state='operator_recovered',"
                "note=note||'; isolated launch roundtrip acknowledgement' "
                "WHERE source_revision='0006' AND state='legacy_unknown'"
            )
            == f"UPDATE {len(expected)}"
        )
    finally:
        await owner.close()


async def _exercise_launch_staged_roundtrip(database_url: str, lease: AcceptanceDatabaseLease) -> None:
    owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    _alembic(database_url, "downgrade", "u5b3f4a5b6c7")
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u5b3f4a5b6c7"
        assert await owner.fetchval(
            "SELECT to_regprocedure('public.create_launch_release(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text)') IS NULL"
        )
        assert await owner.fetchval("SELECT to_regprocedure('public.bind_launch_release_actor_tenants()') IS NOT NULL")
        expected_legacy_fks = {
            "launch_releases_campaign_id_fkey",
            "launch_releases_code_batch_id_fkey",
            "launch_releases_created_by_fkey",
            "launch_releases_brand_confirmed_by_fkey",
            "launch_releases_launched_by_fkey",
            "launch_releases_suspended_by_fkey",
        }
        actual_fks = await owner.fetch(
            "SELECT conname FROM pg_constraint WHERE conrelid='launch_releases'::regclass AND contype='f'"
        )
        actual_fk_names = {row["conname"] for row in actual_fks}
        assert expected_legacy_fks <= actual_fk_names
        assert "launch_releases_tenant_id_fkey" in actual_fk_names
        assert "fk_launch_releases_tenant_readiness_code_item" in actual_fk_names
    finally:
        await owner.close()

    _alembic(database_url, "downgrade", "u5b0c1d2e3f4")
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u5b0c1d2e3f4"
        await owner.execute(
            "CREATE INDEX ix_launch_release_actions_tenant_release "
            "ON launch_release_actions(tenant_id,release_id,created_at)"
        )
        await owner.execute(
            "UPDATE pg_index SET indisvalid=false WHERE indexrelid='ix_launch_release_actions_tenant_release'::regclass"
        )
        await owner.execute(
            "CREATE INDEX ix_launch_releases_tenant_readiness_code_item "
            "ON launch_releases(tenant_id,readiness_code_item_id)"
        )
        await owner.execute(
            "UPDATE pg_index SET indisvalid=false "
            "WHERE indexrelid='ix_launch_releases_tenant_readiness_code_item'::regclass"
        )
        assert not await owner.fetchval(
            "SELECT indisvalid FROM pg_index WHERE indexrelid='ix_launch_release_actions_tenant_release'::regclass"
        )
        assert not await owner.fetchval(
            "SELECT indisvalid FROM pg_index WHERE indexrelid='ix_launch_releases_tenant_readiness_code_item'::regclass"
        )
    finally:
        await owner.close()
    _alembic(database_url, "upgrade", "u5b1d2e3f4a5")
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval(
            "SELECT indisvalid FROM pg_index WHERE indexrelid='ix_launch_release_actions_tenant_release'::regclass"
        )
        assert await owner.fetchval(
            "SELECT indisvalid FROM pg_index WHERE indexrelid='ix_launch_releases_tenant_readiness_code_item'::regclass"
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_index index_catalog JOIN pg_class index_class "
            "ON index_class.oid=index_catalog.indexrelid WHERE NOT index_catalog.indisvalid "
            "AND index_class.relname LIKE '%launch%')"
        )
    finally:
        await owner.close()
    await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
    _alembic(database_url, "check")


async def test_launch_authority_staged_roundtrip_and_invalid_index_retry(migrated_pg_url: str) -> None:
    repository_head = _sole_head_with_launch_ancestry()
    database_name = f"yimatong_acceptance_launch_{uuid.uuid4().hex[:12]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(
        database_name=database_name,
        database_dsn=database_url,
        owner_token=uuid.uuid4().hex,
    )
    primary_error: BaseException | None = None
    cleanup_errors: list[BaseException] = []
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        await _resolve_owned_launch_roundtrip_markers(database_url)
        try:
            await _exercise_launch_staged_roundtrip(database_url, lease)
        finally:
            await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        owner = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == repository_head
        finally:
            await owner.close()
        _alembic(database_url, "check")
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if lease.created:
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
                    primary_error.add_note(f"isolated launch database cleanup also failed: {cleanup_error!r}")
            else:
                raise cleanup_errors[0]


async def test_launch_v2_live_release_is_invalidated_at_v3_cutover(migrated_pg_url: str) -> None:
    _sole_head_with_launch_ancestry()
    database_name = f"yimatong_acceptance_launch_v2_{uuid.uuid4().hex[:12]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(database_name, database_url, uuid.uuid4().hex)
    primary_error: BaseException | None = None
    cleanup_errors: list[BaseException] = []
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        _alembic(database_url, "upgrade", "u5b3f4a5b6c7")
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        owner = await asyncpg.connect(owner_dsn)
        graph = await _seed_ready_graph(owner, "launch-v2-cutover", historical_legacy_export=True)
        release_id = uuid7()
        try:
            await owner.execute(
                "INSERT INTO launch_releases("
                "id,tenant_id,page_template_id,page_version_id,campaign_id,code_batch_id,status,readiness_snapshot,"
                "content_digest,readiness_manifest,created_by,created_by_tenant_id,brand_confirmed_by,"
                "brand_confirmed_by_tenant_id,brand_confirmed_at,brand_confirmation_digest,launched_by,"
                "launched_by_tenant_id,launched_at,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,$5,$6,'live',$7,$8,$9,$10,$2,$10,$2,now(),$8,$10,$2,now(),now(),now())",
                release_id,
                graph["tenant"],
                graph["template"],
                graph["version"],
                graph["campaign"],
                graph["code_batch"],
                json.dumps({"version": 2, "ready": True}),
                "0" * 64,
                json.dumps({"version": 2, "scan_evidence": {"id": str(uuid7())}}),
                graph["account"],
            )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM permissions WHERE tenant_id=$1 "
                    "AND (code LIKE 'channel:%' OR code LIKE 'risk:%')",
                    graph["tenant"],
                )
                == 0
            )
        finally:
            await owner.close()
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        owner = await asyncpg.connect(owner_dsn)
        try:
            cut_over = await owner.fetchrow(
                "SELECT status,invalidation_reason,readiness_manifest,readiness_code_item_id "
                "FROM launch_releases WHERE id=$1",
                release_id,
            )
            assert cut_over and cut_over["status"] == "invalidated"
            assert cut_over["invalidation_reason"] == "authority_cutover"
            assert _as_json(cut_over["readiness_manifest"])["version"] == 2
            assert cut_over["readiness_code_item_id"] is None
            assert {
                (row["role_name"], row["code"])
                for row in await owner.fetch(
                    "SELECT role.name role_name,permission.code FROM role_permissions grant_row "
                    "JOIN roles role ON role.tenant_id=grant_row.tenant_id AND role.id=grant_row.role_id "
                    "JOIN permissions permission ON permission.tenant_id=grant_row.tenant_id "
                    "AND permission.id=grant_row.permission_id "
                    "WHERE grant_row.tenant_id=$1 AND permission.code LIKE 'channel:%'",
                    graph["tenant"],
                )
            } == {
                ("admin", "channel:read"),
                ("admin", "channel:manage"),
                ("admin", "channel:allocate"),
                ("admin", "channel:scope"),
            }
            assert {
                (row["role_name"], row["code"])
                for row in await owner.fetch(
                    "SELECT role.name role_name,permission.code FROM role_permissions grant_row "
                    "JOIN roles role ON role.tenant_id=grant_row.tenant_id AND role.id=grant_row.role_id "
                    "JOIN permissions permission ON permission.tenant_id=grant_row.tenant_id "
                    "AND permission.id=grant_row.permission_id "
                    "WHERE grant_row.tenant_id=$1 AND permission.code LIKE 'risk:%'",
                    graph["tenant"],
                )
            } == {
                ("admin", "risk:read"),
                ("admin", "risk:manage"),
                ("admin", "risk:evaluate"),
            }
        finally:
            await owner.close()
        _alembic(database_url, "check")
        await _resolve_owned_launch_roundtrip_markers(database_url)
        _alembic(database_url, "downgrade", "u6c4b5c6d7e8")
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval("SELECT to_regclass('public.launch_release_actions') IS NULL")
            assert not await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name='launch_releases' AND column_name='readiness_manifest')"
            )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM permissions WHERE tenant_id=$1 "
                    "AND (code LIKE 'channel:%' OR code LIKE 'risk:%')",
                    graph["tenant"],
                )
                == 0
            )
        finally:
            await owner.close()
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        _alembic(database_url, "check")
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if lease.created:
            try:
                await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                await _drop_database_with_retry(
                    lease.database_name,
                    ADMIN_DSN,
                    expected_owner_marker=lease.owner_marker,
                    allow_unmarked_created=not lease.marker_written,
                )
            except BaseException as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            if primary_error is not None:
                for cleanup_error in cleanup_errors:
                    primary_error.add_note(f"isolated launch-v2 database cleanup also failed: {cleanup_error!r}")
            else:
                raise cleanup_errors[0]


async def _runtime_call(
    conn: asyncpg.Connection, tenant_id: uuid.UUID, session_id: uuid.UUID, sql: str, *args: object
) -> asyncpg.Record | None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))
        await conn.execute("SELECT set_config('app.auth_session_id',$1,true)", str(session_id))
        return await conn.fetchrow(sql, *args)


async def _complete_and_deliver(
    owner: asyncpg.Connection,
    ids: dict[str, object],
    code_batch_id: uuid.UUID,
    *,
    historical_legacy_export: bool = False,
) -> None:
    await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", code_batch_id)
    if historical_legacy_export:
        export_id = uuid7()
        plaintext = b"public_id,status\nA,created\n"
        await owner.execute(
            "INSERT INTO export_logs "
            "(id,tenant_id,account_id,export_type,resource_id,file_name,row_count,status,code_batch_id,"
            "manifest_version,checksum_sha256,artifact_size_bytes,artifact_ciphertext,artifact_nonce,"
            "artifact_scheme,artifact_key_id,created_at,updated_at) "
            "VALUES($1,$2,$3,'code_csv',$4,'codes.csv',1,'completed',$4,1,$5,$6,$7,$8,"
            "'aes-256-gcm-v1','test-key-1',now(),now())",
            export_id,
            ids["tenant"],
            ids["account"],
            code_batch_id,
            hashlib.sha256(plaintext).hexdigest(),
            len(plaintext),
            b"c" * (len(plaintext) + 16),
            b"n" * 12,
        )
        await owner.execute(
            "UPDATE code_batches SET status='exported',export_manifest_id=$1,exported_at=now() WHERE id=$2",
            export_id,
            code_batch_id,
        )
        await owner.execute("UPDATE code_batches SET status='printing',printing_at=now() WHERE id=$1", code_batch_id)
        await owner.execute(
            "UPDATE code_batches SET status='delivered',delivered_at=now(),delivery_recipient='printer-a' WHERE id=$1",
            code_batch_id,
        )
    else:
        await _insert_manifest_and_deliver(owner, ids, code_batch_id, row_count=1)


async def _seed_ready_graph(
    owner: asyncpg.Connection,
    label: str,
    *,
    exact_public_id: str | None = None,
    historical_legacy_export: bool = False,
) -> dict[str, object]:
    ids = await _seed_catalog(owner, label)
    session_id = await _grant_page_permissions(owner, ids)
    receipt_id = await _insert_receipt(owner, ids)
    code_batch_id = await _insert_batch(owner, ids, receipt_id, quantity=1, expected_item_count=1)
    if exact_public_id is None:
        await _insert_items(owner, ids["tenant"], code_batch_id, 1)
    else:
        await owner.execute(
            "INSERT INTO code_items "
            "(id,tenant_id,code_batch_id,public_id,status,code_type,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,'created','single',now(),now())",
            uuid7(),
            ids["tenant"],
            code_batch_id,
            exact_public_id,
        )
    await _complete_and_deliver(owner, ids, code_batch_id, historical_legacy_export=historical_legacy_export)
    async with owner.transaction():
        public_id = await owner.fetchval(
            "UPDATE code_items SET status='activated',activated_at=now() "
            "WHERE tenant_id=$1 AND code_batch_id=$2 RETURNING public_id",
            ids["tenant"],
            code_batch_id,
        )
        await owner.execute(
            "UPDATE code_batches SET status='activated' WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            code_batch_id,
        )
        await owner.execute("SET CONSTRAINTS trg_enforce_code_item_parent_final_state IMMEDIATE")
    template_id, version_id, campaign_id = (uuid7() for _ in range(3))
    await owner.execute(
        "INSERT INTO page_templates(id,tenant_id,product_id,name,template_type,status,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'product_info','active',now(),now())",
        template_id,
        ids["tenant"],
        ids["product"],
        f"launch page {label}",
    )
    await owner.execute(
        "INSERT INTO page_versions(id,tenant_id,page_template_id,version,config_json,status,created_by,"
        "created_by_tenant_id,published_at,created_at,updated_at) "
        "VALUES($1,$2,$3,1,$4,'published',$5,$2,now(),now(),now())",
        version_id,
        ids["tenant"],
        template_id,
        json.dumps({"dsl": {"title": label}}),
        ids["account"],
    )
    await owner.execute(
        "INSERT INTO campaigns(id,tenant_id,name,campaign_type,status,product_id,start_at,end_at,rules_json,"
        "created_at,updated_at) VALUES($1,$2,$3,'coupon','active',$4,now()-interval '1 day',"
        "now()+interval '1 day','{}',now(),now())",
        campaign_id,
        ids["tenant"],
        f"launch campaign {label}",
        ids["product"],
    )
    scan_event_id = uuid7()
    await owner.execute(
        "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,is_first_scan,is_valid_visit,created_at,updated_at) "
        "VALUES($1,$2,$3,now(),true,true,now(),now())",
        scan_event_id,
        ids["tenant"],
        public_id,
    )
    return ids | {
        "session": session_id,
        "code_batch": code_batch_id,
        "public_id": public_id,
        "template": template_id,
        "version": version_id,
        "campaign": campaign_id,
        "scan_event": scan_event_id,
    }


async def _create_release(
    runtime: asyncpg.Connection,
    graph: dict[str, object],
    release_id: uuid.UUID,
    key: str,
) -> asyncpg.Record:
    row = await _runtime_call(
        runtime,
        graph["tenant"],
        graph["session"],
        "SELECT * FROM create_launch_release($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        graph["tenant"],
        graph["session"],
        uuid7(),
        uuid7(),
        release_id,
        graph["version"],
        graph["campaign"],
        graph["code_batch"],
        key,
    )
    assert row is not None
    return row


async def _launch_release(
    runtime: asyncpg.Connection,
    graph: dict[str, object],
    release_id: uuid.UUID,
    label: str,
) -> asyncpg.Record:
    created = await _create_release(runtime, graph, release_id, f"create-{label}-{uuid7()}")
    confirmed = await _runtime_call(
        runtime,
        graph["tenant"],
        graph["session"],
        "SELECT * FROM confirm_launch_release($1,$2,$3,$4,$5,$6,$7)",
        graph["tenant"],
        graph["session"],
        uuid7(),
        uuid7(),
        release_id,
        created["content_digest"],
        f"confirm-{label}-{uuid7()}",
    )
    assert confirmed is not None
    launched = await _runtime_call(
        runtime,
        graph["tenant"],
        graph["session"],
        "SELECT * FROM launch_launch_release($1,$2,$3,$4,$5,$6,$7)",
        graph["tenant"],
        graph["session"],
        uuid7(),
        uuid7(),
        release_id,
        confirmed["content_digest"],
        f"launch-{label}-{uuid7()}",
    )
    assert launched is not None and launched["status"] == "live"
    return launched


async def test_launch_bound_claim_revalidates_live_release_and_stable_manifest(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    graph = await _seed_ready_graph(owner, "launch-bound-claim")
    benefit_id = uuid7()
    other_campaign_id = uuid7()
    other_benefit_id = uuid7()
    try:
        await owner.execute(
            "INSERT INTO benefits(id,tenant_id,campaign_id,name,benefit_type,config_json,stock_total,stock_used,"
            "per_person_limit,status,created_at,updated_at) "
            "VALUES($1,$2,$3,'bound benefit','external_link',$4,2,0,1,'active',now(),now())",
            benefit_id,
            graph["tenant"],
            graph["campaign"],
            json.dumps({"url": "https://example.invalid/reward", "claimed_budget": 0}),
        )
        release_id = uuid7()
        launched = await _launch_release(runtime, graph, release_id, "first")
        digest = launched["content_digest"]
        claim_sql = "SELECT * FROM claim_campaign_benefit($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)"

        first = await _runtime_call(
            runtime,
            graph["tenant"],
            graph["session"],
            claim_sql,
            graph["tenant"],
            uuid7(),
            benefit_id,
            graph["scan_event"],
            graph["product"],
            graph["public_id"],
            "consumer-one",
            f"claim-{uuid7()}",
            release_id,
            graph["campaign"],
            digest,
        )
        assert first is not None and first["created"] is True and first["stock_used"] == 1
        assert await _runtime_call(
            runtime,
            graph["tenant"],
            graph["session"],
            "SELECT * FROM resolve_current_launch_release($1,$2)",
            graph["tenant"],
            graph["public_id"],
        )
        second = await _runtime_call(
            runtime,
            graph["tenant"],
            graph["session"],
            claim_sql,
            graph["tenant"],
            uuid7(),
            benefit_id,
            graph["scan_event"],
            graph["product"],
            graph["public_id"],
            "consumer-two",
            f"claim-{uuid7()}",
            release_id,
            graph["campaign"],
            digest,
        )
        assert second is not None and second["created"] is True and second["stock_used"] == 2
        with pytest.raises(asyncpg.PostgresError) as exhausted:
            await _runtime_call(
                runtime,
                graph["tenant"],
                graph["session"],
                claim_sql,
                graph["tenant"],
                uuid7(),
                benefit_id,
                graph["scan_event"],
                graph["product"],
                graph["public_id"],
                "consumer-exhausted",
                f"claim-{uuid7()}",
                release_id,
                graph["campaign"],
                digest,
            )
        assert exhausted.value.sqlstate == "23514"
        assert await owner.fetchval("SELECT stock_used FROM benefits WHERE id=$1", benefit_id) == 2
        manifest = _as_json(
            await owner.fetchval("SELECT readiness_manifest FROM launch_releases WHERE id=$1", release_id)
        )
        assert "stock_used" not in manifest["benefits"][0]
        assert "claimed_budget" not in manifest["benefits"][0]["config"]

        await owner.execute(
            "INSERT INTO campaigns(id,tenant_id,name,campaign_type,status,product_id,start_at,end_at,rules_json,"
            "created_at,updated_at) VALUES($1,$2,'other campaign','coupon','active',$3,"
            "now()-interval '1 day',now()+interval '1 day','{}',now(),now())",
            other_campaign_id,
            graph["tenant"],
            graph["product"],
        )
        await owner.execute(
            "INSERT INTO benefits(id,tenant_id,campaign_id,name,benefit_type,config_json,stock_total,stock_used,"
            "per_person_limit,status,created_at,updated_at) "
            "VALUES($1,$2,$3,'other benefit','external_link',$4,10,0,1,'active',now(),now())",
            other_benefit_id,
            graph["tenant"],
            other_campaign_id,
            json.dumps({"url": "https://example.invalid/other"}),
        )
        before = await owner.fetchval("SELECT count(*) FROM benefit_claims WHERE tenant_id=$1", graph["tenant"])
        wrong_campaign = await _runtime_call(
            runtime,
            graph["tenant"],
            graph["session"],
            claim_sql,
            graph["tenant"],
            uuid7(),
            other_benefit_id,
            graph["scan_event"],
            graph["product"],
            graph["public_id"],
            "consumer-three",
            f"claim-{uuid7()}",
            release_id,
            graph["campaign"],
            digest,
        )
        assert wrong_campaign is not None and dict(wrong_campaign) == {
            "outcome": "benefit_not_in_launch_release",
            "claim_id": None,
            "campaign_id": graph["campaign"],
            "stock_used": None,
            "outbox_id": None,
            "created": False,
            "reserved_amount": None,
            "reservation_status": None,
        }
        assert await owner.fetchval("SELECT count(*) FROM benefit_claims WHERE tenant_id=$1", graph["tenant"]) == before

        await _runtime_call(
            runtime,
            graph["tenant"],
            graph["session"],
            "SELECT * FROM suspend_launch_release($1,$2,$3,$4,$5,$6,$7)",
            graph["tenant"],
            graph["session"],
            uuid7(),
            uuid7(),
            release_id,
            "operator pause after token issue",
            f"suspend-{uuid7()}",
        )
        suspended = await _runtime_call(
            runtime,
            graph["tenant"],
            graph["session"],
            claim_sql,
            graph["tenant"],
            uuid7(),
            benefit_id,
            graph["scan_event"],
            graph["product"],
            graph["public_id"],
            "consumer-suspended",
            f"claim-{uuid7()}",
            release_id,
            graph["campaign"],
            digest,
        )
        assert suspended is not None and suspended["outcome"] == "launch_release_not_current"
        assert suspended["created"] is False and suspended["claim_id"] is None
        assert await owner.fetchval("SELECT count(*) FROM benefit_claims WHERE tenant_id=$1", graph["tenant"]) == before
        resumed = await _runtime_call(
            runtime,
            graph["tenant"],
            graph["session"],
            "SELECT * FROM resume_launch_release($1,$2,$3,$4,$5,$6,$7)",
            graph["tenant"],
            graph["session"],
            uuid7(),
            uuid7(),
            release_id,
            digest,
            f"resume-{uuid7()}",
        )
        assert resumed is not None and resumed["status"] == "live"
        superseding_id = uuid7()
        superseding = await _launch_release(runtime, graph, superseding_id, "superseding")
        superseded = await _runtime_call(
            runtime,
            graph["tenant"],
            graph["session"],
            claim_sql,
            graph["tenant"],
            uuid7(),
            benefit_id,
            graph["scan_event"],
            graph["product"],
            graph["public_id"],
            "consumer-superseded",
            f"claim-{uuid7()}",
            release_id,
            graph["campaign"],
            digest,
        )
        assert superseded is not None and superseded["outcome"] == "launch_release_not_current"
        assert superseded["created"] is False and superseded["claim_id"] is None
        assert await owner.fetchval("SELECT count(*) FROM benefit_claims WHERE tenant_id=$1", graph["tenant"]) == before
        release_id = superseding_id
        digest = superseding["content_digest"]

        await owner.execute(
            "UPDATE benefits SET name='changed display name' WHERE tenant_id=$1 AND id=$2",
            graph["tenant"],
            benefit_id,
        )
        drifted = await _runtime_call(
            runtime,
            graph["tenant"],
            graph["session"],
            claim_sql,
            graph["tenant"],
            uuid7(),
            benefit_id,
            graph["scan_event"],
            graph["product"],
            graph["public_id"],
            "consumer-four",
            f"claim-{uuid7()}",
            release_id,
            graph["campaign"],
            digest,
        )
        assert drifted is not None and drifted["outcome"] == "launch_release_not_current"
        assert drifted["created"] is False and drifted["claim_id"] is None and drifted["outbox_id"] is None
        assert await owner.fetchval("SELECT count(*) FROM benefit_claims WHERE tenant_id=$1", graph["tenant"]) == before
        assert (
            await _runtime_call(
                runtime,
                graph["tenant"],
                graph["session"],
                "SELECT * FROM resolve_current_launch_release($1,$2)",
                graph["tenant"],
                graph["public_id"],
            )
            is None
        )
        assert await owner.fetchval("SELECT status FROM launch_releases WHERE id=$1", release_id) == "invalidated"
        invalidated = await _runtime_call(
            runtime,
            graph["tenant"],
            graph["session"],
            claim_sql,
            graph["tenant"],
            uuid7(),
            benefit_id,
            graph["scan_event"],
            graph["product"],
            graph["public_id"],
            "consumer-invalidated",
            f"claim-{uuid7()}",
            release_id,
            graph["campaign"],
            digest,
        )
        assert invalidated is not None and invalidated["outcome"] == "launch_release_not_current"
        assert invalidated["created"] is False and invalidated["claim_id"] is None
        assert await owner.fetchval("SELECT count(*) FROM benefit_claims WHERE tenant_id=$1", graph["tenant"]) == before
    finally:
        await runtime.close()
        await owner.close()


async def test_launch_actor_revocation_acting_and_same_batch_concurrency(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime_a = await asyncpg.connect(runtime_dsn)
    runtime_b = await asyncpg.connect(runtime_dsn)
    graph = await _seed_ready_graph(owner, "launch-concurrency")
    agency = await _seed_catalog(owner, "launch-agency")
    await owner.execute("UPDATE tenants SET tenant_type='agency' WHERE id=$1", agency["tenant"])
    agency_session = await _grant_page_permissions(owner, agency)
    authorization_id = uuid7()
    await owner.execute(
        "INSERT INTO agency_authorizations(id,agency_tenant_id,client_tenant_id,scope,status,granted_by,"
        "granted_at,created_at,updated_at) VALUES($1,$2,$3,'[\"pages\",\"release:execute\"]','active',"
        "$4,now(),now(),now())",
        authorization_id,
        agency["tenant"],
        graph["tenant"],
        graph["account"],
    )
    try:
        permission_id = await owner.fetchval(
            "SELECT permission.id FROM permissions permission JOIN role_permissions role_permission "
            "ON role_permission.tenant_id=permission.tenant_id AND role_permission.permission_id=permission.id "
            "WHERE permission.tenant_id=$1 AND permission.code='page:create' LIMIT 1",
            graph["tenant"],
        )
        await owner.execute(
            "DELETE FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3",
            graph["tenant"],
            graph["admin_role"],
            permission_id,
        )
        with pytest.raises(asyncpg.PostgresError) as revoked_permission:
            await _create_release(runtime_a, graph, uuid7(), f"revoked-{uuid7()}")
        assert revoked_permission.value.sqlstate == "42501"
        assert await owner.fetchval("SELECT count(*) FROM launch_releases WHERE tenant_id=$1", graph["tenant"]) == 0
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            graph["tenant"],
            graph["admin_role"],
            permission_id,
        )

        acting_graph = dict(graph) | {"session": agency_session}
        acting_release = uuid7()
        acting_created = await _create_release(runtime_a, acting_graph, acting_release, f"acting-{uuid7()}")
        assert acting_created["status"] == "pending_confirmation"
        assert await owner.fetchval(
            "SELECT created_by_tenant_id=$1 AND created_by=$2 FROM launch_releases WHERE id=$3",
            agency["tenant"],
            agency["account"],
            acting_release,
        )
        await owner.execute(
            "UPDATE agency_authorizations SET status='revoked',revoked_at=now(),updated_at=now() WHERE id=$1",
            authorization_id,
        )
        with pytest.raises(asyncpg.PostgresError) as revoked_acting:
            await _create_release(runtime_a, acting_graph, uuid7(), f"acting-revoked-{uuid7()}")
        assert revoked_acting.value.sqlstate == "42501"

        first_id, second_id = uuid7(), uuid7()
        first = await _create_release(runtime_a, graph, first_id, f"create-{uuid7()}")
        second = await _create_release(runtime_b, graph, second_id, f"create-{uuid7()}")
        for runtime, release_id, created in (
            (runtime_a, first_id, first),
            (runtime_b, second_id, second),
        ):
            confirmed = await _runtime_call(
                runtime,
                graph["tenant"],
                graph["session"],
                "SELECT * FROM confirm_launch_release($1,$2,$3,$4,$5,$6,$7)",
                graph["tenant"],
                graph["session"],
                uuid7(),
                uuid7(),
                release_id,
                created["content_digest"],
                f"confirm-{uuid7()}",
            )
            assert confirmed and confirmed["status"] == "confirmed"

        lock_transaction = runtime_a.transaction()
        await lock_transaction.start()
        await runtime_a.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('launch-code-batch:'||$1::text||':'||$2::text,0))",
            str(graph["tenant"]),
            str(graph["code_batch"]),
        )
        try:
            with pytest.raises(asyncpg.PostgresError) as concurrent_loser:
                await _runtime_call(
                    runtime_b,
                    graph["tenant"],
                    graph["session"],
                    "SELECT * FROM launch_launch_release($1,$2,$3,$4,$5,$6,$7)",
                    graph["tenant"],
                    graph["session"],
                    uuid7(),
                    uuid7(),
                    second_id,
                    second["content_digest"],
                    f"launch-{second_id}",
                )
            assert concurrent_loser.value.sqlstate == "55P03"
        finally:
            await lock_transaction.rollback()
        winner = await _runtime_call(
            runtime_a,
            graph["tenant"],
            graph["session"],
            "SELECT * FROM launch_launch_release($1,$2,$3,$4,$5,$6,$7)",
            graph["tenant"],
            graph["session"],
            uuid7(),
            uuid7(),
            first_id,
            first["content_digest"],
            f"launch-{first_id}",
        )
        assert winner and winner["status"] == "live"
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM launch_releases WHERE tenant_id=$1 AND code_batch_id=$2 AND status='live'",
                graph["tenant"],
                graph["code_batch"],
            )
            == 1
        )

        conflict_key = f"conflict-{uuid7()}"
        await _create_release(runtime_a, graph, uuid7(), conflict_key)
        conflicting = dict(graph) | {"campaign": uuid7()}
        with pytest.raises(asyncpg.PostgresError) as conflict:
            await _create_release(runtime_a, conflicting, uuid7(), conflict_key)
        assert conflict.value.sqlstate == "22023"
    finally:
        await runtime_b.close()
        await runtime_a.close()
        await owner.close()


async def test_launch_authority_catalog_lifecycle_and_resolver_fail_closed(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    ids = await _seed_catalog(owner, "launch-authority")
    try:
        repository_head = _sole_head_with_launch_ancestry()
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == repository_head
        assert await owner.fetchval("SELECT to_regclass('public.launch_releases') IS NOT NULL")
        assert await owner.fetchval("SELECT to_regclass('public.launch_release_actions') IS NOT NULL")
        for signature in _PUBLIC_SIGNATURES:
            assert await owner.fetchval(
                "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", f"public.{signature}"
            )
            assert not await owner.fetchval(
                "SELECT has_function_privilege('public',$1,'EXECUTE')", f"public.{signature}"
            )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',"
            "'public.mutate_launch_release(uuid,uuid,uuid,uuid,uuid,text,uuid,uuid,uuid,text,text,text)',"
            "'EXECUTE')"
        )
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')",
            f"public.{_BOUND_CLAIM_SIGNATURE}",
        )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')",
            f"public.{_LEGACY_CLAIM_SIGNATURE}",
        )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('public',$1,'EXECUTE')",
            f"public.{_BOUND_CLAIM_SIGNATURE}",
        )
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')",
            f"public.{_OBSERVATION_SIGNATURE}",
        )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('public',$1,'EXECUTE')",
            f"public.{_OBSERVATION_SIGNATURE}",
        )
        for table in ("launch_releases", "launch_release_actions"):
            assert await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,'SELECT')", table)
            for privilege in ("INSERT", "UPDATE", "DELETE"):
                assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,$2)", table, privilege)
            assert await owner.fetchval(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=$1::regclass", table
            )

        session_id = await _grant_page_permissions(owner, ids)
        receipt_id = await _insert_receipt(owner, ids)
        code_batch_id = await _insert_batch(owner, ids, receipt_id, quantity=1, expected_item_count=1)
        await _insert_items(owner, ids["tenant"], code_batch_id, 1)
        await _complete_and_deliver(owner, ids, code_batch_id)
        async with owner.transaction():
            public_id = await owner.fetchval(
                "UPDATE code_items SET status='activated',activated_at=now() "
                "WHERE tenant_id=$1 AND code_batch_id=$2 RETURNING public_id",
                ids["tenant"],
                code_batch_id,
            )
            await owner.execute(
                "UPDATE code_batches SET status='activated' WHERE tenant_id=$1 AND id=$2",
                ids["tenant"],
                code_batch_id,
            )
            await owner.execute("SET CONSTRAINTS trg_enforce_code_item_parent_final_state IMMEDIATE")
        template_id, version_id, campaign_id, connector_id, benefit_id = (uuid7() for _ in range(5))
        await owner.execute(
            "INSERT INTO page_templates(id,tenant_id,product_id,name,template_type,status,created_at,updated_at) "
            "VALUES($1,$2,$3,'launch page','product_info','active',now(),now())",
            template_id,
            ids["tenant"],
            ids["product"],
        )
        await owner.execute(
            "INSERT INTO page_versions(id,tenant_id,page_template_id,version,config_json,status,created_by,"
            "created_by_tenant_id,published_at,created_at,updated_at) "
            "VALUES($1,$2,$3,1,$4,'published',$5,$2,now(),now(),now())",
            version_id,
            ids["tenant"],
            template_id,
            json.dumps({"dsl": {"title": "ready"}}),
            ids["account"],
        )
        await owner.execute(
            "INSERT INTO campaigns(id,tenant_id,name,campaign_type,status,product_id,start_at,end_at,rules_json,"
            "created_at,updated_at) VALUES($1,$2,'launch campaign','coupon','active',$3,now()-interval '1 day',"
            "now()+interval '1 day',$4,now(),now())",
            campaign_id,
            ids["tenant"],
            ids["product"],
            json.dumps({"claim_limit_count": 1}),
        )
        await owner.execute(
            "INSERT INTO connectors(id,tenant_id,name,connector_type,config,enabled,created_at,updated_at) "
            "VALUES($1,$2,'launch connector','generic_http',$3,true,now(),now())",
            connector_id,
            ids["tenant"],
            json.dumps({"api_url": "https://example.invalid"}),
        )
        await owner.execute(
            "INSERT INTO benefits(id,tenant_id,campaign_id,name,benefit_type,config_json,connector_id,stock_total,"
            "stock_used,per_person_limit,status,created_at,updated_at) "
            "VALUES($1,$2,$3,'launch benefit','external_link',$4,$5,10,0,1,'active',now(),now())",
            benefit_id,
            ids["tenant"],
            campaign_id,
            json.dumps({"url": "https://example.invalid/reward"}),
            connector_id,
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM scan_events WHERE tenant_id=$1 AND public_id=$2",
                ids["tenant"],
                public_id,
            )
            == 0
        )

        release_id = uuid7()
        create_key = f"launch-create-{uuid7()}"
        created = await _runtime_call(
            runtime,
            ids["tenant"],
            session_id,
            "SELECT * FROM create_launch_release($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            ids["tenant"],
            session_id,
            uuid7(),
            uuid7(),
            release_id,
            version_id,
            campaign_id,
            code_batch_id,
            create_key,
        )
        assert created and created["status"] == "pending_confirmation" and created["ready"] is True
        replay = await _runtime_call(
            runtime,
            ids["tenant"],
            session_id,
            "SELECT * FROM create_launch_release($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            ids["tenant"],
            session_id,
            uuid7(),
            uuid7(),
            uuid7(),
            version_id,
            campaign_id,
            code_batch_id,
            create_key,
        )
        assert replay and replay["release_id"] == release_id and replay["replayed"] is True
        confirmed = await _runtime_call(
            runtime,
            ids["tenant"],
            session_id,
            "SELECT * FROM confirm_launch_release($1,$2,$3,$4,$5,$6,$7)",
            ids["tenant"],
            session_id,
            uuid7(),
            uuid7(),
            release_id,
            created["content_digest"],
            f"confirm-{uuid7()}",
        )
        assert confirmed and confirmed["status"] == "confirmed"
        with pytest.raises(asyncpg.PostgresError) as prelaunch_observation:
            await _runtime_call(
                runtime,
                ids["tenant"],
                session_id,
                "SELECT * FROM record_launch_release_valid_scan($1,$2,$3,$4)",
                ids["tenant"],
                release_id,
                uuid7(),
                datetime.now(UTC),
            )
        assert prelaunch_observation.value.sqlstate == "23514"
        launched = await _runtime_call(
            runtime,
            ids["tenant"],
            session_id,
            "SELECT * FROM launch_launch_release($1,$2,$3,$4,$5,$6,$7)",
            ids["tenant"],
            session_id,
            uuid7(),
            uuid7(),
            release_id,
            confirmed["content_digest"],
            f"launch-{uuid7()}",
        )
        assert launched and launched["status"] == "live"
        event_id = uuid7()
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            scan = await runtime.fetchrow(
                "SELECT * FROM record_public_code_scan($1,$2,$3,NULL,$4,$5,NULL)",
                ids["tenant"],
                public_id,
                event_id,
                "Mozilla/5.0",
                "browser",
            )
            assert scan and scan["is_valid_visit"] is True
            observed = await runtime.fetchrow(
                "SELECT * FROM record_launch_release_valid_scan($1,$2,$3,$4)",
                ids["tenant"],
                release_id,
                event_id,
                scan["scan_time"],
            )
            assert observed and observed["observation_status"] == "observed"
            assert observed["replayed"] is False
        replayed_observation = await _runtime_call(
            runtime,
            ids["tenant"],
            session_id,
            "SELECT * FROM record_launch_release_valid_scan($1,$2,$3,$4)",
            ids["tenant"],
            release_id,
            event_id,
            scan["scan_time"],
        )
        assert replayed_observation and replayed_observation["replayed"] is True
        second_event_id = uuid7()
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            second_scan = await runtime.fetchrow(
                "SELECT * FROM record_public_code_scan($1,$2,$3,NULL,$4,$5,NULL)",
                ids["tenant"],
                public_id,
                second_event_id,
                "Mozilla/5.0",
                "browser",
            )
            assert second_scan and second_scan["is_valid_visit"] is True
            already_observed = await runtime.fetchrow(
                "SELECT * FROM record_launch_release_valid_scan($1,$2,$3,$4)",
                ids["tenant"],
                release_id,
                second_event_id,
                second_scan["scan_time"],
            )
            assert already_observed and already_observed["observation_status"] == "already_observed"
            assert already_observed["recorded_at"] == scan["scan_time"]
            assert already_observed["replayed"] is True
        with pytest.raises(asyncpg.PostgresError) as cross_tenant_observation:
            await _runtime_call(
                runtime,
                ids["tenant"],
                session_id,
                "SELECT * FROM record_launch_release_valid_scan($1,$2,$3,$4)",
                uuid7(),
                release_id,
                event_id,
                scan["scan_time"],
            )
        assert cross_tenant_observation.value.sqlstate == "42501"
        invalid_event_id = uuid7()
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            invalid_scan = await runtime.fetchrow(
                "SELECT * FROM record_public_code_scan($1,$2,$3,NULL,$4,$5,NULL)",
                ids["tenant"],
                public_id,
                invalid_event_id,
                "curl/8.0",
                "browser",
            )
            assert invalid_scan and invalid_scan["is_valid_visit"] is False
        with pytest.raises(asyncpg.PostgresError) as invalid_observation:
            await _runtime_call(
                runtime,
                ids["tenant"],
                session_id,
                "SELECT * FROM record_launch_release_valid_scan($1,$2,$3,$4)",
                ids["tenant"],
                release_id,
                invalid_event_id,
                invalid_scan["scan_time"],
            )
        assert invalid_observation.value.sqlstate == "23514"
        resolved = await _runtime_call(
            runtime,
            ids["tenant"],
            session_id,
            "SELECT * FROM resolve_current_launch_release($1,$2)",
            ids["tenant"],
            public_id,
        )
        assert resolved and resolved["page_version_id"] == version_id and resolved["campaign_id"] == campaign_id
        manifest = _as_json(
            await owner.fetchval("SELECT readiness_manifest FROM launch_releases WHERE id=$1", release_id)
        )
        assert isinstance(manifest, dict)
        assert manifest["version"] == 3
        assert manifest["page"]["config"] == {"dsl": {"title": "ready"}}
        assert manifest["campaign"]["rules"] == {"claim_limit_count": 1}
        assert manifest["campaign"]["name"] == "launch campaign"
        assert manifest["benefits"][0]["name"] == "launch benefit"
        assert "stock_used" not in manifest["benefits"][0]
        assert "claimed_budget" not in manifest["benefits"][0]["config"]
        assert manifest["benefits"][0]["connector_id"] == str(connector_id)
        assert manifest["code_batch"]["id"] == str(code_batch_id)
        assert manifest["sample_code"] == {
            "id": str(await owner.fetchval("SELECT id FROM code_items WHERE public_id=$1", public_id)),
            "public_id": public_id,
            "status": "activated",
            "code_type": "single",
            "code_batch_id": str(code_batch_id),
        }
        assert "scan_evidence" not in manifest
        assert manifest["production_batch"]["id"] == str(ids["production_batch"])
        assert "takeover" in manifest
        observation = await owner.fetchrow(
            "SELECT readiness_code_item_id,first_valid_scan_event_id,first_valid_scan_time "
            "FROM launch_releases WHERE id=$1",
            release_id,
        )
        assert observation and observation["first_valid_scan_event_id"] == event_id
        assert observation["first_valid_scan_time"] == scan["scan_time"]
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM platform_audit_log WHERE action='launch_release_first_valid_scan_observed' "
                "AND resource=$1 AND details->>'scan_event_id'=$2",
                f"launch_release:{release_id}",
                str(event_id),
            )
            == 1
        )

        await owner.execute(
            "UPDATE campaigns SET rules_json=$1 WHERE tenant_id=$2 AND id=$3",
            json.dumps({"changed": True}),
            ids["tenant"],
            campaign_id,
        )
        stale = await _runtime_call(
            runtime,
            ids["tenant"],
            session_id,
            "SELECT * FROM resolve_current_launch_release($1,$2)",
            ids["tenant"],
            public_id,
        )
        assert stale is None
        assert await owner.fetchval("SELECT status FROM launch_releases WHERE id=$1", release_id) == "invalidated"
    finally:
        await runtime.close()
        await owner.close()


async def test_launch_real_asgi_split_role_lifecycle_and_resolver(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core import database
    from app.main import app
    from app.middleware.rate_limit import rate_limiter
    from app.services.redis_cache import AsyncRedisCache
    from app.utils.security import create_access_token

    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    graph = await _seed_ready_graph(owner, "launch-real-asgi", exact_public_id=generate_public_id())
    runtime_engine = create_async_engine(runtime_url)
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)

    async def cache_is_not_revoked(self, key: str) -> bool:
        return False

    monkeypatch.setattr(AsyncRedisCache, "is_token_revoked", cache_is_not_revoked)
    rate_limiter._cache._mem_store.clear()  # type: ignore[attr-defined]
    token = create_access_token(
        str(graph["tenant"]),
        str(graph["account"]),
        "admin",
        "brand",
        extra={"sid": str(graph["session"]), "auth_version": 0},
    )
    headers = {"Authorization": f"Bearer {token}"}
    create_key = f"asgi-create-{uuid7()}"

    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            prelaunch = await client.get(
                f"/c/{graph['public_id']}",
                headers={"Accept": "application/json", "X-Forwarded-For": "203.0.113.70"},
            )
            assert prelaunch.status_code == 200, prelaunch.text
            assert prelaunch.json()["scan_token"] is None
            assert prelaunch.json()["scan_info"]["paused_reason"] == "launch_not_live"
            assert "campaign" not in prelaunch.json()

            created = await client.post(
                "/api/v1/launch-releases",
                json={
                    "page_version_id": str(graph["version"]),
                    "campaign_id": str(graph["campaign"]),
                    "code_batch_id": str(graph["code_batch"]),
                    "idempotency_key": create_key,
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text
            release_id = uuid.UUID(created.json()["id"])
            assert created.json()["status"] == "pending_confirmation"
            assert created.json()["ready"] is True

            conflicting_create = await client.post(
                "/api/v1/launch-releases",
                json={
                    "page_version_id": str(graph["version"]),
                    "campaign_id": str(uuid7()),
                    "code_batch_id": str(graph["code_batch"]),
                    "idempotency_key": create_key,
                },
                headers=headers,
            )
            assert conflicting_create.status_code == 422, conflicting_create.text

            confirmed = await client.post(
                f"/api/v1/launch-releases/{release_id}/confirm",
                json={"idempotency_key": f"asgi-confirm-{uuid7()}"},
                headers=headers,
            )
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["status"] == "confirmed"
            launched = await client.post(
                f"/api/v1/launch-releases/{release_id}/launch",
                json={"idempotency_key": f"asgi-launch-{uuid7()}"},
                headers=headers,
            )
            assert launched.status_code == 200, launched.text
            assert launched.json()["status"] == "live"

            resolved = await client.get(
                f"/c/{graph['public_id']}",
                headers={"Accept": "application/json", "X-Forwarded-For": "203.0.113.71"},
            )
            assert resolved.status_code == 200, resolved.text
            assert resolved.json()["scan_token"]
            assert resolved.json()["campaign"]["id"] == str(graph["campaign"])
            assert resolved.json()["page_config"] == {"dsl": {"title": "launch-real-asgi"}}
            assert await owner.fetchval(
                "SELECT id=$1 FROM launch_releases WHERE tenant_id=$2 AND code_batch_id=$3 AND status='live'",
                release_id,
                graph["tenant"],
                graph["code_batch"],
            )

            suspended = await client.post(
                f"/api/v1/launch-releases/{release_id}/suspend",
                json={"reason": "controlled pause", "idempotency_key": f"asgi-suspend-{uuid7()}"},
                headers=headers,
            )
            assert suspended.status_code == 200, suspended.text
            assert suspended.json()["status"] == "suspended"
            paused = await client.get(
                f"/c/{graph['public_id']}",
                headers={"Accept": "application/json", "X-Forwarded-For": "203.0.113.72"},
            )
            assert paused.status_code == 200, paused.text
            assert paused.json()["scan_token"] is None
            assert paused.json()["scan_info"]["paused_reason"] == "launch_not_live"
            assert "campaign" not in paused.json()

            resumed = await client.post(
                f"/api/v1/launch-releases/{release_id}/resume",
                json={"idempotency_key": f"asgi-resume-{uuid7()}"},
                headers=headers,
            )
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["status"] == "live"
            live_again = await client.get(
                f"/c/{graph['public_id']}",
                headers={"Accept": "application/json", "X-Forwarded-For": "203.0.113.74"},
            )
            assert live_again.status_code == 200, live_again.text
            assert live_again.json()["scan_token"]
            assert live_again.json()["campaign"]["id"] == str(graph["campaign"])
            assert live_again.json()["page_config"] == {"dsl": {"title": "launch-real-asgi"}}

            await owner.execute(
                "UPDATE campaigns SET rules_json=$1 WHERE tenant_id=$2 AND id=$3",
                json.dumps({"dependency": "drifted"}),
                graph["tenant"],
                graph["campaign"],
            )
            stale = await client.get(
                f"/c/{graph['public_id']}",
                headers={"Accept": "application/json", "X-Forwarded-For": "203.0.113.73"},
            )
            assert stale.status_code == 200, stale.text
            assert stale.json()["scan_token"] is None
            assert stale.json()["scan_info"]["paused_reason"] == "launch_not_live"
            assert await owner.fetchval("SELECT status FROM launch_releases WHERE id=$1", release_id) == "invalidated"

            cannot_resume = await client.post(
                f"/api/v1/launch-releases/{release_id}/resume",
                json={"idempotency_key": f"asgi-resume-stale-{uuid7()}"},
                headers=headers,
            )
            assert cannot_resume.status_code == 409, cannot_resume.text
    finally:
        app.dependency_overrides.clear()
        await runtime_engine.dispose()
        await owner_engine.dispose()
        await owner.close()
