"""Run reversible consent rollout checks before immutable write-fact nodes."""

import asyncpg
import pytest

from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


def _owner_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def test_consumer_consent_staged_roundtrip_and_invalid_index_repair(migrated_pg_url: str) -> None:
    _alembic(migrated_pg_url, "downgrade", "u6e0c1d2e3f4")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        await owner.execute("CREATE UNIQUE INDEX uq_consumer_profiles_tenant_id ON consumer_profiles(id,tenant_id)")
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "u6e1d2e3f4a5", succeeds=False)
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u6e0c1d2e3f4"
        await owner.execute("DROP INDEX uq_consumer_profiles_tenant_id")
        await owner.execute("CREATE UNIQUE INDEX uq_consumer_profiles_tenant_id ON consumer_profiles(tenant_id,id)")
        await owner.execute(
            "UPDATE pg_index SET indisvalid=false WHERE indexrelid='uq_consumer_profiles_tenant_id'::regclass"
        )
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "u6e1d2e3f4a5")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval(
            "SELECT indisvalid FROM pg_index WHERE indexrelid='uq_consumer_profiles_tenant_id'::regclass"
        )
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "head")
    _alembic(migrated_pg_url, "check")
    _alembic(migrated_pg_url, "downgrade", "u5b5b6c7d8e9")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval("SELECT to_regclass('consumer_consent_policies') IS NULL")
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "head")
    _alembic(migrated_pg_url, "check")


async def test_active_consent_subject_index_is_fail_closed_repairable_and_roundtrips(migrated_pg_url: str) -> None:
    index_name = "uq_consent_records_tenant_subject_purpose_policy_active"
    _alembic(migrated_pg_url, "downgrade", "u6g0d1e2f3a4")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        await owner.execute(f"CREATE UNIQUE INDEX {index_name} ON consent_records(id,tenant_id)")
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "u6h0e1f2a3b4", succeeds=False)
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "u6g0d1e2f3a4"
        await owner.execute(f"DROP INDEX {index_name}")
        await owner.execute(
            f"CREATE UNIQUE INDEX {index_name} ON consent_records "
            "(tenant_id,purpose,visitor_subject_hash,policy_id) "
            "WHERE authority_version=1 AND status='granted'"
        )
        await owner.execute(f"UPDATE pg_index SET indisvalid=false WHERE indexrelid='{index_name}'::regclass")
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "u6h0e1f2a3b4")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval(f"SELECT indisvalid FROM pg_index WHERE indexrelid='{index_name}'::regclass")
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "head")
    _alembic(migrated_pg_url, "check")
    _alembic(migrated_pg_url, "downgrade", "u6h0e1f2a3b4")
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        assert await owner.fetchval(
            "SELECT to_regprocedure('public.grant_consumer_consent_without_subject_coalescing"
            "(uuid,uuid,uuid,text,text,text,uuid,timestamptz,text,text,uuid,text,text,text)') IS NULL"
        )
        assert await owner.fetchval(
            "SELECT to_regprocedure('public.grant_consumer_consent"
            "(uuid,uuid,uuid,text,text,text,uuid,timestamptz,text,text,uuid,text,text,text)') IS NOT NULL"
        )
    finally:
        await owner.close()
    _alembic(migrated_pg_url, "upgrade", "head")
    _alembic(migrated_pg_url, "check")
