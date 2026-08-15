"""PostgreSQL proof for exact consumer-to-scan GMV attribution."""

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from uuid6 import uuid7

from app.services.gmv import get_gmv_dashboard, get_roi_report
from tests.test_acceptance.conftest import (
    ADMIN_DSN,
    BACKEND_DIR,
    AcceptanceDatabaseLease,
    _create_owned_database,
    _drop_database_with_retry,
    run_owned_migrations_with_snapshot_retry,
    seed_baseline,
)
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

CALL = """SELECT * FROM public.confirm_gmv_attribution($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)"""


def _owner_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _runtime_dsn(url: str) -> str:
    return _owner_dsn(url).replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _call(
    conn,
    tenant_id,
    auth_session_id,
    order_id,
    consumer_id,
    scan_id,
    scan_time,
    key,
    payload,
    *,
    context_tenant_id=None,
):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(context_tenant_id or tenant_id))
        return await conn.fetchrow(
            CALL,
            tenant_id,
            auth_session_id,
            uuid7(),
            uuid7(),
            order_id,
            consumer_id,
            scan_id,
            scan_time,
            720,
            key,
            payload,
        )


async def _insert_auth_session(
    owner: asyncpg.Connection,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    auth_version: int = 0,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    blank_refresh: bool = False,
) -> uuid.UUID:
    session_id = uuid7()
    await owner.execute(
        "INSERT INTO auth_sessions(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,revoked_at,"
        "created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,now(),now())",
        session_id,
        account_id,
        tenant_id,
        auth_version,
        "" if blank_refresh else uuid.uuid4().hex,
        expires_at or datetime.now(UTC) + timedelta(hours=1),
        revoked_at,
    )
    return session_id


async def _insert_account(
    owner: asyncpg.Connection,
    tenant_id: uuid.UUID,
    *,
    role_name: str | None,
    is_active: bool = True,
    auth_version: int = 0,
) -> uuid.UUID:
    account_id = uuid7()
    organization_id = await owner.fetchval(
        "SELECT id FROM organizations WHERE tenant_id=$1 ORDER BY created_at,id LIMIT 1",
        tenant_id,
    )
    await owner.execute(
        "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,is_active,auth_version,"
        "must_change_password,failed_login_attempts,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'test-hash','U8B auth actor',$5,$6,false,0,now(),now())",
        account_id,
        tenant_id,
        organization_id,
        f"u8b-{account_id}@example.test",
        is_active,
        auth_version,
    )
    if role_name is not None:
        role_id = await owner.fetchval(
            "SELECT id FROM roles WHERE tenant_id=$1 AND name=$2",
            tenant_id,
            role_name,
        )
        if role_id is None:
            role_id = uuid7()
            await owner.execute(
                "INSERT INTO roles(id,tenant_id,name,description,created_at,updated_at) "
                "VALUES($1,$2,$3,'U8B authorization fixture',now(),now())",
                role_id,
                tenant_id,
                role_name,
            )
        await owner.execute(
            "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
            tenant_id,
            account_id,
            role_id,
        )
    return account_id


async def _assert_zero_authority_writes(owner: asyncpg.Connection, tenant_id: uuid.UUID) -> None:
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM gmv_attributions WHERE tenant_id=$1 AND authority_status='confirmed'",
            tenant_id,
        )
        == 0
    )
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM gmv_attribution_confirmations WHERE tenant_id=$1",
            tenant_id,
        )
        == 0
    )


async def test_00_clean_u8b_upgrade_downgrade_upgrade_round_trip(migrated_pg_url: str) -> None:
    database_name = f"yimatong_acceptance_u8b_{uuid.uuid4().hex[:12]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(database_name, database_url, uuid.uuid4().hex)
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        await asyncio.to_thread(_alembic, database_url, "downgrade", "u8a3f4a5b6c7")
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        owner = await asyncpg.connect(_owner_dsn(database_url))
        try:
            await owner.execute((BACKEND_DIR / "scripts" / "init_runtime_role.sql").read_text())
            assert await owner.fetchval(
                "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')",
                "public.confirm_gmv_attribution(uuid,uuid,uuid,uuid,uuid,uuid,uuid,timestamp with time zone,integer,text,text)",
            )
            assert await owner.fetchval(
                "SELECT to_regprocedure($1) IS NULL",
                "public.confirm_gmv_attribution(uuid,uuid,uuid,uuid,uuid,uuid,timestamp with time zone,integer,text,text)",
            )
        finally:
            await owner.close()
        await asyncio.to_thread(_alembic, database_url, "check")
    finally:
        if lease.created:
            await _drop_database_with_retry(
                lease.database_name,
                ADMIN_DSN,
                expected_owner_marker=lease.owner_marker,
                allow_unmarked_created=not lease.marker_written,
            )


async def test_01_populated_cutover_recovers_from_cic_timeout_and_revokes_legacy_updates(
    migrated_pg_url: str,
) -> None:
    database_name = f"yimatong_acceptance_u8b_populated_{uuid.uuid4().hex[:10]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(database_name, database_url, uuid.uuid4().hex)
    locker = None
    owner = None
    runtime = None
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        await asyncio.to_thread(_alembic, database_url, "upgrade", "u8a3f4a5b6c7")
        owner = await asyncpg.connect(_owner_dsn(database_url))
        tenant_id, attribution_id, order_id = uuid7(), uuid7(), uuid7()
        await owner.execute(
            "INSERT INTO gmv_attributions(id,tenant_id,external_order_id,amount,match_type,"
            "attribution_window_hours,confidence_score,original_amount,created_at,updated_at) "
            "VALUES($1,$2,$3,25,'legacy_latest_scan',168,0.5,25,now(),now())",
            attribution_id,
            tenant_id,
            order_id,
        )
        await asyncio.to_thread(_alembic, database_url, "upgrade", "u8b0c1d2e3f4")
        assert (
            await owner.fetchval("SELECT authority_status FROM gmv_attributions WHERE id=$1", attribution_id)
            == "legacy_quarantined"
        )

        locker = await asyncpg.connect(_owner_dsn(database_url))
        transaction = locker.transaction()
        await transaction.start()
        await locker.execute("LOCK TABLE gmv_attributions IN ACCESS EXCLUSIVE MODE")
        failed = await asyncio.to_thread(_alembic, database_url, "upgrade", "u8b1d2e3f4a5", succeeds=False)
        assert "lock timeout" in (failed.stdout + failed.stderr).lower()
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u8b0c1d2e3f4"
        await transaction.rollback()
        await locker.close()
        locker = None

        assert await owner.execute("UPDATE gmv_attributions SET amount=26 WHERE id=$1", attribution_id) == "UPDATE 1"
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        valid_indexes = await owner.fetchval(
            "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
            "WHERE c.relname=ANY($1::text[]) AND i.indisvalid AND i.indisready",
            [
                "uq_auth_sessions_tenant_id_id_u8b",
                "uq_gmv_attributions_tenant_id_u8b",
                "uq_gmv_attributions_confirmed_order_u8b",
            ],
        )
        assert valid_indexes == 3
        assert (
            await owner.fetchval("SELECT authority_status FROM gmv_attributions WHERE id=$1", attribution_id)
            == "legacy_quarantined"
        )

        await owner.execute(f'GRANT CONNECT ON DATABASE "{database_name}" TO yimatong_app')
        await owner.execute("GRANT USAGE ON SCHEMA public TO yimatong_app")
        runtime = await asyncpg.connect(_runtime_dsn(database_url))
        for mutation in (
            "UPDATE gmv_attributions SET amount=27 WHERE id=$1",
            "UPDATE gmv_attributions SET authority_status='confirmed' WHERE id=$1",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with runtime.transaction():
                    await runtime.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))
                    await runtime.execute(mutation, attribution_id)
        assert await owner.fetchval("SELECT amount FROM gmv_attributions WHERE id=$1", attribution_id) == 26
        assert (
            await owner.fetchval("SELECT authority_status FROM gmv_attributions WHERE id=$1", attribution_id)
            == "legacy_quarantined"
        )
    finally:
        if runtime is not None:
            await runtime.close()
        if locker is not None:
            await locker.close()
        if owner is not None:
            await owner.close()
        if lease.created:
            await _drop_database_with_retry(
                lease.database_name,
                ADMIN_DSN,
                expected_owner_marker=lease.owner_marker,
                allow_unmarked_created=not lease.marker_written,
            )


async def test_confirmed_attribution_requires_exact_consumer_scan_and_is_replay_safe(migrated_pg_url: str) -> None:
    baseline = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(baseline["baseline_tenant"]["id"])
    other_tenant_id = uuid.UUID(baseline["control_tenant"]["id"])
    public_id = baseline["first_public_id"]
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    runtime_a = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    runtime_b = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    try:
        admin = await owner.fetchrow(
            "SELECT id,auth_version FROM accounts WHERE tenant_id=$1 AND email='admin@baseline.local'",
            tenant_id,
        )
        control_admin = await owner.fetchrow(
            "SELECT id,auth_version FROM accounts WHERE tenant_id=$1 AND email='admin@baseline-control.local'",
            other_tenant_id,
        )
        assert admin is not None and control_admin is not None
        admin_session_id = await _insert_auth_session(
            owner,
            tenant_id,
            admin["id"],
            auth_version=admin["auth_version"],
        )
        control_session_id = await _insert_auth_session(
            owner,
            other_tenant_id,
            control_admin["id"],
            auth_version=control_admin["auth_version"],
        )
        operator_id = await _insert_account(owner, tenant_id, role_name="operator")
        operator_session_id = await _insert_auth_session(owner, tenant_id, operator_id)
        viewer_id = await _insert_account(owner, tenant_id, role_name="viewer")
        viewer_session_id = await _insert_auth_session(owner, tenant_id, viewer_id)
        distributor_id = await _insert_account(owner, tenant_id, role_name="distributor")
        distributor_session_id = await _insert_auth_session(owner, tenant_id, distributor_id)
        no_role_id = await _insert_account(owner, tenant_id, role_name=None)
        no_role_session_id = await _insert_auth_session(owner, tenant_id, no_role_id)
        inactive_id = await _insert_account(owner, tenant_id, role_name="admin", is_active=False)
        inactive_session_id = await _insert_auth_session(owner, tenant_id, inactive_id)
        stale_id = await _insert_account(owner, tenant_id, role_name="admin", auth_version=2)
        stale_session_id = await _insert_auth_session(owner, tenant_id, stale_id, auth_version=1)
        revoked_session_id = await _insert_auth_session(
            owner,
            tenant_id,
            admin["id"],
            auth_version=admin["auth_version"],
            revoked_at=datetime.now(UTC),
        )
        expired_session_id = await _insert_auth_session(
            owner,
            tenant_id,
            admin["id"],
            auth_version=admin["auth_version"],
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        blank_refresh_session_id = await _insert_auth_session(
            owner,
            tenant_id,
            admin["id"],
            auth_version=admin["auth_version"],
            blank_refresh=True,
        )

        async def rejected_auth_call(
            auth_session_id: uuid.UUID,
            expected_exception: type[Exception],
            *,
            requested_tenant_id: uuid.UUID = tenant_id,
            context_tenant_id: uuid.UUID = tenant_id,
        ) -> None:
            with pytest.raises(expected_exception):
                await _call(
                    runtime_a,
                    requested_tenant_id,
                    auth_session_id,
                    uuid7(),
                    uuid7(),
                    uuid7(),
                    datetime.now(UTC),
                    f"auth-negative-{uuid.uuid4()}",
                    _digest("auth-negative"),
                    context_tenant_id=context_tenant_id,
                )
            await _assert_zero_authority_writes(owner, requested_tenant_id)

        await rejected_auth_call(uuid7(), asyncpg.ForeignKeyViolationError)
        await rejected_auth_call(control_session_id, asyncpg.ForeignKeyViolationError)
        for denied_session_id in (
            revoked_session_id,
            expired_session_id,
            blank_refresh_session_id,
            stale_session_id,
            inactive_session_id,
            viewer_session_id,
            distributor_session_id,
            no_role_session_id,
            operator_session_id,
        ):
            await rejected_auth_call(denied_session_id, asyncpg.InsufficientPrivilegeError)

        await owner.execute("UPDATE tenants SET status='suspended' WHERE id=$1", other_tenant_id)
        try:
            await rejected_auth_call(
                control_session_id,
                asyncpg.InsufficientPrivilegeError,
                requested_tenant_id=other_tenant_id,
                context_tenant_id=other_tenant_id,
            )
        finally:
            await owner.execute("UPDATE tenants SET status='active' WHERE id=$1", other_tenant_id)

        async with owner.transaction():
            await owner.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                f"auth-session:{admin_session_id}",
            )
            await rejected_auth_call(admin_session_id, asyncpg.LockNotAvailableError)

        operator_role_id = await owner.fetchval(
            "SELECT id FROM roles WHERE tenant_id=$1 AND name='operator'",
            tenant_id,
        )
        order_manage_permission_id = await owner.fetchval(
            "SELECT id FROM permissions WHERE tenant_id=$1 AND code='order:manage'",
            tenant_id,
        )
        assert operator_role_id is not None and order_manage_permission_id is not None
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
            tenant_id,
            operator_role_id,
            order_manage_permission_id,
        )

        control_operator_id = await _insert_account(owner, other_tenant_id, role_name="operator")
        control_operator_session_id = await _insert_auth_session(owner, other_tenant_id, control_operator_id)
        control_operator_role_id = await owner.fetchval(
            "SELECT id FROM roles WHERE tenant_id=$1 AND name='operator'",
            other_tenant_id,
        )
        control_order_manage_permission_id = await owner.fetchval(
            "SELECT id FROM permissions WHERE tenant_id=$1 AND code='order:manage'",
            other_tenant_id,
        )
        assert control_operator_role_id is not None and control_order_manage_permission_id is not None
        await owner.execute(
            "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
            other_tenant_id,
            control_operator_role_id,
            control_order_manage_permission_id,
        )
        authorized_control_principals = await owner.fetchval(
            "SELECT count(DISTINCT session.id) FROM auth_sessions session "
            "JOIN account_roles ar ON ar.tenant_id=session.tenant_id AND ar.account_id=session.account_id "
            "JOIN roles role ON role.tenant_id=ar.tenant_id AND role.id=ar.role_id "
            "JOIN role_permissions rp ON rp.tenant_id=role.tenant_id AND rp.role_id=role.id "
            "JOIN permissions permission ON permission.tenant_id=rp.tenant_id AND permission.id=rp.permission_id "
            "WHERE session.id=ANY($1::uuid[]) AND role.name IN ('admin','operator') "
            "AND permission.code='order:manage'",
            [control_session_id, control_operator_session_id],
        )
        assert authorized_control_principals == 2
        try:
            for non_brand_type in ("agency", "regional_org", "platform"):
                await owner.execute("UPDATE tenants SET tenant_type=$2 WHERE id=$1", other_tenant_id, non_brand_type)
                await rejected_auth_call(
                    control_session_id,
                    asyncpg.InsufficientPrivilegeError,
                    requested_tenant_id=other_tenant_id,
                    context_tenant_id=other_tenant_id,
                )
                if non_brand_type == "agency":
                    await rejected_auth_call(
                        control_operator_session_id,
                        asyncpg.InsufficientPrivilegeError,
                        requested_tenant_id=other_tenant_id,
                        context_tenant_id=other_tenant_id,
                    )
        finally:
            await owner.execute("UPDATE tenants SET tenant_type='brand' WHERE id=$1", other_tenant_id)

        consumer_a, consumer_b = uuid7(), uuid7()
        visitor_a, visitor_b = f"visitor-{uuid.uuid4()}", f"visitor-{uuid.uuid4()}"
        phone_a, phone_b = _digest("phone-a"), _digest("phone-b")
        scan_time_a = datetime.now(UTC) - timedelta(hours=3)
        scan_time_b = datetime.now(UTC) - timedelta(hours=1)
        scan_received_a = datetime.now(UTC) - timedelta(hours=2)
        scan_received_b = datetime.now(UTC) - timedelta(minutes=30)
        scan_a, scan_b, order_id = uuid7(), uuid7(), uuid7()
        for consumer_id, phone_hash in ((consumer_a, phone_a), (consumer_b, phone_b)):
            await owner.execute(
                "INSERT INTO consumer_profiles(id,tenant_id,phone_hash,phone_ciphertext,phone_nonce,phone_key_id,"
                "member_level,total_points,lead_contact_suppressed,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,$5,'aes-master-v1','normal',0,false,now(),now())",
                consumer_id,
                tenant_id,
                phone_hash,
                b"x" * 27,
                b"n" * 12,
            )
        for visitor_id, consumer_id in ((visitor_a, consumer_a), (visitor_b, consumer_b)):
            await owner.execute(
                "INSERT INTO anonymous_visitors(id,tenant_id,visitor_id,consumer_id,last_seen_at,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,now(),now(),now())",
                uuid7(),
                tenant_id,
                visitor_id,
                consumer_id,
            )
        for scan_id, scan_time, received_at, visitor_id in (
            (scan_a, scan_time_a, scan_received_a, visitor_a),
            (scan_b, scan_time_b, scan_received_b, visitor_b),
        ):
            await owner.execute(
                "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,is_first_scan,is_valid_visit,visitor_id,"
                "created_at,updated_at) VALUES($1,$2,$3,$4,false,true,$5,$6,$6)",
                scan_id,
                tenant_id,
                public_id,
                scan_time,
                visitor_id,
                received_at,
            )
        order_time = datetime.now(UTC)
        await owner.execute(
            "INSERT INTO external_orders(id,tenant_id,external_id,amount,phone_hash,order_time,matched,source_system,"
            "status,refund_amount,currency,channel,ledger_original_amount,ledger_refunded_amount,ledger_cancelled_amount,"
            "ledger_net_amount,ledger_status,created_at,updated_at) "
            "VALUES($1,$2,$3,100,$4,$5,false,'erp','paid',0,'CNY','online',100,0,0,100,'paid',now(),now())",
            order_id,
            tenant_id,
            f"ORDER-{order_id}",
            phone_a,
            order_time,
        )

        with pytest.raises(asyncpg.ForeignKeyViolationError, match="consumer scan linkage"):
            await _call(
                runtime_a,
                tenant_id,
                admin_session_id,
                order_id,
                consumer_a,
                scan_b,
                scan_time_b,
                "wrong-scan",
                _digest("wrong"),
            )
        assert not await owner.fetchval("SELECT matched FROM external_orders WHERE id=$1", order_id)
        assert await owner.fetchval("SELECT count(*) FROM gmv_attributions WHERE external_order_id=$1", order_id) == 0

        payload = _digest("exact-evidence")
        results = await asyncio.gather(
            _call(
                runtime_a,
                tenant_id,
                admin_session_id,
                order_id,
                consumer_a,
                scan_a,
                scan_time_a,
                "exact-order",
                payload,
            ),
            _call(
                runtime_b,
                tenant_id,
                admin_session_id,
                order_id,
                consumer_a,
                scan_a,
                scan_time_a,
                "exact-order",
                payload,
            ),
        )
        assert sorted(row["replayed"] for row in results) == [False, True]
        assert len({row["attribution_id"] for row in results}) == 1
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM gmv_attributions WHERE tenant_id=$1 AND external_order_id=$2 "
                "AND authority_status='confirmed'",
                tenant_id,
                order_id,
            )
            == 1
        )
        confirmation = await owner.fetchrow(
            "SELECT auth_session_id,actor_account_id,consumer_id,scan_event_id,scan_received_at,window_started_at,window_ends_at,"
            "attribution_window_hours FROM gmv_attribution_confirmations WHERE tenant_id=$1 AND external_order_id=$2",
            tenant_id,
            order_id,
        )
        assert confirmation["auth_session_id"] == admin_session_id
        assert confirmation["actor_account_id"] == admin["id"]
        assert confirmation["consumer_id"] == consumer_a
        assert confirmation["scan_event_id"] == scan_a
        assert confirmation["scan_received_at"] == scan_received_a
        assert confirmation["window_started_at"] == scan_received_a
        assert confirmation["window_ends_at"] == scan_received_a + timedelta(hours=720)

        order_b = uuid7()
        await owner.execute(
            "INSERT INTO external_orders(id,tenant_id,external_id,amount,phone_hash,order_time,matched,source_system,"
            "status,refund_amount,currency,channel,ledger_original_amount,ledger_refunded_amount,ledger_cancelled_amount,"
            "ledger_net_amount,ledger_status,created_at,updated_at) "
            "VALUES($1,$2,$3,40,$4,$5,false,'erp','paid',0,'CNY','retail',40,0,0,40,'paid',now(),now())",
            order_b,
            tenant_id,
            f"ORDER-{order_b}",
            phone_b,
            order_time,
        )
        await _call(
            runtime_a,
            tenant_id,
            operator_session_id,
            order_b,
            consumer_b,
            scan_b,
            scan_time_b,
            "exact-order-b",
            _digest("b"),
        )
        operator_confirmation = await owner.fetchrow(
            "SELECT auth_session_id,actor_account_id FROM gmv_attribution_confirmations "
            "WHERE tenant_id=$1 AND external_order_id=$2",
            tenant_id,
            order_b,
        )
        assert operator_confirmation["auth_session_id"] == operator_session_id
        assert operator_confirmation["actor_account_id"] == operator_id

        engine = create_async_engine(migrated_pg_url)
        try:
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session, session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id',:tenant_id,true)"), {"tenant_id": str(tenant_id)}
                )
                online = await get_gmv_dashboard(session, tenant_id, channel="online")
                assert online["total_gmv"] == 100
                assert online["attributed_orders"] == online["total_orders"] == 1
                assert online["unattributed_orders"] == 0
                assert online["attribution_rate"] is None
                assert online["daily_trend"][0]["orders"] == 1
                assert online["by_channel"] == [{"channel": "online", "gmv": 100.0, "orders": 1}]

                outside = await get_gmv_dashboard(session, tenant_id, start_date=datetime.now(UTC) + timedelta(days=1))
                assert outside["total_gmv"] == outside["total_orders"] == 0
                assert outside["daily_trend"] == outside["by_channel"] == outside["by_campaign"] == []

                for unrelated_campaign in (uuid7(), uuid7()):
                    filtered = await get_gmv_dashboard(session, tenant_id, campaign_id=unrelated_campaign)
                    assert filtered["total_gmv"] == filtered["attributed_orders"] == 0
                    assert filtered["attribution_rate"] is None
                    assert filtered["daily_trend"] == filtered["by_channel"] == filtered["by_campaign"] == []
                    assert await get_roi_report(session, tenant_id, campaign_id=unrelated_campaign) == []
        finally:
            await engine.dispose()

        with pytest.raises(asyncpg.UniqueViolationError, match="idempotency payload conflicts"):
            await _call(
                runtime_a,
                tenant_id,
                admin_session_id,
                order_id,
                consumer_a,
                scan_a,
                scan_time_a,
                "exact-order",
                _digest("changed"),
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="authority denied"):
            await _call(
                runtime_a,
                tenant_id,
                admin_session_id,
                order_id,
                consumer_a,
                scan_a,
                scan_time_a,
                "cross-tenant",
                payload,
                context_tenant_id=other_tenant_id,
            )

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with runtime_a.transaction():
                await runtime_a.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))
                await runtime_a.execute(
                    "INSERT INTO gmv_attributions(id,tenant_id,external_order_id,amount,match_type,authority_status) "
                    "VALUES($1,$2,$3,100,'forged','confirmed')",
                    uuid7(),
                    tenant_id,
                    uuid7(),
                )
        with pytest.raises(asyncpg.DataError, match="invalid GMV attribution request"):
            async with runtime_a.transaction():
                await runtime_a.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_id))
                await runtime_a.fetchrow(
                    CALL,
                    tenant_id,
                    admin_session_id,
                    uuid.uuid4(),
                    uuid.uuid4(),
                    order_id,
                    consumer_a,
                    scan_a,
                    scan_time_a,
                    720,
                    "non-v7-identities",
                    payload,
                )

        await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", "u8b1d2e3f4a5", succeeds=False)
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u8b2e3f4a5b6"
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM gmv_attribution_confirmations WHERE tenant_id=$1 AND external_order_id=$2",
                tenant_id,
                order_id,
            )
            == 1
        )
    finally:
        await runtime_b.close()
        await runtime_a.close()
        await owner.close()
