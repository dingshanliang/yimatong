"""Real ASGI coverage for the authenticated external-order ledger boundary."""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from uuid6 import uuid7

from tests.test_acceptance.conftest import (
    ADMIN_DSN,
    BACKEND_DIR,
    AcceptanceDatabaseLease,
    _create_owned_database,
    _drop_database_with_retry,
    run_owned_migrations_with_snapshot_retry,
)
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic
from tests.test_acceptance.test_consumer_consent_authority import _owner_dsn
from tests.test_acceptance.test_external_order_value_ledger import _seed_actor

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


@pytest.fixture
async def external_order_pg_url(migrated_pg_url: str) -> AsyncGenerator[str, None]:
    database_name = f"yimatong_acceptance_u8a_asgi_{uuid.uuid4().hex[:10]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(database_name, database_url, uuid.uuid4().hex)
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        owner = await asyncpg.connect(_owner_dsn(database_url))
        try:
            await owner.execute((BACKEND_DIR / "scripts" / "init_runtime_role.sql").read_text())
        finally:
            await owner.close()
        yield database_url
    finally:
        if lease.created:
            await _drop_database_with_retry(
                lease.database_name,
                ADMIN_DSN,
                expected_owner_marker=lease.owner_marker,
                allow_unmarked_created=not lease.marker_written,
            )


async def test_external_order_asgi_authority_replay_adjustments_isolation_and_analytics(
    external_order_pg_url: str, monkeypatch
) -> None:
    from app.core import database
    from app.main import app
    from app.services import gmv_access
    from app.services.redis_cache import AsyncRedisCache
    from app.utils.security import create_access_token

    # Populate before the U8A ledger cutover so legacy value classification is
    # exercised; order:read/manage still comes only from the later u8a3 grant.
    await asyncio.to_thread(_alembic, external_order_pg_url, "downgrade", "u8a1d2e3f4a5")
    owner = await asyncpg.connect(_owner_dsn(external_order_pg_url))
    tenant_id, admin_id = await _seed_actor(owner, "u8a-asgi")
    identities: dict[str, tuple[uuid.UUID, int, uuid.UUID]] = {
        "admin": (admin_id, 1, uuid.uuid4()),
    }
    admin_role_id = await owner.fetchval(
        "SELECT role_id FROM account_roles WHERE tenant_id=$1 AND account_id=$2",
        tenant_id,
        admin_id,
    )
    analytics_permission_id = uuid7()
    await owner.execute(
        "INSERT INTO permissions(id,tenant_id,code,description,created_at,updated_at) "
        "VALUES($1,$2,'analytics:view','test analytics authority',now(),now())",
        analytics_permission_id,
        tenant_id,
    )
    await owner.execute(
        "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
        tenant_id,
        admin_role_id,
        analytics_permission_id,
    )
    # This deliberately invalid legacy fact must exist before the U8A cutover
    # so the migration records its recovery marker and leaves the value ledger
    # null. Creating it after head would bypass the legacy classification path.
    quarantined_order_id = uuid7()
    await owner.execute(
        "INSERT INTO external_orders(id,tenant_id,external_id,amount,order_time,matched,source_system,status,refund_amount,currency) "
        "VALUES($1,$2,'QUARANTINED-1',999,now()-interval '1 minute',false,'legacy','cancelled',0,'CNY')",
        quarantined_order_id,
        tenant_id,
    )
    for role_name in ("operator", "viewer", "distributor"):
        account_id, role_id = uuid.uuid4(), uuid.uuid4()
        await owner.execute(
            "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
            role_id,
            tenant_id,
            role_name,
        )
        await owner.execute(
            "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,is_active,auth_version,"
            "failed_login_attempts,created_at,updated_at) SELECT $1,tenant_id,organization_id,$2,'x',$3,true,1,0,"
            "now(),now() FROM accounts WHERE id=$4",
            account_id,
            f"u8a-{role_name}-{account_id.hex[:8]}@example.test",
            role_name,
            admin_id,
        )
        await owner.execute(
            "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
            tenant_id,
            account_id,
            role_id,
        )
        identities[role_name] = (account_id, 1, uuid.uuid4())
    await owner.close()

    await asyncio.to_thread(_alembic, external_order_pg_url, "upgrade", "head")
    owner = await asyncpg.connect(_owner_dsn(external_order_pg_url))
    locker = await asyncpg.connect(_owner_dsn(external_order_pg_url))
    assert (
        await owner.fetchval(
            "SELECT reason FROM external_order_ledger_recovery_markers WHERE tenant_id=$1 AND order_id=$2",
            tenant_id,
            quarantined_order_id,
        )
        == "legacy_cancel_has_no_immutable_provenance"
    )
    quarantined_ledger = await owner.fetchrow(
        "SELECT order_time,ledger_original_amount,ledger_refunded_amount,ledger_cancelled_amount,ledger_net_amount,ledger_status "
        "FROM external_orders WHERE tenant_id=$1 AND id=$2",
        tenant_id,
        quarantined_order_id,
    )
    assert quarantined_ledger is not None
    assert datetime.now(UTC) - timedelta(days=30) <= quarantined_ledger["order_time"] <= datetime.now(UTC)
    assert all(quarantined_ledger[column] is None for column in quarantined_ledger.keys() if column != "order_time")
    granted = await owner.fetch(
        "SELECT role.name,permission.code FROM roles role "
        "JOIN role_permissions rp ON rp.tenant_id=role.tenant_id AND rp.role_id=role.id "
        "JOIN permissions permission ON permission.tenant_id=rp.tenant_id AND permission.id=rp.permission_id "
        "WHERE role.tenant_id=$1 AND permission.code IN ('order:read','order:manage')",
        tenant_id,
    )
    assert {(row["name"], row["code"]) for row in granted} == {
        ("admin", "order:read"),
        ("admin", "order:manage"),
        ("operator", "order:read"),
        ("operator", "order:manage"),
    }
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM external_order_permission_backfill WHERE tenant_id=$1 AND created_grant",
            tenant_id,
        )
        == 4
    )
    assert await owner.fetchval(
        "SELECT EXISTS(SELECT 1 FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3)",
        tenant_id,
        admin_role_id,
        analytics_permission_id,
    )
    for account_id, auth_version, session_id in identities.values():
        await owner.execute(
            "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,now()+interval '1 hour',now(),now())",
            session_id,
            tenant_id,
            account_id,
            auth_version,
            uuid.uuid4().hex,
        )

    runtime_url = external_order_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    runtime_probe = await asyncpg.connect(runtime_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await runtime_probe.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
        assert (
            await runtime_probe.fetchval(
                "SELECT count(*) FROM external_orders WHERE tenant_id=$1 AND order_time>=$2 AND order_time<=$3 "
                "AND ledger_net_amount IS NULL",
                tenant_id,
                datetime.now(UTC) - timedelta(days=30),
                datetime.now(UTC),
            )
            == 1
        )
    finally:
        await runtime_probe.close()
    runtime_engine = create_async_engine(runtime_url)
    owner_engine = create_async_engine(external_order_pg_url)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)

    async def cache_is_not_revoked(self, key: str) -> bool:
        return False

    async def allow_rate(*args, **kwargs):
        return True, 1

    monkeypatch.setattr(AsyncRedisCache, "is_token_revoked", cache_is_not_revoked)
    monkeypatch.setattr(gmv_access._gmv_security_cache, "rate_limit_check_shared", allow_rate)

    def headers(role: str, idempotency_key: uuid.UUID | None = None) -> dict[str, str]:
        account_id, auth_version, session_id = identities[role]
        token = create_access_token(
            str(tenant_id),
            str(account_id),
            role,
            "brand",
            extra={"sid": str(session_id), "auth_version": auth_version},
        )
        result = {"Authorization": f"Bearer {token}"}
        if idempotency_key:
            result["Idempotency-Key"] = str(idempotency_key)
        return result

    import_key, refund_key, cancel_key = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    order = {
        "source_system": "erp",
        "orders": [
            {
                "external_id": "ASGI-ORDER-1",
                "amount": "100.10",
                "currency": "CNY",
                "status": "confirmed",
                "order_time": datetime.now(UTC).isoformat(),
                "customer_reference": "customer-1",
            }
        ],
    }
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            denied = await client.post("/api/v1/gmv/orders/import", json=order, headers=headers("viewer", import_key))
            assert denied.status_code == 403, denied.text
            distributor_denied = await client.post(
                "/api/v1/gmv/orders/import", json=order, headers=headers("distributor", import_key)
            )
            assert distributor_denied.status_code == 403, distributor_denied.text

            api_key_denied = await client.post(
                "/api/v1/gmv/orders/import",
                json=order,
                headers={"X-Api-Key": "not-an-order-credential", "Idempotency-Key": str(import_key)},
            )
            assert api_key_denied.status_code == 401, api_key_denied.text

            platform_token = create_access_token("platform", "platform-admin", "platform_admin", "platform")
            platform_denied = await client.post(
                "/api/v1/gmv/orders/import",
                json=order,
                headers={"Authorization": f"Bearer {platform_token}", "Idempotency-Key": str(import_key)},
            )
            assert platform_denied.status_code == 401, platform_denied.text

            acting_account_id, acting_auth_version, acting_session_id = identities["admin"]
            acting_token = create_access_token(
                str(tenant_id),
                str(acting_account_id),
                "admin",
                "brand",
                extra={
                    "sid": str(acting_session_id),
                    "auth_version": acting_auth_version,
                    "acting_tenant_id": str(uuid.uuid4()),
                },
            )
            acting_denied = await client.post(
                "/api/v1/gmv/orders/import",
                json=order,
                headers={"Authorization": f"Bearer {acting_token}", "Idempotency-Key": str(import_key)},
            )
            assert acting_denied.status_code == 403, acting_denied.text

            operator_read = await client.get("/api/v1/gmv/orders", headers=headers("operator"))
            assert operator_read.status_code == 200, operator_read.text

            lock_transaction = locker.transaction()
            await lock_transaction.start()
            assert await locker.fetchval(
                "SELECT pg_try_advisory_xact_lock(hashtextextended('external-order:'||$1::uuid||':erp:ASGI-ORDER-1',0))",
                tenant_id,
            )
            busy = await client.post("/api/v1/gmv/orders/import", json=order, headers=headers("admin", import_key))
            assert busy.status_code == 200, busy.text
            assert busy.json()["failed"] == 1
            assert busy.json()["errors"][0]["status_code"] == 409
            assert busy.json()["errors"][0]["retry_after"] == "1"
            assert (
                await owner.fetchval("SELECT count(*) FROM external_order_value_receipts WHERE tenant_id=$1", tenant_id)
                == 0
            )
            await lock_transaction.commit()

            created = await client.post("/api/v1/gmv/orders/import", json=order, headers=headers("admin", import_key))
            assert created.status_code == 200, created.text
            assert created.json()["created"] == 1
            replay = await client.post("/api/v1/gmv/orders/import", json=order, headers=headers("admin", import_key))
            assert replay.status_code == 200, replay.text
            assert replay.json()["replayed"] == 1
            changed = await client.post(
                "/api/v1/gmv/orders/import",
                json={**order, "orders": [{**order["orders"][0], "amount": "100.11"}]},
                headers=headers("admin", import_key),
            )
            assert changed.status_code == 200, changed.text
            assert changed.json()["failed"] == 1
            assert changed.json()["errors"][0]["status_code"] == 409

            refund = await client.post(
                "/api/v1/gmv/orders/ASGI-ORDER-1/refund",
                json={
                    "source_system": "erp",
                    "amount": "30.05",
                    "currency": "CNY",
                    "reason": "customer return",
                    "occurred_at": datetime.now(UTC).isoformat(),
                },
                headers=headers("admin", refund_key),
            )
            assert refund.status_code == 200, refund.text
            assert refund.json()["net_amount"] == 70.05
            cancel = await client.post(
                "/api/v1/gmv/orders/ASGI-ORDER-1/cancel",
                json={
                    "source_system": "erp",
                    "reason": "payment reversed",
                    "occurred_at": datetime.now(UTC).isoformat(),
                },
                headers=headers("admin", cancel_key),
            )
            assert cancel.status_code == 200, cancel.text
            assert cancel.json()["net_amount"] == 0

            funnel = await client.get("/api/v1/analytics/conversion-funnel", headers=headers("admin"))
            assert funnel.status_code == 200, funnel.text
            assert funnel.json()["net_amount"] == 0
            assert funnel.json()["order_amount"] == 100.1
            assert funnel.json()["cancelled_amount"] == 70.05
            assert funnel.json()["order_data_quality"] == "incomplete"
            assert funnel.json()["quarantined_order_count"] == 1

            cross_token = create_access_token(
                str(uuid.uuid4()),
                str(admin_id),
                "admin",
                "brand",
                extra={"sid": str(identities["admin"][2]), "auth_version": 1},
            )
            cross = await client.post(
                "/api/v1/gmv/orders/import",
                json=order,
                headers={"Authorization": f"Bearer {cross_token}", "Idempotency-Key": str(uuid.uuid4())},
            )
            assert cross.status_code == 401, cross.text

            operator_order = {
                **order,
                "orders": [{**order["orders"][0], "external_id": "ASGI-OPERATOR-1", "amount": "1.00"}],
            }
            operator_created = await client.post(
                "/api/v1/gmv/orders/import",
                json=operator_order,
                headers=headers("operator", uuid.uuid4()),
            )
            assert operator_created.status_code == 200, operator_created.text
            assert operator_created.json()["created"] == 1

            async def immutable_fact_counts() -> tuple[int, int]:
                row = await owner.fetchrow(
                    "SELECT (SELECT count(*) FROM external_order_value_receipts WHERE tenant_id=$1),"
                    "(SELECT count(*) FROM external_order_value_events WHERE tenant_id=$1)",
                    tenant_id,
                )
                return row[0], row[1]

            denied_order = {
                **order,
                "orders": [{**order["orders"][0], "external_id": "ASGI-SESSION-DENIED"}],
            }
            facts_before_denials = await immutable_fact_counts()
            admin_session_id = identities["admin"][2]

            await owner.execute("UPDATE auth_sessions SET revoked_at=now() WHERE id=$1", admin_session_id)
            revoked = await client.post(
                "/api/v1/gmv/orders/import",
                json=denied_order,
                headers=headers("admin", uuid.uuid4()),
            )
            assert revoked.status_code == 401, revoked.text
            assert await immutable_fact_counts() == facts_before_denials
            await owner.execute("UPDATE auth_sessions SET revoked_at=NULL WHERE id=$1", admin_session_id)

            await owner.execute(
                "UPDATE auth_sessions SET expires_at=now()-interval '1 second' WHERE id=$1",
                admin_session_id,
            )
            expired = await client.post(
                "/api/v1/gmv/orders/import",
                json=denied_order,
                headers=headers("admin", uuid.uuid4()),
            )
            assert expired.status_code == 401, expired.text
            assert await immutable_fact_counts() == facts_before_denials
            await owner.execute(
                "UPDATE auth_sessions SET expires_at=now()+interval '1 hour' WHERE id=$1",
                admin_session_id,
            )

            await owner.execute("UPDATE auth_sessions SET auth_version=2 WHERE id=$1", admin_session_id)
            version_stale = await client.post(
                "/api/v1/gmv/orders/import",
                json=denied_order,
                headers=headers("admin", uuid.uuid4()),
            )
            assert version_stale.status_code == 401, version_stale.text
            assert await immutable_fact_counts() == facts_before_denials
            await owner.execute("UPDATE auth_sessions SET auth_version=1 WHERE id=$1", admin_session_id)

            admin_role_id = await owner.fetchval(
                "SELECT id FROM roles WHERE tenant_id=$1 AND name='admin'",
                tenant_id,
            )
            manage_permission_id = await owner.fetchval(
                "SELECT id FROM permissions WHERE tenant_id=$1 AND code='order:manage'",
                tenant_id,
            )
            await owner.execute(
                "DELETE FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3",
                tenant_id,
                admin_role_id,
                manage_permission_id,
            )
            permission_removed = await client.post(
                "/api/v1/gmv/orders/import",
                json=denied_order,
                headers=headers("admin", uuid.uuid4()),
            )
            assert permission_removed.status_code == 403, permission_removed.text
            assert await immutable_fact_counts() == facts_before_denials
            await owner.execute(
                "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
                tenant_id,
                admin_role_id,
                manage_permission_id,
            )

            oversized = await client.post(
                "/api/v1/gmv/orders/import",
                content=b"{" + b"x" * (256 * 1024) + b"}",
                headers={**headers("admin", uuid.uuid4()), "Content-Type": "application/json"},
            )
            assert oversized.status_code == 413, oversized.text

            async def reject_rate(*args, **kwargs):
                return False, 0

            monkeypatch.setattr(gmv_access._gmv_security_cache, "rate_limit_check_shared", reject_rate)
            limited = await client.post("/api/v1/gmv/orders/import", json=order, headers=headers("admin", uuid.uuid4()))
            assert limited.status_code == 429, limited.text
            assert limited.headers["Retry-After"] == "60"
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()
        await locker.close()
        await owner.close()
