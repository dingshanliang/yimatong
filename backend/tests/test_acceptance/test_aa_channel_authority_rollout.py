"""Run reversible U07A rollout checks before immutable channel facts."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from tests.test_acceptance.conftest import ADMIN_DSN
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


def _owner_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


@asynccontextmanager
async def _isolated_head_database(source_url: str) -> AsyncIterator[str]:
    database_name = f"yimatong_acceptance_u7aroll_{uuid.uuid4().hex[:12]}"
    database_url = make_url(source_url).set(database=database_name).render_as_string(hide_password=False)
    admin = await asyncpg.connect(ADMIN_DSN)
    try:
        await admin.execute(f'CREATE DATABASE "{database_name}" OWNER yimatong')
    finally:
        await admin.close()
    try:
        _alembic(database_url, "upgrade", "head")
        yield database_url
    finally:
        admin = await asyncpg.connect(ADMIN_DSN)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid<>pg_backend_pid()",
                database_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await admin.close()


async def _assert_channel_authority_rollout(migrated_pg_url: str) -> None:
    index_name = "uq_distributors_tenant_id_id_u7a"
    _alembic(migrated_pg_url, "downgrade", "u7a0c1d2e3f4")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        await owner.execute(f"CREATE UNIQUE INDEX {index_name} ON distributors(id,tenant_id)")
    finally:
        await owner.close()

    _alembic(migrated_pg_url, "upgrade", "u7a1d2e3f4a5", succeeds=False)
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u7a0c1d2e3f4"
        await owner.execute(f"DROP INDEX {index_name}")
        await owner.execute(f"CREATE UNIQUE INDEX {index_name} ON distributors(tenant_id,id)")
        await owner.execute(f"UPDATE pg_index SET indisvalid=false WHERE indexrelid='{index_name}'::regclass")
    finally:
        await owner.close()

    _alembic(migrated_pg_url, "upgrade", "u7a2e3f4a5b6")
    _alembic(migrated_pg_url, "downgrade", "u7a1d2e3f4a5")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        expected_legacy_fks = {
            "regions_distributor_id_fkey",
            "stores_distributor_id_fkey",
            "stores_region_id_fkey",
            "code_allocations_batch_id_fkey",
            "code_allocations_distributor_id_fkey",
            "code_allocations_store_id_fkey",
            "fk_code_allocations_region_id_regions",
            "account_channel_scopes_account_id_fkey",
            "account_channel_scopes_distributor_id_fkey",
            "account_channel_scopes_store_id_fkey",
            "fk_account_channel_scopes_region_id_regions",
        }
        actual_legacy_fks = set(
            await owner.fetch(
                "SELECT conname FROM pg_constraint WHERE contype='f' "
                "AND conrelid=ANY(ARRAY['regions'::regclass,'stores'::regclass,"
                "'code_allocations'::regclass,'account_channel_scopes'::regclass])"
            )
        )
        assert {row["conname"] for row in actual_legacy_fks} == expected_legacy_fks
        assert await owner.fetchval("SELECT indisvalid FROM pg_index WHERE indexrelid=$1::regclass", index_name)
    finally:
        await owner.close()

    _alembic(migrated_pg_url, "downgrade", "u7a0c1d2e3f4")
    _alembic(migrated_pg_url, "downgrade", "u6j0b1c2d3e4", succeeds=False)
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u7a0c1d2e3f4"
        assert await owner.fetchval("SELECT count(*) FROM legacy_pii_recovery_markers WHERE state='legacy_unknown'") > 0
        await owner.execute(
            "UPDATE legacy_pii_recovery_markers SET state='operator_recovered', "
            "note=note||'; acceptance recovery acknowledgement'"
        )
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "downgrade", "u6j0b1c2d3e4")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval("SELECT to_regclass('legacy_pii_recovery_markers') IS NULL")
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "head")
    _alembic(migrated_pg_url, "check")


async def test_channel_authority_stages_repair_indexes_and_restore_legacy_fks(migrated_pg_url: str) -> None:
    async with _isolated_head_database(migrated_pg_url) as isolated_pg_url:
        await _assert_channel_authority_rollout(isolated_pg_url)
