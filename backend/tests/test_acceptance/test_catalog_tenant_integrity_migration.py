"""Real PostgreSQL acceptance for tenant-safe product catalog relationships."""

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

PARENT_REVISION = "c21e5f7a9b31"
REVISION = "d32f6a8b9c40"


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


async def _insert_tenant(conn: asyncpg.Connection, tenant_id: uuid.UUID, suffix: str) -> None:
    await conn.execute(
        "INSERT INTO tenants (id,name,slug,status,plan,tenant_type,created_at,updated_at) "
        "VALUES ($1,$2,$3,'active','free','brand',now(),now())",
        tenant_id,
        f"catalog {suffix}",
        f"catalog-{suffix}-{tenant_id.hex[:8]}",
    )


async def _insert_catalog(
    conn: asyncpg.Connection,
    tenant_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    brand_id, product_id, sku_id, batch_id = (uuid.uuid4() for _ in range(4))
    await conn.execute(
        "INSERT INTO brands (id,tenant_id,name,status,created_at,updated_at) VALUES ($1,$2,$3,'active',now(),now())",
        brand_id,
        tenant_id,
        f"brand-{brand_id.hex[:8]}",
    )
    await conn.execute(
        "INSERT INTO products (id,tenant_id,brand_id,name,status,created_at,updated_at) "
        "VALUES ($1,$2,$3,$4,'active',now(),now())",
        product_id,
        tenant_id,
        brand_id,
        f"product-{product_id.hex[:8]}",
    )
    await conn.execute(
        "INSERT INTO skus (id,tenant_id,product_id,code,name,status,created_at,updated_at) "
        "VALUES ($1,$2,$3,$4,$5,'active',now(),now())",
        sku_id,
        tenant_id,
        product_id,
        f"sku-{sku_id.hex[:8]}",
        "Catalog SKU",
    )
    await conn.execute(
        "INSERT INTO production_batches "
        "(id,tenant_id,product_id,sku_id,batch_code,production_date,expiry_date,status,created_at,updated_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,'active',now(),now())",
        batch_id,
        tenant_id,
        product_id,
        sku_id,
        f"batch-{batch_id.hex[:8]}",
        date.today(),
        date.today() + timedelta(days=30),
    )
    return brand_id, product_id, sku_id, batch_id


async def test_preflight_reports_exact_cross_tenant_row_before_ddl(migrated_pg_url: str) -> None:
    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    tenant_a, tenant_b, brand_id, product_id = (uuid.uuid4() for _ in range(4))
    conn = await asyncpg.connect(dsn)
    try:
        await _insert_tenant(conn, tenant_a, "preflight-a")
        await _insert_tenant(conn, tenant_b, "preflight-b")
        await conn.execute(
            "INSERT INTO brands (id,tenant_id,name,status,created_at,updated_at) "
            "VALUES ($1,$2,'preflight brand','active',now(),now())",
            brand_id,
            tenant_a,
        )
        await conn.execute(
            "INSERT INTO products (id,tenant_id,brand_id,name,status,created_at,updated_at) "
            "VALUES ($1,$2,$3,'cross tenant product','active',now(),now())",
            product_id,
            tenant_b,
            brand_id,
        )
    finally:
        await conn.close()

    failed = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
    output = f"{failed.stdout}\n{failed.stderr}"
    assert f"products.tenant_brand={product_id}" in output

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == PARENT_REVISION
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='fk_products_tenant_brand')"
        )
        assert await conn.fetchval("SELECT tenant_id FROM products WHERE id=$1", product_id) == tenant_b
        await conn.execute("DELETE FROM products WHERE id=$1", product_id)
        await conn.execute("DELETE FROM brands WHERE id=$1", brand_id)
        await conn.execute("DELETE FROM tenants WHERE id=ANY($1::uuid[])", [tenant_a, tenant_b])
    finally:
        await conn.close()
    _alembic(migrated_pg_url, "upgrade", "head")
    _alembic(
        migrated_pg_url,
        "-x",
        "baseline_legacy_timestamp_nullability=true",
        "check",
    )


async def test_catalog_fingerprint_and_clean_roundtrip(migrated_pg_url: str) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == REVISION
        expected_fks = {
            "fk_brands_tenant": ("brands", "tenants", "{tenant_id}", "{id}"),
            "fk_products_tenant": ("products", "tenants", "{tenant_id}", "{id}"),
            "fk_skus_tenant": ("skus", "tenants", "{tenant_id}", "{id}"),
            "fk_production_batches_tenant": ("production_batches", "tenants", "{tenant_id}", "{id}"),
            "fk_product_assets_tenant": ("product_assets", "tenants", "{tenant_id}", "{id}"),
            "fk_products_tenant_brand": ("products", "brands", "{tenant_id,brand_id}", "{tenant_id,id}"),
            "fk_skus_tenant_product": ("skus", "products", "{tenant_id,product_id}", "{tenant_id,id}"),
            "fk_product_assets_tenant_product": (
                "product_assets",
                "products",
                "{tenant_id,product_id}",
                "{tenant_id,id}",
            ),
            "fk_production_batches_tenant_product": (
                "production_batches",
                "products",
                "{tenant_id,product_id}",
                "{tenant_id,id}",
            ),
            "fk_production_batches_tenant_product_sku": (
                "production_batches",
                "skus",
                "{tenant_id,product_id,sku_id}",
                "{tenant_id,product_id,id}",
            ),
        }
        rows = await conn.fetch(
            """
            SELECT con.conname, child.relname AS child_table, parent.relname AS parent_table,
                   (SELECT array_agg(att.attname ORDER BY ord.n)
                    FROM unnest(con.conkey) WITH ORDINALITY AS ord(attnum,n)
                    JOIN pg_attribute AS att ON att.attrelid=con.conrelid AND att.attnum=ord.attnum) AS local_cols,
                   (SELECT array_agg(att.attname ORDER BY ord.n)
                    FROM unnest(con.confkey) WITH ORDINALITY AS ord(attnum,n)
                    JOIN pg_attribute AS att ON att.attrelid=con.confrelid AND att.attnum=ord.attnum) AS remote_cols,
                   con.convalidated, con.condeferrable, con.condeferred,
                   con.confmatchtype, con.confupdtype, con.confdeltype
            FROM pg_constraint AS con
            JOIN pg_class AS child ON child.oid=con.conrelid
            JOIN pg_class AS parent ON parent.oid=con.confrelid
            WHERE con.conname=ANY($1::text[])
            ORDER BY con.conname
            """,
            list(expected_fks),
        )
        assert len(rows) == len(expected_fks)
        for row in rows:
            child, parent, local_cols, remote_cols = expected_fks[row["conname"]]
            assert (row["child_table"], row["parent_table"]) == (child, parent)
            assert "{" + ",".join(row["local_cols"]) + "}" == local_cols
            assert "{" + ",".join(row["remote_cols"]) + "}" == remote_cols
            assert row["convalidated"] is True
            assert row["condeferrable"] is False and row["condeferred"] is False
            assert (row["confmatchtype"], row["confupdtype"], row["confdeltype"]) == (b"s", b"a", b"a")

        for table in ("brands", "products", "skus", "production_batches", "product_assets"):
            rls = await conn.fetchrow(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=$1::regclass",
                f"public.{table}",
            )
            assert (rls["relrowsecurity"], rls["relforcerowsecurity"]) == (True, True)
        assert (
            await conn.fetchval(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='platform_audit_log' AND column_name='operator_id'"
            )
            == 64
        )
        assert await conn.fetchval(
            "SELECT proconfig @> ARRAY['search_path=pg_catalog, public'] "
            "FROM pg_proc WHERE oid='public.append_api_key_catalog_audit_event(uuid,uuid,uuid,text,uuid,jsonb)'::regprocedure"
        )
        assert await conn.fetchval(
            "SELECT has_function_privilege('yimatong_app',"
            "'public.append_api_key_catalog_audit_event(uuid,uuid,uuid,text,uuid,jsonb)','EXECUTE')"
        )
        assert not await conn.fetchval(
            "SELECT has_function_privilege('public',"
            "'public.append_api_key_catalog_audit_event(uuid,uuid,uuid,text,uuid,jsonb)','EXECUTE')"
        )
        auth_definition = await conn.fetchval(
            "SELECT pg_get_functiondef('public.append_authenticated_audit_event(uuid,uuid,text,text,text,jsonb)'::regprocedure)"
        )
        for action in (
            "brand_deleted",
            "sku_created",
            "production_batch_imported",
            "product_asset_updated",
        ):
            assert f"WHEN '{action}'" in auth_definition
    finally:
        await conn.close()

    oversized_id = uuid.uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_audit_log "
            "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
            "VALUES ($1,$2,$3,'catalog_acceptance','catalog',now(),now(),now())",
            oversized_id,
            f"api-key:{uuid.uuid4()}",
            str(uuid.uuid4()),
        )
    finally:
        await conn.close()
    failed = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
    assert f"oversized rows: {oversized_id}" in f"{failed.stdout}\n{failed.stderr}"
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == REVISION
        assert (
            await conn.fetchval(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='platform_audit_log' AND column_name='operator_id'"
            )
            == 64
        )
        await conn.execute("DELETE FROM platform_audit_log WHERE id=$1", oversized_id)
    finally:
        await conn.close()

    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    conn = await asyncpg.connect(dsn)
    try:
        assert (
            await conn.fetchval(
                "SELECT to_regprocedure($1)",
                "public.append_api_key_catalog_audit_event(uuid,uuid,uuid,text,uuid,jsonb)",
            )
            is None
        )
        assert (
            await conn.fetchval(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='platform_audit_log' AND column_name='operator_id'"
            )
            == 36
        )
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='fk_production_batches_tenant_product_sku')"
        )
        auth_definition = await conn.fetchval(
            "SELECT pg_get_functiondef('public.append_authenticated_audit_event(uuid,uuid,text,text,text,jsonb)'::regprocedure)"
        )
        assert "production_batch_imported" not in auth_definition
    finally:
        await conn.close()
    _alembic(migrated_pg_url, "upgrade", "head")


async def test_runtime_constraints_evidence_and_api_key_audit_are_fail_closed(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    owner = await asyncpg.connect(dsn)
    try:
        await _insert_tenant(owner, tenant_a, "runtime-a")
        await _insert_tenant(owner, tenant_b, "runtime-b")
        _brand_a, product_a, sku_a, _batch_a = await _insert_catalog(owner, tenant_a)
        brand_b, product_b, sku_b, _batch_b = await _insert_catalog(owner, tenant_b)

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await owner.execute(
                "INSERT INTO products (id,tenant_id,brand_id,name,status,created_at,updated_at) "
                "VALUES ($1,$2,$3,'Cross brand','active',now(),now())",
                uuid.uuid4(),
                tenant_a,
                brand_b,
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await owner.execute(
                "INSERT INTO skus (id,tenant_id,product_id,code,name,status,created_at,updated_at) "
                "VALUES ($1,$2,$3,$4,'Cross product','active',now(),now())",
                uuid.uuid4(),
                tenant_a,
                product_b,
                f"cross-product-{uuid.uuid4().hex[:8]}",
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await owner.execute(
                "INSERT INTO product_assets "
                "(id,tenant_id,product_id,asset_type,name,status,created_at,updated_at) "
                "VALUES ($1,$2,$3,'image','Cross product asset','inactive',now(),now())",
                uuid.uuid4(),
                tenant_a,
                product_b,
            )

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await owner.execute(
                "INSERT INTO production_batches "
                "(id,tenant_id,product_id,sku_id,batch_code,production_date,expiry_date,status,created_at,updated_at) "
                "VALUES ($1,$2,$3,$4,$5,CURRENT_DATE,CURRENT_DATE,'active',now(),now())",
                uuid.uuid4(),
                tenant_a,
                product_a,
                sku_b,
                f"cross-sku-{uuid.uuid4().hex[:8]}",
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await owner.execute(
                "INSERT INTO production_batches "
                "(id,tenant_id,product_id,sku_id,batch_code,production_date,expiry_date,status,created_at,updated_at) "
                "VALUES ($1,$2,$3,$4,$5,CURRENT_DATE,CURRENT_DATE-1,'active',now(),now())",
                uuid.uuid4(),
                tenant_a,
                product_a,
                sku_a,
                f"bad-date-{uuid.uuid4().hex[:8]}",
            )

        for unsafe_url in (
            "",
            "http://example.test/report",
            "data:text/plain,bad",
            "javascript:bad",
            "/api/v1/files/public/a/../../../auth/logout",
            "/api/v1/files/public/a/%2e%2e/%2e%2e/auth/logout",
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                await owner.execute(
                    "INSERT INTO product_assets "
                    "(id,tenant_id,product_id,asset_type,name,issuer,file_url,status,created_at,updated_at) "
                    "VALUES ($1,$2,$3,'test_report','Unsafe','Issuer',$4,'active',now(),now())",
                    uuid.uuid4(),
                    tenant_a,
                    product_a,
                    unsafe_url,
                )
        for evidence_url in ("https://evidence.example/report", "/api/v1/files/public/report.pdf"):
            await owner.execute(
                "INSERT INTO product_assets "
                "(id,tenant_id,product_id,asset_type,name,issuer,file_url,status,created_at,updated_at) "
                "VALUES ($1,$2,$3,'certificate','Trusted','Issuer',$4,'active',now(),now())",
                uuid.uuid4(),
                tenant_a,
                product_a,
                evidence_url,
            )
        with pytest.raises(asyncpg.InvalidParameterValueError, match="active trust evidence is already expired"):
            await owner.execute(
                "INSERT INTO product_assets "
                "(id,tenant_id,product_id,asset_type,name,issuer,file_url,valid_until,status,created_at,updated_at) "
                "VALUES ($1,$2,$3,'certificate','Expired','Issuer','https://evidence.example/old',"
                "CURRENT_DATE-1,'active',now()-interval '1 year',now())",
                uuid.uuid4(),
                tenant_a,
                product_a,
            )

        api_key_id = uuid.uuid4()
        permissions = ["product:list", "product:create", "product:update"]
        await owner.execute(
            "INSERT INTO api_keys "
            "(id,tenant_id,name,key_prefix,key_digest,role,permissions,revoked,revoked_at,created_at,updated_at) "
            "VALUES ($1,$2,'Catalog acceptance','ymt_12345678',$3,'erp_sync',$4::json,false,NULL,now(),now())",
            api_key_id,
            tenant_a,
            uuid.uuid4().hex + uuid.uuid4().hex,
            json.dumps(permissions),
        )
    finally:
        await owner.close()

    await runtime_pg_conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant_a))
    forged_audit_id = uuid.uuid4()
    await runtime_pg_conn.execute(
        "SELECT set_config('app.api_key_id',$1,true)",
        f"{api_key_id}:{'00' * 32}",
    )
    savepoint = runtime_pg_conn.transaction()
    await savepoint.start()
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime_pg_conn.fetchrow(
                "SELECT * FROM append_api_key_catalog_audit_event($1,$2,$3,$4,$5,$6::jsonb)",
                forged_audit_id,
                api_key_id,
                tenant_a,
                "product_updated",
                product_a,
                "{}",
            )
    finally:
        await savepoint.rollback()
    assert not await runtime_pg_conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$1)",
        forged_audit_id,
    )
    assert await runtime_pg_conn.fetchval("SELECT lock_active_api_key($1,$2)", tenant_a, api_key_id)
    audit_id = uuid.uuid4()
    row = await runtime_pg_conn.fetchrow(
        "SELECT * FROM append_api_key_catalog_audit_event($1,$2,$3,$4,$5,$6::jsonb)",
        audit_id,
        api_key_id,
        tenant_a,
        "product_updated",
        product_a,
        json.dumps({"result": "success"}),
    )
    assert row["resolved_operator_id"] == f"api-key:{api_key_id}"
    recorded_audit = await runtime_pg_conn.fetchrow(
        "SELECT operator_id,resource FROM platform_audit_log WHERE id=$1",
        audit_id,
    )
    assert (recorded_audit["operator_id"], recorded_audit["resource"]) == (
        f"api-key:{api_key_id}",
        f"product:{product_a}",
    )

    savepoint = runtime_pg_conn.transaction()
    await savepoint.start()
    try:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await runtime_pg_conn.fetchrow(
                "SELECT * FROM append_api_key_catalog_audit_event($1,$2,$3,$4,$5,$6::jsonb)",
                uuid.uuid4(),
                api_key_id,
                tenant_a,
                "sku_updated",
                sku_b,
                "{}",
            )
    finally:
        await savepoint.rollback()

    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await runtime_pg_conn.execute("UPDATE platform_audit_log SET resource='tampered' WHERE id=$1", audit_id)
