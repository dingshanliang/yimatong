"""Online campaign timestamp and legacy claim-state rollout contract."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from tests.test_acceptance.conftest import (
    ADMIN_DSN,
    AcceptanceDatabaseLease,
    _create_owned_database,
    _drop_database_with_retry,
    run_owned_migrations_with_snapshot_retry,
)
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "u5a3d4e5f6a7"
CURRENT_HEAD_REVISION = "u6b5c6d7e8f9"


async def _seed_minimal_campaign_catalog(conn: asyncpg.Connection) -> dict[str, uuid.UUID]:
    """Create only the old-schema parents needed by this campaign migration probe."""

    tenant_id, brand_id, product_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await conn.execute(
        "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
        "VALUES($1,'campaign rollout',$2,'active','free','brand',now(),now())",
        tenant_id,
        f"campaign-rollout-{tenant_id.hex[:8]}",
    )
    await conn.execute(
        "INSERT INTO brands(id,tenant_id,name,status,created_at,updated_at) "
        "VALUES($1,$2,'campaign rollout brand','active',now(),now())",
        brand_id,
        tenant_id,
    )
    await conn.execute(
        "INSERT INTO products(id,tenant_id,brand_id,name,status,created_at,updated_at) "
        "VALUES($1,$2,$3,'campaign rollout product','active',now(),now())",
        product_id,
        tenant_id,
        brand_id,
    )
    return {"tenant": tenant_id, "product": product_id}


async def _resolve_isolated_recovery_markers(conn: asyncpg.Connection) -> None:
    expected_markers = {
        ("0006", "distributors", "contact_phone"),
        ("0006", "kyc_records", "real_name"),
        ("0006", "kyc_records", "id_number"),
        ("0006", "kyc_records", "phone"),
    }
    markers = await conn.fetch(
        "SELECT source_revision,source_table,source_column,state FROM legacy_pii_recovery_markers"
    )
    assert {(row["source_revision"], row["source_table"], row["source_column"]) for row in markers} == expected_markers
    unresolved = [row for row in markers if row["state"] == "legacy_unknown"]
    assert all(row["state"] in {"legacy_unknown", "operator_recovered"} for row in markers)
    if unresolved:
        assert (
            await conn.execute(
                "UPDATE legacy_pii_recovery_markers SET state='operator_recovered',"
                "note=note||'; isolated campaign rollout acknowledgement' "
                "WHERE source_revision='0006' AND state='legacy_unknown'"
            )
            == f"UPDATE {len(unresolved)}"
        )


@asynccontextmanager
async def _isolated_campaign_rollout_database(source_url: str) -> AsyncIterator[str]:
    """Run the deep campaign round trip without consuming sibling claim facts."""

    database_name = f"yimatong_acceptance_campaignrt_{uuid.uuid4().hex[:12]}"
    database_url = make_url(source_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(
        database_name=database_name, database_dsn=database_url, owner_token=uuid.uuid4().hex
    )
    primary_error: BaseException | None = None
    initial_revision: str | None = None
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        owner = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            initial_revision = await owner.fetchval("SELECT version_num FROM alembic_version")
            assert initial_revision is not None
            await _resolve_isolated_recovery_markers(owner)
        finally:
            await owner.close()
        yield database_url
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        if lease.created:
            if initial_revision is not None:
                try:
                    await asyncio.to_thread(_alembic, database_url, "upgrade", initial_revision)
                except BaseException as exc:
                    cleanup_errors.append(exc)
            try:
                await _drop_database_with_retry(
                    lease.database_name,
                    ADMIN_DSN,
                    expected_owner_marker=lease.owner_marker,
                    allow_unmarked_created=lease.created and not lease.marker_written,
                )
            except BaseException as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            if primary_error is not None:
                for cleanup_error in cleanup_errors:
                    primary_error.add_note(f"isolated campaign rollout cleanup also failed: {cleanup_error!r}")
            else:
                raise cleanup_errors[0]


async def test_campaign_time_shadow_rollout_and_legacy_claim_states(migrated_pg_url: str) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as isolated_pg_url:
        await _assert_campaign_time_shadow_rollout_and_legacy_claim_states(isolated_pg_url)


async def _assert_campaign_time_shadow_rollout_and_legacy_claim_states(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", PARENT_REVISION)
    owner = await asyncpg.connect(owner_dsn)
    ids = await _seed_minimal_campaign_catalog(owner)
    campaign_id, benefit_id = uuid.uuid4(), uuid.uuid4()
    try:
        await owner.execute(
            "INSERT INTO campaigns(id,tenant_id,name,campaign_type,status,product_id,start_at,end_at,rules_json,"
            "created_at,updated_at) VALUES($1,$2,'legacy campaign','scan','draft',$3,"
            "'2026-08-11T09:00:00','2026-08-12T09:00:00','{}',now(),now())",
            campaign_id,
            ids["tenant"],
            ids["product"],
        )
        await owner.execute(
            "INSERT INTO benefits(id,tenant_id,campaign_id,name,benefit_type,config_json,stock_total,stock_used,"
            "per_person_limit,status,created_at,updated_at) VALUES($1,$2,$3,'legacy benefit','platform_coupon','{}',"
            "10,3,3,'active',now(),now())",
            benefit_id,
            ids["tenant"],
            campaign_id,
        )
        for claim_status, delivery_status in (
            ("claimed", "pending"),
            ("delivered", "delivered"),
            ("used", "delivered"),
        ):
            await owner.execute(
                "INSERT INTO benefit_claims(id,tenant_id,benefit_id,campaign_id,consumer_id,idempotency_key,"
                "claim_type,status,delivery_status,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,$5,$6,'claim',$7,$8,now(),now())",
                uuid.uuid4(),
                ids["tenant"],
                benefit_id,
                campaign_id,
                f"consumer-{claim_status}",
                f"idem-{claim_status}",
                claim_status,
                delivery_status,
            )
    finally:
        await owner.close()

    await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "u6a0b1c2d3e4")
    owner = await asyncpg.connect(owner_dsn)
    try:
        legacy = await owner.fetchrow(
            "SELECT start_at_tz,end_at_tz FROM campaigns WHERE tenant_id=$1 AND id=$2", ids["tenant"], campaign_id
        )
        assert legacy["start_at_tz"] is None and legacy["end_at_tz"] is None
        expand_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO campaigns(id,tenant_id,name,campaign_type,status,start_at,end_at,rules_json,created_at,updated_at) "
            "VALUES($1,$2,'expand writer','scan','draft','2026-08-13T09:00:00+08:00',"
            "'2026-08-14T09:00:00+08:00','{}',now(),now())",
            expand_id,
            ids["tenant"],
        )
        assert await owner.fetchval(
            "SELECT start_at_tz IS NOT NULL AND end_at_tz IS NOT NULL FROM campaigns WHERE id=$1", expand_id
        )
    finally:
        await owner.close()

    await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")

    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval(
            "SELECT data_type='timestamp with time zone' FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='campaigns' AND column_name='start_at'"
        )
        assert await owner.fetchval(
            "SELECT start_at='2026-08-11 01:00:00+00'::timestamptz "
            "AND end_at='2026-08-12 01:00:00+00'::timestamptz FROM campaigns WHERE id=$1",
            campaign_id,
        )
        claim_states = await owner.fetch(
            "SELECT status,delivery_status FROM benefit_claims WHERE tenant_id=$1 AND benefit_id=$2",
            ids["tenant"],
            benefit_id,
        )
        assert {(row["status"], row["delivery_status"]) for row in claim_states} == {
            ("claimed", "pending"),
            ("delivered", "delivered"),
            ("used", "delivered"),
        }
    finally:
        await owner.close()

    owner = await asyncpg.connect(owner_dsn)
    try:
        await _resolve_isolated_recovery_markers(owner)
    finally:
        await owner.close()
    await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", PARENT_REVISION)
    owner = await asyncpg.connect(owner_dsn)
    try:
        assert await owner.fetchval(
            "SELECT data_type='character varying' FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='campaigns' AND column_name='start_at'"
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name='campaigns' AND column_name='start_at_tz')"
        )
    finally:
        await owner.close()
    await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")


async def test_campaign_concurrent_index_failure_is_retryable(migrated_pg_url: str) -> None:
    async with _isolated_campaign_rollout_database(migrated_pg_url) as isolated_pg_url:
        await _assert_campaign_concurrent_index_failure_is_retryable(isolated_pg_url)


async def _assert_campaign_concurrent_index_failure_is_retryable(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", "u6a0b1c2d3e4")
    owner = await asyncpg.connect(owner_dsn)
    blocker = await asyncpg.connect(owner_dsn)
    ids = await _seed_minimal_campaign_catalog(owner)
    campaign_id = uuid.uuid4()
    blocker_tx: asyncpg.Transaction | None = None
    timeout_set = False
    try:
        await owner.execute(
            "INSERT INTO campaigns(id,tenant_id,name,campaign_type,status,start_at,end_at,rules_json,created_at,updated_at) "
            "VALUES($1,$2,'index retry','scan','draft','2026-08-11T09:00:00','2026-08-12T09:00:00',"
            "'{}',now(),now())",
            campaign_id,
            ids["tenant"],
        )
        blocker_tx = blocker.transaction()
        await blocker_tx.start()
        await blocker.execute("UPDATE campaigns SET updated_at=updated_at WHERE id=$1", campaign_id)
        await owner.execute("ALTER ROLE yimatong SET statement_timeout='3s'")
        timeout_set = True
        failed = await asyncio.to_thread(
            _alembic,
            migrated_pg_url,
            "upgrade",
            "u6a1b2c3d4e5",
            succeeds=False,
        )
        assert "statement timeout" in f"{failed.stdout}\n{failed.stderr}".lower()
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u6a0b1c2d3e4"
        shell = await owner.fetchrow(
            "SELECT indisvalid,pg_get_indexdef(indexrelid) AS definition FROM pg_index "
            "WHERE indexrelid=to_regclass('public.uq_campaigns_tenant_id_id')"
        )
        if shell is not None:
            assert shell["indisvalid"] is False
            assert shell["definition"] == (
                "CREATE UNIQUE INDEX uq_campaigns_tenant_id_id ON public.campaigns USING btree (tenant_id, id)"
            )
        await blocker_tx.rollback()
        blocker_tx = None
        await owner.execute("ALTER ROLE yimatong RESET statement_timeout")
        timeout_set = False
        await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == CURRENT_HEAD_REVISION
        assert await owner.fetchval(
            "SELECT indisvalid FROM pg_index WHERE indexrelid='public.uq_campaigns_tenant_id_id'::regclass"
        )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_index idx JOIN pg_class cls ON cls.oid=idx.indexrelid "
            "WHERE cls.relnamespace='public'::regnamespace AND (NOT idx.indisvalid OR cls.relname LIKE 'u6a%temp%'))"
        )
    finally:
        if blocker_tx is not None:
            await blocker_tx.rollback()
        if timeout_set:
            await owner.execute("ALTER ROLE yimatong RESET statement_timeout")
        await blocker.close()
        await owner.close()
        await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")
