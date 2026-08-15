"""Migration contract for payload-bound campaign claim idempotency."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url

from tests.test_acceptance.conftest import (
    ADMIN_DSN,
    AcceptanceDatabaseLease,
    _create_owned_database,
    _drop_database_with_retry,
    run_owned_migrations_with_snapshot_retry,
)
from tests.test_acceptance.test_code_batch_delivery_contract import _seed_catalog
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

HEAD_REVISION = "u6b5c6d7e8f9"
PARENT_REVISION = "u7c2a3b4c5d6"
DEEP_PARENT_REVISION = "649cdfd94581"
BOUND_SIGNATURE = "public.claim_campaign_benefit(uuid,uuid,uuid,uuid,uuid,text,text,text,uuid,uuid,text)"


@pytest_asyncio.fixture
async def isolated_claim_migration_pg_url(migrated_pg_url: str) -> AsyncIterator[str]:
    """Give each downgrade test a marker-owned database with no sibling facts."""

    database_name = f"yimatong_acceptance_claim_{uuid.uuid4().hex[:12]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(database_name, database_url, uuid.uuid4().hex)
    primary_error: BaseException | None = None
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        yield database_url
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
            except BaseException as cleanup_error:
                if primary_error is not None:
                    primary_error.add_note(f"isolated claim migration cleanup also failed: {cleanup_error!r}")
                else:
                    raise


async def _catalog(conn: asyncpg.Connection) -> dict[str, object]:
    return {
        "revision": await conn.fetchval("SELECT version_num FROM alembic_version"),
        "column": await conn.fetchval(
            "SELECT data_type||':'||character_maximum_length||':'||is_nullable "
            "FROM information_schema.columns WHERE table_schema='public' AND table_name='benefit_claims' "
            "AND column_name='request_digest'"
        ),
        "constraint": await conn.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_benefit_claims_request_digest'"
        ),
        "index": await conn.fetchval(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
            "AND indexname='uq_benefit_claims_bound_idempotency'"
        ),
        "trigger": await conn.fetchval(
            "SELECT pg_get_triggerdef(oid) FROM pg_trigger "
            "WHERE tgname='trg_guard_benefit_claim_request_digest' AND NOT tgisinternal"
        ),
        "function": await conn.fetchval("SELECT pg_get_functiondef(to_regprocedure($1))", BOUND_SIGNATURE),
        "runtime_execute": await conn.fetchval(
            "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", BOUND_SIGNATURE
        ),
    }


async def test_claim_idempotency_catalog_round_trips_exactly(isolated_claim_migration_pg_url: str) -> None:
    database_url = isolated_claim_migration_pg_url
    owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    try:
        initial = await _catalog(owner)
        assert initial["revision"] == HEAD_REVISION
        assert initial["column"] == "character varying:64:YES"
        assert initial["constraint"] is not None
        assert initial["index"] is not None and "WHERE (request_digest IS NOT NULL)" in initial["index"]
        assert initial["trigger"] is not None
        assert initial["runtime_execute"] is True
    finally:
        await owner.close()

    await asyncio.to_thread(_alembic, database_url, "downgrade", PARENT_REVISION)
    owner = await asyncpg.connect(owner_dsn)
    try:
        parent = await _catalog(owner)
        assert parent["revision"] == PARENT_REVISION
        assert parent["column"] is None
        assert parent["constraint"] is None
        assert parent["index"] is None
        assert parent["trigger"] is None
        assert parent["function"] is not None
        assert parent["runtime_execute"] is True
    finally:
        await owner.close()

    await asyncio.to_thread(_alembic, database_url, "upgrade", "head")
    owner = await asyncpg.connect(owner_dsn)
    try:
        restored = await _catalog(owner)
        assert restored == initial
    finally:
        await owner.close()
    await asyncio.to_thread(_alembic, database_url, "check")


async def test_bound_claim_blocks_downgrade_without_head_or_data_drift(
    isolated_claim_migration_pg_url: str,
) -> None:
    database_url = isolated_claim_migration_pg_url
    owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    ids = await _seed_catalog(owner, "claim-downgrade-preflight")
    benefit_id, claim_id = uuid.uuid4(), uuid.uuid4()
    digest = "a" * 64
    try:
        await owner.execute(
            "INSERT INTO benefits(id,tenant_id,campaign_id,name,benefit_type,config_json,stock_total,stock_used,"
            "per_person_limit,status,created_at,updated_at) "
            "VALUES($1,$2,NULL,'preflight','platform_coupon','{}',1,1,1,'active',now(),now())",
            benefit_id,
            ids["tenant"],
        )
        await owner.execute(
            "INSERT INTO benefit_claims(id,tenant_id,benefit_id,campaign_id,consumer_id,idempotency_key,"
            "request_digest,claim_type,status,delivery_status,reservation_status,created_at,updated_at) "
            "VALUES($1,$2,$3,NULL,$4,$5,$6,'claim','success','not_required','not_required',now(),now())",
            claim_id,
            ids["tenant"],
            benefit_id,
            f"anon:v1:{uuid.uuid4().hex}",
            f"claim:v1:{uuid.uuid4().hex}",
            digest,
        )
        before = await owner.fetchrow(
            "SELECT xmin::text AS xmin,md5(row_to_json(claim)::text) AS digest FROM benefit_claims claim "
            "WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            claim_id,
        )
        assert before is not None
    finally:
        await owner.close()

    blocked = await asyncio.to_thread(
        _alembic,
        database_url,
        "downgrade",
        PARENT_REVISION,
        succeeds=False,
    )
    assert "cannot downgrade benefit claim request binding while bound claims exist" in blocked.stderr
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == HEAD_REVISION
        assert (
            await owner.fetchval(
                "SELECT request_digest FROM benefit_claims WHERE tenant_id=$1 AND id=$2", ids["tenant"], claim_id
            )
            == digest
        )
        assert (
            await owner.fetchval(
                "SELECT stock_used FROM benefits WHERE tenant_id=$1 AND id=$2", ids["tenant"], benefit_id
            )
            == 1
        )
        after = await owner.fetchrow(
            "SELECT xmin::text AS xmin,md5(row_to_json(claim)::text) AS digest FROM benefit_claims claim "
            "WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            claim_id,
        )
        assert after == before
    finally:
        await owner.close()


async def test_deep_downgrade_blocks_risk_receipt_at_u6b_head(
    isolated_claim_migration_pg_url: str,
) -> None:
    database_url = isolated_claim_migration_pg_url
    owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    ids = await _seed_catalog(owner, "claim-risk-preflight")
    receipt_id = uuid.uuid4()
    try:
        await owner.execute(
            "INSERT INTO risk_action_receipts(id,tenant_id,action,idempotency_key,payload_digest,result,recorded_at) "
            "VALUES($1,$2,'evaluate',$3,$4,'{}',now())",
            receipt_id,
            ids["tenant"],
            f"risk:v1:{uuid.uuid4().hex}",
            "b" * 64,
        )
        before = await owner.fetchrow(
            "SELECT xmin::text AS xmin,md5(row_to_json(receipt)::text) AS digest "
            "FROM risk_action_receipts receipt WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            receipt_id,
        )
        assert before is not None
        function_before = await owner.fetchval("SELECT pg_get_functiondef(to_regprocedure($1))", BOUND_SIGNATURE)
    finally:
        await owner.close()

    blocked = await asyncio.to_thread(
        _alembic,
        database_url,
        "downgrade",
        DEEP_PARENT_REVISION,
        succeeds=False,
    )
    assert "risk action receipts are immutable facts" in f"{blocked.stdout}\n{blocked.stderr}"
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == HEAD_REVISION
        after = await owner.fetchrow(
            "SELECT xmin::text AS xmin,md5(row_to_json(receipt)::text) AS digest "
            "FROM risk_action_receipts receipt WHERE tenant_id=$1 AND id=$2",
            ids["tenant"],
            receipt_id,
        )
        assert after == before
        assert (
            await owner.fetchval("SELECT pg_get_functiondef(to_regprocedure($1))", BOUND_SIGNATURE) == function_before
        )
        assert await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name='benefit_claims' AND column_name='request_digest')"
        )
    finally:
        await owner.close()
