"""Real PostgreSQL proof for canonical account email identity migration."""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.test_acceptance.conftest import BACKEND_DIR

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "j618bf978c10"


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


async def test_case_collision_blocks_then_clean_upgrade_and_downgrade_are_safe(migrated_pg_url: str):
    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    tenant_id, organization_id, first_id, second_id = (uuid.uuid4() for _ in range(4))
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, status, plan, tenant_type, created_at, updated_at) "
            "VALUES ($1, 'email migration tenant', $2, 'active', 'free', 'brand', now(), now())",
            tenant_id,
            f"email-migration-{tenant_id.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO organizations (id, tenant_id, name, created_at, updated_at) "
            "VALUES ($1, $2, 'Email Org', now(), now())",
            organization_id,
            tenant_id,
        )
        for account_id, email in ((first_id, " Member@Example.COM "), (second_id, "member@example.com")):
            await conn.execute(
                "INSERT INTO accounts "
                "(id, tenant_id, organization_id, email, hashed_password, name, is_active, auth_version, "
                "must_change_password, failed_login_attempts, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, 'unused', 'Member', true, 0, false, 0, now(), now())",
                account_id,
                tenant_id,
                organization_id,
                email,
            )
    finally:
        await conn.close()

    failed = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
    failure_output = f"{failed.stdout}\n{failed.stderr}"
    assert "collide after lower(trim(email))" in failure_output

    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == PARENT_REVISION
        assert await conn.fetchval("SELECT email FROM accounts WHERE id=$1", first_id) == " Member@Example.COM "
        await conn.execute("DELETE FROM accounts WHERE id=$1", second_id)
    finally:
        await conn.close()

    _alembic(migrated_pg_url, "upgrade", "head")
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT email FROM accounts WHERE id=$1", first_id) == "member@example.com"
        index_definition = await conn.fetchval(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND indexname=$1",
            "uq_accounts_tenant_email_ci",
        )
        assert index_definition is not None
        assert "lower((email)::text)" in index_definition
        assert (
            await conn.fetchval(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_accounts_email_canonical'"
            )
            == "CHECK (((email)::text = lower(TRIM(BOTH FROM email))))"
        )
    finally:
        await conn.close()

    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT email FROM accounts WHERE id=$1", first_id) == "member@example.com"
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM pg_constraint WHERE conname='uq_account_tenant_email' AND contype='u'"
            )
            == 1
        )
        assert await conn.fetchval("SELECT count(*) FROM pg_indexes WHERE indexname='uq_accounts_tenant_email_ci'") == 0
    finally:
        await conn.close()
    _alembic(migrated_pg_url, "upgrade", "head")


async def test_legacy_wecom_order_is_backfilled_and_blocks_old_replay(migrated_pg_url: str):
    from app.services.wecom_integration import process_wecom_callback_event

    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    tenant_id, connector_id, active_id, deleted_id, malformed_id, overflow_id = (uuid.uuid4() for _ in range(6))
    deleted_at = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    malformed_added_at = datetime(2025, 2, 3, 4, 5, 6, tzinfo=UTC)
    overflow_deleted_at = datetime(2025, 3, 4, 5, 6, 7, tzinfo=UTC)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug, status, plan, tenant_type, created_at, updated_at) "
            "VALUES ($1, 'legacy wecom tenant', $2, 'active', 'free', 'brand', now(), now())",
            tenant_id,
            f"legacy-wecom-{tenant_id.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO connectors (id, tenant_id, name, connector_type, config, enabled) "
            "VALUES ($1, $2, 'legacy-wecom', 'wecom_customer_contact', '{}'::jsonb, true)",
            connector_id,
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO wecom_external_contacts "
            "(id, tenant_id, connector_id, external_userid, user_id, state, status, raw_event, "
            "verification_source, change_type, event_fingerprint, welcome_code_pending, added_at, created_at) "
            "VALUES ($1, $2, $3, 'legacy-active', 'staff', 'legacy-state', 'active', "
            "$4::jsonb, 'confirmed_callback', 'add_external_contact', 'legacy-active-fp', false, now(), now())",
            active_id,
            tenant_id,
            connector_id,
            '{"CreateTime":"700","Sequence":"9"}',
        )
        await conn.execute(
            "INSERT INTO wecom_external_contacts "
            "(id, tenant_id, connector_id, external_userid, user_id, state, status, raw_event, "
            "verification_source, change_type, event_fingerprint, welcome_code_pending, deleted_at, created_at) "
            "VALUES ($1, $2, $3, 'legacy-deleted', 'staff', 'deleted-state', 'deleted', '{}'::jsonb, "
            "'termination_callback', 'del_external_contact', 'legacy-deleted-fp', false, $4, now())",
            deleted_id,
            tenant_id,
            connector_id,
            deleted_at,
        )
        await conn.execute(
            "INSERT INTO wecom_external_contacts "
            "(id, tenant_id, connector_id, external_userid, state, status, raw_event, verification_source, "
            "change_type, event_fingerprint, welcome_code_pending, added_at, created_at) "
            "VALUES ($1, $2, $3, 'legacy-malformed', 'malformed-state', 'active', $4::jsonb, "
            "'confirmed_callback', 'add_external_contact', 'legacy-malformed-fp', false, $5, now())",
            malformed_id,
            tenant_id,
            connector_id,
            '{"CreateTime":"not-a-number","Sequence":"also-invalid"}',
            malformed_added_at,
        )
        await conn.execute(
            "INSERT INTO wecom_external_contacts "
            "(id, tenant_id, connector_id, external_userid, state, status, raw_event, verification_source, "
            "change_type, event_fingerprint, welcome_code_pending, deleted_at, created_at) "
            "VALUES ($1, $2, $3, 'legacy-overflow', 'overflow-state', 'deleted', $4::jsonb, "
            "'termination_callback', 'del_external_contact', 'legacy-overflow-fp', false, $5, now())",
            overflow_id,
            tenant_id,
            connector_id,
            '{"CreateTime":"999999999999999999999999999999","Sequence":"999999999999999999999999999999"}',
            overflow_deleted_at,
        )
    finally:
        await conn.close()

    _alembic(migrated_pg_url, "upgrade", "head")
    conn = await asyncpg.connect(dsn)
    try:
        active = await conn.fetchrow(
            "SELECT event_time, event_sequence FROM public.wecom_external_contacts WHERE id=$1", active_id
        )
        deleted = await conn.fetchrow(
            "SELECT event_time, event_sequence, deleted_at FROM public.wecom_external_contacts WHERE id=$1",
            deleted_id,
        )
        malformed = await conn.fetchrow(
            "SELECT event_time, event_sequence FROM public.wecom_external_contacts WHERE id=$1", malformed_id
        )
        overflow = await conn.fetchrow(
            "SELECT event_time, event_sequence FROM public.wecom_external_contacts WHERE id=$1", overflow_id
        )
        columns = await conn.fetch(
            "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='wecom_external_contacts' "
            "AND column_name IN ('event_time','event_sequence') ORDER BY column_name"
        )
        assert int(active["event_time"].timestamp()) == 700
        assert active["event_sequence"] == 9
        assert deleted["event_time"] == deleted["deleted_at"]
        assert deleted["event_sequence"] == 0
        assert malformed["event_time"] == malformed_added_at
        assert malformed["event_sequence"] == 0
        assert overflow["event_time"] == overflow_deleted_at
        assert overflow["event_sequence"] == 0
        assert [(row["column_name"], row["is_nullable"]) for row in columns] == [
            ("event_sequence", "NO"),
            ("event_time", "NO"),
        ]
        defaults = {row["column_name"]: row["column_default"] for row in columns}
        assert defaults["event_sequence"] == "0"
        assert defaults["event_time"] == "now()"
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM pg_constraint WHERE conname IN "
                "('ck_wecom_external_contacts_event_time_nn','ck_wecom_external_contacts_event_sequence_nn')"
            )
            == 0
        )
    finally:
        await conn.close()

    engine = create_async_engine(migrated_pg_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        stale = await process_wecom_callback_event(
            session,
            connector_id=connector_id,
            event={
                "Event": "change_external_contact",
                "ChangeType": "add_external_contact",
                "ExternalUserID": "legacy-active",
                "UserID": "staff",
                "State": "legacy-state",
                "CreateTime": 600,
                "Sequence": 99,
            },
        )
        await session.commit()
    await engine.dispose()
    assert stale == {"status": "ignored", "reason": "non_newer_event"}

    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    conn = await asyncpg.connect(dsn)
    try:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name='wecom_external_contacts' AND column_name IN ('event_time','event_sequence')"
            )
            == 0
        )
        assert (
            await conn.fetchval("SELECT status FROM public.wecom_external_contacts WHERE id=$1", deleted_id)
            == "deleted"
        )
    finally:
        await conn.close()
    _alembic(migrated_pg_url, "upgrade", "head")


async def test_shadow_schema_and_same_named_index_cannot_redirect_migration(migrated_pg_url: str):
    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("CREATE SCHEMA email_shadow")
        await conn.execute("CREATE TABLE email_shadow.accounts (tenant_id uuid, email varchar(255))")
        await conn.execute("CREATE TABLE email_shadow.wecom_external_contacts (id uuid)")
        await conn.execute(
            "CREATE UNIQUE INDEX uq_accounts_tenant_email_ci ON email_shadow.accounts (tenant_id, lower(email))"
        )
        database_name = await conn.fetchval("SELECT current_database()")
        current_role = await conn.fetchval("SELECT current_user")
        quoted_database = await conn.fetchval("SELECT quote_ident($1)", database_name)
        quoted_role = await conn.fetchval("SELECT quote_ident($1)", current_role)
        await conn.execute(
            f"ALTER ROLE {quoted_role} IN DATABASE {quoted_database} SET search_path TO email_shadow, public"
        )
    finally:
        await conn.close()

    try:
        _alembic(migrated_pg_url, "upgrade", "head")
        probe = await asyncpg.connect(dsn)
        try:
            assert await probe.fetchval("SHOW search_path") == "email_shadow, public"
            assert (
                await probe.fetchval(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' "
                    "AND table_name='wecom_external_contacts' AND column_name IN ('event_time','event_sequence')"
                )
                == 2
            )
            assert (
                await probe.fetchval(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema='email_shadow' "
                    "AND table_name='wecom_external_contacts' AND column_name IN ('event_time','event_sequence')"
                )
                == 0
            )
            assert (
                await probe.fetchval(
                    "SELECT count(*) FROM pg_indexes WHERE schemaname='public' "
                    "AND indexname='uq_accounts_tenant_email_ci'"
                )
                == 1
            )
            assert (
                await probe.fetchval(
                    "SELECT count(*) FROM pg_indexes WHERE schemaname='email_shadow' "
                    "AND indexname='uq_accounts_tenant_email_ci'"
                )
                == 1
            )
        finally:
            await probe.close()
    finally:
        cleanup = await asyncpg.connect(dsn)
        try:
            database_name = await cleanup.fetchval("SELECT current_database()")
            current_role = await cleanup.fetchval("SELECT current_user")
            quoted_database = await cleanup.fetchval("SELECT quote_ident($1)", database_name)
            quoted_role = await cleanup.fetchval("SELECT quote_ident($1)", current_role)
            await cleanup.execute(f"ALTER ROLE {quoted_role} IN DATABASE {quoted_database} RESET search_path")
        finally:
            await cleanup.close()

    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    _alembic(migrated_pg_url, "upgrade", "head")


async def test_wrong_valid_index_fails_closed_and_invalid_exact_index_is_rebuilt(migrated_pg_url: str):
    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("CREATE UNIQUE INDEX uq_accounts_tenant_email_ci ON public.accounts (email, tenant_id)")
    finally:
        await conn.close()

    wrong = _alembic(migrated_pg_url, "upgrade", "head", succeeds=False)
    assert "Refusing to reuse index uq_accounts_tenant_email_ci" in f"{wrong.stdout}\n{wrong.stderr}"
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE indexname='uq_accounts_tenant_email_ci'")
        await conn.execute("DROP INDEX public.uq_accounts_tenant_email_ci")
        await conn.execute(
            "CREATE UNIQUE INDEX uq_accounts_tenant_email_ci ON public.accounts (tenant_id, lower(email))"
        )
        await conn.execute(
            "UPDATE pg_index SET indisvalid=false WHERE indexrelid='public.uq_accounts_tenant_email_ci'::regclass"
        )
    finally:
        await conn.close()

    _alembic(migrated_pg_url, "upgrade", "head")
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT i.indisvalid, i.indisready FROM pg_index i "
            "WHERE i.indexrelid='public.uq_accounts_tenant_email_ci'::regclass"
        )
        assert row["indisvalid"] is True
        assert row["indisready"] is True
    finally:
        await conn.close()


async def test_downgrade_legacy_unique_uses_guarded_concurrent_index(migrated_pg_url: str):
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("CREATE UNIQUE INDEX uq_account_tenant_email ON public.accounts (email, tenant_id)")
    finally:
        await conn.close()

    wrong = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
    assert "Refusing to replace unexpected index uq_account_tenant_email" in f"{wrong.stdout}\n{wrong.stderr}"
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == "b201eb830d00"
        await conn.execute("DROP INDEX public.uq_account_tenant_email")
        await conn.execute("CREATE UNIQUE INDEX uq_account_tenant_email ON public.accounts (tenant_id, email)")
        await conn.execute(
            "UPDATE pg_index SET indisvalid=false WHERE indexrelid='public.uq_account_tenant_email'::regclass"
        )
    finally:
        await conn.close()

    _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
    conn = await asyncpg.connect(dsn)
    try:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM pg_constraint WHERE conname='uq_account_tenant_email' AND contype='u'"
            )
            == 1
        )
        assert (
            await conn.fetchval(
                "SELECT i.indisvalid FROM pg_index i WHERE i.indexrelid='public.uq_account_tenant_email'::regclass"
            )
            is True
        )
    finally:
        await conn.close()
    _alembic(migrated_pg_url, "upgrade", "head")
