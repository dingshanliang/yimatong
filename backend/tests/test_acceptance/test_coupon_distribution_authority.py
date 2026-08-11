"""PostgreSQL acceptance for tenant-bound coupon distribution authority."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from tests.test_acceptance.test_code_batch_delivery_contract import _seed_catalog
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic, _assert_sqlstate

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

_PARENT = "u6b3f4a5b6c7"


async def test_coupon_rollout_pauses_keep_pool_derived_runtime_isolation(migrated_pg_url: str) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", _PARENT)
    owner = await asyncpg.connect(dsn)
    own = await _seed_catalog(owner, "coupon-pause-own")
    other = await _seed_catalog(owner, "coupon-pause-other")
    own_pool, other_pool = uuid.uuid4(), uuid.uuid4()
    own_code, other_code = uuid.uuid4(), uuid.uuid4()
    for pool_id, ids, label in ((own_pool, own, "own"), (other_pool, other, "other")):
        await owner.execute(
            "INSERT INTO coupon_pools(id,tenant_id,name,total_codes,remaining,created_at,updated_at) "
            "VALUES($1,$2,$3,1,1,now(),now())",
            pool_id,
            ids["tenant"],
            label,
        )
    for code_id, pool_id, code in (
        (own_code, own_pool, "OWN-LEGACY"),
        (other_code, other_pool, "OTHER-LEGACY"),
    ):
        await owner.execute(
            "INSERT INTO coupon_codes(id,pool_id,code,distributed,created_at,updated_at) "
            "VALUES($1,$2,$3,false,now(),now())",
            code_id,
            pool_id,
            code,
        )
    try:
        for revision in ("u6c0d1e2f3a4", "u6c1e2f3a4b5", "u6c2f3a4b5c6"):
            await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", revision)
            runtime = await asyncpg.connect(dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
            inserted = uuid.uuid4()
            try:
                await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(own["tenant"]))
                assert await runtime.fetchval(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='coupon_codes'::regclass"
                )
                policy = await runtime.fetchval(
                    "SELECT pg_get_expr(polqual,polrelid) FROM pg_policy "
                    "WHERE polrelid='coupon_codes'::regclass AND polname='tenant_isolation'"
                )
                assert "coupon_pools" in policy and "owner.id = coupon_codes.pool_id" in policy
                assert await runtime.fetchval("SELECT count(*) FROM coupon_codes WHERE id=$1", own_code) == 1
                assert await runtime.fetchval("SELECT count(*) FROM coupon_codes WHERE id=$1", other_code) == 0
                await runtime.execute(
                    "INSERT INTO coupon_codes(id,pool_id,code,distributed,created_at,updated_at) "
                    "VALUES($1,$2,$3,false,now(),now())",
                    inserted,
                    own_pool,
                    f"PAUSE-{revision}-{inserted}",
                )
                assert (
                    await runtime.execute(
                        "UPDATE coupon_codes SET consumer_id='legacy',distributed=true WHERE id=$1", inserted
                    )
                    == "UPDATE 1"
                )
                assert (
                    await runtime.execute("UPDATE coupon_codes SET consumer_id='cross' WHERE id=$1", other_code)
                    == "UPDATE 0"
                )
                assert await runtime.execute("DELETE FROM coupon_codes WHERE id=$1", other_code) == "DELETE 0"
                savepoint = runtime.transaction()
                await savepoint.start()
                with pytest.raises(asyncpg.InsufficientPrivilegeError, match="row-level security"):
                    await runtime.execute(
                        "INSERT INTO coupon_codes(id,pool_id,code,distributed,created_at,updated_at) "
                        "VALUES($1,$2,$3,false,now(),now())",
                        uuid.uuid4(),
                        other_pool,
                        f"CROSS-{revision}-{uuid.uuid4()}",
                    )
                await savepoint.rollback()
                assert await runtime.execute("DELETE FROM coupon_codes WHERE id=$1", inserted) == "DELETE 1"
            finally:
                await runtime.close()
    finally:
        await owner.close()
    await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")


async def test_coupon_tenant_backfill_and_consumer_fk_fail_closed_retry(migrated_pg_url: str) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", _PARENT)
    owner = await asyncpg.connect(dsn)
    ids = await _seed_catalog(owner, "coupon-rollout")
    pool_id = uuid.uuid4()
    code_id = uuid.uuid4()
    orphan_id = uuid.uuid4()
    orphan_tenant = uuid.uuid4()
    try:
        await owner.execute(
            "INSERT INTO coupon_pools(id,tenant_id,name,total_codes,remaining,created_at,updated_at) "
            "VALUES($1,$2,'legacy',1,1,now(),now())",
            pool_id,
            ids["tenant"],
        )
        await owner.execute(
            "INSERT INTO coupon_codes(id,pool_id,code,distributed,created_at,updated_at) "
            "VALUES($1,$2,$3,false,now(),now())",
            code_id,
            pool_id,
            f"LEGACY-{code_id}",
        )
        await owner.execute(
            "INSERT INTO consumer_profiles(id,tenant_id,member_level,total_points,created_at,updated_at) "
            "VALUES($1,$2,'normal',0,now(),now())",
            orphan_id,
            orphan_tenant,
        )
    finally:
        await owner.close()
    failed = await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head", succeeds=False)
    assert failed.returncode != 0
    assert "consumer_profiles contains orphan tenant ids" in failed.stderr
    owner = await asyncpg.connect(dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == _PARENT
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='coupon_codes' "
            "AND column_name='tenant_id')"
        )
        await owner.execute("DELETE FROM consumer_profiles WHERE id=$1", orphan_id)
    finally:
        await owner.close()
    await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")
    owner = await asyncpg.connect(dsn)
    try:
        assert await owner.fetchval("SELECT tenant_id=$2 FROM coupon_codes WHERE id=$1", code_id, ids["tenant"])
        for constraint in (
            "fk_consumer_profiles_tenant",
            "fk_coupon_pools_tenant",
            "fk_coupon_codes_tenant_pool",
            "fk_coupon_codes_tenant_claim",
        ):
            assert await owner.fetchval("SELECT convalidated FROM pg_constraint WHERE conname=$1", constraint)
    finally:
        await owner.close()


async def test_coupon_unique_index_failure_leaves_exact_retryable_shell(migrated_pg_url: str) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", _PARENT)
    owner = await asyncpg.connect(dsn)
    ids = await _seed_catalog(owner, "coupon-index-retry")
    pool_id = uuid.uuid4()
    duplicate_ids = [uuid.uuid4(), uuid.uuid4()]
    try:
        await owner.execute(
            "INSERT INTO coupon_pools(id,tenant_id,name,total_codes,remaining,created_at,updated_at) "
            "VALUES($1,$2,'duplicates',2,2,now(),now())",
            pool_id,
            ids["tenant"],
        )
        for code_id in duplicate_ids:
            await owner.execute(
                "INSERT INTO coupon_codes(id,pool_id,code,distributed,created_at,updated_at) "
                "VALUES($1,$2,'DUPLICATE',false,now(),now())",
                code_id,
                pool_id,
            )
    finally:
        await owner.close()
    await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "u6c1e2f3a4b5")
    failed = await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head", succeeds=False)
    assert failed.returncode != 0
    owner = await asyncpg.connect(dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u6c1e2f3a4b5"
        shell = await owner.fetchrow(
            "SELECT pg_get_indexdef(indexrelid) AS definition,indisvalid FROM pg_index "
            "WHERE indexrelid=to_regclass('public.uq_coupon_codes_tenant_code_idx')"
        )
        assert shell is not None and shell["indisvalid"] is False
        assert "ON public.coupon_codes USING btree (tenant_id, code)" in shell["definition"]
        await owner.execute("DELETE FROM coupon_codes WHERE id=$1", duplicate_ids[1])
    finally:
        await owner.close()
    await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")
    owner = await asyncpg.connect(dsn)
    try:
        assert await owner.fetchval(
            "SELECT indisvalid FROM pg_index WHERE indexrelid='public.uq_coupon_codes_tenant_code_idx'::regclass"
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_index WHERE NOT indisvalid "
            "AND indrelid IN ('coupon_codes'::regclass,'coupon_pools'::regclass))"
        )
    finally:
        await owner.close()


async def test_coupon_allocation_is_claim_bound_replay_safe_and_runtime_dml_closed(migrated_pg_url: str) -> None:
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(dsn)
    runtime = await asyncpg.connect(dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    ids = await _seed_catalog(owner, "coupon-authority")
    pool_id = uuid.uuid4()
    benefit_id = uuid.uuid4()
    consumer = f"consumer-{uuid.uuid4()}"
    claim_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    code_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    await owner.execute(
        "INSERT INTO coupon_pools(id,tenant_id,name,total_codes,remaining,created_at,updated_at) "
        "VALUES($1,$2,'authority',3,3,now(),now())",
        pool_id,
        ids["tenant"],
    )
    for code_id in code_ids:
        await owner.execute(
            "INSERT INTO coupon_codes(id,tenant_id,pool_id,code,distributed,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,false,now(),now())",
            code_id,
            ids["tenant"],
            pool_id,
            f"COUPON-{code_id}",
        )
    await owner.execute(
        "INSERT INTO benefits(id,tenant_id,name,benefit_type,config_json,stock_total,stock_used,per_person_limit,"
        "status,created_at,updated_at) VALUES($1,$2,'coupon','platform_coupon','{}',3,3,3,'active',now(),now())",
        benefit_id,
        ids["tenant"],
    )
    for index, claim_id in enumerate(claim_ids):
        await owner.execute(
            "INSERT INTO benefit_claims(id,tenant_id,benefit_id,consumer_id,idempotency_key,claim_type,status,"
            "delivery_status,reservation_status,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,'claim','success','pending','not_required',now(),now())",
            claim_id,
            ids["tenant"],
            benefit_id,
            consumer,
            f"coupon-{index}",
        )
    try:
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(ids["tenant"]))
        first = await runtime.fetchrow(
            "SELECT * FROM allocate_coupon_code($1,$2,$3,$4)",
            ids["tenant"],
            pool_id,
            consumer,
            claim_ids[0],
        )
        assert first["remaining"] == 2 and first["replayed"] is False
        replay = await runtime.fetchrow(
            "SELECT * FROM allocate_coupon_code($1,$2,$3,$4)",
            ids["tenant"],
            pool_id,
            consumer,
            claim_ids[0],
        )
        assert replay["coupon_code_id"] == first["coupon_code_id"]
        assert replay["remaining"] == 2 and replay["replayed"] is True

        async def allocate(claim_id: uuid.UUID) -> asyncpg.Record:
            conn = await asyncpg.connect(dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
            try:
                await conn.execute("SELECT set_config('app.tenant_id',$1,false)", str(ids["tenant"]))
                return await conn.fetchrow(
                    "SELECT * FROM allocate_coupon_code($1,$2,$3,$4)",
                    ids["tenant"],
                    pool_id,
                    consumer,
                    claim_id,
                )
            finally:
                await conn.close()

        concurrent = await asyncio.gather(*(allocate(claim_id) for claim_id in claim_ids[1:]))
        assert {row["coupon_code_id"] for row in concurrent}.isdisjoint({first["coupon_code_id"]})
        assert len({row["coupon_code_id"] for row in concurrent}) == 2
        assert {row["remaining"] for row in concurrent} == {0, 1}
        assert all(row["replayed"] is False for row in concurrent)
        assert await owner.fetchval("SELECT remaining FROM coupon_pools WHERE id=$1", pool_id) == 0
        await _assert_sqlstate(
            runtime.execute("UPDATE coupon_codes SET consumer_id='forged' WHERE id=$1", first["coupon_code_id"]),
            "42501",
        )
        await _assert_sqlstate(
            runtime.execute("DELETE FROM coupon_codes WHERE id=$1", first["coupon_code_id"]), "42501"
        )
        assert await owner.fetchval(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='coupon_codes'::regclass"
        )
        signature = "public.allocate_coupon_code(uuid,uuid,text,uuid)"
        assert await owner.fetchval("SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", signature)
        assert not await owner.fetchval("SELECT has_function_privilege('public',$1,'EXECUTE')", signature)
    finally:
        await runtime.close()
        await owner.close()
