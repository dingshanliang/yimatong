"""Real PostgreSQL proof for authoritative production-batch recall integrity."""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import date, timedelta

import asyncpg
import pytest

from tests.test_acceptance.conftest import BACKEND_DIR

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "d32f6a8b9c40"
REVISION = "2ed1cb06d0ca"


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
        timeout=180,
    )
    if succeeds:
        assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    else:
        assert result.returncode != 0, f"alembic {' '.join(args)} unexpectedly succeeded"
    return result


async def _insert_tenant_catalog(
    conn: asyncpg.Connection,
    label: str,
) -> dict[str, uuid.UUID]:
    ids = {
        key: uuid.uuid4()
        for key in (
            "tenant",
            "organization",
            "account",
            "admin_role",
            "brand",
            "product",
            "sku",
            "production_batch",
        )
    }
    transaction = conn.transaction()
    await transaction.start()
    await conn.execute(
        "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
        "VALUES($1,$2,$3,'active','free','brand',now(),now())",
        ids["tenant"],
        f"authority {label}",
        f"authority-{label}-{ids['tenant'].hex[:8]}",
    )
    await conn.execute(
        "INSERT INTO organizations(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
        ids["organization"],
        ids["tenant"],
        f"authority {label} org",
    )
    await conn.execute(
        "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,failed_login_attempts,"
        "is_active,auth_version,must_change_password,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'x',$5,0,true,0,false,now(),now())",
        ids["account"],
        ids["tenant"],
        ids["organization"],
        f"authority-{label}-{ids['account'].hex[:8]}@test.local",
        f"authority {label}",
    )
    await conn.execute(
        "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,'admin',now(),now())",
        ids["admin_role"],
        ids["tenant"],
    )
    await conn.execute(
        "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
        ids["tenant"],
        ids["account"],
        ids["admin_role"],
    )
    await conn.execute(
        "INSERT INTO brands(id,tenant_id,name,status,created_at,updated_at) VALUES($1,$2,$3,'active',now(),now())",
        ids["brand"],
        ids["tenant"],
        f"authority {label} brand",
    )
    await conn.execute(
        "INSERT INTO products(id,tenant_id,brand_id,name,status,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'active',now(),now())",
        ids["product"],
        ids["tenant"],
        ids["brand"],
        f"authority {label} product",
    )
    await conn.execute(
        "INSERT INTO skus(id,tenant_id,product_id,code,name,status,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,'active',now(),now())",
        ids["sku"],
        ids["tenant"],
        ids["product"],
        f"AUTH-{label}-{ids['sku'].hex[:8]}",
        f"authority {label} SKU",
    )
    await conn.execute(
        "INSERT INTO production_batches "
        "(id,tenant_id,product_id,sku_id,batch_code,production_date,expiry_date,status,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,'active',now(),now())",
        ids["production_batch"],
        ids["tenant"],
        ids["product"],
        ids["sku"],
        f"AUTH-PB-{label}-{ids['production_batch'].hex[:8]}",
        date.today(),
        date.today() + timedelta(days=365),
    )
    await transaction.commit()
    return ids


async def _insert_code_batch(
    conn: asyncpg.Connection,
    ids: dict[str, uuid.UUID],
    *,
    production_batch_id: uuid.UUID | None,
    status: str = "activated",
) -> uuid.UUID:
    code_batch_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO code_batches "
        "(id,tenant_id,product_id,sku_id,production_batch_id,batch_code,quantity,status,code_type,"
        "generation_mode,created_by,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,$6,3,$7,'single','item_level',$8,now(),now())",
        code_batch_id,
        ids["tenant"],
        ids["product"],
        ids["sku"],
        production_batch_id,
        f"AUTH-CB-{code_batch_id.hex[:8]}",
        status,
        ids["account"],
    )
    return code_batch_id


async def _assert_rejected(conn: asyncpg.Connection, sql: str, *args) -> Exception:
    try:
        async with conn.transaction():
            await conn.execute(sql, *args)
    except Exception as exc:
        return exc
    raise AssertionError("statement unexpectedly succeeded")


async def _insert_code_item(
    conn: asyncpg.Connection,
    tenant_id: uuid.UUID,
    code_batch_id: uuid.UUID,
    *,
    status: str = "created",
) -> uuid.UUID:
    item_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO code_items "
        "(id,tenant_id,code_batch_id,public_id,status,code_type,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,'single',now(),now())",
        item_id,
        tenant_id,
        code_batch_id,
        f"AUTH{item_id.hex[:16]}",
        status,
    )
    return item_id


async def _insert_additional_production_batch(
    conn: asyncpg.Connection,
    ids: dict[str, uuid.UUID],
    label: str,
) -> uuid.UUID:
    production_batch_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO production_batches "
        "(id,tenant_id,product_id,sku_id,batch_code,production_date,expiry_date,status,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,'active',now(),now())",
        production_batch_id,
        ids["tenant"],
        ids["product"],
        ids["sku"],
        f"AUTH-PB-{label}-{production_batch_id.hex[:8]}",
        date.today(),
        date.today() + timedelta(days=365),
    )
    return production_batch_id


async def test_preflight_reports_exact_legacy_rows_before_ddl(migrated_pg_url: str) -> None:
    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(dsn)
    try:
        tenant_a = await _insert_tenant_catalog(owner, "preflight-a")
        tenant_b = await _insert_tenant_catalog(owner, "preflight-b")
        cross_batch = await _insert_code_batch(
            owner,
            tenant_a,
            production_batch_id=tenant_b["production_batch"],
        )
        null_batch = await _insert_code_batch(owner, tenant_a, production_batch_id=None)
        cross_item = uuid.uuid4()
        await owner.execute(
            "INSERT INTO code_items "
            "(id,tenant_id,code_batch_id,public_id,status,code_type,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,'created','single',now(),now())",
            cross_item,
            tenant_b["tenant"],
            cross_batch,
            f"AUTH{cross_item.hex[:16]}",
        )
    finally:
        await owner.close()

    failed = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
    output = f"{failed.stdout}\n{failed.stderr}"
    assert f"code_batches.production_batch_id={null_batch}" in output
    assert f"code_batches.tenant_product_sku_production_batch={cross_batch}" in output
    assert f"code_items.tenant_code_batch={cross_item}" in output

    owner = await asyncpg.connect(dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == PARENT_REVISION
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_name='production_batches' AND column_name='recall_reason')"
        )
        await owner.execute("DELETE FROM code_items WHERE id=$1", cross_item)
        await owner.execute("DELETE FROM code_batches WHERE id=ANY($1::uuid[])", [cross_batch, null_batch])
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "head")


async def test_constraints_and_recall_are_fail_closed_for_owner_and_runtime(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(dsn)
    try:
        tenant_a = await _insert_tenant_catalog(owner, "runtime-a")
        tenant_b = await _insert_tenant_catalog(owner, "runtime-b")
        expired_batch_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO production_batches "
            "(id,tenant_id,product_id,sku_id,batch_code,production_date,expiry_date,status,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,'expired',now(),now())",
            expired_batch_id,
            tenant_a["tenant"],
            tenant_a["product"],
            tenant_a["sku"],
            f"AUTH-EXPIRED-{expired_batch_id.hex[:8]}",
            date.today() - timedelta(days=365),
            date.today() - timedelta(days=1),
        )
        expired_update = await _assert_rejected(
            owner,
            "UPDATE production_batches SET production_date=production_date+1 WHERE id=$1",
            expired_batch_id,
        )
        assert getattr(expired_update, "sqlstate", None) == "22023"

        assert isinstance(
            await _assert_rejected(
                owner,
                _code_batch_insert_sql(),
                uuid.uuid4(),
                tenant_a["tenant"],
                tenant_a["product"],
                tenant_a["sku"],
                tenant_b["production_batch"],
                f"AUTH-CROSS-{uuid.uuid4().hex[:8]}",
                tenant_a["account"],
            ),
            (asyncpg.CheckViolationError, asyncpg.ForeignKeyViolationError),
        )
        assert isinstance(
            await _assert_rejected(
                owner,
                _code_batch_insert_sql(),
                uuid.uuid4(),
                tenant_a["tenant"],
                tenant_a["product"],
                tenant_a["sku"],
                None,
                f"AUTH-NULL-{uuid.uuid4().hex[:8]}",
                tenant_a["account"],
            ),
            (asyncpg.CheckViolationError, asyncpg.NotNullViolationError),
        )

        await runtime_pg_conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a["tenant"]))
        assert isinstance(
            await _assert_rejected(
                runtime_pg_conn,
                _code_batch_insert_sql(),
                uuid.uuid4(),
                tenant_a["tenant"],
                tenant_a["product"],
                tenant_a["sku"],
                tenant_b["production_batch"],
                f"AUTH-RUNTIME-{uuid.uuid4().hex[:8]}",
                tenant_a["account"],
            ),
            (asyncpg.CheckViolationError, asyncpg.ForeignKeyViolationError),
        )

        code_batch_id = await _insert_code_batch(
            owner,
            tenant_a,
            production_batch_id=tenant_a["production_batch"],
            status="completed",
        )
        delivered_batch_id = await _insert_code_batch(
            owner,
            tenant_a,
            production_batch_id=tenant_a["production_batch"],
            status="printing",
        )
        activated_batch_id = await _insert_code_batch(
            owner,
            tenant_a,
            production_batch_id=tenant_a["production_batch"],
            status="completed",
        )
        recalled_status_batches = (
            (code_batch_id, "printing"),
            (delivered_batch_id, "delivered"),
            (activated_batch_id, "activated"),
        )
        item_ids = [uuid.uuid4() for _ in range(3)]
        await owner.executemany(
            "INSERT INTO code_items "
            "(id,tenant_id,code_batch_id,public_id,status,code_type,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,'single',now(),now())",
            [
                (item_ids[0], tenant_a["tenant"], code_batch_id, f"AUTH{item_ids[0].hex[:16]}", "activated"),
                (item_ids[1], tenant_a["tenant"], code_batch_id, f"AUTH{item_ids[1].hex[:16]}", "bound"),
                (item_ids[2], tenant_a["tenant"], code_batch_id, f"AUTH{item_ids[2].hex[:16]}", "created"),
            ],
        )
        await owner.execute(
            "UPDATE production_batches SET status='recalled',recall_reason='quality hold',"
            "recalled_at=now(),recalled_by=$2 WHERE id=$1",
            tenant_a["production_batch"],
            str(tenant_a["account"]),
        )
        statuses = await owner.fetch(
            "SELECT id,status::text AS status FROM code_items WHERE id=ANY($1::uuid[]) ORDER BY id::text",
            item_ids,
        )
        observed = {row["id"]: row["status"] for row in statuses}
        assert observed[item_ids[0]] == "frozen"
        assert observed[item_ids[1]] == "frozen"
        assert observed[item_ids[2]] == "created"
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM code_items AS item "
                "JOIN code_batches AS code_batch ON code_batch.id=item.code_batch_id "
                "JOIN production_batches AS production_batch ON production_batch.id=code_batch.production_batch_id "
                "WHERE production_batch.id=$1",
                tenant_a["production_batch"],
            )
            == 3
        )

        assert isinstance(
            await _assert_rejected(
                owner,
                "UPDATE code_items SET status='activated' WHERE id=$1",
                item_ids[2],
            ),
            asyncpg.CheckViolationError,
        )
        for guarded_batch_id, target_status in recalled_status_batches:
            owner_status = await _assert_rejected(
                owner,
                "UPDATE code_batches SET status=$2::codebatchstatus WHERE id=$1",
                guarded_batch_id,
                target_status,
            )
            assert getattr(owner_status, "sqlstate", None) == "23514"
            runtime_status = await _assert_rejected(
                runtime_pg_conn,
                "UPDATE code_batches SET status=$2::codebatchstatus WHERE id=$1 AND tenant_id=$3",
                guarded_batch_id,
                target_status,
                tenant_a["tenant"],
            )
            assert getattr(runtime_status, "sqlstate", None) == "23514"
        await owner.execute("UPDATE code_batches SET status='failed' WHERE id=$1", activated_batch_id)
        assert await owner.fetchval("SELECT status::text FROM code_batches WHERE id=$1", activated_batch_id) == "failed"
        recalled_reversal = await _assert_rejected(
            owner,
            "UPDATE production_batches SET status='active',recall_reason=NULL,recalled_at=NULL,recalled_by=NULL "
            "WHERE id=$1",
            tenant_a["production_batch"],
        )
        assert getattr(recalled_reversal, "sqlstate", None) == "22023"
        immutable_updates = (
            (
                "UPDATE production_batches SET production_date=production_date+1 WHERE id=$1",
                (tenant_a["production_batch"],),
            ),
            ("UPDATE production_batches SET origin='rewritten' WHERE id=$1", (tenant_a["production_batch"],)),
            (
                "UPDATE production_batches SET product_id=$2 WHERE id=$1",
                (tenant_a["production_batch"], tenant_b["product"]),
            ),
            ("UPDATE production_batches SET recall_reason='rewritten' WHERE id=$1", (tenant_a["production_batch"],)),
        )
        for statement, parameters in immutable_updates:
            rejected = await _assert_rejected(owner, statement, *parameters)
            assert getattr(rejected, "sqlstate", None) == "22023"
    finally:
        await owner.close()

    failed = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
    assert str(tenant_a["production_batch"]) in f"{failed.stdout}\n{failed.stderr}"
    owner = await asyncpg.connect(dsn)
    try:
        await owner.execute("DELETE FROM code_items WHERE id=ANY($1::uuid[])", item_ids)
        await owner.execute(
            "DELETE FROM code_batches WHERE id=ANY($1::uuid[])",
            [code_batch_id, delivered_batch_id, activated_batch_id],
        )
        await owner.execute("DELETE FROM production_batches WHERE id=$1", tenant_a["production_batch"])
        await owner.execute("DELETE FROM production_batches WHERE id=$1", expired_batch_id)
    finally:
        await owner.close()


def _code_batch_insert_sql() -> str:
    return (
        "INSERT INTO code_batches "
        "(id,tenant_id,product_id,sku_id,production_batch_id,batch_code,quantity,status,code_type,"
        "generation_mode,created_by,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,$6,1,'activated','single','item_level',$7,now(),now())"
    )


async def test_past_expiry_rejects_link_and_activation_for_owner_and_runtime(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(dsn)
    ids: dict[str, uuid.UUID] | None = None
    code_batch_ids: list[uuid.UUID] = []
    item_id: uuid.UUID | None = None
    try:
        ids = await _insert_tenant_catalog(owner, "past-expiry")
        code_batch_id = await _insert_code_batch(
            owner,
            ids,
            production_batch_id=ids["production_batch"],
            status="completed",
        )
        delivered_batch_id = await _insert_code_batch(
            owner,
            ids,
            production_batch_id=ids["production_batch"],
            status="printing",
        )
        activated_batch_id = await _insert_code_batch(
            owner,
            ids,
            production_batch_id=ids["production_batch"],
            status="completed",
        )
        code_batch_ids.extend((code_batch_id, delivered_batch_id, activated_batch_id))
        expired_status_batches = (
            (code_batch_id, "printing"),
            (delivered_batch_id, "delivered"),
            (activated_batch_id, "activated"),
        )
        item_id = await _insert_code_item(owner, ids["tenant"], code_batch_id)
        shanghai_business_date = await owner.fetchval("SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')::date")
        prior_shanghai_date = shanghai_business_date - timedelta(days=1)
        shanghai_boundary_instant = await owner.fetchval(
            "SELECT (($1::date + TIME '00:30') AT TIME ZONE 'Asia/Shanghai')",
            shanghai_business_date,
        )
        await owner.execute(
            "UPDATE production_batches SET production_date=$2,expiry_date=$3 WHERE id=$1",
            ids["production_batch"],
            shanghai_business_date - timedelta(days=365),
            prior_shanghai_date,
        )

        await runtime_pg_conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
        guarded_connections = (("owner", owner), ("runtime", runtime_pg_conn))
        for session_timezone in ("UTC", "America/Los_Angeles"):
            for principal, connection in guarded_connections:
                await connection.execute("SELECT set_config('TimeZone',$1,false)", session_timezone)
                assert (
                    await connection.fetchval(
                        "SELECT ($1::timestamptz AT TIME ZONE 'Asia/Shanghai')::date",
                        shanghai_boundary_instant,
                    )
                    == shanghai_business_date
                )
                assert (
                    await connection.fetchval("SELECT $1::timestamptz::date", shanghai_boundary_instant)
                    == prior_shanghai_date
                )

                link_rejection = await _assert_rejected(
                    connection,
                    _code_batch_insert_sql(),
                    uuid.uuid4(),
                    ids["tenant"],
                    ids["product"],
                    ids["sku"],
                    ids["production_batch"],
                    f"AUTH-PAST-{principal}-{session_timezone}-{uuid.uuid4().hex[:8]}",
                    ids["account"],
                )
                assert getattr(link_rejection, "sqlstate", None) == "23514"

                activation_rejection = await _assert_rejected(
                    connection,
                    "UPDATE code_items SET status='activated' WHERE id=$1 AND tenant_id=$2",
                    item_id,
                    ids["tenant"],
                )
                assert getattr(activation_rejection, "sqlstate", None) == "23514"

                for guarded_batch_id, target_status in expired_status_batches:
                    status_rejection = await _assert_rejected(
                        connection,
                        "UPDATE code_batches SET status=$2::codebatchstatus WHERE id=$1 AND tenant_id=$3",
                        guarded_batch_id,
                        target_status,
                        ids["tenant"],
                    )
                    assert getattr(status_rejection, "sqlstate", None) == "23514"

        await owner.execute("UPDATE code_batches SET status='failed' WHERE id=$1", activated_batch_id)
        assert await owner.fetchval("SELECT status::text FROM code_batches WHERE id=$1", activated_batch_id) == "failed"

        await owner.execute(
            "UPDATE production_batches SET expiry_date=$2 WHERE id=$1",
            ids["production_batch"],
            shanghai_business_date,
        )
        for session_timezone, principal, connection in (
            ("UTC", "owner", owner),
            ("America/Los_Angeles", "runtime", runtime_pg_conn),
        ):
            await connection.execute("SELECT set_config('TimeZone',$1,false)", session_timezone)
            allowed_batch_id = uuid.uuid4()
            allowed_link_tx = connection.transaction()
            await allowed_link_tx.start()
            try:
                await connection.execute(
                    _code_batch_insert_sql(),
                    allowed_batch_id,
                    ids["tenant"],
                    ids["product"],
                    ids["sku"],
                    ids["production_batch"],
                    f"AUTH-CURRENT-{principal}-{uuid.uuid4().hex[:8]}",
                    ids["account"],
                )
                assert await connection.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM code_batches WHERE id=$1)", allowed_batch_id
                )
            finally:
                await allowed_link_tx.rollback()

        await owner.execute("SELECT set_config('TimeZone','UTC',false)")
        await owner.execute("UPDATE code_batches SET status='printing' WHERE id=$1", code_batch_id)
        await runtime_pg_conn.execute("SELECT set_config('TimeZone','America/Los_Angeles',false)")
        runtime_status_tx = runtime_pg_conn.transaction()
        await runtime_status_tx.start()
        try:
            await runtime_pg_conn.execute(
                "UPDATE code_batches SET status='delivered' WHERE id=$1 AND tenant_id=$2",
                delivered_batch_id,
                ids["tenant"],
            )
            assert (
                await runtime_pg_conn.fetchval("SELECT status::text FROM code_batches WHERE id=$1", delivered_batch_id)
                == "delivered"
            )
        finally:
            await runtime_status_tx.rollback()
        assert await owner.fetchval("SELECT status::text FROM code_batches WHERE id=$1", code_batch_id) == "printing"

        await owner.execute("UPDATE code_items SET status='activated' WHERE id=$1", item_id)
        assert await owner.fetchval("SELECT status::text FROM code_items WHERE id=$1", item_id) == "activated"
        await owner.execute("UPDATE code_items SET status='created' WHERE id=$1", item_id)
        runtime_activation_tx = runtime_pg_conn.transaction()
        await runtime_activation_tx.start()
        try:
            await runtime_pg_conn.execute(
                "UPDATE code_items SET status='activated' WHERE id=$1 AND tenant_id=$2",
                item_id,
                ids["tenant"],
            )
            assert (
                await runtime_pg_conn.fetchval("SELECT status::text FROM code_items WHERE id=$1", item_id)
                == "activated"
            )
        finally:
            await runtime_activation_tx.rollback()
    finally:
        if ids is not None:
            if item_id is not None:
                await owner.execute("DELETE FROM code_items WHERE id=$1", item_id)
            if code_batch_ids:
                await owner.execute("DELETE FROM code_batches WHERE id=ANY($1::uuid[])", code_batch_ids)
            await owner.execute("DELETE FROM production_batches WHERE id=$1", ids["production_batch"])
        await owner.close()


async def test_cross_tenant_recall_is_not_found_and_has_no_audit_drift(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core import database
    from app.main import app
    from app.services.redis_cache import AsyncRedisCache
    from app.utils.security import create_access_token

    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(dsn)
    caller_ids = await _insert_tenant_catalog(owner, "cross-tenant-recall-caller")
    target_ids = await _insert_tenant_catalog(owner, "cross-tenant-recall-target")
    permission_id = uuid.uuid4()
    session_id = uuid.uuid4()
    await owner.execute(
        "INSERT INTO permissions(id,tenant_id,code,created_at,updated_at) VALUES($1,$2,'code:manage',now(),now())",
        permission_id,
        caller_ids["tenant"],
    )
    await owner.execute(
        "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
        caller_ids["tenant"],
        caller_ids["admin_role"],
        permission_id,
    )
    await owner.execute(
        "INSERT INTO auth_sessions "
        "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
        "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
        session_id,
        caller_ids["account"],
        caller_ids["tenant"],
        uuid.uuid4().hex,
    )

    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
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
    token = create_access_token(
        str(caller_ids["tenant"]),
        str(caller_ids["account"]),
        "admin",
        "brand",
        extra={"sid": str(session_id), "auth_version": 0},
    )
    headers = {"Authorization": f"Bearer {token}"}
    target_resource = f"production_batch:{target_ids['production_batch']}"

    try:
        target_before = await owner.fetchrow(
            "SELECT status::text,recall_reason,recalled_at,recalled_by "
            "FROM production_batches WHERE id=$1 AND tenant_id=$2",
            target_ids["production_batch"],
            target_ids["tenant"],
        )
        assert tuple(target_before) == ("active", None, None, None)
        audit_count_before = await owner.fetchval(
            "SELECT count(*) FROM platform_audit_log WHERE action='production_batch_recalled' AND resource=$1",
            target_resource,
        )

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/production-batches/{target_ids['production_batch']}/recall",
                json={"reason": "cross-tenant recall must fail", "confirm": "recall"},
                headers=headers,
            )

        assert response.status_code == 404, response.text
        error_body = response.json()
        assert error_body["detail"] == "Batch not found"
        assert error_body["error_code"] == "HTTP_404"
        assert error_body["request_id"]
        target_after = await owner.fetchrow(
            "SELECT status::text,recall_reason,recalled_at,recalled_by "
            "FROM production_batches WHERE id=$1 AND tenant_id=$2",
            target_ids["production_batch"],
            target_ids["tenant"],
        )
        assert tuple(target_after) == tuple(target_before)
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM platform_audit_log WHERE action='production_batch_recalled' AND resource=$1",
                target_resource,
            )
            == audit_count_before
        )
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()
        await owner.execute(
            "DELETE FROM platform_audit_log WHERE action='production_batch_recalled' AND resource=$1",
            target_resource,
        )
        await owner.execute("DELETE FROM auth_sessions WHERE id=$1", session_id)
        await owner.execute("DELETE FROM role_permissions WHERE permission_id=$1", permission_id)
        await owner.execute("DELETE FROM permissions WHERE id=$1", permission_id)
        await owner.close()


async def test_recall_and_activation_linearize_without_deadlock(migrated_pg_url: str) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(dsn)
    observer = await asyncpg.connect(dsn)
    recall_conn = await asyncpg.connect(dsn)
    activate_conn = await asyncpg.connect(dsn)
    recall_task: asyncio.Task[str] | None = None
    created_rows: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = []
    try:
        ids = await _insert_tenant_catalog(owner, "recall-race")

        recall_first_batch = await _insert_code_batch(
            owner,
            ids,
            production_batch_id=ids["production_batch"],
        )
        recall_first_item = await _insert_code_item(owner, ids["tenant"], recall_first_batch)
        created_rows.append((ids["production_batch"], recall_first_batch, recall_first_item))

        recall_tx = recall_conn.transaction()
        await recall_tx.start()
        await recall_conn.execute(
            "UPDATE production_batches SET status='recalled',recall_reason='recall wins',"
            "recalled_at=now(),recalled_by=$2 WHERE id=$1",
            ids["production_batch"],
            str(ids["account"]),
        )
        activate_tx = activate_conn.transaction()
        await activate_tx.start()
        try:
            with pytest.raises(asyncpg.LockNotAvailableError) as blocked:
                await activate_conn.execute(
                    "UPDATE code_items SET status='activated' WHERE id=$1",
                    recall_first_item,
                )
            assert blocked.value.sqlstate == "55P03"
        finally:
            await activate_tx.rollback()
        await recall_tx.commit()
        assert await owner.fetchval("SELECT status::text FROM code_items WHERE id=$1", recall_first_item) == "created"

        activate_first_pb = await _insert_additional_production_batch(owner, ids, "activate-first")
        activate_first_batch = await _insert_code_batch(
            owner,
            ids,
            production_batch_id=activate_first_pb,
        )
        activate_first_item = await _insert_code_item(owner, ids["tenant"], activate_first_batch)
        created_rows.append((activate_first_pb, activate_first_batch, activate_first_item))

        activate_tx = activate_conn.transaction()
        await activate_tx.start()
        await activate_conn.execute(
            "UPDATE code_items SET status='activated' WHERE id=$1",
            activate_first_item,
        )
        recall_tx = recall_conn.transaction()
        await recall_tx.start()
        recall_task = asyncio.create_task(
            recall_conn.execute(
                "UPDATE production_batches SET status='recalled',recall_reason='activation commits first',"
                "recalled_at=now(),recalled_by=$2 WHERE id=$1",
                activate_first_pb,
                str(ids["account"]),
            )
        )
        recall_pid = recall_conn.get_server_pid()
        for _ in range(100):
            if await observer.fetchval(
                "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",
                recall_pid,
            ):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("recall did not wait behind the activation transaction's production-batch lock")
        assert not recall_task.done()

        await activate_tx.commit()
        await asyncio.wait_for(recall_task, timeout=5)
        recall_task = None
        await recall_tx.commit()
        assert await owner.fetchval("SELECT status::text FROM code_items WHERE id=$1", activate_first_item) == "frozen"
    finally:
        if recall_task is not None and not recall_task.done():
            recall_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await recall_task
        await recall_conn.close()
        await activate_conn.close()
        for production_batch_id, code_batch_id, item_id in reversed(created_rows):
            await owner.execute("DELETE FROM code_items WHERE id=$1", item_id)
            await owner.execute("DELETE FROM code_batches WHERE id=$1", code_batch_id)
            await owner.execute("DELETE FROM production_batches WHERE id=$1", production_batch_id)
        await observer.close()
        await owner.close()


async def test_recall_and_code_batch_forward_transitions_linearize_at_commit(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fastapi
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core import database
    from app.core.database import get_db
    from app.main import app
    from app.services.redis_cache import AsyncRedisCache
    from app.utils.security import create_access_token

    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(dsn)
    ids = await _insert_tenant_catalog(owner, "forward-status-race")
    scenario_specs = (
        ("printing-recall-first", "completed", "printing", "mark-printing", "code_mark_printing"),
        ("printing-transition-first", "completed", "printing", "mark-printing", "code_mark_printing"),
        ("delivered-recall-first", "printing", "delivered", "mark-delivered", "code_mark_delivered"),
        ("delivered-transition-first", "printing", "delivered", "mark-delivered", "code_mark_delivered"),
    )
    scenarios: dict[str, dict[str, uuid.UUID | str]] = {}
    for index, (label, initial_status, target_status, endpoint, audit_action) in enumerate(scenario_specs):
        production_batch_id = (
            ids["production_batch"]
            if index == 0
            else await _insert_additional_production_batch(owner, ids, f"forward-{index}")
        )
        code_batch_id = await _insert_code_batch(
            owner,
            ids,
            production_batch_id=production_batch_id,
            status=initial_status,
        )
        code_item_id = await _insert_code_item(
            owner,
            ids["tenant"],
            code_batch_id,
            status="activated",
        )
        scenarios[label] = {
            "production_batch": production_batch_id,
            "code_batch": code_batch_id,
            "code_item": code_item_id,
            "target_status": target_status,
            "endpoint": endpoint,
            "audit_action": audit_action,
        }

    permission_id = uuid.uuid4()
    session_id = uuid.uuid4()
    await owner.execute(
        "INSERT INTO permissions(id,tenant_id,code,created_at,updated_at) VALUES($1,$2,'code:manage',now(),now())",
        permission_id,
        ids["tenant"],
    )
    await owner.execute(
        "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
        ids["tenant"],
        ids["admin_role"],
        permission_id,
    )
    await owner.execute(
        "INSERT INTO auth_sessions "
        "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
        "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
        session_id,
        ids["account"],
        ids["tenant"],
        uuid.uuid4().hex,
    )

    reached_finalize = {label: asyncio.Event() for label in scenarios}
    allow_finalize = {label: asyncio.Event() for label in scenarios}

    class PausingRuntimeSession(AsyncSession):
        async def commit(self) -> None:
            pause_key = self.info.get("u02b_forward_status_pause")
            if pause_key in reached_finalize and not self.info.get("u02b_forward_status_pause_consumed"):
                self.info["u02b_forward_status_pause_consumed"] = True
                reached_finalize[pause_key].set()
                await allow_finalize[pause_key].wait()
            await super().commit()

    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    runtime_engine = create_async_engine(runtime_url)
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_factory = async_sessionmaker(runtime_engine, class_=PausingRuntimeSession, expire_on_commit=False)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)

    async def tagged_get_db(request: fastapi.Request):
        dependency = database.get_db(request)
        session = await dependency.__anext__()
        try:
            pause_key = request.headers.get("X-U02B-Forward-Status-Pause")
            if pause_key:
                session.info["u02b_forward_status_pause"] = pause_key
            yield session
        except BaseException as route_error:
            try:
                await dependency.athrow(route_error)
            except StopAsyncIteration:
                pass
            except BaseException as dependency_error:
                if dependency_error is not route_error:
                    raise
            raise route_error
        else:
            try:
                await dependency.__anext__()
            except StopAsyncIteration:
                pass
            else:
                raise AssertionError("get_db yielded more than one session")
        finally:
            await dependency.aclose()

    async def cache_is_not_revoked(self, key: str) -> bool:
        return False

    async def no_cache_invalidation(product_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(AsyncRedisCache, "is_token_revoked", cache_is_not_revoked)
    monkeypatch.setattr("app.services.resolver_response.invalidate_product_cache", no_cache_invalidation)
    prior_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = tagged_get_db
    token = create_access_token(
        str(ids["tenant"]),
        str(ids["account"]),
        "admin",
        "brand",
        extra={"sid": str(session_id), "auth_version": 0},
    )
    headers = {"Authorization": f"Bearer {token}"}
    request_tasks: list[asyncio.Task] = []

    def recall_request(client: AsyncClient, label: str, *, pause: bool = False):
        scenario = scenarios[label]
        request_headers = headers if not pause else {**headers, "X-U02B-Forward-Status-Pause": label}
        return client.post(
            f"/api/v1/production-batches/{scenario['production_batch']}/recall",
            json={"reason": f"{label} forward status race", "confirm": "recall"},
            headers=request_headers,
        )

    def transition_request(client: AsyncClient, label: str, *, pause: bool = False):
        scenario = scenarios[label]
        request_headers = headers if not pause else {**headers, "X-U02B-Forward-Status-Pause": label}
        return client.post(
            f"/api/v1/code-batches/{scenario['code_batch']}/{scenario['endpoint']}",
            headers=request_headers,
        )

    async def wait_for_finalize(task: asyncio.Task, label: str) -> None:
        event_task = asyncio.create_task(reached_finalize[label].wait())
        done, _ = await asyncio.wait(
            {task, event_task},
            timeout=10,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done:
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
            response = task.result()
            raise AssertionError(
                f"{label} request completed before transaction finalize: "
                f"status={response.status_code} body={response.text}"
            )
        if event_task not in done:
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
            raise TimeoutError(f"{label} request did not reach transaction finalize")
        await event_task

    async def assert_final_state(label: str, *, transition_audits: int) -> None:
        scenario = scenarios[label]
        row = await owner.fetchrow(
            "SELECT production_batch.status::text AS production_status,"
            "code_batch.status::text AS code_batch_status,code_item.status::text AS item_status "
            "FROM production_batches AS production_batch "
            "JOIN code_batches AS code_batch ON code_batch.production_batch_id=production_batch.id "
            "JOIN code_items AS code_item ON code_item.code_batch_id=code_batch.id "
            "WHERE production_batch.id=$1 AND code_batch.id=$2 AND code_item.id=$3",
            scenario["production_batch"],
            scenario["code_batch"],
            scenario["code_item"],
        )
        expected_batch_status = (
            scenario["target_status"]
            if transition_audits
            else ("completed" if str(scenario["target_status"]) == "printing" else "printing")
        )
        assert tuple(row) == ("recalled", expected_batch_status, "frozen")
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM platform_audit_log WHERE target_tenant_id=$1 AND operator_id=$2 "
                "AND action=$3 AND resource=$4",
                str(ids["tenant"]),
                str(ids["account"]),
                scenario["audit_action"],
                f"code_batch:{scenario['code_batch']}",
            )
            == transition_audits
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM platform_audit_log WHERE target_tenant_id=$1 AND operator_id=$2 "
                "AND action='production_batch_recalled' AND resource=$3",
                str(ids["tenant"]),
                str(ids["account"]),
                f"production_batch:{scenario['production_batch']}",
            )
            == 1
        )

    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for transition in ("printing", "delivered"):
                recall_first = f"{transition}-recall-first"
                recall_task = asyncio.create_task(recall_request(client, recall_first, pause=True))
                request_tasks.append(recall_task)
                await wait_for_finalize(recall_task, recall_first)
                transition_task = asyncio.create_task(transition_request(client, recall_first))
                request_tasks.append(transition_task)
                await asyncio.sleep(0.15)
                assert not transition_task.done(), f"{transition} must wait behind recall through pre-commit"
                allow_finalize[recall_first].set()
                recall_response, transition_response = await asyncio.wait_for(
                    asyncio.gather(recall_task, transition_task),
                    timeout=10,
                )
                assert recall_response.status_code == 200, recall_response.text
                assert transition_response.status_code == 409, transition_response.text
                assert "40P01" not in recall_response.text + transition_response.text
                await assert_final_state(recall_first, transition_audits=0)

                transition_first = f"{transition}-transition-first"
                transition_task = asyncio.create_task(transition_request(client, transition_first, pause=True))
                request_tasks.append(transition_task)
                await wait_for_finalize(transition_task, transition_first)
                recall_task = asyncio.create_task(recall_request(client, transition_first))
                request_tasks.append(recall_task)
                await asyncio.sleep(0.15)
                assert not recall_task.done(), f"recall must wait behind {transition} through pre-commit"
                allow_finalize[transition_first].set()
                transition_response, recall_response = await asyncio.wait_for(
                    asyncio.gather(transition_task, recall_task),
                    timeout=10,
                )
                assert transition_response.status_code == 200, transition_response.text
                assert transition_response.json()["status"] == transition
                assert recall_response.status_code == 200, recall_response.text
                assert "40P01" not in transition_response.text + recall_response.text
                await assert_final_state(transition_first, transition_audits=1)
    finally:
        for event in allow_finalize.values():
            event.set()
        for task in request_tasks:
            if not task.done():
                task.cancel()
        if request_tasks:
            await asyncio.gather(*request_tasks, return_exceptions=True)
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior_overrides)
        await runtime_engine.dispose()
        await owner_engine.dispose()
        await owner.execute(
            "DELETE FROM platform_audit_log WHERE target_tenant_id=$1 AND resource=ANY($2::text[])",
            str(ids["tenant"]),
            [
                resource
                for scenario in scenarios.values()
                for resource in (
                    f"production_batch:{scenario['production_batch']}",
                    f"code_batch:{scenario['code_batch']}",
                )
            ],
        )
        await owner.execute(
            "DELETE FROM code_items WHERE tenant_id=$1 AND id=ANY($2::uuid[])",
            ids["tenant"],
            [scenario["code_item"] for scenario in scenarios.values()],
        )
        await owner.execute(
            "DELETE FROM code_batches WHERE tenant_id=$1 AND id=ANY($2::uuid[])",
            ids["tenant"],
            [scenario["code_batch"] for scenario in scenarios.values()],
        )
        await owner.execute(
            "DELETE FROM production_batches WHERE tenant_id=$1 AND id=ANY($2::uuid[])",
            ids["tenant"],
            [scenario["production_batch"] for scenario in scenarios.values()],
        )
        await owner.execute("DELETE FROM auth_sessions WHERE id=$1", session_id)
        await owner.execute("DELETE FROM role_permissions WHERE permission_id=$1", permission_id)
        await owner.execute("DELETE FROM permissions WHERE id=$1", permission_id)
        await owner.close()


async def test_agency_import_http_boundary_is_scope_exact_and_actor_bound(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.api.v1 import imports as imports_api
    from app.core import database
    from app.main import app
    from app.services import agency_auth as agency_auth_service
    from app.services.public_id import generate_public_id
    from app.services.redis_cache import AsyncRedisCache
    from app.utils.security import create_access_token

    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(dsn)
    product_client_ids = await _insert_tenant_catalog(owner, "agency-import-products-client")
    code_client_ids = await _insert_tenant_catalog(owner, "agency-import-codes-client")
    agency_ids = await _insert_tenant_catalog(owner, "agency-import-principal")
    await owner.execute("UPDATE tenants SET tenant_type='agency' WHERE id=$1", agency_ids["tenant"])
    code_batch_id = await _insert_code_batch(
        owner,
        code_client_ids,
        production_batch_id=code_client_ids["production_batch"],
        status="completed",
    )
    permission_ids = [uuid.uuid4(), uuid.uuid4()]
    await owner.executemany(
        "INSERT INTO permissions(id,tenant_id,code,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
        [
            (permission_ids[0], agency_ids["tenant"], "product:create"),
            (permission_ids[1], agency_ids["tenant"], "code:generate"),
        ],
    )
    await owner.executemany(
        "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
        [(agency_ids["tenant"], agency_ids["admin_role"], permission_id) for permission_id in permission_ids],
    )
    session_id = uuid.uuid4()
    authorization_ids = [uuid.uuid4(), uuid.uuid4()]
    await owner.execute(
        "INSERT INTO auth_sessions "
        "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
        "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
        session_id,
        agency_ids["account"],
        agency_ids["tenant"],
        uuid.uuid4().hex,
    )
    await owner.executemany(
        "INSERT INTO agency_authorizations "
        "(id,agency_tenant_id,client_tenant_id,scope,status,granted_by,granted_at,expires_at,created_at,updated_at) "
        "VALUES($1,$2,$3,$4::jsonb,'active',$5,now(),now()+interval '1 hour',now(),now())",
        [
            (
                authorization_ids[0],
                agency_ids["tenant"],
                product_client_ids["tenant"],
                '["products"]',
                product_client_ids["account"],
            ),
            (
                authorization_ids[1],
                agency_ids["tenant"],
                code_client_ids["tenant"],
                '["codes"]',
                code_client_ids["account"],
            ),
        ],
    )

    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    runtime_engine = create_async_engine(runtime_url)
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)
    monkeypatch.setattr(agency_auth_service, "_is_pg", True)

    async def cache_is_not_revoked(self, key: str) -> bool:
        return False

    async def allow_import_rate(*args, **kwargs) -> tuple[bool, int]:
        return True, 1

    monkeypatch.setattr(AsyncRedisCache, "is_token_revoked", cache_is_not_revoked)
    monkeypatch.setattr(imports_api._import_rate_cache, "rate_limit_check_shared", allow_import_rate)
    base_token = create_access_token(
        str(agency_ids["tenant"]),
        str(agency_ids["account"]),
        "admin",
        "agency",
        extra={"sid": str(session_id), "auth_version": 0},
    )
    base_headers = {"Authorization": f"Bearer {base_token}"}
    product_name = f"agency imported product {uuid.uuid4().hex[:8]}"
    denied_product_name = f"agency denied product {uuid.uuid4().hex[:8]}"
    product_csv = (
        "brand_name,product_name,category,description,sku_code,sku_name\n"
        f"Agency Import Brand,{product_name},food,scope proof,AG-{uuid.uuid4().hex[:8]},Agency SKU\n"
    )
    denied_product_csv = (
        "brand_name,product_name,category,description,sku_code,sku_name\n"
        f"Agency Import Brand,{denied_product_name},food,scope proof,AG-{uuid.uuid4().hex[:8]},Agency SKU\n"
    )
    products_scope_public_id = generate_public_id()
    codes_scope_public_id = generate_public_id()

    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            base_denied = await http_client.post(
                "/api/v1/imports/products",
                files={"file": ("products.csv", product_csv, "text/csv")},
                headers=base_headers,
            )
            assert base_denied.status_code == 403, base_denied.text
            assert not await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM products WHERE tenant_id=$1 AND name=$2)",
                product_client_ids["tenant"],
                product_name,
            )

            product_switched = await http_client.post(
                "/api/v1/agency/switch-context",
                json={"client_tenant_id": str(product_client_ids["tenant"])},
                headers=base_headers,
            )
            assert product_switched.status_code == 200, product_switched.text
            assert product_switched.json()["scope"] == ["products"]
            product_headers = {"Authorization": f"Bearer {product_switched.json()['access_token']}"}

            product_import = await http_client.post(
                "/api/v1/imports/products",
                files={"file": ("products.csv", product_csv, "text/csv")},
                headers=product_headers,
            )
            assert product_import.status_code == 200, product_import.text
            assert product_import.json()["imported"] == 1

            products_scope_code_denied = await http_client.post(
                "/api/v1/imports/existing-codes",
                params={"code_batch_id": str(code_batch_id)},
                files={"file": ("codes.csv", f"public_id\n{products_scope_public_id}\n", "text/csv")},
                headers=product_headers,
            )
            assert products_scope_code_denied.status_code == 403, products_scope_code_denied.text
            assert not await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM code_items WHERE tenant_id=$1 AND public_id=$2)",
                code_client_ids["tenant"],
                products_scope_public_id,
            )

            code_switched = await http_client.post(
                "/api/v1/agency/switch-context",
                json={"client_tenant_id": str(code_client_ids["tenant"])},
                headers=base_headers,
            )
            assert code_switched.status_code == 200, code_switched.text
            assert code_switched.json()["scope"] == ["codes"]
            code_headers = {"Authorization": f"Bearer {code_switched.json()['access_token']}"}

            codes_scope_product_denied = await http_client.post(
                "/api/v1/imports/products",
                files={"file": ("products.csv", denied_product_csv, "text/csv")},
                headers=code_headers,
            )
            assert codes_scope_product_denied.status_code == 403, codes_scope_product_denied.text
            assert not await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM products WHERE tenant_id=$1 AND name=$2)",
                code_client_ids["tenant"],
                denied_product_name,
            )

            code_import = await http_client.post(
                "/api/v1/imports/existing-codes",
                params={"code_batch_id": str(code_batch_id)},
                files={"file": ("codes.csv", f"public_id\n{codes_scope_public_id}\n", "text/csv")},
                headers=code_headers,
            )
            assert code_import.status_code == 200, code_import.text
            assert code_import.json()["imported"] == 1

        imported_product_id = await owner.fetchval(
            "SELECT id FROM products WHERE tenant_id=$1 AND name=$2",
            product_client_ids["tenant"],
            product_name,
        )
        assert imported_product_id is not None
        assert await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM code_items WHERE tenant_id=$1 AND code_batch_id=$2 AND public_id=$3)",
            code_client_ids["tenant"],
            code_batch_id,
            codes_scope_public_id,
        )
        catalog_audits = await owner.fetch(
            "SELECT operator_id,target_tenant_id,resource,details FROM platform_audit_log "
            "WHERE operator_id=$1 AND target_tenant_id=$2 AND action='catalog_import_completed'",
            str(agency_ids["account"]),
            str(product_client_ids["tenant"]),
        )
        assert len(catalog_audits) == 1
        assert catalog_audits[0]["resource"].startswith("catalog_import:")
        assert json.loads(catalog_audits[0]["details"]) == {
            "created": 1,
            "updated": 0,
            "errors": 0,
            "import_type": "csv",
        }
        code_audits = await owner.fetch(
            "SELECT operator_id,target_tenant_id,resource,details FROM platform_audit_log "
            "WHERE operator_id=$1 AND target_tenant_id=$2 AND action='code_import_completed'",
            str(agency_ids["account"]),
            str(code_client_ids["tenant"]),
        )
        assert len(code_audits) == 1
        assert code_audits[0]["resource"] == f"code_batch:{code_batch_id}"
        assert json.loads(code_audits[0]["details"]) == {"created": 1, "skipped": 0, "errors": 0}
    finally:
        await runtime_engine.dispose()
        await owner_engine.dispose()
        await owner.execute(
            "DELETE FROM platform_audit_log WHERE operator_id=$1 AND target_tenant_id=ANY($2::text[])",
            str(agency_ids["account"]),
            [str(product_client_ids["tenant"]), str(code_client_ids["tenant"])],
        )
        await owner.execute(
            "DELETE FROM code_items WHERE tenant_id=$1 AND code_batch_id=$2",
            code_client_ids["tenant"],
            code_batch_id,
        )
        await owner.execute("DELETE FROM code_batches WHERE id=$1", code_batch_id)
        await owner.execute(
            "DELETE FROM skus WHERE tenant_id=$1 AND product_id IN "
            "(SELECT id FROM products WHERE tenant_id=$1 AND name=ANY($2::text[]))",
            product_client_ids["tenant"],
            [product_name, denied_product_name],
        )
        await owner.execute(
            "DELETE FROM products WHERE tenant_id=$1 AND name=ANY($2::text[])",
            product_client_ids["tenant"],
            [product_name, denied_product_name],
        )
        await owner.execute(
            "DELETE FROM brands WHERE tenant_id=$1 AND name='Agency Import Brand'",
            product_client_ids["tenant"],
        )
        await owner.execute("DELETE FROM agency_authorizations WHERE id=ANY($1::uuid[])", authorization_ids)
        await owner.execute("DELETE FROM auth_sessions WHERE id=$1", session_id)
        await owner.execute("DELETE FROM role_permissions WHERE permission_id=ANY($1::uuid[])", permission_ids)
        await owner.execute("DELETE FROM permissions WHERE id=ANY($1::uuid[])", permission_ids)
        await owner.close()


async def test_existing_code_import_and_recall_linearize_without_deadlock(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fastapi
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core import database
    from app.core.database import get_db
    from app.main import app
    from app.services.public_id import generate_public_id
    from app.services.redis_cache import AsyncRedisCache
    from app.utils.security import create_access_token

    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(dsn)
    ids = await _insert_tenant_catalog(owner, "existing-import-race")
    recall_first_batch = await _insert_code_batch(
        owner,
        ids,
        production_batch_id=ids["production_batch"],
        status="completed",
    )
    import_first_pb = await _insert_additional_production_batch(owner, ids, "existing-import-first")
    import_first_batch = await _insert_code_batch(
        owner,
        ids,
        production_batch_id=import_first_pb,
        status="completed",
    )
    permission_ids = [uuid.uuid4(), uuid.uuid4()]
    session_id = uuid.uuid4()
    await owner.executemany(
        "INSERT INTO permissions(id,tenant_id,code,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
        [
            (permission_ids[0], ids["tenant"], "code:generate"),
            (permission_ids[1], ids["tenant"], "code:manage"),
        ],
    )
    await owner.executemany(
        "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
        [(ids["tenant"], ids["admin_role"], permission_id) for permission_id in permission_ids],
    )
    await owner.execute(
        "INSERT INTO auth_sessions "
        "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
        "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
        session_id,
        ids["account"],
        ids["tenant"],
        uuid.uuid4().hex,
    )

    reached_finalize = {
        "recall-first": asyncio.Event(),
        "import-first": asyncio.Event(),
    }
    allow_finalize = {
        "recall-first": asyncio.Event(),
        "import-first": asyncio.Event(),
    }

    class PausingRuntimeSession(AsyncSession):
        async def commit(self) -> None:
            pause_key = self.info.get("u02b_pause_finalize")
            if pause_key in reached_finalize:
                reached_finalize[pause_key].set()
                await allow_finalize[pause_key].wait()
            await super().commit()

    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    runtime_engine = create_async_engine(runtime_url)
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_factory = async_sessionmaker(runtime_engine, class_=PausingRuntimeSession, expire_on_commit=False)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)

    async def tagged_get_db(request: fastapi.Request):
        dependency = database.get_db(request)
        session = await dependency.__anext__()
        try:
            pause_key = request.headers.get("X-U02B-Pause-Finalize")
            if pause_key:
                session.info["u02b_pause_finalize"] = pause_key
            yield session
        except BaseException as route_error:
            try:
                await dependency.athrow(route_error)
            except StopAsyncIteration:
                pass
            except BaseException as dependency_error:
                if dependency_error is not route_error:
                    raise
            raise route_error
        else:
            try:
                await dependency.__anext__()
            except StopAsyncIteration:
                pass
            else:
                raise AssertionError("get_db yielded more than one session")
        finally:
            await dependency.aclose()

    async def cache_is_not_revoked(self, key: str) -> bool:
        return False

    async def no_cache_invalidation(product_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(AsyncRedisCache, "is_token_revoked", cache_is_not_revoked)
    monkeypatch.setattr("app.services.resolver_response.invalidate_product_cache", no_cache_invalidation)
    prior_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = tagged_get_db

    token = create_access_token(
        str(ids["tenant"]),
        str(ids["account"]),
        "admin",
        "brand",
        extra={"sid": str(session_id), "auth_version": 0},
    )
    headers = {"Authorization": f"Bearer {token}"}
    recall_first_public_id = generate_public_id()
    import_first_public_id = generate_public_id()
    request_tasks: list[asyncio.Task] = []

    def import_request(client: AsyncClient, code_batch_id: uuid.UUID, public_id: str, *, pause: str | None = None):
        request_headers = headers if pause is None else {**headers, "X-U02B-Pause-Finalize": pause}
        return client.post(
            "/api/v1/imports/existing-codes",
            params={"code_batch_id": str(code_batch_id)},
            files={"file": ("codes.csv", f"public_id\n{public_id}\n", "text/csv")},
            headers=request_headers,
        )

    def recall_request(client: AsyncClient, production_batch_id: uuid.UUID, *, pause: str | None = None):
        request_headers = headers if pause is None else {**headers, "X-U02B-Pause-Finalize": pause}
        return client.post(
            f"/api/v1/production-batches/{production_batch_id}/recall",
            json={"reason": "concurrent existing-code import", "confirm": "recall"},
            headers=request_headers,
        )

    async def wait_for_finalize(task: asyncio.Task, pause_key: str) -> None:
        event_task = asyncio.create_task(reached_finalize[pause_key].wait())
        done, _ = await asyncio.wait(
            {task, event_task},
            timeout=10,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done:
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
            response = task.result()
            raise AssertionError(
                f"{pause_key} request completed before transaction finalize: "
                f"status={response.status_code} body={response.text}"
            )
        if event_task not in done:
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
            raise TimeoutError(f"{pause_key} request did not reach transaction finalize")
        await event_task

    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            recall_task = asyncio.create_task(recall_request(client, ids["production_batch"], pause="recall-first"))
            request_tasks.append(recall_task)
            await wait_for_finalize(recall_task, "recall-first")
            import_task = asyncio.create_task(import_request(client, recall_first_batch, recall_first_public_id))
            request_tasks.append(import_task)
            await asyncio.sleep(0.15)
            assert not import_task.done(), "existing-code import must wait behind the recalling transaction"
            allow_finalize["recall-first"].set()
            recall_response, import_response = await asyncio.wait_for(
                asyncio.gather(recall_task, import_task),
                timeout=10,
            )
            assert recall_response.status_code == 200, recall_response.text
            assert import_response.status_code == 409, import_response.text
            assert import_response.json()["detail"] == "Production batch is not active"

            assert not await owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM code_items WHERE tenant_id=$1 AND public_id=$2 "
                "AND status::text IN ('activated','bound'))",
                ids["tenant"],
                recall_first_public_id,
            )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM platform_audit_log WHERE target_tenant_id=$1 "
                    "AND action='code_import_completed' AND resource=$2",
                    str(ids["tenant"]),
                    f"code_batch:{recall_first_batch}",
                )
                == 0
            )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM platform_audit_log WHERE target_tenant_id=$1 "
                    "AND action='production_batch_recalled' AND resource=$2",
                    str(ids["tenant"]),
                    f"production_batch:{ids['production_batch']}",
                )
                == 1
            )

            import_task = asyncio.create_task(
                import_request(client, import_first_batch, import_first_public_id, pause="import-first")
            )
            request_tasks.append(import_task)
            await wait_for_finalize(import_task, "import-first")
            recall_task = asyncio.create_task(recall_request(client, import_first_pb))
            request_tasks.append(recall_task)
            await asyncio.sleep(0.15)
            assert not recall_task.done(), "recall must wait for the existing-code import transaction"
            allow_finalize["import-first"].set()
            import_response, recall_response = await asyncio.wait_for(
                asyncio.gather(import_task, recall_task),
                timeout=10,
            )
            assert import_response.status_code == 200, import_response.text
            assert import_response.json()["imported"] == 1
            assert recall_response.status_code == 200, recall_response.text

            assert (
                await owner.fetchval(
                    "SELECT status::text FROM code_items WHERE tenant_id=$1 AND public_id=$2",
                    ids["tenant"],
                    import_first_public_id,
                )
                == "frozen"
            )
            for action, resource in (
                ("code_import_completed", f"code_batch:{import_first_batch}"),
                ("production_batch_recalled", f"production_batch:{import_first_pb}"),
            ):
                assert (
                    await owner.fetchval(
                        "SELECT count(*) FROM platform_audit_log WHERE target_tenant_id=$1 "
                        "AND operator_id=$2 AND action=$3 AND resource=$4",
                        str(ids["tenant"]),
                        str(ids["account"]),
                        action,
                        resource,
                    )
                    == 1
                )
    finally:
        for event in allow_finalize.values():
            event.set()
        for task in request_tasks:
            if not task.done():
                task.cancel()
        if request_tasks:
            await asyncio.gather(*request_tasks, return_exceptions=True)
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior_overrides)
        await runtime_engine.dispose()
        await owner_engine.dispose()
        await owner.execute(
            "DELETE FROM platform_audit_log WHERE target_tenant_id=$1 AND (resource=$2 OR resource=$3 OR resource=$4 OR resource=$5)",
            str(ids["tenant"]),
            f"code_batch:{recall_first_batch}",
            f"production_batch:{ids['production_batch']}",
            f"code_batch:{import_first_batch}",
            f"production_batch:{import_first_pb}",
        )
        await owner.execute(
            "DELETE FROM code_items WHERE tenant_id=$1 AND code_batch_id=ANY($2::uuid[])",
            ids["tenant"],
            [recall_first_batch, import_first_batch],
        )
        await owner.execute(
            "DELETE FROM code_batches WHERE id=ANY($1::uuid[])",
            [recall_first_batch, import_first_batch],
        )
        await owner.execute(
            "DELETE FROM production_batches WHERE id=ANY($1::uuid[])",
            [ids["production_batch"], import_first_pb],
        )
        await owner.execute("DELETE FROM auth_sessions WHERE id=$1", session_id)
        await owner.execute("DELETE FROM role_permissions WHERE permission_id=ANY($1::uuid[])", permission_ids)
        await owner.execute("DELETE FROM permissions WHERE id=ANY($1::uuid[])", permission_ids)
        await owner.close()


async def test_old_scan_token_benefit_claim_and_recall_linearize_at_commit(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fastapi
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core import database
    from app.core.database import get_db
    from app.main import app
    from app.middleware.rate_limit import RateLimitResult, rate_limiter
    from app.services import benefit_delivery_handler
    from app.services.redis_cache import AsyncRedisCache
    from app.services.scan_token import create_scan_token
    from app.utils.client_ip import compute_ip_hash
    from app.utils.security import create_access_token

    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(dsn)
    ids = await _insert_tenant_catalog(owner, "old-token-claim-race")
    scenarios: dict[str, dict[str, uuid.UUID | str]] = {}
    for label in ("recall-first", "claim-first"):
        production_batch_id = (
            ids["production_batch"]
            if label == "recall-first"
            else await _insert_additional_production_batch(owner, ids, "old-token-claim-first")
        )
        code_batch_id = await _insert_code_batch(
            owner,
            ids,
            production_batch_id=production_batch_id,
        )
        code_item_id = await _insert_code_item(
            owner,
            ids["tenant"],
            code_batch_id,
            status="activated",
        )
        public_id = f"AUTH{code_item_id.hex[:16]}"
        scan_event_id = uuid.uuid4()
        visitor_id = f"visitor-{label}"
        await owner.execute(
            "INSERT INTO scan_events "
            "(id,tenant_id,public_id,scan_time,is_first_scan,is_valid_visit,visitor_id,created_at,updated_at) "
            "VALUES($1,$2,$3,now(),true,true,$4,now(),now())",
            scan_event_id,
            ids["tenant"],
            public_id,
            visitor_id,
        )
        benefit_id = uuid.uuid4()
        consumer_id = uuid.uuid4()
        initial_points = 11 if label == "recall-first" else 22
        await owner.execute(
            "INSERT INTO benefits "
            "(id,tenant_id,name,benefit_type,config_json,stock_total,stock_used,per_person_limit,status) "
            "VALUES($1,$2,$3,'platform_coupon','{}'::jsonb,3,0,1,'active')",
            benefit_id,
            ids["tenant"],
            f"authority {label} benefit",
        )
        await owner.execute(
            "INSERT INTO consumer_profiles "
            "(id,tenant_id,nickname,member_level,total_points,created_at,updated_at) "
            "VALUES($1,$2,$3,'normal',$4,now(),now())",
            consumer_id,
            ids["tenant"],
            f"authority {label} consumer",
            initial_points,
        )
        scenarios[label] = {
            "production_batch": production_batch_id,
            "code_batch": code_batch_id,
            "code_item": code_item_id,
            "public_id": public_id,
            "scan_event": scan_event_id,
            "visitor_id": visitor_id,
            "benefit": benefit_id,
            "consumer": consumer_id,
            "initial_points": str(initial_points),
        }

    permission_id = uuid.uuid4()
    session_id = uuid.uuid4()
    await owner.execute(
        "INSERT INTO permissions(id,tenant_id,code,created_at,updated_at) VALUES($1,$2,'code:manage',now(),now())",
        permission_id,
        ids["tenant"],
    )
    await owner.execute(
        "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3)",
        ids["tenant"],
        ids["admin_role"],
        permission_id,
    )
    await owner.execute(
        "INSERT INTO auth_sessions "
        "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
        "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
        session_id,
        ids["account"],
        ids["tenant"],
        uuid.uuid4().hex,
    )

    reached_finalize = {label: asyncio.Event() for label in scenarios}
    allow_finalize = {label: asyncio.Event() for label in scenarios}

    class PausingRuntimeSession(AsyncSession):
        async def commit(self) -> None:
            pause_key = self.info.get("u02b_claim_recall_pause")
            if pause_key in reached_finalize and not self.info.get("u02b_claim_recall_pause_consumed"):
                self.info["u02b_claim_recall_pause_consumed"] = True
                reached_finalize[pause_key].set()
                await allow_finalize[pause_key].wait()
            await super().commit()

    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    runtime_engine = create_async_engine(runtime_url)
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_factory = async_sessionmaker(runtime_engine, class_=PausingRuntimeSession, expire_on_commit=False)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)
    monkeypatch.setattr(benefit_delivery_handler, "async_session_factory", runtime_factory)

    async def tagged_get_db(request: fastapi.Request):
        dependency = database.get_db(request)
        session = await dependency.__anext__()
        try:
            pause_key = request.headers.get("X-U02B-Claim-Recall-Pause")
            if pause_key:
                session.info["u02b_claim_recall_pause"] = pause_key
            yield session
        except BaseException as route_error:
            try:
                await dependency.athrow(route_error)
            except StopAsyncIteration:
                pass
            except BaseException as dependency_error:
                if dependency_error is not route_error:
                    raise
            raise route_error
        else:
            try:
                await dependency.__anext__()
            except StopAsyncIteration:
                pass
            else:
                raise AssertionError("get_db yielded more than one session")
        finally:
            await dependency.aclose()

    async def cache_is_not_revoked(self, key: str) -> bool:
        return False

    async def allow_rate_limit(key: str, limit: int, window: int) -> RateLimitResult:
        return RateLimitResult(allowed=True)

    async def no_cache_invalidation(product_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(AsyncRedisCache, "is_token_revoked", cache_is_not_revoked)
    monkeypatch.setattr(rate_limiter, "check", allow_rate_limit)
    monkeypatch.setattr("app.services.resolver_response.invalidate_product_cache", no_cache_invalidation)
    prior_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = tagged_get_db

    admin_token = create_access_token(
        str(ids["tenant"]),
        str(ids["account"]),
        "admin",
        "brand",
        extra={"sid": str(session_id), "auth_version": 0},
    )
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    fixed_ip = "203.0.113.226"
    for scenario in scenarios.values():
        scenario["scan_token"] = create_scan_token(
            public_id=str(scenario["public_id"]),
            ip_hash=compute_ip_hash(fixed_ip),
            tenant_id=str(ids["tenant"]),
            consumer_id=str(scenario["consumer"]),
            scan_event_id=str(scenario["scan_event"]),
            visitor_id=str(scenario["visitor_id"]),
        )

    request_tasks: list[asyncio.Task] = []

    def recall_request(client: AsyncClient, label: str, *, pause: bool = False):
        scenario = scenarios[label]
        headers = admin_headers if not pause else {**admin_headers, "X-U02B-Claim-Recall-Pause": label}
        return client.post(
            f"/api/v1/production-batches/{scenario['production_batch']}/recall",
            json={"reason": f"{label} benefit claim race", "confirm": "recall"},
            headers=headers,
        )

    def claim_request(client: AsyncClient, label: str, *, pause: bool = False):
        scenario = scenarios[label]
        headers = {"X-Forwarded-For": fixed_ip}
        if pause:
            headers["X-U02B-Claim-Recall-Pause"] = label
        return client.post(
            "/api/v1/benefit-claims",
            json={"benefit_id": str(scenario["benefit"]), "scan_token": scenario["scan_token"]},
            headers=headers,
        )

    async def wait_for_finalize(task: asyncio.Task, label: str) -> None:
        event_task = asyncio.create_task(reached_finalize[label].wait())
        done, _ = await asyncio.wait(
            {task, event_task},
            timeout=10,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done:
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
            response = task.result()
            raise AssertionError(
                f"{label} request completed before transaction finalize: "
                f"status={response.status_code} body={response.text}"
            )
        if event_task not in done:
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
            raise TimeoutError(f"{label} request did not reach transaction finalize")
        await event_task

    async def assert_consumer_value_state(label: str, *, claims: int, stock_used: int) -> None:
        scenario = scenarios[label]
        row = await owner.fetchrow(
            "SELECT b.stock_used,"
            "(SELECT count(*) FROM benefit_claims c WHERE c.tenant_id=b.tenant_id AND c.benefit_id=b.id),"
            "(SELECT count(*) FROM benefit_deliveries d WHERE d.tenant_id=b.tenant_id AND d.benefit_id=b.id),"
            "(SELECT count(*) FROM point_transactions p WHERE p.tenant_id=b.tenant_id AND p.consumer_id=$3),"
            "(SELECT total_points FROM consumer_profiles p WHERE p.tenant_id=b.tenant_id AND p.id=$3) "
            "FROM benefits b WHERE b.tenant_id=$1 AND b.id=$2",
            ids["tenant"],
            scenario["benefit"],
            scenario["consumer"],
        )
        assert tuple(row) == (
            stock_used,
            claims,
            0,
            0,
            int(str(scenario["initial_points"])),
        )

    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            recall_task = asyncio.create_task(recall_request(client, "recall-first", pause=True))
            request_tasks.append(recall_task)
            await wait_for_finalize(recall_task, "recall-first")
            claim_task = asyncio.create_task(claim_request(client, "recall-first"))
            request_tasks.append(claim_task)
            await asyncio.sleep(0.15)
            assert not claim_task.done(), "old-token claim must wait behind recall through pre-commit"
            allow_finalize["recall-first"].set()
            recall_response, claim_response = await asyncio.wait_for(
                asyncio.gather(recall_task, claim_task),
                timeout=10,
            )
            assert recall_response.status_code == 200, recall_response.text
            assert claim_response.status_code == 403, claim_response.text
            assert claim_response.json()["detail"]["code"] == "production_batch_unavailable"
            assert "40P01" not in recall_response.text + claim_response.text
            assert (
                await owner.fetchval(
                    "SELECT status::text FROM production_batches WHERE id=$1",
                    scenarios["recall-first"]["production_batch"],
                )
                == "recalled"
            )
            assert (
                await owner.fetchval(
                    "SELECT status::text FROM code_items WHERE id=$1",
                    scenarios["recall-first"]["code_item"],
                )
                == "frozen"
            )
            await assert_consumer_value_state("recall-first", claims=0, stock_used=0)

            claim_task = asyncio.create_task(claim_request(client, "claim-first", pause=True))
            request_tasks.append(claim_task)
            await wait_for_finalize(claim_task, "claim-first")
            recall_task = asyncio.create_task(recall_request(client, "claim-first"))
            request_tasks.append(recall_task)
            await asyncio.sleep(0.15)
            assert not recall_task.done(), "recall must wait behind claim through pre-commit"
            allow_finalize["claim-first"].set()
            claim_response, recall_response = await asyncio.wait_for(
                asyncio.gather(claim_task, recall_task),
                timeout=10,
            )
            assert claim_response.status_code == 201, claim_response.text
            claim_payload = claim_response.json()
            assert claim_payload["status"] == "claimed"
            assert claim_payload["benefit_id"] == str(scenarios["claim-first"]["benefit"])
            assert uuid.UUID(claim_payload["claim_id"])
            assert recall_response.status_code == 200, recall_response.text
            assert "40P01" not in claim_response.text + recall_response.text
            assert (
                await owner.fetchval(
                    "SELECT status::text FROM production_batches WHERE id=$1",
                    scenarios["claim-first"]["production_batch"],
                )
                == "recalled"
            )
            assert (
                await owner.fetchval(
                    "SELECT status::text FROM code_items WHERE id=$1",
                    scenarios["claim-first"]["code_item"],
                )
                == "frozen"
            )
            await assert_consumer_value_state("claim-first", claims=1, stock_used=1)

            for scenario in scenarios.values():
                assert (
                    await owner.fetchval(
                        "SELECT count(*) FROM platform_audit_log WHERE target_tenant_id=$1 AND operator_id=$2 "
                        "AND action='production_batch_recalled' AND resource=$3",
                        str(ids["tenant"]),
                        str(ids["account"]),
                        f"production_batch:{scenario['production_batch']}",
                    )
                    == 1
                )
    finally:
        for event in allow_finalize.values():
            event.set()
        for task in request_tasks:
            if not task.done():
                task.cancel()
        if request_tasks:
            await asyncio.gather(*request_tasks, return_exceptions=True)
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior_overrides)
        await runtime_engine.dispose()
        await owner_engine.dispose()
        await owner.execute(
            "DELETE FROM platform_audit_log WHERE target_tenant_id=$1 AND resource=ANY($2::text[])",
            str(ids["tenant"]),
            [f"production_batch:{scenario['production_batch']}" for scenario in scenarios.values()],
        )
        await owner.execute(
            "DELETE FROM benefit_deliveries WHERE tenant_id=$1 AND benefit_id=ANY($2::uuid[])",
            ids["tenant"],
            [scenario["benefit"] for scenario in scenarios.values()],
        )
        await owner.execute(
            "DELETE FROM campaign_claim_outbox WHERE tenant_id=$1 AND claim_id IN "
            "(SELECT id FROM benefit_claims WHERE tenant_id=$1 AND benefit_id=ANY($2::uuid[]))",
            ids["tenant"],
            [scenario["benefit"] for scenario in scenarios.values()],
        )
        await owner.execute(
            "DELETE FROM benefit_claims WHERE tenant_id=$1 AND benefit_id=ANY($2::uuid[])",
            ids["tenant"],
            [scenario["benefit"] for scenario in scenarios.values()],
        )
        await owner.execute(
            "DELETE FROM benefits WHERE tenant_id=$1 AND id=ANY($2::uuid[])",
            ids["tenant"],
            [scenario["benefit"] for scenario in scenarios.values()],
        )
        await owner.execute(
            "DELETE FROM point_transactions WHERE tenant_id=$1 AND consumer_id=ANY($2::uuid[])",
            ids["tenant"],
            [scenario["consumer"] for scenario in scenarios.values()],
        )
        await owner.execute(
            "DELETE FROM consumer_profiles WHERE tenant_id=$1 AND id=ANY($2::uuid[])",
            ids["tenant"],
            [scenario["consumer"] for scenario in scenarios.values()],
        )
        await owner.execute(
            "DELETE FROM scan_events WHERE tenant_id=$1 AND id=ANY($2::uuid[])",
            ids["tenant"],
            [scenario["scan_event"] for scenario in scenarios.values()],
        )
        await owner.execute(
            "DELETE FROM code_items WHERE tenant_id=$1 AND id=ANY($2::uuid[])",
            ids["tenant"],
            [scenario["code_item"] for scenario in scenarios.values()],
        )
        await owner.execute(
            "DELETE FROM code_batches WHERE tenant_id=$1 AND id=ANY($2::uuid[])",
            ids["tenant"],
            [scenario["code_batch"] for scenario in scenarios.values()],
        )
        await owner.execute(
            "DELETE FROM production_batches WHERE tenant_id=$1 AND id=ANY($2::uuid[])",
            ids["tenant"],
            [scenario["production_batch"] for scenario in scenarios.values()],
        )
        await owner.execute("DELETE FROM auth_sessions WHERE id=$1", session_id)
        await owner.execute("DELETE FROM role_permissions WHERE permission_id=$1", permission_id)
        await owner.execute("DELETE FROM permissions WHERE id=$1", permission_id)
        await owner.close()


async def test_code_action_audit_resources_are_tenant_owned_for_self_and_agency(migrated_pg_url: str) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(dsn)
    target = await _insert_tenant_catalog(owner, "audit-resource-target")
    other = await _insert_tenant_catalog(owner, "audit-resource-other")
    agency = await _insert_tenant_catalog(owner, "audit-resource-agency")
    target_batch = await _insert_code_batch(
        owner,
        target,
        production_batch_id=target["production_batch"],
    )
    target_item = await _insert_code_item(owner, target["tenant"], target_batch)
    target_public_id = f"AUTH{target_item.hex[:16]}"
    other_batch = await _insert_code_batch(
        owner,
        other,
        production_batch_id=other["production_batch"],
    )
    other_item = await _insert_code_item(owner, other["tenant"], other_batch)
    other_public_id = f"AUTH{other_item.hex[:16]}"
    target_session_id = uuid.uuid4()
    agency_session_id = uuid.uuid4()
    authorization_id = uuid.uuid4()
    await owner.execute("UPDATE tenants SET tenant_type='agency' WHERE id=$1", agency["tenant"])
    await owner.executemany(
        "INSERT INTO auth_sessions "
        "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
        "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
        [
            (target_session_id, target["account"], target["tenant"], uuid.uuid4().hex),
            (agency_session_id, agency["account"], agency["tenant"], uuid.uuid4().hex),
        ],
    )
    await owner.execute(
        "INSERT INTO agency_authorizations "
        "(id,agency_tenant_id,client_tenant_id,scope,status,granted_by,granted_at,expires_at,created_at,updated_at) "
        "VALUES($1,$2,$3,'[\"codes\"]'::json,'active',$4,now(),now()+interval '1 hour',now(),now())",
        authorization_id,
        agency["tenant"],
        target["tenant"],
        target["account"],
    )

    valid_resources = (
        ("code_bind", f"code_item:{target_public_id}"),
        ("code_mark_printing", f"code_batch:{target_batch}"),
        ("code_mark_delivered", f"code_batch:{target_batch}"),
    )
    invalid_resources = (
        ("code_bind", f"wrong:{target_public_id}", "22023"),
        ("code_bind", "code_item:", "22023"),
        ("code_bind", None, "22023"),
        ("code_bind", f"code_item:AUTH{uuid.uuid4().hex[:16]}", "23503"),
        ("code_bind", f"code_item:{other_public_id}", "23503"),
        ("code_mark_printing", f"code_item:{target_batch}", "22023"),
        ("code_mark_printing", "code_batch:not-a-uuid", "22023"),
        ("code_mark_printing", None, "22023"),
        ("code_mark_printing", f"code_batch:{uuid.uuid4()}", "23503"),
        ("code_mark_printing", f"code_batch:{other_batch}", "23503"),
        ("code_mark_delivered", f"code_item:{target_batch}", "22023"),
        ("code_mark_delivered", "code_batch:not-a-uuid", "22023"),
        ("code_mark_delivered", None, "22023"),
        ("code_mark_delivered", f"code_batch:{uuid.uuid4()}", "23503"),
        ("code_mark_delivered", f"code_batch:{other_batch}", "23503"),
    )
    runtime_dsn = dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    runtime = await asyncpg.connect(runtime_dsn)
    successful_audits: list[tuple[uuid.UUID, uuid.UUID]] = []
    rejected_audits: list[uuid.UUID] = []
    try:
        for context_tenant_id, session_id, expected_actor_id in (
            (target["tenant"], target_session_id, target["account"]),
            (agency["tenant"], agency_session_id, agency["account"]),
        ):
            await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(context_tenant_id))
            for action, resource in valid_resources:
                audit_id = uuid.uuid4()
                resolved = await runtime.fetchrow(
                    "SELECT * FROM append_authenticated_audit_event($1,$2,$3,$4,$5,$6::jsonb)",
                    audit_id,
                    session_id,
                    str(target["tenant"]),
                    action,
                    resource,
                    '{"result":"success"}',
                )
                assert resolved["resolved_operator_id"] == str(expected_actor_id)
                successful_audits.append((audit_id, expected_actor_id))

            for action, resource, expected_sqlstate in invalid_resources:
                audit_id = uuid.uuid4()
                rejected = await _assert_rejected(
                    runtime,
                    "SELECT * FROM append_authenticated_audit_event($1,$2,$3,$4,$5,$6::jsonb)",
                    audit_id,
                    session_id,
                    str(target["tenant"]),
                    action,
                    resource,
                    '{"result":"success"}',
                )
                assert getattr(rejected, "sqlstate", None) == expected_sqlstate
                rejected_audits.append(audit_id)
    finally:
        await runtime.close()

    try:
        persisted = await owner.fetch(
            "SELECT id,operator_id,target_tenant_id FROM platform_audit_log WHERE id=ANY($1::uuid[])",
            [audit_id for audit_id, _ in successful_audits],
        )
        assert {row["id"] for row in persisted} == {audit_id for audit_id, _ in successful_audits}
        assert all(row["target_tenant_id"] == str(target["tenant"]) for row in persisted)
        expected_actors = {audit_id: str(actor_id) for audit_id, actor_id in successful_audits}
        assert all(row["operator_id"] == expected_actors[row["id"]] for row in persisted)
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=ANY($1::uuid[]))",
            rejected_audits,
        )
    finally:
        await owner.execute(
            "DELETE FROM platform_audit_log WHERE id=ANY($1::uuid[])",
            [audit_id for audit_id, _ in successful_audits],
        )
        await owner.execute("DELETE FROM agency_authorizations WHERE id=$1", authorization_id)
        await owner.execute(
            "DELETE FROM auth_sessions WHERE id=ANY($1::uuid[])",
            [target_session_id, agency_session_id],
        )
        await owner.execute("DELETE FROM code_items WHERE id=ANY($1::uuid[])", [target_item, other_item])
        await owner.execute("DELETE FROM code_batches WHERE id=ANY($1::uuid[])", [target_batch, other_batch])
        await owner.close()


async def test_catalog_audit_contract_and_clean_roundtrip(migrated_pg_url: str) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(dsn)
    try:
        ids = await _insert_tenant_catalog(owner, "audit")
        code_batch_id = await _insert_code_batch(
            owner,
            ids,
            production_batch_id=ids["production_batch"],
        )
        session_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO auth_sessions "
            "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
            session_id,
            ids["account"],
            ids["tenant"],
            uuid.uuid4().hex,
        )

        expected_fks = {
            "fk_code_batches_tenant",
            "fk_code_batches_tenant_product",
            "fk_code_batches_tenant_product_sku",
            "fk_code_batches_tenant_product_sku_production_batch",
            "fk_code_items_tenant",
            "fk_code_items_tenant_code_batch",
        }
        constraints = await owner.fetch(
            "SELECT conname,convalidated FROM pg_constraint WHERE conname=ANY($1::text[]) ORDER BY conname",
            list(expected_fks),
        )
        assert {row["conname"] for row in constraints} == expected_fks
        assert all(row["convalidated"] for row in constraints)
        assert await owner.fetchval(
            "SELECT is_nullable='NO' FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='code_batches' AND column_name='production_batch_id'"
        )
        for table in ("production_batches", "code_batches", "code_items"):
            assert await owner.fetchval(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=$1::regclass",
                f"public.{table}",
            )

        batch_guard_signature = "public.guard_code_batch_production_link()"
        batch_guard_definition = await owner.fetchval(
            "SELECT pg_get_functiondef($1::regprocedure)", batch_guard_signature
        )
        for guarded_status in ("generating", "completed", "exported", "printing", "delivered", "activated"):
            assert f"'{guarded_status}'" in batch_guard_definition
        assert "NEW.status IS NOT DISTINCT FROM OLD.status" in batch_guard_definition
        assert "FOR SHARE OF production_batch NOWAIT" in batch_guard_definition
        assert await owner.fetchval(
            "SELECT prosecdef AND proconfig @> ARRAY['search_path=pg_catalog, public'] "
            "FROM pg_proc WHERE oid=$1::regprocedure",
            batch_guard_signature,
        )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('public',$1,'EXECUTE')",
            batch_guard_signature,
        )
        batch_guard_trigger = await owner.fetchval(
            "SELECT pg_get_triggerdef(oid) FROM pg_trigger "
            "WHERE tgrelid='public.code_batches'::regclass "
            "AND tgname='trg_guard_code_batch_production_link' AND NOT tgisinternal"
        )
        assert "UPDATE OF tenant_id, product_id, sku_id, production_batch_id, status" in batch_guard_trigger

        item_guard_signature = "public.guard_code_item_production_batch_active()"
        item_guard_definition = await owner.fetchval(
            "SELECT pg_get_functiondef($1::regprocedure)", item_guard_signature
        )
        for guard_definition in (batch_guard_definition, item_guard_definition):
            normalized_guard = guard_definition.upper()
            assert "CURRENT_DATE" not in normalized_guard
            assert normalized_guard.count("CURRENT_TIMESTAMP AT TIME ZONE 'ASIA/SHANGHAI'") == 1
            assert "FOR SHARE OF PRODUCTION_BATCH NOWAIT" in normalized_guard
        assert await owner.fetchval(
            "SELECT prosecdef AND proconfig @> ARRAY['search_path=pg_catalog, public'] "
            "FROM pg_proc WHERE oid=$1::regprocedure",
            item_guard_signature,
        )
        assert not await owner.fetchval(
            "SELECT has_function_privilege('public',$1,'EXECUTE')",
            item_guard_signature,
        )

        signature = "public.append_authenticated_audit_event(uuid,uuid,text,text,text,jsonb)"
        definition = await owner.fetchval("SELECT pg_get_functiondef($1::regprocedure)", signature)
        internal_signature = (
            "public.append_authenticated_audit_event_lifecycle_internal(uuid,uuid,text,text,text,jsonb)"
        )
        internal_definition = await owner.fetchval("SELECT pg_get_functiondef($1::regprocedure)", internal_signature)
        for action in (
            "code_batch_created",
            "code_batch_updated",
            "code_import_completed",
            "code_bind",
            "code_mark_printing",
            "code_mark_delivered",
            "catalog_import_completed",
        ):
            assert f"WHEN '{action}'" in internal_definition
        assert "production_batch_recalled" not in definition
        assert not await owner.fetchval("SELECT has_function_privilege('public',$1,'EXECUTE')", internal_signature)
        assert not await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", internal_signature
        )
        recall_definition = await owner.fetchval(
            "SELECT pg_get_functiondef('public.recall_production_batch(uuid,uuid,uuid,uuid,text)'::regprocedure)"
        )
        assert "FROM public.authorize_code_lifecycle_actor(" in recall_definition
        assert "INSERT INTO public.platform_audit_log(" in recall_definition
        assert "'production_batch_recalled'" in recall_definition
        assert "'production_batch:'||requested_production_batch_id::text" in recall_definition
        assert "resolved_actor" in recall_definition
        assert await owner.fetchval(
            "SELECT proconfig @> ARRAY['search_path=pg_catalog, public'] FROM pg_proc WHERE oid=$1::regprocedure",
            signature,
        )
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')",
            signature,
        )
        assert not await owner.fetchval("SELECT has_function_privilege('public',$1,'EXECUTE')", signature)
    finally:
        await owner.close()

    runtime_dsn = dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    runtime = await asyncpg.connect(runtime_dsn)
    await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(ids["tenant"]))
    audit_id = uuid.uuid4()
    resolved = await runtime.fetchrow(
        "SELECT * FROM append_authenticated_audit_event($1,$2,$3,$4,$5,$6::jsonb)",
        audit_id,
        session_id,
        str(ids["tenant"]),
        "code_batch_created",
        f"code_batch:{code_batch_id}",
        '{"result":"success"}',
    )
    assert resolved["resolved_operator_id"] == str(ids["account"])
    catalog_successes: list[tuple[uuid.UUID, uuid.UUID, dict[str, int | str]]] = []
    for import_type in ("excel", "csv"):
        catalog_brand_id = uuid.uuid4()
        catalog_audit_id = uuid.uuid4()
        catalog_details: dict[str, int | str] = {
            "created": 1,
            "updated": 0,
            "errors": 0,
            "import_type": import_type,
        }
        catalog_tx = runtime.transaction()
        await catalog_tx.start()
        try:
            await runtime.execute(
                "INSERT INTO brands(id,tenant_id,name,status,created_at,updated_at) "
                "VALUES($1,$2,$3,'active',now(),now())",
                catalog_brand_id,
                ids["tenant"],
                f"authority catalog {import_type} {catalog_brand_id.hex[:8]}",
            )
            resolved = await runtime.fetchrow(
                "SELECT * FROM append_authenticated_audit_event($1,$2,$3,$4,$5,$6::jsonb)",
                catalog_audit_id,
                session_id,
                str(ids["tenant"]),
                "catalog_import_completed",
                f"catalog_import:{uuid.uuid4()}",
                json.dumps(catalog_details),
            )
            assert resolved["resolved_operator_id"] == str(ids["account"])
            await catalog_tx.commit()
        except BaseException:
            await catalog_tx.rollback()
            raise
        catalog_successes.append((catalog_brand_id, catalog_audit_id, catalog_details))

    rolled_back_brand_id = uuid.uuid4()
    rolled_back_audit_id = uuid.uuid4()
    failed_catalog_tx = runtime.transaction()
    await failed_catalog_tx.start()
    await runtime.execute(
        "INSERT INTO brands(id,tenant_id,name,status,created_at,updated_at) VALUES($1,$2,$3,'active',now(),now())",
        rolled_back_brand_id,
        ids["tenant"],
        f"authority catalog rollback {rolled_back_brand_id.hex[:8]}",
    )
    with pytest.raises(asyncpg.DataError):
        await runtime.fetchrow(
            "SELECT * FROM append_authenticated_audit_event($1,$2,$3,$4,$5,$6::jsonb)",
            rolled_back_audit_id,
            session_id,
            str(ids["tenant"]),
            "catalog_import_completed",
            f"catalog_import:{uuid.uuid4()}",
            '{"created":1,"updated":0,"errors":0,"import_type":"csv","raw_rows":[]}',
        )
    await failed_catalog_tx.rollback()
    await runtime.close()

    owner = await asyncpg.connect(dsn)
    try:
        for catalog_brand_id, catalog_audit_id, catalog_details in catalog_successes:
            assert await owner.fetchval("SELECT EXISTS(SELECT 1 FROM brands WHERE id=$1)", catalog_brand_id)
            persisted_audit = await owner.fetchrow(
                "SELECT operator_id,target_tenant_id,action,details::text FROM platform_audit_log WHERE id=$1",
                catalog_audit_id,
            )
            assert persisted_audit["operator_id"] == str(ids["account"])
            assert persisted_audit["target_tenant_id"] == str(ids["tenant"])
            assert persisted_audit["action"] == "catalog_import_completed"
            assert json.loads(persisted_audit["details"]) == catalog_details
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM brands WHERE id=$1)", rolled_back_brand_id)
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$1)", rolled_back_audit_id
        )
        await owner.execute(
            "DELETE FROM platform_audit_log WHERE id=ANY($1::uuid[])",
            [audit_id, *(catalog_audit_id for _, catalog_audit_id, _ in catalog_successes)],
        )
        await owner.execute(
            "DELETE FROM brands WHERE id=ANY($1::uuid[])",
            [catalog_brand_id for catalog_brand_id, _, _ in catalog_successes],
        )
        await owner.execute("DELETE FROM auth_sessions WHERE id=$1", session_id)
        await owner.execute("DELETE FROM code_batches WHERE id=$1", code_batch_id)
    finally:
        await owner.close()

    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    owner = await asyncpg.connect(dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == PARENT_REVISION
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_name='production_batches' AND column_name='recall_reason')"
        )
        definition = await owner.fetchval(
            "SELECT pg_get_functiondef('public.append_authenticated_audit_event"
            "(uuid,uuid,text,text,text,jsonb)'::regprocedure)"
        )
        assert "production_batch_recalled" not in definition
        assert "code_batch_created" not in definition
        assert "code_bind" not in definition
        assert "code_mark_printing" not in definition
        assert "code_mark_delivered" not in definition
        assert "catalog_import_completed" not in definition
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "head")
    _alembic(migrated_pg_url, "-x", "baseline_legacy_timestamp_nullability=true", "check")
