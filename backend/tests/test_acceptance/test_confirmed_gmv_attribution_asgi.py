"""Real ASGI proof for durable-session-bound confirmed GMV attribution."""

import asyncio
import hashlib
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
async def confirmed_gmv_pg_url(migrated_pg_url: str) -> AsyncGenerator[str, None]:
    database_name = f"yimatong_acceptance_u8b_asgi_{uuid.uuid4().hex[:10]}"
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


async def test_confirmed_attribution_asgi_requires_live_direct_order_manager(
    confirmed_gmv_pg_url: str, monkeypatch
) -> None:
    from app.core import database
    from app.main import app
    from app.services import gmv_access
    from app.services.redis_cache import AsyncRedisCache
    from app.utils.security import create_access_token

    await asyncio.to_thread(_alembic, confirmed_gmv_pg_url, "downgrade", "u8a2e3f4a5b6")
    owner = await asyncpg.connect(_owner_dsn(confirmed_gmv_pg_url))
    tenant_id, admin_id = await _seed_actor(owner, "u8b-asgi")
    admin_session_id = await owner.fetchval(
        "SELECT id FROM auth_sessions WHERE tenant_id=$1 AND account_id=$2", tenant_id, admin_id
    )
    identities: dict[str, tuple[uuid.UUID, uuid.UUID]] = {"admin": (admin_id, admin_session_id)}
    organization_id = await owner.fetchval("SELECT organization_id FROM accounts WHERE id=$1", admin_id)
    for role_name in ("operator", "viewer", "distributor"):
        account_id, role_id, session_id = uuid7(), uuid7(), uuid7()
        await owner.execute(
            "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
            role_id,
            tenant_id,
            role_name,
        )
        await owner.execute(
            "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,is_active,auth_version,"
            "failed_login_attempts,created_at,updated_at) VALUES($1,$2,$3,$4,'x',$5,true,1,0,now(),now())",
            account_id,
            tenant_id,
            organization_id,
            f"u8b-{role_name}-{account_id.hex[:8]}@example.test",
            role_name,
        )
        await owner.execute(
            "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
            tenant_id,
            account_id,
            role_id,
        )
        await owner.execute(
            "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,"
            "created_at,updated_at) VALUES($1,$2,$3,1,$4,now()+interval '1 hour',now(),now())",
            session_id,
            tenant_id,
            account_id,
            uuid.uuid4().hex,
        )
        identities[role_name] = (account_id, session_id)

    foreign_tenant_id, foreign_admin_id = await _seed_actor(owner, "u8b-asgi-foreign")
    foreign_session_id = await owner.fetchval(
        "SELECT id FROM auth_sessions WHERE tenant_id=$1 AND account_id=$2", foreign_tenant_id, foreign_admin_id
    )
    await owner.execute("UPDATE tenants SET tenant_type='agency' WHERE id=$1", foreign_tenant_id)
    await owner.close()

    await asyncio.to_thread(_alembic, confirmed_gmv_pg_url, "upgrade", "head")
    owner = await asyncpg.connect(_owner_dsn(confirmed_gmv_pg_url))
    grants = await owner.fetch(
        "SELECT role.name,permission.code FROM roles role "
        "JOIN role_permissions rp ON rp.tenant_id=role.tenant_id AND rp.role_id=role.id "
        "JOIN permissions permission ON permission.tenant_id=rp.tenant_id AND permission.id=rp.permission_id "
        "WHERE role.tenant_id=$1 AND permission.code='order:manage'",
        tenant_id,
    )
    assert {(row["name"], row["code"]) for row in grants} == {
        ("admin", "order:manage"),
        ("operator", "order:manage"),
    }

    brand_id, product_id, sku_id, production_batch_id, batch_id, code_id = (uuid7() for _ in range(6))
    public_id = f"U8B{uuid.uuid4().hex[:12]}"
    await owner.execute(
        "INSERT INTO brands(id,tenant_id,name,status,created_at,updated_at) "
        "VALUES($1,$2,'u8b brand','active',now(),now())",
        brand_id,
        tenant_id,
    )
    await owner.execute(
        "INSERT INTO products(id,tenant_id,brand_id,name,status,created_at,updated_at) "
        "VALUES($1,$2,$3,'u8b product','active',now(),now())",
        product_id,
        tenant_id,
        brand_id,
    )
    await owner.execute(
        "INSERT INTO skus(id,tenant_id,product_id,code,name,status,created_at,updated_at) "
        "VALUES($1,$2,$3,'U8B-SKU','u8b sku','active',now(),now())",
        sku_id,
        tenant_id,
        product_id,
    )
    today = datetime.now(UTC).date()
    await owner.execute(
        "INSERT INTO production_batches(id,tenant_id,product_id,sku_id,batch_code,production_date,expiry_date,"
        "status,created_at,updated_at) VALUES($1,$2,$3,$4,'U8B-PB',$5,$6,'active',now(),now())",
        production_batch_id,
        tenant_id,
        product_id,
        sku_id,
        today,
        today + timedelta(days=365),
    )
    await owner.execute(
        "INSERT INTO code_batches(id,tenant_id,product_id,sku_id,production_batch_id,batch_code,quantity,status,"
        "code_type,generation_mode,created_by,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,'U8B-ASGI',1,'activated','single','item_level',$6,now(),now())",
        batch_id,
        tenant_id,
        product_id,
        sku_id,
        production_batch_id,
        admin_id,
    )
    await owner.execute(
        "INSERT INTO code_items(id,tenant_id,code_batch_id,public_id,status,code_type,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'activated','single',now(),now())",
        code_id,
        tenant_id,
        batch_id,
        public_id,
    )

    evidence: list[dict] = []
    for label in ("admin", "operator", "denied"):
        consumer_id, visitor_pk, scan_id, order_id = (uuid7() for _ in range(4))
        visitor_id = f"visitor-{label}-{uuid.uuid4()}"
        phone_hash = hashlib.sha256(f"phone-{label}".encode()).hexdigest()
        scan_time = datetime.now(UTC) - timedelta(minutes=10)
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
        await owner.execute(
            "INSERT INTO anonymous_visitors(id,tenant_id,visitor_id,consumer_id,last_seen_at,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,now(),now(),now())",
            visitor_pk,
            tenant_id,
            visitor_id,
            consumer_id,
        )
        await owner.execute(
            "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,is_first_scan,is_valid_visit,visitor_id,"
            "created_at,updated_at) VALUES($1,$2,$3,$4,false,true,$5,now(),now())",
            scan_id,
            tenant_id,
            public_id,
            scan_time,
            visitor_id,
        )
        await owner.execute(
            "INSERT INTO external_orders(id,tenant_id,external_id,amount,phone_hash,order_time,matched,source_system,"
            "status,refund_amount,currency,channel,ledger_original_amount,ledger_refunded_amount,"
            "ledger_cancelled_amount,ledger_net_amount,ledger_status,created_at,updated_at) "
            "VALUES($1,$2,$3,10,$4,now(),false,'erp','paid',0,'CNY','online',10,0,0,10,'paid',now(),now())",
            order_id,
            tenant_id,
            f"U8B-{label}-{order_id}",
            phone_hash,
        )
        evidence.append(
            {
                "external_order_id": str(order_id),
                "consumer_id": str(consumer_id),
                "scan_event_id": str(scan_id),
                "scan_time": scan_time.isoformat(),
                "attribution_window_hours": 720,
            }
        )

    runtime_url = confirmed_gmv_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    runtime_engine = create_async_engine(runtime_url)
    owner_engine = create_async_engine(confirmed_gmv_pg_url)
    monkeypatch.setattr(
        database,
        "async_session_factory",
        async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False),
    )
    monkeypatch.setattr(
        database,
        "control_session_factory",
        async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False),
    )
    monkeypatch.setattr(database, "_is_pg", True)

    async def cache_is_not_revoked(self, key: str) -> bool:
        return False

    async def allow_rate(*args, **kwargs):
        return True, 1

    monkeypatch.setattr(AsyncRedisCache, "is_token_revoked", cache_is_not_revoked)
    monkeypatch.setattr(gmv_access._gmv_security_cache, "rate_limit_check_shared", allow_rate)

    def headers(role: str, *, session_id: uuid.UUID | None = None) -> dict[str, str]:
        account_id, durable_session_id = identities[role]
        token = create_access_token(
            str(tenant_id),
            str(account_id),
            role,
            "brand",
            extra={"sid": str(session_id or durable_session_id), "auth_version": 1},
        )
        return {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}

    def agency_headers() -> dict[str, str]:
        token = create_access_token(
            str(foreign_tenant_id),
            str(foreign_admin_id),
            "admin",
            "agency",
            extra={"sid": str(foreign_session_id), "auth_version": 1},
        )
        return {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}

    async def fact_counts() -> tuple[int, int]:
        row = await owner.fetchrow(
            "SELECT (SELECT count(*) FROM gmv_attributions WHERE tenant_id=$1),"
            "(SELECT count(*) FROM gmv_attribution_confirmations WHERE tenant_id=$1)",
            tenant_id,
        )
        return row[0], row[1]

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            for index, role in enumerate(("admin", "operator")):
                response = await client.post(
                    "/api/v1/gmv/attributions/confirm", json=evidence[index], headers=headers(role)
                )
                assert response.status_code == 200, response.text
                assert response.json()["authority_status"] == "confirmed"
            assert await fact_counts() == (2, 2)

            before_denials = await fact_counts()

            async def assert_denied(request_headers: dict[str, str], expected_status: int) -> None:
                response = await client.post(
                    "/api/v1/gmv/attributions/confirm", json=evidence[2], headers=request_headers
                )
                assert response.status_code == expected_status, response.text
                assert await fact_counts() == before_denials

            await assert_denied(headers("viewer"), 403)
            await assert_denied(headers("distributor"), 403)
            await assert_denied(agency_headers(), 403)
            await assert_denied(headers("admin", session_id=uuid7()), 401)
            await assert_denied(headers("admin", session_id=foreign_session_id), 401)

            await owner.execute("UPDATE auth_sessions SET revoked_at=now() WHERE id=$1", admin_session_id)
            await assert_denied(headers("admin"), 401)
            await owner.execute("UPDATE auth_sessions SET revoked_at=NULL WHERE id=$1", admin_session_id)

            await owner.execute(
                "UPDATE auth_sessions SET expires_at=now()-interval '1 second' WHERE id=$1", admin_session_id
            )
            await assert_denied(headers("admin"), 401)
            await owner.execute(
                "UPDATE auth_sessions SET expires_at=now()+interval '1 hour' WHERE id=$1", admin_session_id
            )

            await owner.execute("UPDATE auth_sessions SET auth_version=2 WHERE id=$1", admin_session_id)
            await assert_denied(headers("admin"), 401)
            await owner.execute("UPDATE auth_sessions SET auth_version=1 WHERE id=$1", admin_session_id)

            operator_id = identities["operator"][0]
            await owner.execute("UPDATE accounts SET is_active=false WHERE id=$1", operator_id)
            await assert_denied(headers("operator"), 401)
            await owner.execute("UPDATE accounts SET is_active=true WHERE id=$1", operator_id)

            admin_role_id = await owner.fetchval("SELECT id FROM roles WHERE tenant_id=$1 AND name='admin'", tenant_id)
            permission_id = await owner.fetchval(
                "SELECT id FROM permissions WHERE tenant_id=$1 AND code='order:manage'", tenant_id
            )
            await owner.execute(
                "DELETE FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3",
                tenant_id,
                admin_role_id,
                permission_id,
            )
            await assert_denied(headers("admin"), 403)
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()
        await owner.close()
