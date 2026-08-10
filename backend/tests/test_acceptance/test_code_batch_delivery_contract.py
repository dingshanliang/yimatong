"""PostgreSQL contract for atomic code generation and delivery manifests."""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
import uuid
from datetime import date, timedelta

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.code_export import generate_code_csv
from tests.test_acceptance.conftest import BACKEND_DIR

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "2ed1cb06d0ca"
EXPAND_REVISION = "3f91c0d2e4a6"
FINALIZE_REVISION = "4a92d1e3f5b7"
HEAD_REVISION = "d297eb0518da"
C7_REVISION = "c7d8e9f0a1b2"
C7_PARENT_REVISION = "f8b1b3069972"


def _alembic(database_url: str, *args: str, succeeds: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "database_url": database_url,
            "migration_database_url": database_url,
            "control_database_url": database_url,
        }
    )
    arguments = [sys.executable, "-m", "alembic"]
    if args == ("check",):
        arguments.extend(["-x", "baseline_legacy_timestamp_nullability=true"])
    arguments.extend(args)
    result = subprocess.run(
        arguments,
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


def _app_cli(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    runtime_url = database_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    assert make_url(runtime_url).username == "yimatong_app"
    assert make_url(database_url).username == "yimatong"
    env.update(
        {
            "database_url": runtime_url,
            "migration_database_url": database_url,
            "control_database_url": database_url,
        }
    )
    assert env["database_url"] == runtime_url
    assert env["migration_database_url"] == env["control_database_url"] == database_url
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"app.cli {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    return result


async def _seed_catalog(conn: asyncpg.Connection, label: str) -> dict[str, uuid.UUID]:
    transaction = conn.transaction()
    await transaction.start()
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
    await conn.execute(
        "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
        "VALUES($1,$2,$3,'active','free','brand',now(),now())",
        ids["tenant"],
        f"delivery {label}",
        f"delivery-{label}-{ids['tenant'].hex[:8]}",
    )
    await conn.execute(
        "INSERT INTO organizations(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
        ids["organization"],
        ids["tenant"],
        f"delivery {label} org",
    )
    await conn.execute(
        "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,failed_login_attempts,"
        "is_active,auth_version,must_change_password,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'x',$5,0,true,0,false,now(),now())",
        ids["account"],
        ids["tenant"],
        ids["organization"],
        f"delivery-{ids['account'].hex[:8]}@test.local",
        f"delivery {label} actor",
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
        f"delivery {label} brand",
    )
    await conn.execute(
        "INSERT INTO products(id,tenant_id,brand_id,name,status,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'active',now(),now())",
        ids["product"],
        ids["tenant"],
        ids["brand"],
        f"delivery {label} product",
    )
    await conn.execute(
        "INSERT INTO skus(id,tenant_id,product_id,code,name,status,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,'active',now(),now())",
        ids["sku"],
        ids["tenant"],
        ids["product"],
        f"DELIVERY-{ids['sku'].hex[:8]}",
        f"delivery {label} sku",
    )
    await conn.execute(
        "INSERT INTO production_batches "
        "(id,tenant_id,product_id,sku_id,batch_code,production_date,expiry_date,status,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,'active',now(),now())",
        ids["production_batch"],
        ids["tenant"],
        ids["product"],
        ids["sku"],
        f"DELIVERY-PB-{ids['production_batch'].hex[:8]}",
        date.today(),
        date.today() + timedelta(days=365),
    )
    await transaction.commit()
    return ids


async def _insert_receipt(conn: asyncpg.Connection, ids: dict[str, uuid.UUID]) -> uuid.UUID:
    receipt_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO code_batch_generation_receipts "
        "(id,tenant_id,created_by,idempotency_digest,request_fingerprint,created_at) "
        "VALUES($1,$2,$3,$4,$5,now())",
        receipt_id,
        ids["tenant"],
        ids["account"],
        receipt_id.hex * 2,
        uuid.uuid4().hex * 2,
    )
    return receipt_id


async def _insert_batch(
    conn: asyncpg.Connection,
    ids: dict[str, uuid.UUID],
    receipt_id: uuid.UUID,
    *,
    source: str = "generated",
    quantity: int = 2,
    expected_item_count: int = 2,
    code_type: str = "single",
) -> uuid.UUID:
    batch_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO code_batches "
        "(id,tenant_id,product_id,sku_id,production_batch_id,batch_code,quantity,status,code_type,"
        "generation_mode,created_by,contract_version,source,expected_item_count,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,'generating',$8,'item_level',$9,1,$10,$11,now(),now())",
        batch_id,
        ids["tenant"],
        ids["product"],
        ids["sku"],
        ids["production_batch"],
        f"DELIVERY-CB-{batch_id.hex[:8]}",
        quantity,
        code_type,
        ids["account"],
        source,
        expected_item_count,
    )
    await conn.execute(
        "UPDATE code_batch_generation_receipts SET code_batch_id=$1 WHERE id=$2 AND tenant_id=$3",
        batch_id,
        receipt_id,
        ids["tenant"],
    )
    return batch_id


async def _insert_legacy_batch(
    conn: asyncpg.Connection,
    ids: dict[str, uuid.UUID],
    *,
    quantity: int = 2,
) -> uuid.UUID:
    """Execute the pre-contract INSERT shape emitted by the drained app."""

    batch_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO code_batches "
        "(id,tenant_id,product_id,sku_id,production_batch_id,batch_code,quantity,status,code_type,"
        "generation_mode,created_by,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,'pending','single','item_level',$8,now(),now())",
        batch_id,
        ids["tenant"],
        ids["product"],
        ids["sku"],
        ids["production_batch"],
        f"LEGACY-CB-{batch_id.hex[:8]}",
        quantity,
        ids["account"],
    )
    return batch_id


async def _insert_legacy_export(
    conn: asyncpg.Connection,
    ids: dict[str, uuid.UUID],
    batch_id: uuid.UUID,
    export_id: uuid.UUID,
) -> None:
    """Execute the pre-contract export-log INSERT with no manifest columns."""

    await conn.execute(
        "INSERT INTO export_logs "
        "(id,tenant_id,account_id,export_type,resource_id,file_name,row_count,status,created_at,updated_at) "
        "VALUES($1,$2,$3,'code_csv',$4,'legacy-codes.csv',1,'completed',now(),now())",
        export_id,
        ids["tenant"],
        ids["account"],
        batch_id,
    )


async def _insert_items(
    conn: asyncpg.Connection,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    count: int,
) -> None:
    for _ in range(count):
        item_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO code_items "
            "(id,tenant_id,code_batch_id,public_id,status,code_type,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,'created','single',now(),now())",
            item_id,
            tenant_id,
            batch_id,
            f"D{item_id.hex[:16]}",
        )


async def _insert_manifest_and_deliver(
    conn: asyncpg.Connection,
    ids: dict[str, uuid.UUID],
    batch_id: uuid.UUID,
    *,
    row_count: int,
) -> tuple[uuid.UUID, bytes, bytes]:
    manifest_id = uuid.uuid4()
    plaintext = b"public_id,status\nA,created\n"
    ciphertext = b"c" * (len(plaintext) + 16)
    nonce = b"n" * 12
    await conn.execute(
        "INSERT INTO export_logs "
        "(id,tenant_id,account_id,export_type,resource_id,file_name,row_count,status,code_batch_id,"
        "manifest_version,checksum_sha256,artifact_size_bytes,artifact_ciphertext,artifact_nonce,"
        "artifact_scheme,artifact_key_id,created_at,updated_at) "
        "VALUES($1,$2,$3,'code_csv',$4,'codes.csv',$5,'completed',$4,1,$6,$7,$8,$9,"
        "'aes-256-gcm-v1','test-key-1',now(),now())",
        manifest_id,
        ids["tenant"],
        ids["account"],
        batch_id,
        row_count,
        hashlib.sha256(plaintext).hexdigest(),
        len(plaintext),
        ciphertext,
        nonce,
    )
    await conn.execute(
        "UPDATE code_batches SET status='exported',export_manifest_id=$1,exported_at=now() WHERE id=$2",
        manifest_id,
        batch_id,
    )
    await conn.execute("UPDATE code_batches SET status='printing',printing_at=now() WHERE id=$1", batch_id)
    await conn.execute(
        "UPDATE code_batches SET status='delivered',delivered_at=now(),delivery_recipient='printer-a' WHERE id=$1",
        batch_id,
    )
    return manifest_id, ciphertext, nonce


async def _purge_owned_delivery_fixture(conn: asyncpg.Connection, tenant_id: uuid.UUID) -> None:
    """Remove this test's durable delivery rows without weakening production ACL."""

    tables = (
        "code_items",
        "code_batches",
        "code_batch_generation_receipts",
        "export_logs",
    )
    async with conn.transaction():
        try:
            for table in tables:
                await conn.execute(f"ALTER TABLE public.{table} DISABLE TRIGGER USER")
            await conn.execute("DELETE FROM code_allocations WHERE tenant_id=$1", tenant_id)
            await conn.execute("DELETE FROM code_items WHERE tenant_id=$1", tenant_id)
            await conn.execute("UPDATE code_batches SET export_manifest_id=NULL WHERE tenant_id=$1", tenant_id)
            await conn.execute("DELETE FROM code_batch_generation_receipts WHERE tenant_id=$1", tenant_id)
            await conn.execute("DELETE FROM export_logs WHERE tenant_id=$1", tenant_id)
            await conn.execute("DELETE FROM code_batches WHERE tenant_id=$1", tenant_id)
        finally:
            for table in reversed(tables):
                await conn.execute(f"ALTER TABLE public.{table} ENABLE TRIGGER USER")


async def test_empty_head_roundtrip_and_metadata_match(migrated_pg_url: str) -> None:
    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        assert not await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='code_batches' AND column_name='contract_version')"
        )
        assert not await conn.fetchval("SELECT to_regclass('public.code_batch_generation_receipts') IS NOT NULL")
    finally:
        await conn.close()

    _alembic(migrated_pg_url, "upgrade", "head")
    _alembic(migrated_pg_url, "check")


async def test_expand_drain_finalize_runtime_compatibility(migrated_pg_url: str) -> None:
    _alembic(migrated_pg_url, "downgrade", EXPAND_REVISION)
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == EXPAND_REVISION
        assert not await owner.fetchval("SELECT EXISTS (SELECT 1 FROM code_delivery_contract_rollout_state)")
        ids = await _seed_catalog(owner, "expand-finalize")
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(ids["tenant"]))

        # Exact pre-contract flow: pending INSERT with no new columns, created
        # items, then direct pending -> completed and completed -> activated.
        legacy_batch = await _insert_legacy_batch(runtime, ids)
        assert await owner.fetchval("SELECT contract_version FROM code_batches WHERE id=$1", legacy_batch) == 0
        await _insert_items(runtime, ids["tenant"], legacy_batch, 2)
        await runtime.execute("UPDATE code_batches SET status='completed' WHERE id=$1", legacy_batch)
        async with runtime.transaction():
            await runtime.execute(
                "UPDATE code_items SET status='activated',activated_at=now() WHERE code_batch_id=$1",
                legacy_batch,
            )
            await runtime.execute("UPDATE code_batches SET status='activated' WHERE id=$1", legacy_batch)
            await runtime.execute("SET CONSTRAINTS trg_enforce_code_item_parent_final_state IMMEDIATE")

        legacy_items = await owner.fetch("SELECT id FROM code_items WHERE code_batch_id=$1 ORDER BY id", legacy_batch)
        await runtime.execute(
            "UPDATE code_items SET status='bound',bound_at=now() WHERE id=$1",
            legacy_items[0]["id"],
        )
        await runtime.execute(
            "UPDATE code_items SET status='revoked',revoked_at=now() WHERE id=$1",
            legacy_items[1]["id"],
        )

        # Keep one completed legacy batch for the post-finalize controlled
        # export upgrade, while also proving v1 works during expand.
        export_legacy_batch = await _insert_legacy_batch(runtime, ids, quantity=1)
        await _insert_items(runtime, ids["tenant"], export_legacy_batch, 1)
        await runtime.execute("UPDATE code_batches SET status='completed' WHERE id=$1", export_legacy_batch)

        expand_receipt = await _insert_receipt(runtime, ids)
        expand_v1_batch = await _insert_batch(runtime, ids, expand_receipt, quantity=1, expected_item_count=1)
        await _insert_items(runtime, ids["tenant"], expand_v1_batch, 1)
        await runtime.execute("UPDATE code_batches SET status='completed' WHERE id=$1", expand_v1_batch)

        # A writer that has not drained makes finalize time out and atomically
        # leaves both the Alembic revision and marker in expand state.
        writer = runtime.transaction()
        await writer.start()
        await runtime.execute("UPDATE code_batches SET batch_code=batch_code WHERE id=$1", legacy_batch)
        blocked = await asyncio.to_thread(
            _alembic,
            migrated_pg_url,
            "upgrade",
            "head",
            succeeds=False,
        )
        assert "lock timeout" in f"{blocked.stdout}\n{blocked.stderr}".lower()
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == EXPAND_REVISION
        assert not await owner.fetchval("SELECT EXISTS (SELECT 1 FROM code_delivery_contract_rollout_state)")
        await writer.rollback()

        # Legacy-export-first order: an exact old export INSERT owns a
        # RowExclusiveLock on export_logs.  Finalize must wait at the combined
        # fence, then linearize after the legacy commit and publish the marker.
        legacy_export_id = uuid.uuid4()
        export_writer = runtime.transaction()
        await export_writer.start()
        await _insert_legacy_export(runtime, ids, export_legacy_batch, legacy_export_id)
        started_at = asyncio.get_running_loop().time()
        finalize_task = asyncio.create_task(asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head"))
        finalize_waiting = False
        marker_absent_while_waiting = False
        wait_deadline = started_at + 2.0
        try:
            while asyncio.get_running_loop().time() < wait_deadline:
                if finalize_task.done():
                    break
                finalize_waiting = bool(
                    await owner.fetchval(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname=current_database() AND wait_event_type='Lock' "
                        "AND query LIKE 'LOCK TABLE public.code_batches, public.code_items, public.export_logs%')"
                    )
                )
                if finalize_waiting:
                    marker_absent_while_waiting = not await owner.fetchval(
                        "SELECT EXISTS (SELECT 1 FROM code_delivery_contract_rollout_state)"
                    )
                    break
                await asyncio.sleep(0.05)
        finally:
            await export_writer.commit()
        assert finalize_waiting
        assert marker_absent_while_waiting
        assert asyncio.get_running_loop().time() - started_at < 5.0
        finalized = await asyncio.wait_for(finalize_task, timeout=10)
        assert "40P01" not in f"{finalized.stdout}\n{finalized.stderr}"
        legacy_export_created_at = await owner.fetchval(
            "SELECT created_at FROM export_logs WHERE id=$1 AND manifest_version IS NULL",
            legacy_export_id,
        )
        assert legacy_export_created_at is not None
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == HEAD_REVISION
        marker = await owner.fetchrow(
            "SELECT phase,finalized_at,finalized_by FROM code_delivery_contract_rollout_state WHERE id=1"
        )
        assert marker is not None and marker["phase"] == "finalized"
        assert marker["finalized_at"] is not None
        assert legacy_export_created_at <= marker["finalized_at"]
        assert marker["finalized_by"] == await owner.fetchval("SELECT current_user")
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert not await owner.fetchval(
                "SELECT has_table_privilege('yimatong_app','public.code_delivery_contract_rollout_state',$1)",
                privilege,
            )

        # Finalize-first order: the same old export INSERT is rejected with
        # stable 42501 and leaves no row after the marker commits.
        rejected_export_id = uuid.uuid4()
        legacy_export_count = await owner.fetchval(
            "SELECT count(*) FROM export_logs WHERE tenant_id=$1 AND manifest_version IS NULL",
            ids["tenant"],
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with runtime.transaction():
                await _insert_legacy_export(runtime, ids, export_legacy_batch, rejected_export_id)
        assert not await owner.fetchval("SELECT EXISTS (SELECT 1 FROM export_logs WHERE id=$1)", rejected_export_id)
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM export_logs WHERE tenant_id=$1 AND manifest_version IS NULL",
                ids["tenant"],
            )
            == legacy_export_count
        )

        # The drained old binary gets a stable 42501 before any row is written.
        before_count = await owner.fetchval("SELECT count(*) FROM code_batches WHERE tenant_id=$1", ids["tenant"])
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with runtime.transaction():
                await _insert_legacy_batch(runtime, ids)
        assert (
            await owner.fetchval("SELECT count(*) FROM code_batches WHERE tenant_id=$1", ids["tenant"]) == before_count
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with runtime.transaction():
                await runtime.execute("UPDATE code_items SET status='expired' WHERE id=$1", legacy_items[0]["id"])
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with runtime.transaction():
                await runtime.execute("UPDATE code_batches SET batch_code=batch_code WHERE id=$1", legacy_batch)

        # New runtime writes remain available after finalize.
        final_receipt = await _insert_receipt(runtime, ids)
        final_v1_batch = await _insert_batch(runtime, ids, final_receipt, quantity=1, expected_item_count=1)
        await _insert_items(runtime, ids["tenant"], final_v1_batch, 1)
        await runtime.execute("UPDATE code_batches SET status='completed' WHERE id=$1", final_v1_batch)

        # Existing completed v0 stock has exactly one supported mutation: the
        # new exporter atomically promotes it to v1 while attaching a manifest.
        manifest_id = uuid.uuid4()
        plaintext = b"public_id,status\nA,created\n"
        await runtime.execute(
            "INSERT INTO export_logs "
            "(id,tenant_id,account_id,export_type,resource_id,file_name,row_count,status,code_batch_id,"
            "manifest_version,checksum_sha256,artifact_size_bytes,artifact_ciphertext,artifact_nonce,"
            "artifact_scheme,artifact_key_id,created_at,updated_at) "
            "VALUES($1,$2,$3,'code_csv',$4,'legacy.csv',1,'completed',$4,1,$5,$6,$7,$8,"
            "'aes-256-gcm-v1','test-key-1',now(),now())",
            manifest_id,
            ids["tenant"],
            ids["account"],
            export_legacy_batch,
            hashlib.sha256(plaintext).hexdigest(),
            len(plaintext),
            b"c" * (len(plaintext) + 16),
            b"n" * 12,
        )
        await runtime.execute(
            "UPDATE code_batches SET contract_version=1,status='exported',export_manifest_id=$1,exported_at=now() "
            "WHERE id=$2",
            manifest_id,
            export_legacy_batch,
        )
        assert await owner.fetchval("SELECT contract_version FROM code_batches WHERE id=$1", export_legacy_batch) == 1

        protected_counts = tuple(
            await owner.fetchrow(
                "SELECT "
                "(SELECT count(*) FROM code_batches WHERE tenant_id=$1 AND contract_version=1) batches,"
                "(SELECT count(*) FROM code_batch_generation_receipts WHERE tenant_id=$1) receipts,"
                "(SELECT count(*) FROM export_logs WHERE tenant_id=$1 AND manifest_version IS NOT NULL) manifests",
                ids["tenant"],
            )
        )
        assert all(count > 0 for count in protected_counts)

        # U03B staged downgrades do not cross the U03A contract owner and must
        # preserve its durable rows at both the finalize and expand revisions.
        _alembic(migrated_pg_url, "downgrade", FINALIZE_REVISION)
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == FINALIZE_REVISION
        assert (
            tuple(
                await owner.fetchrow(
                    "SELECT "
                    "(SELECT count(*) FROM code_batches WHERE tenant_id=$1 AND contract_version=1) batches,"
                    "(SELECT count(*) FROM code_batch_generation_receipts WHERE tenant_id=$1) receipts,"
                    "(SELECT count(*) FROM export_logs WHERE tenant_id=$1 AND manifest_version IS NOT NULL) manifests",
                    ids["tenant"],
                )
            )
            == protected_counts
        )
        _alembic(migrated_pg_url, "upgrade", "head")

        # Finalize downgrade restores expand behavior without discarding rows.
        _alembic(migrated_pg_url, "downgrade", EXPAND_REVISION)
        assert (
            tuple(
                await owner.fetchrow(
                    "SELECT "
                    "(SELECT count(*) FROM code_batches WHERE tenant_id=$1 AND contract_version=1) batches,"
                    "(SELECT count(*) FROM code_batch_generation_receipts WHERE tenant_id=$1) receipts,"
                    "(SELECT count(*) FROM export_logs WHERE tenant_id=$1 AND manifest_version IS NOT NULL) manifests",
                    ids["tenant"],
                )
            )
            == protected_counts
        )
        restored_legacy = await _insert_legacy_batch(runtime, ids, quantity=1)
        await _insert_items(runtime, ids["tenant"], restored_legacy, 1)
        await runtime.execute("UPDATE code_batches SET status='completed' WHERE id=$1", restored_legacy)
        _alembic(migrated_pg_url, "upgrade", "head")
        _alembic(migrated_pg_url, "check")
        await _purge_owned_delivery_fixture(owner, ids["tenant"])
        assert not await owner.fetchval("SELECT EXISTS (SELECT 1 FROM code_batches WHERE contract_version=1)")
        assert not await owner.fetchval("SELECT EXISTS (SELECT 1 FROM code_batch_generation_receipts)")
        assert not await owner.fetchval("SELECT EXISTS (SELECT 1 FROM export_logs WHERE manifest_version IS NOT NULL)")
    finally:
        await runtime.close()
        await owner.close()


async def test_c7_downgrade_blocks_delivery_rows_before_enum_ddl(migrated_pg_url: str) -> None:
    clean = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        assert not await clean.fetchval("SELECT EXISTS (SELECT 1 FROM code_batches WHERE contract_version=1)")
        assert not await clean.fetchval("SELECT EXISTS (SELECT 1 FROM code_batch_generation_receipts)")
        assert not await clean.fetchval("SELECT EXISTS (SELECT 1 FROM export_logs WHERE manifest_version IS NOT NULL)")
    finally:
        await clean.close()
    _alembic(migrated_pg_url, "downgrade", C7_REVISION)
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    ids = {key: uuid.uuid4() for key in ("tenant", "brand", "product", "sku", "production_batch", "batch")}
    try:
        await conn.execute(
            "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
            "VALUES($1,'c7 delivery','c7-delivery','active','free','brand',now(),now())",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO brands(id,tenant_id,name,status,created_at,updated_at) "
            "VALUES($1,$2,'c7 brand','active',now(),now())",
            ids["brand"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO products(id,tenant_id,brand_id,name,status,created_at,updated_at) "
            "VALUES($1,$2,$3,'c7 product','active',now(),now())",
            ids["product"],
            ids["tenant"],
            ids["brand"],
        )
        await conn.execute(
            "INSERT INTO skus(id,tenant_id,product_id,code,name,status,created_at,updated_at) "
            "VALUES($1,$2,$3,'C7-SKU','c7 sku','active',now(),now())",
            ids["sku"],
            ids["tenant"],
            ids["product"],
        )
        await conn.execute(
            "INSERT INTO production_batches "
            "(id,tenant_id,product_id,sku_id,batch_code,production_date,expiry_date,status,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,'C7-PB',$5,$6,'active',now(),now())",
            ids["production_batch"],
            ids["tenant"],
            ids["product"],
            ids["sku"],
            date.today(),
            date.today() + timedelta(days=365),
        )
        await conn.execute(
            "INSERT INTO code_batches "
            "(id,tenant_id,product_id,sku_id,production_batch_id,batch_code,quantity,status,code_type,"
            "generation_mode,created_by,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,'C7-CB',1,'delivered','single','item_level',$6,now(),now())",
            ids["batch"],
            ids["tenant"],
            ids["product"],
            ids["sku"],
            ids["production_batch"],
            uuid.uuid4(),
        )
    finally:
        await conn.close()

    _alembic(migrated_pg_url, "upgrade", "head")
    # Delivery enum values are still owned at 2ed1, so a shallower downgrade
    # must retain the legacy delivered row instead of applying the c7 blocker.
    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == PARENT_REVISION
        assert await conn.fetchval("SELECT status::text FROM code_batches WHERE id=$1", ids["batch"]) == "delivered"
    finally:
        await conn.close()
    _alembic(migrated_pg_url, "upgrade", "head")

    blocked = _alembic(migrated_pg_url, "downgrade", C7_PARENT_REVISION, succeeds=False)
    assert "Cannot discard code batch delivery state" in f"{blocked.stdout}\n{blocked.stderr}"

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == HEAD_REVISION
        assert await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid "
            "WHERE t.typname='codebatchstatus' AND e.enumlabel='delivered')"
        )
        assert not await conn.fetchval("SELECT has_table_privilege('yimatong_app','code_items','UPDATE')")
        assert await conn.fetchval(
            "SELECT to_regprocedure('public.transition_code_item_lifecycle(uuid,uuid,uuid,uuid,text,text)') IS NOT NULL"
        )
        assert await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='interception_records' AND column_name='code_item_id')"
        )
        await conn.execute("DELETE FROM code_batches WHERE id=$1", ids["batch"])
    finally:
        await conn.close()

    _alembic(migrated_pg_url, "downgrade", C7_PARENT_REVISION)
    conn = await asyncpg.connect(dsn)
    try:
        assert not await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid "
            "WHERE t.typname='codebatchstatus' AND e.enumlabel='delivered')"
        )
    finally:
        await conn.close()
    _alembic(migrated_pg_url, "upgrade", "head")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute((BACKEND_DIR / "scripts" / "init_runtime_role.sql").read_text())
    finally:
        await conn.close()


async def test_schema_and_hard_cap_contract(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        columns = {
            row["column_name"]
            for row in await owner.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='code_batches'"
            )
        }
        assert {
            "contract_version",
            "source",
            "expected_item_count",
            "export_manifest_id",
            "exported_at",
            "printing_at",
            "delivered_at",
            "delivery_recipient",
        } <= columns

        ids = await _seed_catalog(owner, "hard-cap")
        receipt_id = await _insert_receipt(owner, ids)
        with pytest.raises(asyncpg.CheckViolationError):
            async with owner.transaction():
                await _insert_batch(
                    owner,
                    ids,
                    receipt_id,
                    quantity=5_001,
                    expected_item_count=10_002,
                    code_type="paired",
                )
    finally:
        await owner.close()


async def test_count_lifecycle_manifest_and_immutability(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        ids = await _seed_catalog(owner, "lifecycle")
        receipt_id = await _insert_receipt(owner, ids)
        batch_id = await _insert_batch(owner, ids, receipt_id)
        await _insert_items(owner, ids["tenant"], batch_id, 1)
        with pytest.raises(asyncpg.CheckViolationError):
            async with owner.transaction():
                await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", batch_id)
        await _insert_items(owner, ids["tenant"], batch_id, 1)
        await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", batch_id)

        with pytest.raises(asyncpg.CheckViolationError):
            async with owner.transaction():
                await owner.execute("UPDATE code_batches SET status='printing' WHERE id=$1", batch_id)
        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
            async with owner.transaction():
                await owner.execute("DELETE FROM code_items WHERE code_batch_id=$1", batch_id)

        await _insert_manifest_and_deliver(owner, ids, batch_id, row_count=2)
        async with owner.transaction():
            await owner.execute(
                "UPDATE code_items SET status='activated',activated_at=now() WHERE code_batch_id=$1",
                batch_id,
            )
            await owner.execute("UPDATE code_batches SET status='activated' WHERE id=$1", batch_id)
        assert await owner.fetchval("SELECT status::text FROM code_batches WHERE id=$1", batch_id) == "activated"

        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
            async with owner.transaction():
                await owner.execute("UPDATE code_batches SET batch_code='changed' WHERE id=$1", batch_id)
    finally:
        await owner.close()


async def test_runtime_activation_requires_final_batch_state_and_artifact_getter(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        ids = await _seed_catalog(owner, "activation-boundary")
        receipt_id = await _insert_receipt(owner, ids)
        batch_id = await _insert_batch(owner, ids, receipt_id, quantity=1, expected_item_count=1)
        await _insert_items(owner, ids["tenant"], batch_id, 1)
        await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", batch_id)
        await runtime_pg_conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with runtime_pg_conn.transaction():
                await runtime_pg_conn.execute(
                    "UPDATE code_items SET status='activated',activated_at=now() "
                    "WHERE tenant_id=$1 AND code_batch_id=$2",
                    ids["tenant"],
                    batch_id,
                )
        manifest_id, ciphertext, nonce = await _insert_manifest_and_deliver(owner, ids, batch_id, row_count=1)

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with runtime_pg_conn.transaction():
                await runtime_pg_conn.execute(
                    "UPDATE code_items SET status='activated',activated_at=now() "
                    "WHERE tenant_id=$1 AND code_batch_id=$2",
                    ids["tenant"],
                    batch_id,
                )
        assert (
            await owner.fetchval(
                "SELECT status::text FROM code_items WHERE tenant_id=$1 AND code_batch_id=$2",
                ids["tenant"],
                batch_id,
            )
            == "created"
        )

        auth_session_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO auth_sessions "
            "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
            auth_session_id,
            ids["account"],
            ids["tenant"],
            uuid.uuid4().hex,
        )
        async with runtime_pg_conn.transaction():
            await runtime_pg_conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
            activated = await runtime_pg_conn.fetchrow(
                "SELECT * FROM transition_code_batch_lifecycle($1,$2,$3,$4,'activate',NULL)",
                ids["tenant"],
                auth_session_id,
                uuid.uuid4(),
                batch_id,
            )
        assert activated["current_status"] == "activated"
        assert activated["affected_item_count"] == 1

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with runtime_pg_conn.transaction():
                await runtime_pg_conn.fetchval(
                    "SELECT artifact_ciphertext FROM export_logs WHERE tenant_id=$1 AND id=$2",
                    ids["tenant"],
                    manifest_id,
                )
        envelope = await runtime_pg_conn.fetchrow(
            "SELECT * FROM get_code_export_artifact($1,$2,$3)",
            ids["tenant"],
            batch_id,
            manifest_id,
        )
        assert bytes(envelope["artifact_ciphertext"]) == ciphertext
        assert bytes(envelope["artifact_nonce"]) == nonce
        assert envelope["artifact_scheme"] == "aes-256-gcm-v1"
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime_pg_conn.fetchrow(
                "SELECT * FROM get_code_export_artifact($1,$2,$3)",
                uuid.uuid4(),
                batch_id,
                manifest_id,
            )
    finally:
        await owner.close()


async def test_direct_item_update_fails_fast_when_batch_is_locked(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        ids = await _seed_catalog(owner, "activation-lock")
        receipt_id = await _insert_receipt(owner, ids)
        batch_id = await _insert_batch(owner, ids, receipt_id, quantity=1, expected_item_count=1)
        await _insert_items(owner, ids["tenant"], batch_id, 1)
        await runtime_pg_conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
        transaction = owner.transaction()
        await transaction.start()
        try:
            await owner.execute("SELECT id FROM code_batches WHERE id=$1 FOR UPDATE", batch_id)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime_pg_conn.execute(
                    "UPDATE code_items SET status='revoked',revoked_at=now() WHERE tenant_id=$1 AND code_batch_id=$2",
                    ids["tenant"],
                    batch_id,
                )
        finally:
            await transaction.rollback()
    finally:
        await owner.close()


async def test_receipt_rls_uniqueness_and_immutability(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        first = await _seed_catalog(owner, "receipt-first")
        second = await _seed_catalog(owner, "receipt-second")
        first_receipt = await _insert_receipt(owner, first)
        await _insert_receipt(owner, second)

        with pytest.raises(asyncpg.UniqueViolationError):
            async with owner.transaction():
                await owner.execute(
                    "INSERT INTO code_batch_generation_receipts "
                    "(id,tenant_id,created_by,idempotency_digest,request_fingerprint,created_at) "
                    "SELECT $1,tenant_id,created_by,idempotency_digest,$2,now() "
                    "FROM code_batch_generation_receipts WHERE id=$3",
                    uuid.uuid4(),
                    uuid.uuid4().hex * 2,
                    first_receipt,
                )

        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
            async with owner.transaction():
                await owner.execute(
                    "UPDATE code_batch_generation_receipts SET request_fingerprint=$1 WHERE id=$2",
                    uuid.uuid4().hex * 2,
                    first_receipt,
                )

        async with runtime_pg_conn.transaction():
            await runtime_pg_conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(first["tenant"]))
            visible = await runtime_pg_conn.fetch(
                "SELECT tenant_id FROM code_batch_generation_receipts ORDER BY tenant_id"
            )
            assert [row["tenant_id"] for row in visible] == [first["tenant"]]
    finally:
        await owner.close()


async def test_paired_batches_require_exact_outer_inner_pairs(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        ids = await _seed_catalog(owner, "paired")

        invalid_receipt = await _insert_receipt(owner, ids)
        invalid_batch = await _insert_batch(
            owner,
            ids,
            invalid_receipt,
            quantity=2,
            expected_item_count=4,
            code_type="paired",
        )
        invalid_pairs = (
            (uuid.uuid4(), "outer"),
            (uuid.uuid4(), "inner"),
            (uuid.uuid4(), "outer"),
            (uuid.uuid4(), "inner"),
        )
        for pair_id, item_type in invalid_pairs:
            item_id = uuid.uuid4()
            await owner.execute(
                "INSERT INTO code_items "
                "(id,tenant_id,code_batch_id,public_id,status,code_type,pair_id,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,'created',$5,$6,now(),now())",
                item_id,
                ids["tenant"],
                invalid_batch,
                f"P{item_id.hex[:16]}",
                item_type,
                pair_id,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            async with owner.transaction():
                await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", invalid_batch)

        valid_receipt = await _insert_receipt(owner, ids)
        valid_batch = await _insert_batch(
            owner,
            ids,
            valid_receipt,
            quantity=2,
            expected_item_count=4,
            code_type="paired",
        )
        for _ in range(2):
            pair_id = uuid.uuid4()
            for item_type in ("outer", "inner"):
                item_id = uuid.uuid4()
                await owner.execute(
                    "INSERT INTO code_items "
                    "(id,tenant_id,code_batch_id,public_id,status,code_type,pair_id,created_at,updated_at) "
                    "VALUES($1,$2,$3,$4,'created',$5,$6,now(),now())",
                    item_id,
                    ids["tenant"],
                    valid_batch,
                    f"P{item_id.hex[:16]}",
                    item_type,
                    pair_id,
                )
        await owner.execute("UPDATE code_batches SET status='completed' WHERE id=$1", valid_batch)
        assert await owner.fetchval("SELECT status::text FROM code_batches WHERE id=$1", valid_batch) == "completed"
    finally:
        await owner.close()


async def test_downgrade_fails_before_discarding_v1_contract(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        ids = await _seed_catalog(owner, "downgrade-block")
        receipt_id = await _insert_receipt(owner, ids)
        await _insert_batch(owner, ids, receipt_id)
    finally:
        await owner.close()

    blocked = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
    output = f"{blocked.stdout}\n{blocked.stderr}"
    assert "Cannot discard atomic code delivery contract" in output

    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == HEAD_REVISION
        assert await owner.fetchval("SELECT to_regclass('public.code_batch_generation_receipts') IS NOT NULL")
        assert not await owner.fetchval("SELECT has_table_privilege('yimatong_app','code_items','UPDATE')")
        assert await owner.fetchval(
            "SELECT to_regprocedure('public.transition_code_item_lifecycle(uuid,uuid,uuid,uuid,text,text)') IS NOT NULL"
        )
        assert await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='interception_records' AND column_name='code_item_id')"
        )
        assert await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='code_items' AND column_name='frozen_from_status')"
        )
    finally:
        await owner.close()


async def test_runtime_cannot_delete_code_or_manifest_relations(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        ids = await _seed_catalog(owner, "runtime-acl")
        await runtime_pg_conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(ids["tenant"]))
        receipt_id = await _insert_receipt(runtime_pg_conn, ids)
        batch_id = await _insert_batch(runtime_pg_conn, ids, receipt_id)
        await _insert_items(runtime_pg_conn, ids["tenant"], batch_id, 1)
        for table in ("code_batches", "code_items", "export_logs", "code_batch_generation_receipts"):
            assert not await owner.fetchval(
                "SELECT has_table_privilege('yimatong_app',$1,'DELETE')",
                f"public.{table}",
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime_pg_conn.execute("DELETE FROM code_batches WHERE id=$1", batch_id)
    finally:
        await owner.close()


async def test_official_cli_seeds_complete_encrypted_delivery_chains(migrated_pg_url: str) -> None:
    _app_cli(migrated_pg_url, "all")
    _app_cli(migrated_pg_url, "baseline", "build", "--target", "baseline-base")

    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    owned_tenant_ids: list[uuid.UUID] = []
    try:
        for tenant_slug in ("demo", "baseline-base"):
            tenant_id = await owner.fetchval("SELECT id FROM tenants WHERE slug=$1", tenant_slug)
            assert tenant_id is not None
            owned_tenant_ids.append(tenant_id)
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM auth_sessions WHERE tenant_id=$1 AND current_refresh_jti LIKE 'cli-%'",
                    tenant_id,
                )
                == 0
            )
            lifecycle_operators = {
                row["operator_id"]
                for row in await owner.fetch(
                    "SELECT DISTINCT operator_id FROM platform_audit_log "
                    "WHERE target_tenant_id=$1 AND action='code_activate'",
                    str(tenant_id),
                )
            }
            admin_operators = {
                str(row["account_id"])
                for row in await owner.fetch(
                    "SELECT DISTINCT ar.account_id FROM account_roles ar "
                    "JOIN roles r ON r.tenant_id=ar.tenant_id AND r.id=ar.role_id "
                    "WHERE ar.tenant_id=$1 AND r.name='admin'",
                    tenant_id,
                )
            }
            assert lifecycle_operators
            assert lifecycle_operators <= admin_operators
            batches = await owner.fetch(
                "SELECT id,product_id,sku_id,production_batch_id,expected_item_count,export_manifest_id "
                "FROM code_batches "
                "WHERE tenant_id=$1 AND contract_version=1 AND status='activated'",
                tenant_id,
            )
            assert batches
            for batch in batches:
                assert (
                    await owner.fetchval(
                        "SELECT count(*) FROM code_items WHERE tenant_id=$1 AND code_batch_id=$2",
                        tenant_id,
                        batch["id"],
                    )
                    == batch["expected_item_count"]
                )
                assert (
                    await owner.fetchval(
                        "SELECT count(*) FROM code_batch_generation_receipts WHERE tenant_id=$1 AND code_batch_id=$2",
                        tenant_id,
                        batch["id"],
                    )
                    == 1
                )
                manifest = await owner.fetchrow(
                    "SELECT row_count,artifact_size_bytes,octet_length(artifact_ciphertext) AS ciphertext_size,"
                    "octet_length(artifact_nonce) AS nonce_size,artifact_scheme,artifact_key_id "
                    "FROM export_logs WHERE tenant_id=$1 AND id=$2 AND code_batch_id=$3",
                    tenant_id,
                    batch["export_manifest_id"],
                    batch["id"],
                )
                assert manifest is not None
                assert manifest["row_count"] == batch["expected_item_count"]
                assert manifest["ciphertext_size"] == manifest["artifact_size_bytes"] + 16
                assert manifest["nonce_size"] == 12
                assert manifest["artifact_scheme"] == "aes-256-gcm-v1"
                assert manifest["artifact_key_id"].startswith("aes-master-v")

            replay_batch = batches[0]
            runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
            runtime_engine = create_async_engine(runtime_url)
            runtime_factory = async_sessionmaker(runtime_engine, expire_on_commit=False)
            try:
                async with runtime_factory() as db, db.begin():
                    await db.execute(
                        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                        {"tenant_id": str(tenant_id)},
                    )
                    original = await generate_code_csv(db, tenant_id, replay_batch["id"], uuid.uuid4())

                await owner.execute(
                    "UPDATE products SET name=name || ' replay-change' WHERE tenant_id=$1 AND id=$2",
                    tenant_id,
                    replay_batch["product_id"],
                )
                await owner.execute(
                    "UPDATE skus SET name=name || ' replay-change' WHERE tenant_id=$1 AND product_id=$2 AND id=$3",
                    tenant_id,
                    replay_batch["product_id"],
                    replay_batch["sku_id"],
                )
                await owner.execute(
                    "UPDATE production_batches SET origin=origin || ' replay-change' "
                    "WHERE tenant_id=$1 AND product_id=$2 AND sku_id=$3 AND id=$4",
                    tenant_id,
                    replay_batch["product_id"],
                    replay_batch["sku_id"],
                    replay_batch["production_batch_id"],
                )

                async with runtime_factory() as db, db.begin():
                    await db.execute(
                        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                        {"tenant_id": str(tenant_id)},
                    )
                    replay = await generate_code_csv(db, tenant_id, replay_batch["id"], uuid.uuid4())
                assert replay == original
            finally:
                await runtime_engine.dispose()
    finally:
        for tenant_id in owned_tenant_ids:
            await _purge_owned_delivery_fixture(owner, tenant_id)
        await owner.close()
