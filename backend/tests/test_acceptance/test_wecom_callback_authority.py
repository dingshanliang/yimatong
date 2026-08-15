"""Database authority for cryptographically verified official WeCom events."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from tests.test_acceptance.test_campaign_authority_rollout import _isolated_campaign_rollout_database
from tests.test_acceptance.test_code_batch_delivery_contract import _seed_catalog
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

SIGNATURE = "apply_verified_wecom_contact_event(uuid,uuid,uuid,text,text,text,text,text,timestamptz,bigint,text)"


async def _callback(database_url: str) -> asyncpg.Connection:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn.replace("yimatong:yimatong@", "yimatong_callback:yimatong_callback@"))


async def _runtime(database_url: str) -> asyncpg.Connection:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))


async def _apply(
    conn: asyncpg.Connection,
    tenant_id: uuid.UUID,
    connector_id: uuid.UUID,
    change_type: str,
    external_userid: str,
    state: str | None,
    event_time: datetime,
    sequence: int,
    payload: str,
    user_id: str = "sales-user",
) -> asyncpg.Record:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))
        return await conn.fetchrow(
            f"SELECT * FROM {SIGNATURE.split('(')[0]}($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            tenant_id,
            uuid.uuid4(),
            connector_id,
            change_type,
            external_userid,
            user_id,
            state,
            "union-safe",
            event_time,
            sequence,
            hashlib.sha256(payload.encode()).hexdigest(),
        )


async def _apply_with_busy_retry(*args, **kwargs) -> asyncpg.Record:
    for _ in range(20):
        try:
            return await _apply(*args, **kwargs)
        except asyncpg.LockNotAvailableError:
            await asyncio.sleep(0.01)
    return await _apply(*args, **kwargs)


async def test_verified_wecom_events_are_durable_monotonic_and_function_only(migrated_pg_url: str) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        owner = await asyncpg.connect(owner_dsn)
        callback = await _callback(database_url)
        runtime = await _runtime(database_url)
        try:
            ids = await _seed_catalog(owner, "verified-wecom-authority")
            connector_id, way_id = uuid.uuid4(), uuid.uuid4()
            state = f"state-{uuid.uuid4().hex}"
            external_userid = f"external-{uuid.uuid4().hex}"
            await owner.execute(
                "INSERT INTO connectors(id,tenant_id,name,connector_type,config,enabled,created_at,updated_at) "
                "VALUES($1,$2,'official WeCom','wecom_customer_contact','{}',true,now(),now())",
                connector_id,
                ids["tenant"],
            )
            await owner.execute(
                "INSERT INTO wecom_contact_ways(id,tenant_id,connector_id,state,user_ids,status,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,'[\"sales-user\",\"other-sales\"]','active',now(),now())",
                way_id,
                ids["tenant"],
                connector_id,
                state,
            )
            base = datetime.now(UTC).replace(microsecond=0)
            half = await _apply(
                callback,
                ids["tenant"],
                connector_id,
                "add_half_external_contact",
                external_userid,
                state,
                base,
                1,
                "half",
            )
            assert half["outcome"] == "recorded" and not half["confirmed"] and half["welcome_code_pending"]
            half_replay = await _apply(
                callback,
                ids["tenant"],
                connector_id,
                "add_half_external_contact",
                external_userid,
                state,
                base,
                1,
                "half",
            )
            assert half_replay["replayed"] and half_replay["receipt_id"] == half["receipt_id"]
            with pytest.raises(asyncpg.UniqueViolationError):
                await _apply(
                    callback,
                    ids["tenant"],
                    connector_id,
                    "add_half_external_contact",
                    external_userid,
                    state,
                    base,
                    1,
                    "changed-half",
                )
            added = await _apply(
                callback,
                ids["tenant"],
                connector_id,
                "add_external_contact",
                external_userid,
                state,
                base + timedelta(seconds=1),
                1,
                "add",
            )
            assert added["outcome"] == "recorded" and added["confirmed"] and added["current_status"] == "active"
            deleted = await _apply(
                callback,
                ids["tenant"],
                connector_id,
                "del_follow_user",
                external_userid,
                None,
                base + timedelta(seconds=2),
                1,
                "delete",
            )
            assert deleted["outcome"] == "recorded" and not deleted["confirmed"]
            stale = await _apply(
                callback,
                ids["tenant"],
                connector_id,
                "add_external_contact",
                external_userid,
                state,
                base + timedelta(seconds=1),
                2,
                "stale-add",
            )
            assert stale["outcome"] == "ignored_stale" and not stale["confirmed"]
            reactivated = await _apply(
                callback,
                ids["tenant"],
                connector_id,
                "add_external_contact",
                external_userid,
                state,
                base + timedelta(seconds=3),
                1,
                "new-add",
            )
            assert reactivated["confirmed"] and reactivated["current_status"] == "active"
            receipts_before = await owner.fetchval(
                "SELECT count(*) FROM wecom_callback_receipts WHERE tenant_id=$1", ids["tenant"]
            )
            contacts_before = await owner.fetchval(
                "SELECT count(*) FROM wecom_external_contacts WHERE tenant_id=$1", ids["tenant"]
            )
            for denied_user, error in (
                ("", asyncpg.InsufficientPrivilegeError),
                ("unknown-sales", asyncpg.ForeignKeyViolationError),
            ):
                with pytest.raises(error):
                    await _apply(
                        callback,
                        ids["tenant"],
                        connector_id,
                        "add_external_contact",
                        external_userid,
                        state,
                        base + timedelta(seconds=4),
                        1,
                        f"deny-{denied_user}",
                        user_id=denied_user,
                    )
            assert (
                await owner.fetchval("SELECT count(*) FROM wecom_callback_receipts WHERE tenant_id=$1", ids["tenant"])
                == receipts_before
            )
            assert (
                await owner.fetchval("SELECT count(*) FROM wecom_external_contacts WHERE tenant_id=$1", ids["tenant"])
                == contacts_before
            )
            other_member = await _apply(
                callback,
                ids["tenant"],
                connector_id,
                "add_external_contact",
                external_userid,
                state,
                base + timedelta(seconds=4),
                1,
                "other-member-add",
                user_id="other-sales",
            )
            assert other_member["confirmed"] and other_member["contact_id"] != reactivated["contact_id"]
            await _apply(
                callback,
                ids["tenant"],
                connector_id,
                "del_external_contact",
                external_userid,
                state,
                base + timedelta(seconds=5),
                1,
                "sales-delete-only",
            )
            member_states = await owner.fetch(
                "SELECT user_id,status FROM wecom_external_contacts WHERE tenant_id=$1 AND connector_id=$2 "
                "AND external_userid=$3 ORDER BY user_id",
                ids["tenant"],
                connector_id,
                external_userid,
            )
            assert [(row["user_id"], row["status"]) for row in member_states] == [
                ("other-sales", "active"),
                ("sales-user", "deleted"),
            ]
            callback_two = await _callback(database_url)
            concurrent_external = f"external-{uuid.uuid4().hex}"
            try:
                older_add, newer_delete = await asyncio.gather(
                    _apply_with_busy_retry(
                        callback,
                        ids["tenant"],
                        connector_id,
                        "add_external_contact",
                        concurrent_external,
                        state,
                        base + timedelta(seconds=10),
                        1,
                        "concurrent-add",
                    ),
                    _apply_with_busy_retry(
                        callback_two,
                        ids["tenant"],
                        connector_id,
                        "del_external_contact",
                        concurrent_external,
                        state,
                        base + timedelta(seconds=11),
                        1,
                        "concurrent-delete",
                    ),
                )
                assert {older_add["outcome"], newer_delete["outcome"]} <= {"recorded", "ignored_stale"}
                assert await owner.fetchval(
                    "SELECT bool_and(status='deleted') FROM wecom_external_contacts "
                    "WHERE tenant_id=$1 AND connector_id=$2 AND external_userid=$3",
                    ids["tenant"],
                    connector_id,
                    concurrent_external,
                )
            finally:
                await callback_two.close()
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM wecom_callback_receipts WHERE tenant_id=$1 AND connector_id=$2",
                    ids["tenant"],
                    connector_id,
                )
                == 9
            )
            lock_external = f"external-{uuid.uuid4().hex}"
            blocker = await _callback(database_url)
            try:
                await blocker.execute("BEGIN")
                await blocker.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
                lock_key = f"wecom-contact:{ids['tenant']}:{connector_id}:sales-user:{lock_external}"
                await blocker.fetchval("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", lock_key)
                receipts_before_lock = await owner.fetchval(
                    "SELECT count(*) FROM wecom_callback_receipts WHERE tenant_id=$1", ids["tenant"]
                )
                contacts_before_lock = await owner.fetchval(
                    "SELECT count(*) FROM wecom_external_contacts WHERE tenant_id=$1", ids["tenant"]
                )
                started = time.monotonic()
                with pytest.raises(asyncpg.LockNotAvailableError):
                    await _apply(
                        callback,
                        ids["tenant"],
                        connector_id,
                        "add_external_contact",
                        lock_external,
                        state,
                        base + timedelta(seconds=20),
                        1,
                        "lock-contention",
                    )
                assert time.monotonic() - started < 2
                assert (
                    await owner.fetchval(
                        "SELECT count(*) FROM wecom_callback_receipts WHERE tenant_id=$1", ids["tenant"]
                    )
                    == receipts_before_lock
                )
                assert (
                    await owner.fetchval(
                        "SELECT count(*) FROM wecom_external_contacts WHERE tenant_id=$1", ids["tenant"]
                    )
                    == contacts_before_lock
                )
                await blocker.execute("ROLLBACK")
                retried = await _apply(
                    callback,
                    ids["tenant"],
                    connector_id,
                    "add_external_contact",
                    lock_external,
                    state,
                    base + timedelta(seconds=20),
                    1,
                    "lock-contention",
                )
                assert retried["outcome"] == "recorded"
            finally:
                if blocker.is_in_transaction():
                    await blocker.execute("ROLLBACK")
                await blocker.close()
            projection = await owner.fetchrow(
                "SELECT status,verification_source,welcome_code_pending,raw_event,contact_way_id "
                "FROM wecom_external_contacts WHERE tenant_id=$1 AND id=$2",
                ids["tenant"],
                reactivated["contact_id"],
            )
            assert dict(projection) == {
                "status": "deleted",
                "verification_source": "termination_callback",
                "welcome_code_pending": False,
                "raw_event": "{}",
                "contact_way_id": way_id,
            }
            source_receipt = half["receipt_id"]
            for contact_id, expected_error in (
                (None, asyncpg.NotNullViolationError),
                (uuid.uuid4(), asyncpg.ForeignKeyViolationError),
            ):
                with pytest.raises(expected_error):
                    await owner.execute(
                        "INSERT INTO wecom_callback_receipts(id,tenant_id,connector_id,event_identity_digest,"
                        "payload_digest,change_type,external_userid,user_id,state,unionid,event_time,event_sequence,"
                        "outcome,contact_id,result,recorded_at) SELECT $1,tenant_id,connector_id,$2,payload_digest,"
                        "change_type,external_userid,user_id,state,unionid,event_time,event_sequence,outcome,$3,result,now() "
                        "FROM wecom_callback_receipts WHERE id=$4",
                        uuid.uuid4(),
                        uuid.uuid4().hex + uuid.uuid4().hex,
                        contact_id,
                        source_receipt,
                    )
            other = await _seed_catalog(owner, "wecom-cross-tenant")
            other_connector_id, other_contact_id = uuid.uuid4(), uuid.uuid4()
            await owner.execute(
                "INSERT INTO connectors(id,tenant_id,name,connector_type,config,enabled,created_at,updated_at) "
                "VALUES($1,$2,'other official WeCom','wecom_customer_contact','{}',true,now(),now())",
                other_connector_id,
                other["tenant"],
            )
            await owner.execute(
                "INSERT INTO wecom_external_contacts(id,tenant_id,connector_id,external_userid,status,raw_event,"
                "event_time,event_sequence,welcome_code_pending,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,'active','{}',now(),0,false,now(),now())",
                other_contact_id,
                other["tenant"],
                other_connector_id,
                f"external-{uuid.uuid4().hex}",
            )
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await owner.execute(
                    "INSERT INTO wecom_callback_receipts(id,tenant_id,connector_id,event_identity_digest,"
                    "payload_digest,change_type,external_userid,user_id,state,unionid,event_time,event_sequence,"
                    "outcome,contact_id,result,recorded_at) SELECT $1,tenant_id,connector_id,$2,payload_digest,"
                    "change_type,external_userid,user_id,state,unionid,event_time,event_sequence,outcome,$3,result,now() "
                    "FROM wecom_callback_receipts WHERE id=$4",
                    uuid.uuid4(),
                    uuid.uuid4().hex + uuid.uuid4().hex,
                    other_contact_id,
                    source_receipt,
                )
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await owner.execute(
                    "DELETE FROM wecom_external_contacts WHERE tenant_id=$1 AND id=$2",
                    ids["tenant"],
                    half["contact_id"],
                )
            deny_statements = (
                ("UPDATE wecom_external_contacts SET status='active' WHERE tenant_id=$1", ids["tenant"]),
                ("TRUNCATE wecom_external_contacts",),
                ("DELETE FROM wecom_callback_receipts WHERE tenant_id=$1", ids["tenant"]),
            )
            for statement in deny_statements:
                async with runtime.transaction():
                    await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await runtime.execute(statement[0], *statement[1:])
            async with callback.transaction():
                await callback.execute("SELECT set_config('app.tenant_id',$1,true)", str(uuid.uuid4()))
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await callback.fetchrow(
                        f"SELECT * FROM {SIGNATURE.split('(')[0]}($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                        ids["tenant"],
                        uuid.uuid4(),
                        connector_id,
                        "add_external_contact",
                        external_userid,
                        "sales-user",
                        state,
                        "union-safe",
                        base + timedelta(seconds=4),
                        1,
                        "e" * 64,
                    )
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await runtime.execute(
                        f"SELECT * FROM {SIGNATURE.split('(')[0]}($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                        ids["tenant"],
                        uuid.uuid4(),
                        connector_id,
                        "add_external_contact",
                        external_userid,
                        "sales-user",
                        state,
                        "union-safe",
                        base + timedelta(seconds=4),
                        1,
                        "f" * 64,
                    )
        finally:
            await asyncio.gather(owner.close(), callback.close(), runtime.close())


async def test_wecom_authority_clean_roundtrip_and_receipt_downgrade_preflight(migrated_pg_url: str) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        await asyncio.to_thread(_alembic, database_url, "downgrade", "u6k1d2e3f4a5")
        await asyncio.to_thread(_alembic, database_url, "upgrade", "u6l3f4a5b6c7")
        owner = await asyncpg.connect(owner_dsn)
        try:
            legacy_function = await owner.fetchval(
                "SELECT pg_get_functiondef(to_regprocedure($1))", f"public.{SIGNATURE}"
            )
        finally:
            await owner.close()
        await asyncio.to_thread(_alembic, database_url, "upgrade", "head")
        await asyncio.to_thread(_alembic, database_url, "downgrade", "u6l3f4a5b6c7")
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert (
                await owner.fetchval("SELECT pg_get_functiondef(to_regprocedure($1))", f"public.{SIGNATURE}")
                == legacy_function
            )
            assert await owner.fetchval("SELECT to_regclass('public.uq_wecom_external_contacts_source') IS NOT NULL")
            assert not await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='uq_wecom_external_contacts_member_source_u6l')"
            )
        finally:
            await owner.close()
        await asyncio.to_thread(_alembic, database_url, "upgrade", "head")
        await asyncio.to_thread(_alembic, database_url, "check")
        owner = await asyncpg.connect(owner_dsn)
        callback = await _callback(database_url)
        try:
            ids = await _seed_catalog(owner, "verified-wecom-downgrade")
            connector_id, way_id = uuid.uuid4(), uuid.uuid4()
            state = f"state-{uuid.uuid4().hex}"
            await owner.execute(
                "INSERT INTO connectors(id,tenant_id,name,connector_type,config,enabled,created_at,updated_at) "
                "VALUES($1,$2,'official WeCom downgrade','wecom_customer_contact','{}',true,now(),now())",
                connector_id,
                ids["tenant"],
            )
            await owner.execute(
                "INSERT INTO wecom_contact_ways(id,tenant_id,connector_id,state,user_ids,status,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,'[\"sales-user\"]','active',now(),now())",
                way_id,
                ids["tenant"],
                connector_id,
                state,
            )
            recorded = await _apply(
                callback,
                ids["tenant"],
                connector_id,
                "del_external_contact",
                f"external-{uuid.uuid4().hex}",
                state,
                datetime.now(UTC).replace(microsecond=0),
                1,
                "downgrade-fact",
            )
            before = await owner.fetchrow(
                "SELECT xmin::text AS xmin,md5(row_to_json(receipt)::text) AS digest "
                "FROM wecom_callback_receipts receipt WHERE id=$1",
                recorded["receipt_id"],
            )
            function_before = await owner.fetchval(
                "SELECT pg_get_functiondef(to_regprocedure($1))", f"public.{SIGNATURE}"
            )
        finally:
            await asyncio.gather(owner.close(), callback.close())
        blocked = await asyncio.to_thread(
            _alembic,
            database_url,
            "downgrade",
            "u6k1d2e3f4a5",
            succeeds=False,
        )
        assert "immutable verified WeCom callback receipts" in f"{blocked.stdout}\n{blocked.stderr}"
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u6l4a5b6c7d8"
            assert (
                await owner.fetchrow(
                    "SELECT xmin::text AS xmin,md5(row_to_json(receipt)::text) AS digest "
                    "FROM wecom_callback_receipts receipt WHERE id=$1",
                    recorded["receipt_id"],
                )
                == before
            )
            assert (
                await owner.fetchval("SELECT pg_get_functiondef(to_regprocedure($1))", f"public.{SIGNATURE}")
                == function_before
            )
        finally:
            await owner.close()


async def test_wecom_receipt_contact_backfills_only_from_authoritative_result(migrated_pg_url: str) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        await asyncio.to_thread(_alembic, database_url, "downgrade", "u6l0c1d2e3f4")
        owner = await asyncpg.connect(owner_dsn)
        try:
            ids = await _seed_catalog(owner, "verified-wecom-populated-upgrade")
            connector_id, contact_id, receipt_id, way_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            external_userid = f"external-{uuid.uuid4().hex}"
            state = f"state-{uuid.uuid4().hex}"
            await owner.execute(
                "INSERT INTO connectors(id,tenant_id,name,connector_type,config,enabled,created_at,updated_at) "
                "VALUES($1,$2,'upgrade official WeCom','wecom_customer_contact','{}',true,now(),now())",
                connector_id,
                ids["tenant"],
            )
            await owner.execute(
                "INSERT INTO wecom_contact_ways(id,tenant_id,connector_id,state,user_ids,status,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,'[\"sales-user\"]','active',now(),now())",
                way_id,
                ids["tenant"],
                connector_id,
                state,
            )
            await owner.execute(
                "INSERT INTO wecom_external_contacts(id,tenant_id,connector_id,contact_way_id,external_userid,user_id,"
                "state,status,verification_source,change_type,raw_event,event_time,event_sequence,welcome_code_pending,"
                "created_at,updated_at) VALUES($1,$2,$3,$4,$5,'sales-user',$6,'active','confirmed_callback',"
                "'add_external_contact','{}',now(),0,false,now(),now())",
                contact_id,
                ids["tenant"],
                connector_id,
                way_id,
                external_userid,
                state,
            )
            await owner.execute(
                "INSERT INTO wecom_callback_receipts(id,tenant_id,connector_id,event_identity_digest,payload_digest,"
                "change_type,external_userid,user_id,state,event_time,event_sequence,outcome,contact_id,result,recorded_at) "
                "VALUES($1,$2,$3,$4,$5,'add_external_contact',$6,' sales-user ',$7,now(),0,'recorded',NULL,"
                "json_build_object('contact_id',$8::text,'confirmed',true),now())",
                receipt_id,
                ids["tenant"],
                connector_id,
                "a" * 64,
                "b" * 64,
                external_userid,
                state,
                str(contact_id),
            )
            await owner.execute(
                "CREATE UNIQUE INDEX uq_wecom_external_contacts_tenant_id_id_u6l "
                "ON wecom_external_contacts(tenant_id,id)"
            )
            await owner.execute(
                "UPDATE pg_index SET indisvalid=false WHERE "
                "indexrelid='uq_wecom_external_contacts_tenant_id_id_u6l'::regclass"
            )
        finally:
            await owner.close()
        await asyncio.to_thread(_alembic, database_url, "upgrade", "head")
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert (
                await owner.fetchval("SELECT contact_id FROM wecom_callback_receipts WHERE id=$1", receipt_id)
                == contact_id
            )
            assert (
                await owner.fetchval("SELECT user_id FROM wecom_callback_receipts WHERE id=$1", receipt_id)
                == "sales-user"
            )
            assert (
                await owner.fetchval(
                    "SELECT is_nullable FROM information_schema.columns WHERE table_schema='public' "
                    "AND table_name='wecom_callback_receipts' AND column_name='contact_id'"
                )
                == "NO"
            )
            await asyncio.to_thread(_alembic, database_url, "check")
        finally:
            await owner.close()


async def test_wecom_member_preflight_and_malformed_index_fail_before_catalog_drift(migrated_pg_url: str) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        await asyncio.to_thread(_alembic, database_url, "downgrade", "u6l2e3f4a5b6")
        owner = await asyncpg.connect(owner_dsn)
        try:
            ids = await _seed_catalog(owner, "wecom-member-preflight")
            connector_id, contact_id, way_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            state = f"state-{uuid.uuid4().hex}"
            await owner.execute(
                "INSERT INTO connectors(id,tenant_id,name,connector_type,config,enabled,created_at,updated_at) "
                "VALUES($1,$2,'member preflight WeCom','wecom_customer_contact','{}',true,now(),now())",
                connector_id,
                ids["tenant"],
            )
            await owner.execute(
                "INSERT INTO wecom_external_contacts(id,tenant_id,connector_id,external_userid,state,status,"
                "verification_source,raw_event,event_time,event_sequence,welcome_code_pending,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,$5,'active','confirmed_callback','{}',now(),0,false,now(),now())",
                contact_id,
                ids["tenant"],
                connector_id,
                f"external-{uuid.uuid4().hex}",
                state,
            )
        finally:
            await owner.close()
        failed = await asyncio.to_thread(_alembic, database_url, "upgrade", "head", succeeds=False)
        assert "one exact serving member" in f"{failed.stdout}\n{failed.stderr}"
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u6l2e3f4a5b6"
            assert not await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_proc WHERE proname='apply_verified_wecom_contact_event_u6l3')"
            )
            await owner.execute(
                "INSERT INTO wecom_contact_ways(id,tenant_id,connector_id,state,user_ids,status,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,'[\"sales-user\"]','active',now(),now())",
                way_id,
                ids["tenant"],
                connector_id,
                state,
            )
            await owner.execute(
                "UPDATE wecom_external_contacts SET user_id='sales-user',contact_way_id=$1 WHERE id=$2",
                way_id,
                contact_id,
            )
            await owner.execute(
                "CREATE INDEX uq_wecom_external_contacts_member_source_u6l ON wecom_external_contacts(id)"
            )
            malformed = await owner.fetchval(
                "SELECT pg_get_indexdef('uq_wecom_external_contacts_member_source_u6l'::regclass)"
            )
        finally:
            await owner.close()
        failed = await asyncio.to_thread(_alembic, database_url, "upgrade", "head", succeeds=False)
        assert "refusing unexpected WeCom member authority index" in f"{failed.stdout}\n{failed.stderr}"
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u6l2e3f4a5b6"
            assert (
                await owner.fetchval("SELECT pg_get_indexdef('uq_wecom_external_contacts_member_source_u6l'::regclass)")
                == malformed
            )
            await owner.execute("DROP INDEX uq_wecom_external_contacts_member_source_u6l")
        finally:
            await owner.close()
        await asyncio.to_thread(_alembic, database_url, "upgrade", "head")


async def test_historical_wecom_member_recovery_is_auditable_and_required_before_cutover(
    migrated_pg_url: str,
) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        await asyncio.to_thread(_alembic, database_url, "downgrade", "u6l0c1d2e3f4")
        owner = await asyncpg.connect(owner_dsn)
        try:
            ids = await _seed_catalog(owner, "wecom-historical-recovery")
            connector_id, contact_id, way_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            state = f"state-{uuid.uuid4().hex}"
            await owner.execute(
                "INSERT INTO connectors(id,tenant_id,name,connector_type,config,enabled,created_at,updated_at) "
                "VALUES($1,$2,'historical WeCom','wecom_customer_contact','{}',true,now(),now())",
                connector_id,
                ids["tenant"],
            )
            await owner.execute(
                "INSERT INTO wecom_contact_ways(id,tenant_id,connector_id,state,user_ids,status,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,'[\"recovered-member\"]','active',now(),now())",
                way_id,
                ids["tenant"],
                connector_id,
                state,
            )
            await owner.execute(
                "INSERT INTO wecom_external_contacts(id,tenant_id,connector_id,contact_way_id,external_userid,state,"
                "status,verification_source,change_type,raw_event,event_time,event_sequence,welcome_code_pending,"
                "created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,'active','confirmed_callback',"
                "'add_external_contact','{}',now(),0,false,now(),now())",
                contact_id,
                ids["tenant"],
                connector_id,
                way_id,
                f"external-{uuid.uuid4().hex}",
                state,
            )
            before = await owner.fetchrow(
                "SELECT xmin::text AS xmin,md5(row_to_json(contact)::text) AS digest "
                "FROM wecom_external_contacts contact WHERE id=$1",
                contact_id,
            )
        finally:
            await owner.close()
        await asyncio.to_thread(_alembic, database_url, "upgrade", "u6l2e3f4a5b6")
        blocked = await asyncio.to_thread(_alembic, database_url, "upgrade", "head", succeeds=False)
        assert "one exact serving member" in f"{blocked.stdout}\n{blocked.stderr}"
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u6l2e3f4a5b6"
            assert (
                await owner.fetchval(
                    "SELECT resolution_state FROM wecom_member_recovery_markers WHERE tenant_id=$1 AND contact_id=$2",
                    ids["tenant"],
                    contact_id,
                )
                == "unresolved"
            )
            assert (
                await owner.fetchrow(
                    "SELECT xmin::text AS xmin,md5(row_to_json(contact)::text) AS digest "
                    "FROM wecom_external_contacts contact WHERE id=$1",
                    contact_id,
                )
                == before
            )
            assert not await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_class WHERE relname=ANY($1::text[]))",
                [
                    "uq_wecom_external_contacts_member_source_u6l",
                    "ix_wecom_external_contacts_member_order_u6l",
                    "ix_wecom_callback_receipts_member_order_u6l",
                ],
            )
            recovery_signature = "public.acknowledge_wecom_member_recovery(uuid,uuid,text,text)"
            for principal in ("public", "yimatong_app", "yimatong_callback"):
                assert not await owner.fetchval(
                    "SELECT has_function_privilege($1,$2,'EXECUTE')",
                    principal,
                    recovery_signature,
                )
            for principal in ("yimatong_app", "yimatong_callback"):
                assert not await owner.fetchval(
                    "SELECT has_table_privilege($1,'public.wecom_member_recovery_markers','SELECT')",
                    principal,
                )
            await owner.execute(
                "SELECT acknowledge_wecom_member_recovery($1,$2,'recovered-member',$3)",
                ids["tenant"],
                contact_id,
                "acceptance owner reviewed exact official member evidence",
            )
            marker = await owner.fetchrow(
                "SELECT resolution_state,acknowledged_user_id,acknowledged_at IS NOT NULL AS acknowledged "
                "FROM wecom_member_recovery_markers WHERE tenant_id=$1 AND contact_id=$2",
                ids["tenant"],
                contact_id,
            )
            assert tuple(marker) == ("operator_acknowledged", "recovered-member", True)
        finally:
            await owner.close()
        await asyncio.to_thread(_alembic, database_url, "upgrade", "head")
        await asyncio.to_thread(_alembic, database_url, "downgrade", "u6l0c1d2e3f4")
        await asyncio.to_thread(_alembic, database_url, "upgrade", "head")
        await asyncio.to_thread(_alembic, database_url, "check")


@pytest.mark.parametrize(
    ("start_revision", "candidate_indexes"),
    [
        (
            "u6l0c1d2e3f4",
            ["uq_wecom_external_contacts_tenant_id_id_u6l", "ix_wecom_callback_receipts_tenant_contact"],
        ),
        (
            "u6l2e3f4a5b6",
            [
                "uq_wecom_external_contacts_member_source_u6l",
                "ix_wecom_external_contacts_member_order_u6l",
                "ix_wecom_callback_receipts_member_order_u6l",
            ],
        ),
    ],
)
async def test_wecom_online_index_timeout_keeps_catalog_unchanged_and_retries(
    migrated_pg_url: str,
    start_revision: str,
    candidate_indexes: list[str],
) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        await asyncio.to_thread(_alembic, database_url, "downgrade", start_revision)
        owner = await asyncpg.connect(owner_dsn)
        blocker = await asyncpg.connect(owner_dsn)
        try:
            ids = await _seed_catalog(owner, "wecom-cic-timeout")
            connector_id, contact_id = uuid.uuid4(), uuid.uuid4()
            await owner.execute(
                "INSERT INTO connectors(id,tenant_id,name,connector_type,config,enabled,created_at,updated_at) "
                "VALUES($1,$2,'timeout official WeCom','wecom_customer_contact','{}',true,now(),now())",
                connector_id,
                ids["tenant"],
            )
            await owner.execute(
                "INSERT INTO wecom_external_contacts(id,tenant_id,connector_id,external_userid,status,raw_event,"
                "event_time,event_sequence,welcome_code_pending,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,'active','{}',now(),0,false,now(),now())",
                contact_id,
                ids["tenant"],
                connector_id,
                f"external-{uuid.uuid4().hex}",
            )
            before = await owner.fetchrow(
                "SELECT xmin::text AS xmin,md5(row_to_json(contact)::text) AS digest "
                "FROM wecom_external_contacts contact WHERE id=$1",
                contact_id,
            )
            function_before = await owner.fetchval(
                "SELECT pg_get_functiondef(to_regprocedure($1))", f"public.{SIGNATURE}"
            )
            await blocker.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            await blocker.fetchval("SELECT count(*) FROM wecom_external_contacts WHERE tenant_id=$1", ids["tenant"])
            started = time.monotonic()
            blocked = await asyncio.to_thread(
                _alembic,
                database_url,
                "upgrade",
                "head",
                succeeds=False,
            )
            elapsed = time.monotonic() - started
            assert elapsed < 15
            assert "statement timeout" in f"{blocked.stdout}\n{blocked.stderr}".lower()
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == start_revision
            residual_indexes = await owner.fetch(
                "SELECT relname FROM pg_class WHERE relname=ANY($1::text[]) ORDER BY relname",
                candidate_indexes,
            )
            assert residual_indexes == []
            assert (
                await owner.fetchrow(
                    "SELECT xmin::text AS xmin,md5(row_to_json(contact)::text) AS digest "
                    "FROM wecom_external_contacts contact WHERE id=$1",
                    contact_id,
                )
                == before
            )
            assert (
                await owner.fetchval("SELECT pg_get_functiondef(to_regprocedure($1))", f"public.{SIGNATURE}")
                == function_before
            )
            assert await owner.fetchval("SHOW lock_timeout") == "0"
            assert await owner.fetchval("SHOW statement_timeout") == "0"
        finally:
            if blocker.is_in_transaction():
                await blocker.execute("ROLLBACK")
            await asyncio.gather(owner.close(), blocker.close())
        await asyncio.to_thread(_alembic, database_url, "upgrade", "head")
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u6l4a5b6c7d8"
            assert await owner.fetchval(
                "SELECT bool_and(indisvalid) FROM pg_index WHERE indexrelid=ANY($1::regclass[])",
                candidate_indexes,
            )
        finally:
            await owner.close()
