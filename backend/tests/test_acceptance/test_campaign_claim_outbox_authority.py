"""Real PostgreSQL contract for atomic campaign claims and durable delivery."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.test_acceptance.test_code_batch_delivery_contract import _insert_batch, _insert_receipt, _seed_catalog

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def _runtime_connection(database_url: str) -> asyncpg.Connection:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))


async def _callback_connection(database_url: str) -> asyncpg.Connection:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn.replace("yimatong:yimatong@", "yimatong_callback:yimatong_callback@"))


async def _grant_campaign_permissions(conn: asyncpg.Connection, ids: dict[str, uuid.UUID]) -> uuid.UUID:
    for code in ("campaign:create", "campaign:manage"):
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


async def _runtime_campaign_call(
    conn: asyncpg.Connection,
    tenant_id: uuid.UUID,
    sql: str,
    *args: object,
) -> asyncpg.Record:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))
        row = await conn.fetchrow(sql, *args)
        assert row is not None
        return row


async def _seed_claim_evidence(
    owner: asyncpg.Connection, ids: dict[str, uuid.UUID]
) -> tuple[uuid.UUID, str, uuid.UUID, uuid.UUID]:
    receipt_id = await _insert_receipt(owner, ids)
    batch_id = await _insert_batch(owner, ids, receipt_id, quantity=1, expected_item_count=1)
    item_id = uuid.uuid4()
    public_id = f"C{item_id.hex[:16]}"
    async with owner.transaction():
        await owner.execute("ALTER TABLE public.code_batches DISABLE TRIGGER USER")
        await owner.execute("ALTER TABLE public.code_items DISABLE TRIGGER USER")
        try:
            await owner.execute(
                "UPDATE code_batches SET status='activated',updated_at=now() WHERE tenant_id=$1 AND id=$2",
                ids["tenant"],
                batch_id,
            )
            await owner.execute(
                "INSERT INTO code_items(id,tenant_id,code_batch_id,public_id,status,code_type,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,'activated','single',now(),now())",
                item_id,
                ids["tenant"],
                batch_id,
                public_id,
            )
        finally:
            await owner.execute("ALTER TABLE public.code_items ENABLE TRIGGER USER")
            await owner.execute("ALTER TABLE public.code_batches ENABLE TRIGGER USER")
    scan_event_id = uuid.uuid4()
    await owner.execute(
        "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,is_first_scan,is_valid_visit,created_at,updated_at) "
        "VALUES($1,$2,$3,now(),true,true,now(),now())",
        scan_event_id,
        ids["tenant"],
        public_id,
    )
    return scan_event_id, public_id, item_id, batch_id


async def test_campaign_claim_catalog_and_runtime_acl(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        signatures = [
            "public.claim_campaign_benefit(uuid,uuid,uuid,uuid,uuid,text,text,text)",
            "public.lease_campaign_claim_outbox(uuid,text,integer,integer)",
            "public.record_campaign_claim_delivery_result(uuid,uuid,uuid,uuid,uuid,text,text,jsonb,integer)",
            "public.complete_campaign_claim_outbox(uuid,uuid,uuid)",
            "public.fail_campaign_claim_outbox(uuid,uuid,uuid,text,integer)",
            "public.redeem_campaign_benefit_claim(uuid,uuid,uuid,uuid)",
        ]
        for signature in signatures:
            assert await owner.fetchval("SELECT to_regprocedure($1) IS NOT NULL", signature)
            assert await owner.fetchval("SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", signature)
        for table in ("campaigns", "benefits", "benefit_claims", "benefit_deliveries", "campaign_claim_outbox"):
            assert await owner.fetchval("SELECT has_table_privilege('yimatong_app',$1,'SELECT')", f"public.{table}")
            assert not await owner.fetchval(
                "SELECT has_table_privilege('yimatong_app',$1,'INSERT,UPDATE,DELETE')", f"public.{table}"
            )
        for internal in (
            "public.authorize_campaign_actor(uuid,uuid,text)",
            "public.validate_campaign_benefit_config(text,jsonb)",
            "public.mutate_campaign_benefit(uuid,uuid,uuid,text,uuid,uuid,text,text,jsonb,integer,integer,boolean,uuid,text)",
        ):
            assert not await owner.fetchval("SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", internal)
        callback_signature = "public.settle_campaign_claim_callback(uuid,uuid,uuid,uuid,uuid,text,text,jsonb)"
        assert await owner.fetchval("SELECT to_regprocedure($1) IS NOT NULL", callback_signature)
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_callback',$1,'EXECUTE')", callback_signature
        )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", callback_signature
        )
        assert not await owner.fetchval("SELECT has_function_privilege('public',$1,'EXECUTE')", callback_signature)
        callback_role = await owner.fetchrow(
            "SELECT rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolbypassrls FROM pg_roles "
            "WHERE rolname='yimatong_callback'"
        )
        assert callback_role is not None and not any(callback_role.values())
        assert not await owner.fetchval(
            "SELECT has_table_privilege('yimatong_callback','public.benefit_claims','SELECT,INSERT,UPDATE,DELETE')"
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM information_schema.role_table_grants WHERE grantee='yimatong_callback'"
            )
            == 0
        )
        assert (
            await owner.fetchval(
                "WITH sequences AS MATERIALIZED (SELECT cls.oid FROM pg_class cls "
                "JOIN pg_namespace ns ON ns.oid=cls.relnamespace "
                "WHERE ns.nspname='public' AND cls.relkind='S') SELECT count(*) FROM sequences "
                "WHERE has_sequence_privilege('yimatong_callback',oid,'USAGE,SELECT,UPDATE')"
            )
            == 0
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM pg_proc proc JOIN pg_namespace ns ON ns.oid=proc.pronamespace "
                "WHERE ns.nspname='public' AND proc.prosecdef AND proc.oid<>to_regprocedure($1) "
                "AND has_function_privilege('yimatong_callback',proc.oid,'EXECUTE')",
                callback_signature,
            )
            == 0
        )
    finally:
        await owner.close()


async def test_campaign_management_is_actor_bound_audited_and_resource_serialized(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await _runtime_connection(migrated_pg_url)
    target = await _seed_catalog(owner, "campaign-management-target")
    agency = await _seed_catalog(owner, "campaign-management-agency")
    await owner.execute("UPDATE tenants SET tenant_type='agency' WHERE id=$1", agency["tenant"])
    agency_session = await _grant_campaign_permissions(owner, agency)
    authorization_id = uuid.uuid4()
    await owner.execute(
        "INSERT INTO agency_authorizations "
        "(id,agency_tenant_id,client_tenant_id,scope,status,granted_by,granted_at,created_at,updated_at) "
        "VALUES($1,$2,$3,'[\"campaigns\"]','active',$4,now(),now(),now())",
        authorization_id,
        agency["tenant"],
        target["tenant"],
        target["account"],
    )
    campaign_id = uuid.uuid4()
    other_campaign_id = uuid.uuid4()
    benefit_id = uuid.uuid4()
    disposable_benefit_id = uuid.uuid4()

    create_campaign_sql = (
        "SELECT * FROM create_campaign($1,$2,$3,$4,$5,$6,$7,now()-interval '1 hour',"
        "now()+interval '1 day',$8::jsonb,$9)"
    )
    update_campaign_sql = (
        "SELECT * FROM update_campaign($1,$2,$3,$4,$5,$6::uuid,$7::text,$8::text,"
        "$9::timestamptz,$10::timestamptz,$11::jsonb,$12::text)"
    )
    try:
        created = await _runtime_campaign_call(
            runtime,
            target["tenant"],
            create_campaign_sql,
            target["tenant"],
            agency_session,
            uuid.uuid4(),
            campaign_id,
            target["product"],
            "Agency campaign",
            "coupon",
            "{}",
            "acting creation",
        )
        assert created["current_status"] == "draft"
        await _runtime_campaign_call(
            runtime,
            target["tenant"],
            create_campaign_sql,
            target["tenant"],
            agency_session,
            uuid.uuid4(),
            other_campaign_id,
            target["product"],
            "Other campaign",
            "coupon",
            "{}",
            "independent resource",
        )
        benefit = await _runtime_campaign_call(
            runtime,
            target["tenant"],
            "SELECT * FROM create_benefit($1,$2,$3,$4,$5::uuid,$6,$7,$8::jsonb,$9,$10,$11::uuid)",
            target["tenant"],
            agency_session,
            uuid.uuid4(),
            benefit_id,
            None,
            "Attached coupon",
            "platform_coupon",
            "{}",
            10,
            1,
            None,
        )
        assert benefit["campaign_id"] is None
        attached = await _runtime_campaign_call(
            runtime,
            target["tenant"],
            "SELECT * FROM attach_benefit($1,$2,$3,$4,$5)",
            target["tenant"],
            agency_session,
            uuid.uuid4(),
            benefit_id,
            campaign_id,
        )
        assert attached["campaign_id"] == campaign_id

        lock_tx = owner.transaction()
        await lock_tx.start()
        await owner.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('campaign:'||$1::text||':'||$2::text,0))",
            str(target["tenant"]),
            str(campaign_id),
        )
        try:
            with pytest.raises(asyncpg.PostgresError) as locked:
                await _runtime_campaign_call(
                    runtime,
                    target["tenant"],
                    update_campaign_sql,
                    target["tenant"],
                    agency_session,
                    uuid.uuid4(),
                    campaign_id,
                    False,
                    None,
                    "Must not commit",
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            assert locked.value.sqlstate == "55P03"
            independent = await _runtime_campaign_call(
                runtime,
                target["tenant"],
                update_campaign_sql,
                target["tenant"],
                agency_session,
                uuid.uuid4(),
                other_campaign_id,
                False,
                None,
                "Independent update",
                None,
                None,
                None,
                None,
                None,
            )
            assert independent["current_status"] == "draft"
        finally:
            await lock_tx.rollback()

        endpoint_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO webhook_endpoints "
            "(id,tenant_id,url,events,secret,enabled,batch_mode,batch_size,created_at,updated_at) "
            "VALUES($1,$2,'https://example.invalid/campaign','[\"campaign.active\",\"campaign.ended\"]',"
            "'test-secret',true,false,100,now(),now())",
            endpoint_id,
            target["tenant"],
        )
        active = await _runtime_campaign_call(
            runtime,
            target["tenant"],
            "SELECT * FROM transition_campaign($1,$2,$3,$4,'active')",
            target["tenant"],
            agency_session,
            uuid.uuid4(),
            campaign_id,
        )
        assert active["current_status"] == "active" and active["published_at"] is not None
        assert await owner.fetchval(
            "SELECT count(*)=1 FROM webhook_deliveries WHERE tenant_id=$1 AND endpoint_id=$2 "
            "AND event_type='campaign.active' AND payload->>'campaign_id'=$3",
            target["tenant"],
            endpoint_id,
            str(campaign_id),
        )

        with pytest.raises(asyncpg.PostgresError) as direct_dml:
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(target["tenant"]))
                await runtime.execute(
                    "UPDATE campaigns SET name='forged' WHERE tenant_id=$1 AND id=$2",
                    target["tenant"],
                    campaign_id,
                )
        assert direct_dml.value.sqlstate == "42501"

        await _runtime_campaign_call(
            runtime,
            target["tenant"],
            "SELECT * FROM create_benefit($1,$2,$3,$4,$5::uuid,$6,$7,$8::jsonb,$9,$10,$11::uuid)",
            target["tenant"],
            agency_session,
            uuid.uuid4(),
            disposable_benefit_id,
            None,
            "Disposable coupon",
            "platform_coupon",
            "{}",
            1,
            1,
            None,
        )
        await _runtime_campaign_call(
            runtime,
            target["tenant"],
            "SELECT * FROM delete_benefit($1,$2,$3,$4)",
            target["tenant"],
            agency_session,
            uuid.uuid4(),
            disposable_benefit_id,
        )
        await _runtime_campaign_call(
            runtime,
            target["tenant"],
            "SELECT * FROM delete_campaign($1,$2,$3,$4)",
            target["tenant"],
            agency_session,
            uuid.uuid4(),
            other_campaign_id,
        )
        assert await owner.fetchval(
            "SELECT count(*)>=8 FROM platform_audit_log WHERE target_tenant_id=$1 AND operator_id=$2",
            str(target["tenant"]),
            str(agency["account"]),
        )

        await owner.execute(
            "UPDATE agency_authorizations SET status='revoked',revoked_at=now(),updated_at=now() WHERE id=$1",
            authorization_id,
        )
        before_name = await owner.fetchval(
            "SELECT name FROM campaigns WHERE tenant_id=$1 AND id=$2", target["tenant"], campaign_id
        )
        with pytest.raises(asyncpg.PostgresError) as revoked:
            await _runtime_campaign_call(
                runtime,
                target["tenant"],
                update_campaign_sql,
                target["tenant"],
                agency_session,
                uuid.uuid4(),
                campaign_id,
                False,
                None,
                "Revoked update",
                None,
                None,
                None,
                None,
                None,
            )
        assert revoked.value.sqlstate == "42501"
        assert (
            await owner.fetchval(
                "SELECT name FROM campaigns WHERE tenant_id=$1 AND id=$2", target["tenant"], campaign_id
            )
            == before_name
        )
    finally:
        await asyncio.gather(runtime.close(), owner.close())


async def test_cash_claim_reservation_and_terminal_refund_are_atomic(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await _runtime_connection(migrated_pg_url)
    ids = await _seed_catalog(owner, "campaign-cash")
    connector_id = uuid.uuid4()
    benefit_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    claimed_idempotency_key = f"claim:v1:{uuid.uuid4().hex}"
    consumer_ref = f"anon:v1:{uuid.uuid4().hex}"
    scan_event_id, public_id, _item_id, _batch_id = await _seed_claim_evidence(owner, ids)
    await owner.execute(
        "INSERT INTO connectors(id,tenant_id,name,connector_type,config,enabled,created_at,updated_at) "
        "VALUES($1,$2,'cash','wechat_pay','{}',true,now(),now())",
        connector_id,
        ids["tenant"],
    )
    await owner.execute(
        "INSERT INTO benefits(id,tenant_id,campaign_id,name,benefit_type,config_json,connector_id,"
        "stock_total,stock_used,per_person_limit,status,created_at,updated_at) "
        "VALUES($1,$2,NULL,'cash','cash_red_packet',"
        '\'{"validity_type":"after_claim_days","validity_days":1,"amount_type":"fixed",'
        '"fixed_amount":100,"budget":100,"claimed_budget":0}\'::jsonb,$3,2,0,1,\'active\',now(),now())',
        benefit_id,
        ids["tenant"],
        connector_id,
    )
    try:
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            claimed = await runtime.fetchrow(
                "SELECT * FROM claim_campaign_benefit($1,$2,$3,$4,$5,$6,$7,$8)",
                ids["tenant"],
                claim_id,
                benefit_id,
                scan_event_id,
                ids["product"],
                public_id,
                consumer_ref,
                claimed_idempotency_key,
            )
            assert claimed["created"] is True
            assert claimed["reserved_amount"] == 100
            assert claimed["reservation_status"] == "reserved"
            outbox_id = claimed["outbox_id"]
            first_stock = claimed["stock_used"]
        replay_claim_id = uuid.uuid4()
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            replayed = await runtime.fetchrow(
                "SELECT * FROM claim_campaign_benefit($1,$2,$3,$4,$5,$6,$7,$8)",
                ids["tenant"],
                replay_claim_id,
                benefit_id,
                scan_event_id,
                ids["product"],
                public_id,
                consumer_ref,
                claimed_idempotency_key,
            )
        assert replayed["outcome"] == "replayed"
        assert replayed["created"] is False
        assert replayed["claim_id"] == claim_id and replayed["outbox_id"] == outbox_id
        assert replayed["stock_used"] == first_stock
        assert (
            await owner.fetchval(
                "SELECT (config_json->>'claimed_budget')::integer FROM benefits WHERE tenant_id=$1 AND id=$2",
                ids["tenant"],
                benefit_id,
            )
            == 100
        )
        assert await owner.fetchval(
            "SELECT payload::jsonb ? 'consumer_ref' AND NOT (payload::jsonb ? 'openid') FROM campaign_claim_outbox "
            "WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            outbox_id,
        )
        for attempt in range(1, 9):
            async with runtime.transaction():
                await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
                lease = await runtime.fetchrow(
                    "SELECT * FROM lease_campaign_claim_outbox($1,'worker-a',1,30)", ids["tenant"]
                )
                assert lease["attempt_count"] == attempt
                failed = await runtime.fetchrow(
                    "SELECT * FROM fail_campaign_claim_outbox($1,$2,$3,'provider unavailable',0)",
                    ids["tenant"],
                    outbox_id,
                    lease["lease_token"],
                )
                assert failed["current_status"] == ("dead_letter" if attempt == 8 else "pending")
        benefit = await owner.fetchrow(
            "SELECT stock_used,(config_json->>'claimed_budget')::integer AS claimed_budget "
            "FROM benefits WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            benefit_id,
        )
        assert dict(benefit) == {"stock_used": 0, "claimed_budget": 0}
        assert await owner.fetchval(
            "SELECT reservation_status='refunded' AND status='failed' FROM benefit_claims WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            claim_id,
        )
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            with pytest.raises(asyncpg.PostgresError) as stale:
                await runtime.fetchrow(
                    "SELECT * FROM fail_campaign_claim_outbox($1,$2,$3,'duplicate',0)",
                    ids["tenant"],
                    outbox_id,
                    lease["lease_token"],
                )
            assert stale.value.sqlstate == "55P03"
        assert (
            await owner.fetchval(
                "SELECT stock_used FROM benefits WHERE tenant_id=$1 AND id=$2", ids["tenant"], benefit_id
            )
            == 0
        )
    finally:
        await runtime.close()
        await owner.close()


async def test_campaign_consumer_limit_is_cross_benefit_replay_safe_and_serialized(
    migrated_pg_url: str,
) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    runtime_a = await _runtime_connection(migrated_pg_url)
    runtime_b = await _runtime_connection(migrated_pg_url)
    ids = await _seed_catalog(owner, "campaign-wide-limit")
    scan_event_id, public_id, _item_id, _batch_id = await _seed_claim_evidence(owner, ids)
    campaign_id = uuid.uuid4()
    benefit_a, benefit_b = uuid.uuid4(), uuid.uuid4()
    consumer_ref = f"anon:v1:{uuid.uuid4().hex}"
    await owner.execute(
        "INSERT INTO campaigns(id,tenant_id,name,campaign_type,status,product_id,start_at,end_at,rules_json,"
        "created_at,updated_at) VALUES($1,$2,'wide limit','coupon','active',$3,now()-interval '1 hour',"
        'now()+interval \'1 day\',\'{"participation_condition_type":"any_scan","claim_limit_count":1}\','
        "now(),now())",
        campaign_id,
        ids["tenant"],
        ids["product"],
    )
    await owner.executemany(
        "INSERT INTO benefits(id,tenant_id,campaign_id,name,benefit_type,config_json,stock_total,stock_used,"
        "per_person_limit,status,created_at,updated_at) VALUES($1,$2,$3,$4,'platform_coupon','{}',10,0,10,"
        "'active',now(),now())",
        [
            (benefit_a, ids["tenant"], campaign_id, "benefit A"),
            (benefit_b, ids["tenant"], campaign_id, "benefit B"),
        ],
    )
    claim_sql = "SELECT * FROM claim_campaign_benefit($1,$2,$3,$4,$5,$6,$7,$8)"
    first_claim = uuid.uuid4()
    first_key = f"claim:v1:{uuid.uuid4().hex}"
    try:
        async with runtime_a.transaction():
            await runtime_a.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            first = await runtime_a.fetchrow(
                claim_sql,
                ids["tenant"],
                first_claim,
                benefit_a,
                scan_event_id,
                ids["product"],
                public_id,
                consumer_ref,
                first_key,
            )
        assert first["created"] is True
        async with runtime_a.transaction():
            await runtime_a.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            replay = await runtime_a.fetchrow(
                claim_sql,
                ids["tenant"],
                uuid.uuid4(),
                benefit_a,
                scan_event_id,
                ids["product"],
                public_id,
                consumer_ref,
                first_key,
            )
        assert replay["created"] is False and replay["claim_id"] == first_claim
        async with runtime_b.transaction():
            await runtime_b.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            with pytest.raises(asyncpg.PostgresError) as exceeded:
                await runtime_b.fetchrow(
                    claim_sql,
                    ids["tenant"],
                    uuid.uuid4(),
                    benefit_b,
                    scan_event_id,
                    ids["product"],
                    public_id,
                    consumer_ref,
                    f"claim:v1:{uuid.uuid4().hex}",
                )
        assert exceeded.value.sqlstate == "23514"
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM benefit_claims WHERE tenant_id=$1 AND campaign_id=$2 AND consumer_id=$3",
                ids["tenant"],
                campaign_id,
                consumer_ref,
            )
            == 1
        )
        assert await owner.fetchval(
            "SELECT stock_used=0 FROM benefits WHERE tenant_id=$1 AND id=$2", ids["tenant"], benefit_b
        )

        concurrent_consumer = f"anon:v1:{uuid.uuid4().hex}"
        tx_a = runtime_a.transaction()
        await tx_a.start()
        await runtime_a.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
        concurrent_first = await runtime_a.fetchrow(
            claim_sql,
            ids["tenant"],
            uuid.uuid4(),
            benefit_a,
            scan_event_id,
            ids["product"],
            public_id,
            concurrent_consumer,
            f"claim:v1:{uuid.uuid4().hex}",
        )
        assert concurrent_first["created"] is True
        async with runtime_b.transaction():
            await runtime_b.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            with pytest.raises(asyncpg.PostgresError) as busy:
                await runtime_b.fetchrow(
                    claim_sql,
                    ids["tenant"],
                    uuid.uuid4(),
                    benefit_b,
                    scan_event_id,
                    ids["product"],
                    public_id,
                    concurrent_consumer,
                    f"claim:v1:{uuid.uuid4().hex}",
                )
        assert busy.value.sqlstate == "55P03"
        await tx_a.commit()
        async with runtime_b.transaction():
            await runtime_b.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            with pytest.raises(asyncpg.PostgresError) as retry_exceeded:
                await runtime_b.fetchrow(
                    claim_sql,
                    ids["tenant"],
                    uuid.uuid4(),
                    benefit_b,
                    scan_event_id,
                    ids["product"],
                    public_id,
                    concurrent_consumer,
                    f"claim:v1:{uuid.uuid4().hex}",
                )
        assert retry_exceeded.value.sqlstate == "23514"
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM benefit_claims WHERE tenant_id=$1 AND campaign_id=$2 AND consumer_id=$3",
                ids["tenant"],
                campaign_id,
                concurrent_consumer,
            )
            == 1
        )
    finally:
        await asyncio.gather(runtime_a.close(), runtime_b.close(), owner.close())


async def test_claim_rechecks_pb_cb_item_and_risk_under_authority_locks(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await _runtime_connection(migrated_pg_url)
    ids = await _seed_catalog(owner, "campaign-claim-guards")
    benefit_id = uuid.uuid4()
    scan_event_id, public_id, item_id, batch_id = await _seed_claim_evidence(owner, ids)
    await owner.execute(
        "INSERT INTO benefits(id,tenant_id,name,benefit_type,config_json,stock_total,stock_used,per_person_limit,"
        "status,created_at,updated_at) VALUES($1,$2,'guarded','platform_coupon','{}',10,0,10,'active',now(),now())",
        benefit_id,
        ids["tenant"],
    )

    async def rejected(sqlstate: str) -> None:
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            with pytest.raises(asyncpg.PostgresError) as error:
                await runtime.fetchrow(
                    "SELECT * FROM claim_campaign_benefit($1,$2,$3,$4,$5,$6,$7,$8)",
                    ids["tenant"],
                    uuid.uuid4(),
                    benefit_id,
                    scan_event_id,
                    ids["product"],
                    public_id,
                    f"consumer-{uuid.uuid4().hex}",
                    f"claim:v1:{uuid.uuid4().hex}",
                )
            assert error.value.sqlstate == sqlstate
        assert (
            await owner.fetchval(
                "SELECT stock_used FROM benefits WHERE tenant_id=$1 AND id=$2", ids["tenant"], benefit_id
            )
            == 0
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM benefit_claims WHERE tenant_id=$1 AND benefit_id=$2)",
            ids["tenant"],
            benefit_id,
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM campaign_claim_outbox WHERE tenant_id=$1)", ids["tenant"]
        )

    try:
        pb_lock = owner.transaction()
        await pb_lock.start()
        await owner.fetchval(
            "SELECT id FROM production_batches WHERE tenant_id=$1 AND id=$2 FOR UPDATE",
            ids["tenant"],
            ids["production_batch"],
        )
        await rejected("55P03")
        await pb_lock.rollback()

        await owner.execute(
            "UPDATE production_batches SET production_date=current_date-2,expiry_date=current_date-1 "
            "WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            ids["production_batch"],
        )
        await rejected("23514")
        await owner.execute(
            "UPDATE production_batches SET production_date=current_date,expiry_date=current_date+365 "
            "WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            ids["production_batch"],
        )

        async with owner.transaction():
            await owner.execute("ALTER TABLE public.production_batches DISABLE TRIGGER USER")
            try:
                await owner.execute(
                    "UPDATE production_batches SET status='recalled',recall_reason='acceptance recall',"
                    "recalled_at=now(),recalled_by=$3::text WHERE tenant_id=$1 AND id=$2",
                    ids["tenant"],
                    ids["production_batch"],
                    str(ids["account"]),
                )
            finally:
                await owner.execute("ALTER TABLE public.production_batches ENABLE TRIGGER USER")
        await rejected("23514")
        async with owner.transaction():
            await owner.execute("ALTER TABLE public.production_batches DISABLE TRIGGER USER")
            try:
                await owner.execute(
                    "UPDATE production_batches SET status='active',recall_reason=NULL,recalled_at=NULL,recalled_by=NULL "
                    "WHERE tenant_id=$1 AND id=$2",
                    ids["tenant"],
                    ids["production_batch"],
                )
            finally:
                await owner.execute("ALTER TABLE public.production_batches ENABLE TRIGGER USER")

        async with owner.transaction():
            await owner.execute("ALTER TABLE public.code_batches DISABLE TRIGGER USER")
            try:
                await owner.execute(
                    "UPDATE code_batches SET status='completed' WHERE tenant_id=$1 AND id=$2",
                    ids["tenant"],
                    batch_id,
                )
            finally:
                await owner.execute("ALTER TABLE public.code_batches ENABLE TRIGGER USER")
        await rejected("23514")
        async with owner.transaction():
            await owner.execute("ALTER TABLE public.code_batches DISABLE TRIGGER USER")
            try:
                await owner.execute(
                    "UPDATE code_batches SET status='activated' WHERE tenant_id=$1 AND id=$2",
                    ids["tenant"],
                    batch_id,
                )
            finally:
                await owner.execute("ALTER TABLE public.code_batches ENABLE TRIGGER USER")

        async with owner.transaction():
            await owner.execute("ALTER TABLE public.code_items DISABLE TRIGGER USER")
            try:
                await owner.execute(
                    "UPDATE code_items SET status='frozen',frozen_from_status='activated',frozen_at=now(),"
                    "frozen_by='acceptance',freeze_reason='risk',freeze_provenance_version=1 "
                    "WHERE tenant_id=$1 AND id=$2",
                    ids["tenant"],
                    item_id,
                )
            finally:
                await owner.execute("ALTER TABLE public.code_items ENABLE TRIGGER USER")
        await rejected("23514")
        async with owner.transaction():
            await owner.execute("ALTER TABLE public.code_items DISABLE TRIGGER USER")
            try:
                await owner.execute(
                    "UPDATE code_items SET status='activated',frozen_from_status=NULL,frozen_at=NULL,frozen_by=NULL,"
                    "freeze_reason=NULL,freeze_provenance_version=NULL WHERE tenant_id=$1 AND id=$2",
                    ids["tenant"],
                    item_id,
                )
            finally:
                await owner.execute("ALTER TABLE public.code_items ENABLE TRIGGER USER")

        tenant_lock = owner.transaction()
        await tenant_lock.start()
        await owner.fetchval("SELECT id FROM tenants WHERE id=$1 FOR UPDATE", ids["tenant"])
        await rejected("55P03")
        await tenant_lock.rollback()
        await owner.execute(
            "INSERT INTO risk_alerts(id,tenant_id,alert_type,public_id,code_item_id,detail,resolved,risk_level,"
            "created_at,updated_at) VALUES($1,$2,'suspected_copy',$3,$4,'acceptance risk',false,'high',now(),now())",
            uuid.uuid4(),
            ids["tenant"],
            public_id,
            item_id,
        )
        await rejected("23514")
    finally:
        await runtime.close()
        await owner.close()


async def test_verified_callback_role_settles_exact_claim_atomically(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import database
    from app.main import app
    from app.services import campaign_callback_authority
    from app.services.connectors.secrets import encrypt_secrets

    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    callback = await _callback_connection(migrated_pg_url)
    runtime = await _runtime_connection(migrated_pg_url)
    runtime_engine = create_async_engine(migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    control_engine = create_async_engine(migrated_pg_url)
    callback_engine = create_async_engine(
        migrated_pg_url.replace("yimatong:yimatong@", "yimatong_callback:yimatong_callback@")
    )
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    control_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)
    callback_factory = async_sessionmaker(callback_engine, class_=AsyncSession, expire_on_commit=False)
    ids = await _seed_catalog(owner, "campaign-callback")
    connector_id, benefit_id, claim_id, outbox_id, delivery_id = (uuid.uuid4() for _ in range(5))
    external_id = str(claim_id)
    callback_secret = "campaign-callback-secret"
    monkeypatch.setenv("AES_MASTER_KEY_V1", "11" * 32)
    await owner.execute(
        "INSERT INTO connectors(id,tenant_id,name,connector_type,config,secrets_encrypted,enabled,created_at,updated_at) "
        "VALUES($1,$2,'cash callback','generic_http','{\"api_url\":\"https://api.example.com\"}',"
        "$3,true,now(),now())",
        connector_id,
        ids["tenant"],
        encrypt_secrets({"callback_secret": callback_secret}),
    )
    await owner.execute(
        "INSERT INTO benefits(id,tenant_id,name,benefit_type,config_json,connector_id,stock_total,stock_used,"
        "per_person_limit,status,created_at,updated_at) VALUES($1,$2,'cash callback','cash_red_packet',"
        '\'{"amount_type":"fixed","fixed_amount":100,"budget":100,"claimed_budget":100}\'::jsonb,'
        "$3,1,1,1,'active',now(),now())",
        benefit_id,
        ids["tenant"],
        connector_id,
    )
    await owner.execute(
        "INSERT INTO benefit_claims(id,tenant_id,benefit_id,consumer_id,idempotency_key,claim_type,status,"
        "delivery_status,reserved_amount,reservation_status,created_at,updated_at) "
        "VALUES($1,$2,$3,'consumer-callback','callback-idem','claim','success','pending',100,'reserved',now(),now())",
        claim_id,
        ids["tenant"],
        benefit_id,
    )
    await owner.execute(
        "INSERT INTO campaign_claim_outbox(id,tenant_id,claim_id,event_type,payload,status,attempt_count,max_attempts,"
        "next_attempt_at,created_at,updated_at) VALUES($1,$2,$3,'claim_committed','{}','pending',0,8,now(),now(),now())",
        outbox_id,
        ids["tenant"],
        claim_id,
    )
    try:
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            lease = await runtime.fetchrow(
                "SELECT * FROM lease_campaign_claim_outbox($1,'callback-worker',1,30)", ids["tenant"]
            )
            pending = await runtime.fetchrow(
                "SELECT * FROM record_campaign_claim_delivery_result($1,$2,$3,$4,$5,'pending',$6,$7::jsonb,30)",
                ids["tenant"],
                outbox_id,
                lease["lease_token"],
                delivery_id,
                connector_id,
                external_id,
                '{"provider_state":"PROCESSING"}',
            )
            assert pending["current_status"] == "awaiting_callback"
            assert pending["claim_delivery_status"] == "processing"
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            assert (
                await runtime.fetchrow(
                    "SELECT * FROM lease_campaign_claim_outbox($1,'too-early-worker',1,30)", ids["tenant"]
                )
                is None
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime.execute(
                    "UPDATE benefit_deliveries SET status='success' WHERE tenant_id=$1 AND id=$2",
                    ids["tenant"],
                    delivery_id,
                )
        assert await owner.fetchval(
            "SELECT campaign_outbox_id=$3 AND external_id=$4 FROM benefit_deliveries WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            delivery_id,
            outbox_id,
            external_id,
        )
        await owner.execute(
            "UPDATE campaign_claim_outbox SET next_attempt_at=now()-interval '1 second' WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            outbox_id,
        )
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            timeout_retry = await runtime.fetchrow(
                "SELECT * FROM lease_campaign_claim_outbox($1,'callback-timeout-worker',1,30)", ids["tenant"]
            )
            assert timeout_retry["callback_timed_out"] is True
            assert timeout_retry["delivery_id"] == delivery_id
            assert timeout_retry["external_id"] == external_id
        monkeypatch.setattr(database, "async_session_factory", runtime_factory)
        monkeypatch.setattr(database, "control_session_factory", control_factory)
        monkeypatch.setattr(database, "_is_pg", True)
        monkeypatch.setattr(campaign_callback_authority, "callback_session_factory", callback_factory)
        callback_body = json.dumps(
            {"id": external_id, "status": "success", "provider_state": "SUCCESS"},
            separators=(",", ":"),
        ).encode()
        callback_signature = hmac.new(callback_secret.encode(), callback_body, hashlib.sha256).hexdigest()
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/v1/connectors/connectors/{connector_id}/callback",
                content=callback_body,
                headers={"Content-Type": "application/json", "X-Callback-Sig": callback_signature},
            )
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ok"}
        state = await owner.fetchrow(
            "SELECT claim.reservation_status,claim.delivery_status,box.status AS outbox_status,"
            "delivery.status AS delivery_status_value FROM benefit_claims claim "
            "JOIN campaign_claim_outbox box ON box.tenant_id=claim.tenant_id AND box.claim_id=claim.id "
            "JOIN benefit_deliveries delivery ON delivery.tenant_id=claim.tenant_id AND delivery.claim_id=claim.id "
            "WHERE claim.tenant_id=$1 AND claim.id=$2",
            ids["tenant"],
            claim_id,
        )
        assert dict(state) == {
            "reservation_status": "settled",
            "delivery_status": "success",
            "outbox_status": "delivered",
            "delivery_status_value": "success",
        }
        async with callback.transaction():
            await callback.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            replayed = await callback.fetchrow(
                "SELECT * FROM settle_campaign_claim_callback($1,$2,$3,$4,$5,$6,'success',$7::jsonb)",
                ids["tenant"],
                uuid.uuid4(),
                connector_id,
                delivery_id,
                claim_id,
                external_id,
                "{}",
            )
            assert replayed["replayed"] is True
        async with callback.transaction():
            await callback.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            with pytest.raises(asyncpg.PostgresError) as conflict:
                await callback.fetchrow(
                    "SELECT * FROM settle_campaign_claim_callback($1,$2,$3,$4,$5,$6,'failed',$7::jsonb)",
                    ids["tenant"],
                    uuid.uuid4(),
                    connector_id,
                    delivery_id,
                    claim_id,
                    external_id,
                    "{}",
                )
            assert conflict.value.sqlstate == "23505"
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime.fetchrow(
                    "SELECT * FROM settle_campaign_claim_callback($1,$2,$3,$4,$5,$6,'success',$7::jsonb)",
                    ids["tenant"],
                    uuid.uuid4(),
                    connector_id,
                    delivery_id,
                    claim_id,
                    external_id,
                    "{}",
                )
    finally:
        await runtime.close()
        await callback.close()
        await owner.close()
        await runtime_engine.dispose()
        await control_engine.dispose()
        await callback_engine.dispose()


async def test_api_key_bound_coupon_redeem_is_audited_and_fail_closed(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await _runtime_connection(migrated_pg_url)
    ids = await _seed_catalog(owner, "campaign-redeem")
    benefit_id, claim_id, failed_claim_id, api_key_id = (uuid.uuid4() for _ in range(4))
    await owner.execute(
        "INSERT INTO benefits(id,tenant_id,name,benefit_type,config_json,stock_total,stock_used,per_person_limit,"
        "status,created_at,updated_at) VALUES($1,$2,'coupon','platform_coupon','{}',2,2,2,'active',now(),now())",
        benefit_id,
        ids["tenant"],
    )
    await owner.executemany(
        "INSERT INTO benefit_claims(id,tenant_id,benefit_id,consumer_id,idempotency_key,claim_type,status,"
        "delivery_status,reservation_status,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,'claim',$6,'not_required','not_required',now(),now())",
        [
            (claim_id, ids["tenant"], benefit_id, "redeemable", "redeemable", "claimed"),
            (failed_claim_id, ids["tenant"], benefit_id, "failed", "failed", "failed"),
        ],
    )
    async with owner.transaction():
        await owner.execute("ALTER TABLE public.api_keys DISABLE TRIGGER USER")
        try:
            await owner.execute(
                "INSERT INTO api_keys(id,tenant_id,name,key_prefix,key_digest,role,permissions,revoked,expires_at,"
                "created_at,updated_at) VALUES($1,$2,'redeemer','ymt_12345678',$3,'coupon_operator',"
                "api_key_permissions_for_role('coupon_operator'),false,"
                "now()+interval '1 day',now(),now())",
                api_key_id,
                ids["tenant"],
                uuid.uuid4().hex * 2,
            )
        finally:
            await owner.execute("ALTER TABLE public.api_keys ENABLE TRIGGER USER")
    try:
        audit_id = uuid.uuid4()
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            assert await runtime.fetchval("SELECT lock_active_api_key($1,$2)", ids["tenant"], api_key_id)
            redeemed = await runtime.fetchrow(
                "SELECT * FROM redeem_campaign_benefit_claim($1,$2,$3,$4)",
                ids["tenant"],
                api_key_id,
                audit_id,
                claim_id,
            )
            assert redeemed["current_status"] == "used"
        assert await owner.fetchval(
            "SELECT action='coupon_redeemed' AND operator_id=$2::text FROM platform_audit_log WHERE id=$1",
            audit_id,
            str(api_key_id),
        )
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            assert await runtime.fetchval("SELECT lock_active_api_key($1,$2)", ids["tenant"], api_key_id)
            with pytest.raises(asyncpg.PostgresError) as duplicate:
                await runtime.fetchrow(
                    "SELECT * FROM redeem_campaign_benefit_claim($1,$2,$3,$4)",
                    ids["tenant"],
                    api_key_id,
                    uuid.uuid4(),
                    claim_id,
                )
            assert duplicate.value.sqlstate == "23514"
        async with runtime.transaction():
            await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            assert await runtime.fetchval("SELECT lock_active_api_key($1,$2)", ids["tenant"], api_key_id)
            with pytest.raises(asyncpg.PostgresError) as failed:
                await runtime.fetchrow(
                    "SELECT * FROM redeem_campaign_benefit_claim($1,$2,$3,$4)",
                    ids["tenant"],
                    api_key_id,
                    uuid.uuid4(),
                    failed_claim_id,
                )
            assert failed.value.sqlstate == "23514"
        assert await owner.fetchval("SELECT status FROM benefit_claims WHERE id=$1", failed_claim_id) == "failed"
    finally:
        await runtime.close()
        await owner.close()
