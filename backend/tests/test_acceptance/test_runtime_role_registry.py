"""Acceptance gates for the reviewed runtime relation registry and repaired RLS gaps."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url
from uuid6 import uuid7

from app.models.ai_generation import AiGeneration  # noqa: F401
from app.models.base import Base
from app.models.platform_config import PlatformConfig  # noqa: F401
from app.models.platform_opening import PlatformTenantOpening  # noqa: F401
from app.models.role_template_backup import (  # noqa: F401
    OperatorCampaignManageGrant,
    OrganizationParentRepairBackup,
    RoleTemplateBackup,
    TenantPlatformRoleAssignmentBackup,
)
from app.models.tenant_health import TenantHealthMetrics  # noqa: F401
from tests.test_acceptance.conftest import (
    ADMIN_DSN,
    BACKEND_DIR,
    AcceptanceDatabaseLease,
    _create_owned_database,
    _drop_database_with_retry,
    run_owned_migrations_with_snapshot_retry,
    seed_baseline,
)
from tests.test_acceptance.test_code_batch_delivery_contract import _seed_catalog

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

BUSINESS_GAP_TABLES = (
    "account_roles",
    "ai_generations",
    "code_allocations",
    "coupon_codes",
    "gmv_daily_stats",
    "pilot_milestone_corrections",
    "pilot_milestones",
    "regional_code_rules",
    "regional_templates",
    "retrospectives",
    "role_permissions",
    "sync_mappings",
    "tenant_domains",
    "tenant_quota_usage",
    "whitelabel_configs",
)
PILOT_READ_ONLY_TABLES = frozenset({"pilot_milestone_corrections", "pilot_milestones", "retrospectives"})
PILOT_RUNTIME_FUNCTIONS = (
    "materialize_pilot_milestones(uuid,uuid,jsonb)",
    "update_pending_retrospective_authority(uuid,uuid,uuid,uuid,bigint,text,text,jsonb,date,text,text)",
    "complete_retrospective_authority(uuid,uuid,uuid,uuid,bigint,text,text,jsonb,date,text,text,text)",
    "append_retrospective_note_authority(uuid,uuid,uuid,uuid,bigint,text,text,text)",
)
PILOT_CONTROL_FUNCTIONS = (
    "assert_pilot_tenant_actor(uuid,uuid,text,text,uuid)",
    "append_pilot_milestone_correction(uuid,uuid,uuid,text,timestamp with time zone,text,text,text,text)",
    "materialize_due_retrospective(uuid,uuid,uuid,integer,timestamp with time zone,timestamp with time zone,date,jsonb,text,uuid)",
)

NO_DELETE_RUNTIME_TABLES = (
    "code_batch_generation_receipts",
    "code_batches",
    "code_items",
)
SENSITIVE_ARTIFACT_TABLES = ("export_logs",)

CONTROL_TABLES = (
    "auth_sessions",
    "consumed_refresh_tokens",
    "invite_registration_receipts",
    "operator_campaign_manage_grants",
    "organization_parent_repair_backups",
    "platform_auth_sessions",
    "platform_configs",
    "platform_tenant_openings",
    "role_template_backups",
    "tenant_invite_codes",
    "tenant_platform_role_assignment_backups",
)

APPEND_ONLY_RUNTIME_TABLES = ("platform_audit_log",)
READ_ONLY_GLOBAL_TABLES = ("quota_rollout_state",)
CHANNEL_RESTRICTED_TABLES = (
    "distributors",
    "regions",
    "stores",
    "code_allocations",
    "account_channel_scopes",
    "channel_action_receipts",
)
DIVERSION_RESTRICTED_TABLES = (
    "diversion_clues",
    "diversion_observations",
    "diversion_evidence",
    "diversion_investigation_history",
    "diversion_action_receipts",
)
RISK_RESTRICTED_TABLES = (
    "campaign_risk_rules",
    "interception_records",
    "risk_action_outbox",
    "risk_action_receipts",
    "risk_alerts",
    "risk_campaign_pauses",
    "risk_notifications",
    "risk_rules",
)
WECOM_RESTRICTED_TABLES = ("wecom_callback_receipts", "wecom_external_contacts")
CHANNEL_PUBLIC_FUNCTIONS = (
    "create_channel_distributor(uuid,uuid,uuid,uuid,text,text,text,text,text,text,text)",
    "update_channel_distributor(uuid,uuid,uuid,uuid,bigint,text,text,text,text,text,text)",
    "create_channel_region(uuid,uuid,uuid,uuid,text,text,text,text,text,text,jsonb,uuid,text)",
    "update_channel_region(uuid,uuid,uuid,uuid,bigint,text,text,text,text,text,jsonb,uuid,text)",
    "create_channel_store(uuid,uuid,uuid,uuid,text,text,text,uuid,uuid,text,text)",
    "update_channel_store(uuid,uuid,uuid,uuid,bigint,text,text,uuid,uuid,text,text)",
    "archive_channel_store(uuid,uuid,uuid,uuid,bigint,text)",
    "assign_code_batch_channel(uuid,uuid,uuid,uuid,text,uuid,uuid)",
    "allocate_code_batch_channel(uuid,uuid,uuid,uuid,text,uuid,text,uuid,bigint,text)",
    "reassign_code_batch_channel(uuid,uuid,uuid,uuid,text,uuid,bigint,text,uuid,bigint,text)",
    "archive_code_batch_allocation(uuid,uuid,uuid,uuid,text,uuid,bigint,text)",
    "set_account_channel_scope(uuid,uuid,uuid,uuid,text,uuid,text,uuid)",
    "delete_account_channel_scope(uuid,uuid,uuid,uuid,bigint,text)",
    "get_my_channel_scope(uuid,uuid,text)",
    "mutate_risk_rule(uuid,uuid,uuid,text,uuid,bigint,text,text,text,text,jsonb,boolean)",
    "set_campaign_risk_rule(uuid,uuid,uuid,uuid,uuid,boolean,text)",
    "resume_risk_campaign_pause(uuid,uuid,uuid,uuid,bigint,text,text)",
    "evaluate_execute_scan_risk(uuid,uuid,uuid,uuid,text,jsonb)",
    "freeze_code_item_with_risk_alert(uuid,uuid,uuid,uuid,uuid,uuid,text,text)",
    "mark_risk_notification_read(uuid,uuid,uuid,uuid,text)",
    "mark_all_risk_notifications_read(uuid,uuid,uuid,text)",
)
MIGRATION_ONLY_TABLES = (
    "agency_authorization_integrity_backups",
    "alembic_version",
    "api_key_catalog_audit_context_secrets",
    "api_key_legacy_secret_backups",
    "code_delivery_contract_rollout_state",
    "channel_permission_backfill",
    "connector_secret_migration_backups",
    "consumer_detail_role_grant_backfills",
    "external_order_ledger_recovery_markers",
    "external_order_permission_backfill",
    "legacy_pii_recovery_markers",
    "rls_force_remediation_backups",
    "risk_permission_backfill",
    "runtime_privilege_remediation_backup",
    "webhook_permission_backfill",
    "wecom_member_recovery_markers",
)
PARENT_REVISION = "649cdfd94581"
CURRENT_HEAD_REVISION = "u9a3e4f5a6b7"


def _sole_repository_head() -> str:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert heads == [CURRENT_HEAD_REVISION], f"expected sole repository head {CURRENT_HEAD_REVISION}, got {heads}"
    return heads[0]


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


@asynccontextmanager
async def _isolated_head_database(source_url: str) -> AsyncIterator[str]:
    """Lease a migration-only database so round-trips cannot consume sibling test facts."""

    database_name = f"yimatong_acceptance_authrt_{uuid.uuid4().hex[:12]}"
    database_url = make_url(source_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(
        database_name=database_name,
        database_dsn=database_url,
        owner_token=uuid.uuid4().hex,
    )
    primary_error: BaseException | None = None
    cleanup_errors: list[BaseException] = []
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        await asyncio.to_thread(run_owned_migrations_with_snapshot_retry, lease)
        owner = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
        try:
            markers = await owner.fetch(
                "SELECT source_revision,source_table,source_column,state FROM legacy_pii_recovery_markers"
            )
            expected = {
                ("0006", "distributors", "contact_phone"),
                ("0006", "kyc_records", "real_name"),
                ("0006", "kyc_records", "id_number"),
                ("0006", "kyc_records", "phone"),
            }
            assert {(row["source_revision"], row["source_table"], row["source_column"]) for row in markers} == expected
            assert all(row["state"] == "legacy_unknown" for row in markers)
            assert (
                await owner.execute(
                    "UPDATE legacy_pii_recovery_markers SET state='operator_recovered',"
                    "note=note||'; isolated registry blocker acknowledgement' "
                    "WHERE source_revision='0006' AND state='legacy_unknown'"
                )
                == f"UPDATE {len(expected)}"
            )
        finally:
            await owner.close()
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
            except BaseException as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            if primary_error is not None:
                for cleanup_error in cleanup_errors:
                    primary_error.add_note(f"isolated registry database cleanup also failed: {cleanup_error!r}")
            else:
                raise cleanup_errors[0]


async def _assert_insert_denied(conn: asyncpg.Connection, query: str, *args: object) -> None:
    savepoint = conn.transaction()
    await savepoint.start()
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="row-level security"):
        await conn.execute(query, *args)
    await savepoint.rollback()


async def _assert_association_insert_denied(conn: asyncpg.Connection, query: str, *args: object) -> None:
    savepoint = conn.transaction()
    await savepoint.start()
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await conn.execute(query, *args)
    await savepoint.rollback()


async def _assert_statement_privilege_denied(conn: asyncpg.Connection, query: str, *args: object) -> None:
    savepoint = conn.transaction()
    await savepoint.start()
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.execute(query, *args)
    await savepoint.rollback()


async def _assert_authorization_migration_round_trip(migrated_pg_url: str) -> None:
    """Exercise parent/head data rollback and restore runtime least privilege on one leased DB."""
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    try:
        initial_revision = await owner.fetchval("SELECT version_num FROM alembic_version")
        pii_marker_states = await owner.fetch(
            "SELECT source_revision,source_table,source_column,state FROM legacy_pii_recovery_markers"
        )
        assert await owner.fetchval("SELECT count(*) FROM channel_action_receipts") == 0
        await owner.execute(
            "UPDATE legacy_pii_recovery_markers SET state='operator_recovered' "
            "WHERE state NOT IN ('plaintext_absent','operator_recovered')"
        )
    finally:
        await owner.close()

    agency_id, client_id, authorization_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
        owner = await asyncpg.connect(owner_dsn)
        await owner.executemany(
            "INSERT INTO tenants (id,name,slug,status,plan,tenant_type,created_at,updated_at) "
            "VALUES ($1,$2,$3,'active','free',$4,now(),now())",
            [
                (agency_id, "roundtrip agency", f"roundtrip-agency-{agency_id.hex}", "agency"),
                (client_id, "roundtrip brand", f"roundtrip-brand-{client_id.hex}", "brand"),
            ],
        )
        await owner.execute(
            "INSERT INTO agency_authorizations "
            "(id,agency_tenant_id,client_tenant_id,scope,status,granted_at,created_at,updated_at) "
            'VALUES ($1,$2,$3,\'["pages","products","pages"]\'::json,\'active\',now(),now(),NULL)',
            authorization_id,
            agency_id,
            client_id,
        )
        await owner.close()

        _alembic(migrated_pg_url, "upgrade", initial_revision)
        owner = await asyncpg.connect(owner_dsn)
        canonical = await owner.fetchrow(
            "SELECT scope::jsonb AS scope,updated_at IS NOT NULL AS timestamp_fixed "
            "FROM agency_authorizations WHERE id=$1",
            authorization_id,
        )
        assert canonical["scope"] == '["products", "pages"]'
        assert canonical["timestamp_fixed"]
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM agency_authorization_integrity_backups WHERE authorization_id=$1",
                authorization_id,
            )
            == 1
        )
        await owner.execute(
            "UPDATE legacy_pii_recovery_markers SET state='operator_recovered' "
            "WHERE state NOT IN ('plaintext_absent','operator_recovered')"
        )
        await owner.close()

        _alembic(migrated_pg_url, "downgrade", PARENT_REVISION)
        owner = await asyncpg.connect(owner_dsn)
        restored = await owner.fetchrow(
            "SELECT scope::jsonb AS scope,updated_at IS NULL AS timestamp_restored "
            "FROM agency_authorizations WHERE id=$1",
            authorization_id,
        )
        assert restored["scope"] == '["pages", "products", "pages"]'
        assert restored["timestamp_restored"]
        for privilege in ("INSERT", "UPDATE", "DELETE"):
            assert await owner.fetchval(
                "SELECT has_table_privilege('yimatong_app','public.agency_authorizations',$1)", privilege
            )
        assert (
            await owner.fetchval(
                "SELECT to_regprocedure('public.renew_agency_authorization(uuid,uuid,uuid,jsonb,uuid,uuid,timestamptz)')"
            )
            is None
        )
        await owner.close()

        _alembic(migrated_pg_url, "upgrade", initial_revision)
        owner = await asyncpg.connect(owner_dsn)
        await owner.execute((BACKEND_DIR / "scripts" / "init_runtime_role.sql").read_text())
        assert await owner.fetchval(
            "SELECT has_table_privilege('yimatong_app','public.agency_authorizations','SELECT')"
        )
        assert await owner.fetchval(
            "SELECT has_function_privilege('acceptance_control',"
            "'public.agency_authorization_scope_is_canonical(json)','EXECUTE')"
        )
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not await owner.fetchval(
                "SELECT has_table_privilege('yimatong_app','public.agency_authorizations',$1)", privilege
            )
        await owner.close()
    finally:
        owner = await asyncpg.connect(owner_dsn)
        try:
            current_revision = await owner.fetchval("SELECT version_num FROM alembic_version")
        finally:
            await owner.close()
        if current_revision != initial_revision:
            _alembic(migrated_pg_url, "upgrade", initial_revision)
        owner = await asyncpg.connect(owner_dsn)
        try:
            await owner.executemany(
                "UPDATE legacy_pii_recovery_markers SET state=$4 "
                "WHERE source_revision=$1 AND source_table=$2 AND source_column=$3",
                [
                    (row["source_revision"], row["source_table"], row["source_column"], row["state"])
                    for row in pii_marker_states
                ],
            )
        finally:
            await owner.close()


async def test_authorization_migration_round_trip_restores_data_and_replay_acl(migrated_pg_url: str) -> None:
    async with _isolated_head_database(migrated_pg_url) as isolated_pg_url:
        await _assert_authorization_migration_round_trip(isolated_pg_url)


async def _assert_authorization_migration_downgrade_blocks_channel_receipts_without_head_drift(
    migrated_pg_url: str,
) -> None:
    """A durable channel receipt must block the deep authorization downgrade without moving head."""
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    receipt_id = uuid.uuid4()
    receipt_digest: str | None = None
    receipt_xmin: str | None = None
    try:
        catalog = await _seed_catalog(owner, "registry-channel-blocker")
        tenant_id = catalog["tenant"]
        initial_revision = await owner.fetchval("SELECT version_num FROM alembic_version")
        assert initial_revision == _sole_repository_head()
        actor_id = catalog["account"]
        await owner.execute(
            "INSERT INTO channel_action_receipts "
            "(id,tenant_id,action,idempotency_key,payload_digest,resource_type,resource_id,resource_version,"
            "status,actor_id,actor_tenant_id,audit_id,result,recorded_at) "
            "VALUES($1,$2,'acceptance_probe',$3,$4,'distributor',$5,1,'active',$6,$2,$7,'{}'::json,now())",
            receipt_id,
            tenant_id,
            f"receipt-{receipt_id}",
            "0" * 64,
            uuid.uuid4(),
            actor_id,
            uuid.uuid4(),
        )
        receipt_state = await owner.fetchrow(
            "SELECT xmin::text AS xmin,md5(row_to_json(receipt)::text) AS digest "
            "FROM channel_action_receipts receipt WHERE id=$1",
            receipt_id,
        )
        assert receipt_state is not None
        receipt_xmin, receipt_digest = receipt_state["xmin"], receipt_state["digest"]
    finally:
        await owner.close()

    try:
        blocked = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
        assert "channel action receipts are immutable facts" in f"{blocked.stdout}\n{blocked.stderr}"
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == initial_revision
            after_receipt = await owner.fetchrow(
                "SELECT xmin::text AS xmin,md5(row_to_json(receipt)::text) AS digest "
                "FROM channel_action_receipts receipt WHERE id=$1",
                receipt_id,
            )
            assert after_receipt is not None
            assert (after_receipt["xmin"], after_receipt["digest"]) == (receipt_xmin, receipt_digest)
            assert await owner.fetchval("SELECT to_regclass('public.risk_action_receipts') IS NOT NULL")
            assert await owner.fetchval(
                "SELECT to_regprocedure('public.evaluate_execute_scan_risk(uuid,uuid,uuid,uuid,text,jsonb)') IS NOT NULL"
            )
            assert await owner.fetchval(
                "SELECT to_regprocedure('public.claim_campaign_benefit(uuid,uuid,uuid,uuid,uuid,text,text,text,uuid,uuid,text)') IS NOT NULL"
            )
            assert await owner.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name='benefit_claims' AND column_name='request_digest')"
            )
        finally:
            await owner.close()
    finally:
        pass


async def test_authorization_migration_downgrade_blocks_channel_receipts_without_head_drift(
    migrated_pg_url: str,
) -> None:
    async with _isolated_head_database(migrated_pg_url) as isolated_pg_url:
        await _assert_authorization_migration_downgrade_blocks_channel_receipts_without_head_drift(isolated_pg_url)


async def _assert_deep_downgrade_blocks_diversion_facts_at_u7c_head(migrated_pg_url: str) -> None:
    """Crossing U7B with investigation history must fail before U7C commits."""
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    clue_id, history_id = uuid.uuid4(), uuid.uuid4()
    fact_xmin: str | None = None
    try:
        catalog = await _seed_catalog(owner, "registry-diversion-blocker")
        tenant_id = catalog["tenant"]
        initial_revision = await owner.fetchval("SELECT version_num FROM alembic_version")
        assert initial_revision == _sole_repository_head()
        await owner.execute(
            "INSERT INTO diversion_clues(id,tenant_id,public_id,pending_review,observation_count,version,"
            "investigation_status,resolved,created_at,updated_at) "
            "VALUES($1,$2,$3,false,1,1,'open',false,now(),now())",
            clue_id,
            tenant_id,
            f"D{uuid.uuid4().hex[:18]}",
        )
        await owner.execute(
            "INSERT INTO diversion_investigation_history(id,tenant_id,clue_id,from_status,to_status,reason,changed_at,created_at) "
            "VALUES($1,$2,$3,NULL,'open','acceptance immutable fact',now(),now())",
            history_id,
            tenant_id,
            clue_id,
        )
        fact_state = await owner.fetchrow(
            "SELECT xmin::text AS xmin,md5(row_to_json(history)::text) AS digest "
            "FROM diversion_investigation_history history WHERE id=$1",
            history_id,
        )
        assert fact_state is not None
        fact_xmin, fact_digest = fact_state["xmin"], fact_state["digest"]
    finally:
        await owner.close()
    try:
        blocked = _alembic(migrated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
        assert "immutable diversion investigation facts exist" in f"{blocked.stdout}\n{blocked.stderr}"
        owner = await asyncpg.connect(owner_dsn)
        try:
            assert await owner.fetchval("SELECT version_num FROM alembic_version") == initial_revision
            after_fact = await owner.fetchrow(
                "SELECT xmin::text AS xmin,md5(row_to_json(history)::text) AS digest "
                "FROM diversion_investigation_history history WHERE id=$1",
                history_id,
            )
            assert after_fact is not None
            assert (after_fact["xmin"], after_fact["digest"]) == (fact_xmin, fact_digest)
            assert await owner.fetchval(
                "SELECT to_regprocedure('public.claim_campaign_benefit(uuid,uuid,uuid,uuid,uuid,text,text,text,uuid,uuid,text)') IS NOT NULL"
            )
            assert await owner.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name='benefit_claims' AND column_name='request_digest')"
            )
        finally:
            await owner.close()
    finally:
        pass


async def test_deep_downgrade_blocks_diversion_facts_at_u7c_head(migrated_pg_url: str) -> None:
    async with _isolated_head_database(migrated_pg_url) as isolated_pg_url:
        await _assert_deep_downgrade_blocks_diversion_facts_at_u7c_head(isolated_pg_url)


async def _public_catalog_digest(conn: asyncpg.Connection) -> str:
    return await conn.fetchval(
        "SELECT md5(string_agg(item,E'\\n' ORDER BY item)) FROM ("
        "SELECT 'rel:'||class.relname||':'||class.relrowsecurity||':'||class.relforcerowsecurity item "
        "FROM pg_class class JOIN pg_namespace namespace ON namespace.oid=class.relnamespace "
        "WHERE namespace.nspname='public' AND class.relkind IN ('r','p') UNION ALL "
        "SELECT 'con:'||constraint_row.conname||':'||constraint_row.convalidated||':'||"
        "pg_get_constraintdef(constraint_row.oid) FROM pg_constraint constraint_row "
        "JOIN pg_namespace namespace ON namespace.oid=constraint_row.connamespace "
        "WHERE namespace.nspname='public' UNION ALL "
        "SELECT 'idx:'||class.relname||':'||index_row.indisvalid||':'||index_row.indisready||':'||"
        "pg_get_indexdef(index_row.indexrelid) FROM pg_index index_row "
        "JOIN pg_class class ON class.oid=index_row.indexrelid "
        "JOIN pg_namespace namespace ON namespace.oid=class.relnamespace "
        "WHERE namespace.nspname='public' UNION ALL "
        "SELECT 'proc:'||proc.oid::regprocedure::text||':'||md5(pg_get_functiondef(proc.oid)) "
        "FROM pg_proc proc JOIN pg_namespace namespace ON namespace.oid=proc.pronamespace "
        "WHERE namespace.nspname='public' UNION ALL "
        "SELECT 'policy:'||policy.polrelid::regclass::text||':'||policy.polname||':'||"
        "COALESCE(pg_get_expr(policy.polqual,policy.polrelid),'')||':'||"
        "COALESCE(pg_get_expr(policy.polwithcheck,policy.polrelid),'') FROM pg_policy policy "
        "JOIN pg_class class ON class.oid=policy.polrelid "
        "JOIN pg_namespace namespace ON namespace.oid=class.relnamespace WHERE namespace.nspname='public'"
        ") catalog"
    )


async def _fact_state(conn: asyncpg.Connection, table: str, row_id: uuid.UUID) -> tuple[str, str]:
    row = await conn.fetchrow(
        f"SELECT xmin::text AS xmin,md5(row_to_json(fact)::text) AS digest FROM {table} fact WHERE id=$1",
        row_id,
    )
    assert row is not None
    return row["xmin"], row["digest"]


async def test_u8d_head_preflights_newer_immutable_facts_before_any_deep_downgrade_drift(
    migrated_pg_url: str,
) -> None:
    async with _isolated_head_database(migrated_pg_url) as isolated_pg_url:
        owner_dsn = isolated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
        owner = await asyncpg.connect(owner_dsn)
        try:
            ids = await _seed_catalog(owner, "u8d-head-preflight")
            session_id = uuid.uuid4()
            await owner.execute(
                "INSERT INTO auth_sessions(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,"
                "created_at,updated_at) VALUES($1,$2,$3,0,$4,now()+interval '1 hour',now(),now())",
                session_id,
                ids["account"],
                ids["tenant"],
                uuid.uuid4().hex,
            )
            event_id, export_id, confirmation_id, recovery_id = (uuid7() for _ in range(4))
            now_at = datetime.now(UTC)
            await owner.execute(
                "INSERT INTO webhook_domain_events(id,tenant_id,event_type,payload,payload_digest,occurred_at) "
                "VALUES($1,$2,'scan.created','{}',$3,$4)",
                event_id,
                ids["tenant"],
                "0" * 64,
                now_at,
            )
            await owner.execute(
                "INSERT INTO export_logs(id,tenant_id,account_id,auth_session_id,export_type,file_name,content_type,"
                "row_count,status,reason,scope_snapshot,idempotency_key,payload_digest,authority_version,"
                "checksum_sha256,artifact_size_bytes,created_at,updated_at) VALUES($1,$2,$3,$4,'scan_stats_xlsx',"
                "'u8d-preflight.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',1,"
                "'prepared','u8d preflight','{}',$5,$6,1,$6,1,now(),now())",
                export_id,
                ids["tenant"],
                ids["account"],
                session_id,
                f"u8d-preflight:{export_id}",
                "1" * 64,
            )
            await owner.execute("SET session_replication_role='replica'")
            try:
                await owner.execute(
                    "INSERT INTO gmv_attribution_confirmations(id,tenant_id,attribution_id,external_order_id,"
                    "auth_session_id,actor_account_id,consumer_id,scan_event_id,scan_event_time,scan_received_at,"
                    "visitor_id,public_id,window_started_at,window_ends_at,attribution_window_hours,idempotency_key,"
                    "payload_digest,provenance_digest,confirmed_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$9,"
                    "'u8d-visitor','U8DPREFLIGHT',$9,$10,168,$11,$12,$12,$9)",
                    confirmation_id,
                    ids["tenant"],
                    uuid.uuid4(),
                    uuid.uuid4(),
                    session_id,
                    ids["account"],
                    uuid.uuid4(),
                    uuid.uuid4(),
                    now_at,
                    now_at + timedelta(hours=168),
                    f"u8d-preflight:{confirmation_id}",
                    "2" * 64,
                )
                await owner.execute(
                    "INSERT INTO external_order_ledger_recovery_markers(id,tenant_id,order_id,reason,"
                    "snapshot_digest,recorded_at) VALUES($1,$2,$3,'u8d_preflight',$4,$5)",
                    recovery_id,
                    ids["tenant"],
                    uuid.uuid4(),
                    "3" * 64,
                    now_at,
                )
            finally:
                await owner.execute("SET session_replication_role='origin'")

            blockers = (
                ("webhook_domain_events", event_id, "durable webhook event facts exist"),
                ("export_logs", export_id, "immutable prepared export facts exist"),
                (
                    "gmv_attribution_confirmations",
                    confirmation_id,
                    "immutable confirmed attribution evidence exists",
                ),
                (
                    "external_order_ledger_recovery_markers",
                    recovery_id,
                    "immutable external order ledger facts exist",
                ),
            )
            for table, row_id, message in blockers:
                catalog_before = await _public_catalog_digest(owner)
                fact_before = await _fact_state(owner, table, row_id)
                blocked = _alembic(isolated_pg_url, "downgrade", PARENT_REVISION, succeeds=False)
                assert message in f"{blocked.stdout}\n{blocked.stderr}"
                assert await owner.fetchval("SELECT version_num FROM alembic_version") == CURRENT_HEAD_REVISION
                assert await _public_catalog_digest(owner) == catalog_before
                assert await _fact_state(owner, table, row_id) == fact_before
                await owner.execute("SET session_replication_role='replica'")
                try:
                    await owner.execute(f"DELETE FROM {table} WHERE id=$1", row_id)
                finally:
                    await owner.execute("SET session_replication_role='origin'")

        finally:
            await owner.close()

    async with _isolated_head_database(migrated_pg_url) as clean_pg_url:
        clean_owner_dsn = clean_pg_url.replace("postgresql+asyncpg://", "postgresql://")
        clean_owner = await asyncpg.connect(clean_owner_dsn)
        try:
            clean_catalog = await _public_catalog_digest(clean_owner)
        finally:
            await clean_owner.close()

        _alembic(clean_pg_url, "downgrade", PARENT_REVISION)
        _alembic(clean_pg_url, "upgrade", "head")

        clean_owner = await asyncpg.connect(clean_owner_dsn)
        try:
            assert await clean_owner.fetchval("SELECT version_num FROM alembic_version") == CURRENT_HEAD_REVISION
            assert await _public_catalog_digest(clean_owner) == clean_catalog
        finally:
            await clean_owner.close()
        _alembic(clean_pg_url, "-x", "baseline_legacy_timestamp_nullability=true", "check")


async def test_registry_catalog_acl_and_control_boundary(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
    control_pg_conn: asyncpg.Connection,
) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        states = await owner.fetch(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, count(p.polname) AS policies "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "LEFT JOIN pg_policy p ON p.polrelid=c.oid "
            "WHERE n.nspname='public' AND c.relname=ANY($1::text[]) "
            "GROUP BY c.relname,c.relrowsecurity,c.relforcerowsecurity ORDER BY c.relname",
            list(
                (
                    *BUSINESS_GAP_TABLES,
                    *APPEND_ONLY_RUNTIME_TABLES,
                    *NO_DELETE_RUNTIME_TABLES,
                    *SENSITIVE_ARTIFACT_TABLES,
                    *DIVERSION_RESTRICTED_TABLES,
                    *RISK_RESTRICTED_TABLES,
                    *WECOM_RESTRICTED_TABLES,
                )
            ),
        )
        assert len(states) == (
            len(BUSINESS_GAP_TABLES)
            + len(APPEND_ONLY_RUNTIME_TABLES)
            + len(NO_DELETE_RUNTIME_TABLES)
            + len(SENSITIVE_ARTIFACT_TABLES)
            + len(DIVERSION_RESTRICTED_TABLES)
            + len(RISK_RESTRICTED_TABLES)
            + len(WECOM_RESTRICTED_TABLES)
        )
        assert all(row["relrowsecurity"] and row["relforcerowsecurity"] and row["policies"] for row in states)

        orm_root_rows = await owner.fetch(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind IN ('r','p') "
            "AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid=c.oid) "
            "AND c.relname <> ALL($1::text[]) ORDER BY c.relname",
            list(MIGRATION_ONLY_TABLES),
        )
        catalog_root_tables = {row["relname"] for row in orm_root_rows}
        metadata_root_tables = {
            table.name for table in Base.metadata.tables.values() if table.schema in (None, "public")
        } - set(MIGRATION_ONLY_TABLES)
        assert {"gmv_attribution_confirmations", "webhook_domain_events"}.issubset(metadata_root_tables)
        assert catalog_root_tables == metadata_root_tables
        migration_only = await owner.fetch(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind='r' AND c.relname=ANY($1::text[])",
            list(MIGRATION_ONLY_TABLES),
        )
        assert {row["relname"] for row in migration_only} == set(MIGRATION_ONLY_TABLES)
        for table in MIGRATION_ONLY_TABLES:
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM information_schema.role_table_grants "
                    "WHERE table_schema='public' AND table_name=$1 "
                    "AND grantee IN ('PUBLIC','yimatong_app')",
                    table,
                )
                == 0
            )

        api_key_columns = await owner.fetch(
            "SELECT column_name,data_type,character_maximum_length,is_nullable "
            "FROM information_schema.columns WHERE table_schema='public' AND table_name='api_keys' "
            "AND column_name=ANY($1::text[]) ORDER BY column_name",
            [
                "created_by",
                "idempotency_key_digest",
                "key",
                "key_digest",
                "key_prefix",
                "permanent_reason",
                "request_fingerprint",
                "revoked_at",
                "rotated_from_id",
            ],
        )
        assert {
            row["column_name"]: (row["data_type"], row["character_maximum_length"], row["is_nullable"])
            for row in api_key_columns
        } == {
            "created_by": ("uuid", None, "YES"),
            "idempotency_key_digest": ("character varying", 64, "YES"),
            "key_digest": ("character varying", 64, "NO"),
            "key_prefix": ("character varying", 20, "NO"),
            "permanent_reason": ("character varying", 200, "YES"),
            "request_fingerprint": ("character varying", 64, "YES"),
            "revoked_at": ("timestamp with time zone", None, "YES"),
            "rotated_from_id": ("uuid", None, "YES"),
        }
        api_key_relation = await owner.fetchrow(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='public.api_keys'::regclass"
        )
        assert api_key_relation["relrowsecurity"] and api_key_relation["relforcerowsecurity"]
        assert await owner.fetchval("SELECT count(*) FROM pg_policy WHERE polrelid='public.api_keys'::regclass") > 0
        assert {
            row["conname"]
            for row in await owner.fetch("SELECT conname FROM pg_constraint WHERE conrelid='public.api_keys'::regclass")
        }.issuperset(
            {
                "ck_api_keys_digest_format",
                "ck_api_keys_expiry_after_creation",
                "ck_api_keys_idempotency_contract",
                "ck_api_keys_permanent_reason",
                "ck_api_keys_post_contract_expiry_max",
                "ck_api_keys_prefix_format",
                "ck_api_keys_revocation_state",
                "ck_api_keys_role_permissions",
                "fk_api_keys_tenant",
                "fk_api_keys_tenant_creator",
                "fk_api_keys_tenant_rotated_from",
                "uq_api_keys_tenant_id_id",
            }
        )
        api_key_indexes = {
            row["indexname"]: row["indexdef"]
            for row in await owner.fetch(
                "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='api_keys'"
            )
        }
        assert "UNIQUE" in api_key_indexes["uq_api_keys_key_digest"]
        assert "(key_digest)" in api_key_indexes["uq_api_keys_key_digest"]
        assert "UNIQUE" in api_key_indexes["uq_api_keys_tenant_creator_idempotency"]
        assert (
            "(tenant_id, created_by, idempotency_key_digest)"
            in api_key_indexes["uq_api_keys_tenant_creator_idempotency"]
        )
        assert "WHERE (idempotency_key_digest IS NOT NULL)" in api_key_indexes["uq_api_keys_tenant_creator_idempotency"]
        assert "(tenant_id, expires_at)" in api_key_indexes["ix_api_keys_tenant_active_expiry"]
        assert (
            "WHERE ((revoked = false) AND (revoked_at IS NULL))" in api_key_indexes["ix_api_keys_tenant_active_expiry"]
        )

        function_contract = {
            "resolve_active_api_key": (
                "text",
                "TABLE(api_key_id uuid, tenant_id uuid, role text, permissions json, expires_at timestamp with time zone)",
            ),
            "lock_active_api_key": ("uuid, uuid", "boolean"),
            "issue_api_key": (
                "uuid, uuid, uuid, uuid, text, text, text, text, timestamp with time zone, text, text, bytea, text, uuid",
                "TABLE(api_key_id uuid, replayed boolean, escrow_ciphertext bytea)",
            ),
            "rotate_api_key": (
                "uuid, uuid, uuid, uuid, uuid, text, text, text, text, timestamp with time zone, text, text, bytea, uuid",
                "TABLE(api_key_id uuid, replayed boolean, escrow_ciphertext bytea)",
            ),
            "revoke_api_key": ("uuid, uuid, uuid, uuid, uuid", "uuid"),
        }
        functions = await owner.fetch(
            "SELECT p.proname,oidvectortypes(p.proargtypes) AS arguments,"
            "pg_get_function_result(p.oid) AS result,p.prosecdef,p.proconfig,p.oid::regprocedure::text AS signature "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.proname=ANY($1::text[]) ORDER BY p.proname",
            list(function_contract),
        )
        assert {row["proname"] for row in functions} == set(function_contract)
        for row in functions:
            assert (row["arguments"], row["result"]) == function_contract[row["proname"]]
            assert row["prosecdef"]
            assert row["proconfig"] == ["search_path=pg_catalog, public"]
            assert await owner.fetchval("SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", row["signature"])
            assert not await owner.fetchval("SELECT has_function_privilege('public',$1,'EXECUTE')", row["signature"])

        for helper_signature in (
            "public.api_key_permissions_for_role(text)",
            "public.assert_api_key_actor(uuid,uuid,uuid,boolean)",
            "public.append_api_key_audit(uuid,uuid,uuid,text,uuid,jsonb)",
            "public.guard_api_key_row()",
        ):
            assert not await owner.fetchval(
                "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", helper_signature
            )
            assert not await owner.fetchval("SELECT has_function_privilege('public',$1,'EXECUTE')", helper_signature)
        for signature in CHANNEL_PUBLIC_FUNCTIONS:
            assert await owner.fetchval(
                "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", f"public.{signature}"
            )
            assert not await owner.fetchval(
                "SELECT has_function_privilege('public',$1,'EXECUTE')", f"public.{signature}"
            )
        for signature in (
            "public.authorize_channel_actor(uuid,uuid,text,boolean)",
            "public.validate_channel_target(uuid,text,uuid)",
            "public.mutate_channel_node(uuid,uuid,uuid,text,text,uuid,bigint,text,jsonb)",
        ):
            assert not await owner.fetchval("SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", signature)
            assert not await owner.fetchval("SELECT has_function_privilege('public',$1,'EXECUTE')", signature)
        for signature in PILOT_RUNTIME_FUNCTIONS:
            qualified = f"public.{signature}"
            assert await owner.fetchval("SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", qualified)
            assert not await owner.fetchval("SELECT has_function_privilege('public',$1,'EXECUTE')", qualified)
        for signature in PILOT_CONTROL_FUNCTIONS:
            qualified = f"public.{signature}"
            assert not await owner.fetchval("SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", qualified)
            assert not await owner.fetchval("SELECT has_function_privilege('public',$1,'EXECUTE')", qualified)
    finally:
        await owner.close()

    for table in BUSINESS_GAP_TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            granted = await runtime_pg_conn.fetchval(
                "SELECT has_table_privilege('yimatong_app', $1, $2)", f"public.{table}", privilege
            )
            expected = (
                privilege == "SELECT"
                if table == "code_allocations" or table in PILOT_READ_ONLY_TABLES
                else table != "coupon_codes" or privilege in {"SELECT", "INSERT"}
            )
            assert granted is expected
    for table in (
        *CHANNEL_RESTRICTED_TABLES,
        *DIVERSION_RESTRICTED_TABLES,
        *RISK_RESTRICTED_TABLES,
        *WECOM_RESTRICTED_TABLES,
    ):
        assert await runtime_pg_conn.fetchval(
            "SELECT has_table_privilege('yimatong_app', $1, 'SELECT')", f"public.{table}"
        )
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not await runtime_pg_conn.fetchval(
                "SELECT has_table_privilege('yimatong_app', $1, $2)", f"public.{table}", privilege
            )
    for table in NO_DELETE_RUNTIME_TABLES:
        direct_write_privileges = ("SELECT", "INSERT") if table == "code_items" else ("SELECT", "INSERT", "UPDATE")
        for privilege in direct_write_privileges:
            assert await runtime_pg_conn.fetchval(
                "SELECT has_table_privilege('yimatong_app', $1, $2)", f"public.{table}", privilege
            )
        if table == "code_items":
            assert not await runtime_pg_conn.fetchval(
                "SELECT has_table_privilege('yimatong_app', $1, 'UPDATE')", f"public.{table}"
            )
        for privilege in ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not await runtime_pg_conn.fetchval(
                "SELECT has_table_privilege('yimatong_app', $1, $2)", f"public.{table}", privilege
            )
    assert not await runtime_pg_conn.fetchval(
        "SELECT has_table_privilege('yimatong_app','public.export_logs','SELECT')"
    )
    for column in (
        "id",
        "tenant_id",
        "account_id",
        "auth_session_id",
        "export_type",
        "resource_id",
        "file_name",
        "content_type",
        "row_count",
        "status",
        "reason",
        "scope_snapshot",
        "idempotency_key",
        "payload_digest",
        "authority_version",
        "code_batch_id",
        "manifest_version",
        "checksum_sha256",
        "artifact_size_bytes",
        "created_at",
        "updated_at",
    ):
        assert await runtime_pg_conn.fetchval(
            "SELECT has_column_privilege('yimatong_app','public.export_logs',$1,'SELECT')",
            column,
        )
    for column in ("artifact_ciphertext", "artifact_nonce", "artifact_scheme", "artifact_key_id"):
        assert not await runtime_pg_conn.fetchval(
            "SELECT has_column_privilege('yimatong_app','public.export_logs',$1,'SELECT')",
            column,
        )
    for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert not await runtime_pg_conn.fetchval(
            "SELECT has_table_privilege('yimatong_app','public.export_logs',$1)", privilege
        )
    assert await runtime_pg_conn.fetchval(
        "SELECT has_function_privilege('yimatong_app','public.get_code_export_artifact(uuid,uuid,uuid)','EXECUTE')"
    )
    assert not await runtime_pg_conn.fetchval(
        "SELECT has_function_privilege('public','public.get_code_export_artifact(uuid,uuid,uuid)','EXECUTE')"
    )
    assert await runtime_pg_conn.fetchval(
        "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')",
        "public.record_prepared_export(uuid,uuid,uuid,text,text,jsonb,text,text,text,integer,text,bigint,"
        "uuid,uuid,integer,bytea,bytea,text,text)",
    )
    assert not await runtime_pg_conn.fetchval(
        "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')",
        "public.record_seed_code_export_manifest(uuid,uuid,uuid,text,integer,text,bigint,bytea,bytea,text,text)",
    )
    for table in CONTROL_TABLES:
        assert not await runtime_pg_conn.fetchval(
            "SELECT has_table_privilege('yimatong_app', $1, 'SELECT')", f"public.{table}"
        )
    assert await runtime_pg_conn.fetchval("SELECT has_table_privilege('yimatong_app','public.api_keys','SELECT')")
    for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert not await runtime_pg_conn.fetchval(
            "SELECT has_table_privilege('yimatong_app','public.api_keys',$1)", privilege
        )
    assert await runtime_pg_conn.fetchval(
        "SELECT has_table_privilege('yimatong_app', 'public.agency_authorizations', 'SELECT')"
    )
    for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert not await runtime_pg_conn.fetchval(
            "SELECT has_table_privilege('yimatong_app', 'public.agency_authorizations', $1)", privilege
        )
    for function_signature in (
        "public.renew_agency_authorization(uuid,uuid,uuid,jsonb,uuid,uuid,timestamptz)",
        "public.revoke_agency_authorization(uuid,uuid,uuid,uuid)",
        "public.append_authenticated_audit_event(uuid,uuid,text,text,text,jsonb)",
        "public.record_public_code_scan(uuid,text,uuid,text,text,text,text)",
        "public.transition_code_item_lifecycle(uuid,uuid,uuid,uuid,text,text)",
        "public.transition_code_batch_lifecycle(uuid,uuid,uuid,uuid,text,text)",
        "public.freeze_code_item_for_risk(uuid,uuid,uuid,uuid,uuid)",
        "public.recall_production_batch(uuid,uuid,uuid,uuid,text)",
    ):
        assert await runtime_pg_conn.fetchval(
            "SELECT has_function_privilege('yimatong_app', $1, 'EXECUTE')", function_signature
        )
        assert not await runtime_pg_conn.fetchval(
            "SELECT has_function_privilege('public', $1, 'EXECUTE')", function_signature
        )
    assert await runtime_pg_conn.fetchval(
        "SELECT has_table_privilege('yimatong_app','public.production_batches','SELECT')"
    )
    for privilege in ("INSERT", "DELETE"):
        assert await runtime_pg_conn.fetchval(
            "SELECT has_table_privilege('yimatong_app','public.production_batches',$1)",
            privilege,
        )
    for privilege in ("UPDATE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert not await runtime_pg_conn.fetchval(
            "SELECT has_table_privilege('yimatong_app','public.production_batches',$1)",
            privilege,
        )
    for column in (
        "product_id",
        "sku_id",
        "batch_code",
        "production_date",
        "expiry_date",
        "origin",
        "updated_at",
        "external_id",
        "source_system",
    ):
        assert await runtime_pg_conn.fetchval(
            "SELECT has_column_privilege('yimatong_app','public.production_batches',$1,'UPDATE')",
            column,
        )
    for column in ("tenant_id", "status", "recall_reason", "recalled_at", "recalled_by"):
        assert not await runtime_pg_conn.fetchval(
            "SELECT has_column_privilege('yimatong_app','public.production_batches',$1,'UPDATE')",
            column,
        )
    assert not await runtime_pg_conn.fetchval(
        "SELECT has_function_privilege('yimatong_app','public.mark_code_item_first_scanned(uuid,text)','EXECUTE')"
    )
    assert not await runtime_pg_conn.fetchval(
        "SELECT has_function_privilege("
        "'yimatong_app','public.append_authenticated_audit_event_lifecycle_internal"
        "(uuid,uuid,text,text,text,jsonb)','EXECUTE')"
    )
    assert not await runtime_pg_conn.fetchval(
        "SELECT has_function_privilege('yimatong_app','public.authorize_code_lifecycle_actor(uuid,uuid)','EXECUTE')"
    )
    assert not await runtime_pg_conn.fetchval(
        "SELECT has_function_privilege('public','public.authorize_code_lifecycle_actor(uuid,uuid)','EXECUTE')"
    )
    for table in APPEND_ONLY_RUNTIME_TABLES:
        for privilege in ("SELECT", "INSERT"):
            assert await runtime_pg_conn.fetchval(
                "SELECT has_table_privilege('yimatong_app', $1, $2)", f"public.{table}", privilege
            )
        for privilege in ("UPDATE", "DELETE"):
            assert not await runtime_pg_conn.fetchval(
                "SELECT has_table_privilege('yimatong_app', $1, $2)", f"public.{table}", privilege
            )
    for table in READ_ONLY_GLOBAL_TABLES:
        assert await runtime_pg_conn.fetchval(
            "SELECT has_table_privilege('yimatong_app', $1, 'SELECT')", f"public.{table}"
        )
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not await runtime_pg_conn.fetchval(
                "SELECT has_table_privilege('yimatong_app', $1, $2)", f"public.{table}", privilege
            )

    await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', '', true)")
    await runtime_pg_conn.execute("SELECT set_config('app.bypass_rls', 'true', true)")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await runtime_pg_conn.fetchval("SELECT count(*) FROM platform_configs")

    key = f"acceptance-{uuid.uuid4()}"
    await control_pg_conn.execute(
        "INSERT INTO platform_configs (id,key,value,updated_at) VALUES ($1,$2,'{}'::json,now())",
        uuid.uuid4(),
        key,
    )
    assert await control_pg_conn.fetchval("SELECT count(*) FROM platform_configs WHERE key=$1", key) == 1


async def test_audit_ledger_runtime_read_append_and_agency_boundary(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
    control_pg_conn: asyncpg.Connection,
) -> None:
    summary = await seed_baseline(migrated_pg_url)
    agency_id = uuid.UUID(summary["baseline_tenant"]["id"])
    client_id = uuid.UUID(summary["control_tenant"]["id"])
    authorization_id = uuid.uuid4()
    agency_session_id = uuid.uuid4()
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await owner.execute("UPDATE tenants SET tenant_type='agency' WHERE id=$1", agency_id)
        agency_account_id = await owner.fetchval("SELECT id FROM accounts WHERE tenant_id=$1 LIMIT 1", agency_id)
        client_account_id = await owner.fetchval("SELECT id FROM accounts WHERE tenant_id=$1 LIMIT 1", client_id)
        agency_auth_version = await owner.fetchval("SELECT auth_version FROM accounts WHERE id=$1", agency_account_id)
        await owner.execute(
            "INSERT INTO auth_sessions "
            "(id,account_id,tenant_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES ($1,$2,$3,$4,$5,now()+interval '1 hour',now(),now())",
            agency_session_id,
            agency_account_id,
            agency_id,
            agency_auth_version,
            uuid.uuid4().hex,
        )
        await owner.execute(
            "INSERT INTO agency_authorizations "
            "(id,agency_tenant_id,client_tenant_id,scope,status,granted_by,granted_at,expires_at,created_at,updated_at) "
            "VALUES ($1,$2,$3,'[\"pages\"]'::json,'active',$4,now(),now()+interval '1 hour',now(),now())",
            authorization_id,
            agency_id,
            client_id,
            client_account_id,
        )
        await owner.executemany(
            "INSERT INTO platform_audit_log "
            "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
            "VALUES ($1,'owner',$2,$3,'acceptance',now(),now(),now())",
            [
                (uuid.uuid4(), str(agency_id), "agency-existing"),
                (uuid.uuid4(), str(client_id), "client-existing"),
            ],
        )
    finally:
        await owner.close()

    await runtime_pg_conn.execute("SELECT set_config('app.bypass_rls', 'false', true)")
    await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(agency_id))
    assert await runtime_pg_conn.fetchval("SELECT count(*) FROM platform_audit_log WHERE action='agency-existing'") == 1
    assert await runtime_pg_conn.fetchval("SELECT count(*) FROM platform_audit_log WHERE action='client-existing'") == 0

    await runtime_pg_conn.execute(
        "INSERT INTO platform_audit_log "
        "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
        "VALUES ($1,'agency',$2,'agency-own','acceptance',now(),now(),now())",
        uuid.uuid4(),
        str(agency_id),
    )
    await _assert_insert_denied(
        runtime_pg_conn,
        "INSERT INTO platform_audit_log "
        "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
        "VALUES ($1,'forged-agency',$2,'page_published','acceptance',now(),now(),now())",
        uuid.uuid4(),
        str(client_id),
    )
    audit_id = uuid.uuid4()
    runtime_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "yimatong:yimatong@", "yimatong_app:yimatong_app@"
    )
    function_conn = await asyncpg.connect(runtime_dsn)
    function_tx = function_conn.transaction()
    await function_tx.start()
    await function_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(agency_id))
    resolved = await function_conn.fetchrow(
        "SELECT * FROM append_authenticated_audit_event($1,$2,$3,$4,$5,$6::jsonb)",
        audit_id,
        agency_session_id,
        str(client_id),
        "page_published",
        "page:test",
        '{"operator_id":"forged"}',
    )
    await function_tx.commit()
    await function_conn.close()
    assert resolved["resolved_operator_id"] == str(agency_account_id)
    assert await control_pg_conn.fetchval("SELECT operator_id FROM platform_audit_log WHERE id=$1", audit_id) == str(
        agency_account_id
    )

    function_conn = await asyncpg.connect(runtime_dsn)
    savepoint = function_conn.transaction()
    await savepoint.start()
    await function_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(agency_id))
    with pytest.raises(asyncpg.RaiseError, match="audit auth session is not live"):
        await function_conn.fetchrow(
            "SELECT * FROM append_authenticated_audit_event($1,$2,$3,$4,$5,$6::jsonb)",
            uuid.uuid4(),
            uuid.uuid4(),
            str(client_id),
            "page_published",
            "page:test",
            "{}",
        )
    await savepoint.rollback()
    await function_conn.close()

    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        revoked = await owner.fetchrow(
            "WITH effective AS ("
            "SELECT id,GREATEST(clock_timestamp(),granted_at) AS revoked_at "
            "FROM agency_authorizations WHERE id=$1 FOR UPDATE"
            ") UPDATE agency_authorizations AS authz "
            "SET status='revoked',revoked_at=effective.revoked_at,updated_at=effective.revoked_at "
            "FROM effective WHERE authz.id=effective.id "
            "RETURNING authz.status,authz.granted_at,authz.revoked_at,authz.updated_at",
            authorization_id,
        )
        assert revoked is not None
        assert revoked["status"] == "revoked"
        assert revoked["revoked_at"] >= revoked["granted_at"]
        assert revoked["updated_at"] == revoked["revoked_at"]

        function_conn = await asyncpg.connect(runtime_dsn)
        try:
            savepoint = function_conn.transaction()
            await savepoint.start()
            await function_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(agency_id))
            with pytest.raises(asyncpg.RaiseError, match="lacks a live scoped authorization"):
                await function_conn.fetchrow(
                    "SELECT * FROM append_authenticated_audit_event($1,$2,$3,$4,$5,$6::jsonb)",
                    uuid.uuid4(),
                    agency_session_id,
                    str(client_id),
                    "page_published",
                    "page:test",
                    "{}",
                )
            await savepoint.rollback()
        finally:
            await function_conn.close()

        await _assert_insert_denied(
            runtime_pg_conn,
            "INSERT INTO platform_audit_log "
            "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
            "VALUES ($1,'agency',$2,'unauthorized','acceptance',now(),now(),now())",
            uuid.uuid4(),
            str(uuid.uuid4()),
        )
        await _assert_statement_privilege_denied(runtime_pg_conn, "UPDATE platform_audit_log SET resource='tampered'")
        await _assert_statement_privilege_denied(runtime_pg_conn, "DELETE FROM platform_audit_log")
        await _assert_statement_privilege_denied(runtime_pg_conn, "TRUNCATE platform_audit_log")

        await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(client_id))
        assert await runtime_pg_conn.fetchval("SELECT count(*) FROM platform_audit_log WHERE id=$1", audit_id) == 1
        control_log_id = uuid.uuid4()
        await control_pg_conn.execute(
            "INSERT INTO platform_audit_log "
            "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
            "VALUES ($1,'platform-admin','platform','control-write','acceptance',now(),now(),now())",
            control_log_id,
        )
        assert (
            await control_pg_conn.fetchval("SELECT count(*) FROM platform_audit_log WHERE id=$1", control_log_id) == 1
        )
        assert (
            await control_pg_conn.fetchval("SELECT count(*) FROM platform_audit_log WHERE action='client-existing'")
            == 1
        )

        await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', '', true)")
        await runtime_pg_conn.execute("SELECT set_config('app.bypass_rls', 'true', true)")
        assert await runtime_pg_conn.fetchval("SELECT count(*) FROM platform_audit_log") == 0
        await _assert_insert_denied(
            runtime_pg_conn,
            "INSERT INTO platform_audit_log "
            "(id,operator_id,target_tenant_id,action,resource,timestamp,created_at,updated_at) "
            "VALUES ($1,'runtime','platform','forged-bypass','acceptance',now(),now(),now())",
            uuid.uuid4(),
        )
    finally:
        await owner.execute("DELETE FROM agency_authorizations WHERE id=$1", authorization_id)
        await owner.execute("DELETE FROM auth_sessions WHERE id=$1", agency_session_id)
        await owner.execute("UPDATE tenants SET tenant_type='brand' WHERE id=$1", agency_id)
        await owner.close()


async def test_pilot_relations_runtime_read_only_and_isolation(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    foreign_milestone_id = uuid.uuid4()
    foreign_retrospective_id = uuid.uuid4()
    foreign_correction_id = uuid.uuid4()
    try:
        for tenant, suffix in ((tenant_a, "a"), (tenant_b, "b")):
            await owner.execute(
                "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
                "VALUES($1,$2,$3,'active','free','brand',now(),now())",
                tenant,
                f"U09 registry {suffix}",
                f"u09-registry-{tenant.hex[-12:]}",
            )
        await owner.execute(
            "INSERT INTO pilot_milestones "
            "(id,tenant_id,milestone_type,achieved_at,source,fact_digest,authority_version,created_at,updated_at) "
            "VALUES ($1,$2,'onboarding',now(),'foreign',$3,0,now(),now())",
            foreign_milestone_id,
            tenant_b,
            "0" * 64,
        )
        await owner.execute(
            "INSERT INTO retrospectives "
            "(id,tenant_id,period_day,window_start,window_end,next_review_date,status,"
            "scorecard_snapshot,snapshot_digest,version,authority_version,actions,created_at,updated_at) "
            "VALUES ($1,$2,7,now(),now()+interval '7 days',current_date,'pending','{}'::json,$3,1,0,"
            "'[]'::json,now(),now())",
            foreign_retrospective_id,
            tenant_b,
            "0" * 64,
        )
        await owner.execute(
            "INSERT INTO pilot_milestone_corrections "
            "(id,tenant_id,milestone_id,milestone_type,corrected_at,source,reason,request_id,actor_type,"
            "actor_principal,idempotency_key,payload_digest,authority_version,created_at) "
            "VALUES ($1,$2,$3,'onboarding',now(),'foreign','foreign',$1,'legacy','legacy-system',$4,$5,0,now())",
            foreign_correction_id,
            tenant_b,
            foreign_milestone_id,
            f"legacy:{foreign_correction_id}",
            "0" * 64,
        )
    finally:
        await owner.close()

    for bypass_value in ("false", "true"):
        await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', '', true)")
        await runtime_pg_conn.execute("SELECT set_config('app.bypass_rls', $1, true)", bypass_value)
        for table in ("pilot_milestones", "retrospectives", "pilot_milestone_corrections"):
            assert await runtime_pg_conn.fetchval(f"SELECT count(*) FROM {table}") == 0

    await runtime_pg_conn.execute("SELECT set_config('app.bypass_rls', 'false', true)")
    await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_a))

    for table, foreign_id in (
        ("pilot_milestones", foreign_milestone_id),
        ("retrospectives", foreign_retrospective_id),
        ("pilot_milestone_corrections", foreign_correction_id),
    ):
        assert await runtime_pg_conn.fetchval(f"SELECT count(*) FROM {table} WHERE id=$1", foreign_id) == 0
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not await runtime_pg_conn.fetchval(
                "SELECT has_table_privilege('yimatong_app',$1,$2)", f"public.{table}", privilege
            )


async def test_repaired_business_relations_runtime_crud_and_isolation(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
) -> None:
    summary = await seed_baseline(migrated_pg_url)
    tenant_a = uuid.UUID(summary["baseline_tenant"]["id"])
    tenant_b = uuid.UUID(summary["control_tenant"]["id"])
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    allocation_fixture_ids: tuple[uuid.UUID, uuid.UUID] | None = None
    distributor_fixture_ids: tuple[uuid.UUID, uuid.UUID] | None = None
    owned_auth_session_ids: list[uuid.UUID] = []

    def ids() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        return uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    try:
        org_a, org_b1, org_b2 = ids()
        account_a, account_b1, account_b2 = ids()
        role_a, role_b1, role_b2 = ids()
        permission_a, permission_b1, permission_b2 = ids()
        pool_a, pool_b1, _pool_b2 = ids()
        regional_a, regional_b1, regional_b2 = ids()
        for org_id, tenant_id, label in (
            (org_a, tenant_a, "a"),
            (org_b1, tenant_b, "b1"),
            (org_b2, tenant_b, "b2"),
        ):
            await owner.execute(
                "INSERT INTO organizations (id,tenant_id,name,created_at,updated_at) VALUES ($1,$2,$3,now(),now())",
                org_id,
                tenant_id,
                f"registry-{label}",
            )
        for account_id, tenant_id, org_id, label in (
            (account_a, tenant_a, org_a, "a"),
            (account_b1, tenant_b, org_b1, "b1"),
            (account_b2, tenant_b, org_b2, "b2"),
        ):
            await owner.execute(
                "INSERT INTO accounts (id,tenant_id,organization_id,email,hashed_password,name,"
                "failed_login_attempts,created_at,updated_at) VALUES ($1,$2,$3,$4,'x',$5,0,now(),now())",
                account_id,
                tenant_id,
                org_id,
                f"registry-{label}-{account_id.hex[:8]}@test.local",
                label,
            )
        for role_id, permission_id, tenant_id, label in (
            (role_a, permission_a, tenant_a, "a"),
            (role_b1, permission_b1, tenant_b, "b1"),
            (role_b2, permission_b2, tenant_b, "b2"),
        ):
            await owner.execute(
                "INSERT INTO roles (id,tenant_id,name,created_at,updated_at) VALUES ($1,$2,$3,now(),now())",
                role_id,
                tenant_id,
                f"registry-{label}-{role_id.hex[:6]}",
            )
            await owner.execute(
                "INSERT INTO permissions (id,tenant_id,code,created_at,updated_at) VALUES ($1,$2,$3,now(),now())",
                permission_id,
                tenant_id,
                f"registry:{label}:{permission_id.hex[:6]}",
            )
        for pool_id, tenant_id, label in ((pool_a, tenant_a, "a"), (pool_b1, tenant_b, "b")):
            await owner.execute(
                "INSERT INTO coupon_pools (id,tenant_id,name,total_codes,remaining,created_at,updated_at) "
                "VALUES ($1,$2,$3,10,10,now(),now())",
                pool_id,
                tenant_id,
                label,
            )
        for regional_id, tenant_id, label in (
            (regional_a, tenant_a, "a"),
            (regional_b1, tenant_b, "b1"),
            (regional_b2, tenant_b, "b2"),
        ):
            await owner.execute(
                "INSERT INTO regional_orgs (id,tenant_id,name,org_type,config,created_at,updated_at) "
                "VALUES ($1,$2,$3,'association','{}'::json,now(),now())",
                regional_id,
                tenant_id,
                label,
            )
        batch_a = await owner.fetchval("SELECT id FROM code_batches WHERE tenant_id=$1 ORDER BY id LIMIT 1", tenant_a)
        batch_b = await owner.fetchval("SELECT id FROM code_batches WHERE tenant_id=$1 ORDER BY id LIMIT 1", tenant_b)
        assert batch_a and batch_b

        allocation_a, allocation_b = uuid.uuid4(), uuid.uuid4()
        distributor_a, distributor_b = uuid.uuid4(), uuid.uuid4()
        allocation_fixture_ids = (allocation_a, allocation_b)
        distributor_fixture_ids = (distributor_a, distributor_b)
        await owner.executemany(
            "INSERT INTO distributors "
            "(id,tenant_id,name,code,status,contact_phone_recovery_state,version,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,'active','absent',1,now(),now())",
            [
                (distributor_a, tenant_a, "registry allocation a", f"REG-A-{distributor_a.hex[:8]}"),
                (distributor_b, tenant_b, "registry allocation b", f"REG-B-{distributor_b.hex[:8]}"),
            ],
        )
        # This registry test proves direct runtime DML is closed, so its owner-only
        # visibility fixtures intentionally bypass the business function. Keep the
        # rows fully shaped like U07A authority output; no legacy partial insert.
        await owner.executemany(
            "INSERT INTO code_allocations "
            "(id,tenant_id,batch_id,distributor_id,quantity,effective_from,version,change_reason,"
            "allocation_root_id,target_type,target_id,action,status,actor_id,actor_tenant_id,audit_id) "
            "VALUES($1,$2,$3,$4,1,now(),1,'registry visibility fixture',$1,'distributor',$4,"
            "'allocate','active',$5,$2,$6)",
            [
                (allocation_a, tenant_a, batch_a, distributor_a, account_a, uuid.uuid4()),
                (allocation_b, tenant_b, batch_b, distributor_b, account_b1, uuid.uuid4()),
            ],
        )

        cases = [
            (
                "ai_generations",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO ai_generations (id,tenant_id,type,input_snapshot) VALUES ($1,$2,'copywriting','{}'::jsonb)",
                (uuid.uuid4(), tenant_a),
                (uuid.uuid4(), tenant_b),
                "status='accepted'",
            ),
            (
                "gmv_daily_stats",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO gmv_daily_stats (id,tenant_id,stat_date,attributed_gmv,attributed_orders,scan_count,scan_uv) "
                "VALUES ($1,$2,now(),0,0,0,0)",
                (uuid.uuid4(), tenant_a),
                (uuid.uuid4(), tenant_b),
                "scan_count=2",
            ),
            (
                "regional_code_rules",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO regional_code_rules (id,org_id,rule_name,pattern,prefix) VALUES ($1,$2,$3,'*','')",
                (uuid.uuid4(), regional_a, f"a-{uuid.uuid4()}"),
                (uuid.uuid4(), regional_b1, f"b-{uuid.uuid4()}"),
                "prefix='U'",
            ),
            (
                "regional_templates",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO regional_templates (id,org_id,name,config) VALUES ($1,$2,$3,'{}'::json)",
                (uuid.uuid4(), regional_a, f"a-{uuid.uuid4()}"),
                (uuid.uuid4(), regional_b1, f"b-{uuid.uuid4()}"),
                "name='updated'",
            ),
            (
                "sync_mappings",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO sync_mappings (id,tenant_id,local_entity_type,local_entity_id,source_system,external_id,sync_direction) "
                "VALUES ($1,$2,'consumer_profile',$3,'test',$4,'push_only')",
                (uuid.uuid4(), tenant_a, uuid.uuid4(), f"a-{uuid.uuid4()}"),
                (uuid.uuid4(), tenant_b, uuid.uuid4(), f"b-{uuid.uuid4()}"),
                "external_phone_hash='updated'",
            ),
            (
                "tenant_domains",
                "id",
                (uuid.uuid4(),),
                "INSERT INTO tenant_domains (id,tenant_id,domain,ssl_status,verified,cname_target) "
                "VALUES ($1,$2,$3,'pending',false,'cname.test')",
                (uuid.uuid4(), tenant_a, f"a-{uuid.uuid4()}.test"),
                (uuid.uuid4(), tenant_b, f"b-{uuid.uuid4()}.test"),
                "verified=true",
            ),
        ]

        # Seed one foreign row per simple relation as the control owner.
        foreign_keys: dict[str, uuid.UUID] = {}
        for table, _key_col, _unused, insert_sql, _own_args, foreign_args, _update in cases:
            await owner.execute(insert_sql, *foreign_args)
            foreign_keys[table] = foreign_args[0]

        diversion_facts: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]] = {}
        risk_facts: dict[uuid.UUID, dict[str, uuid.UUID]] = {}
        for tenant_id, ip_hash, detected_city in (
            (tenant_a, "a" * 64, "上海"),
            (tenant_b, "b" * 64, "深圳"),
        ):
            item = await owner.fetchrow(
                "SELECT ci.id,ci.public_id FROM code_items ci JOIN code_batches cb "
                "ON cb.tenant_id=ci.tenant_id AND cb.id=ci.code_batch_id "
                "WHERE ci.tenant_id=$1 AND cb.product_id IS NOT NULL AND ci.status='activated' "
                "AND ci.frozen_from_status IS NULL AND ci.frozen_at IS NULL AND ci.frozen_by IS NULL "
                "AND ci.freeze_reason IS NULL AND ci.freeze_provenance_version IS NULL "
                "ORDER BY ci.id LIMIT 1",
                tenant_id,
            )
            assert item is not None, "seed must provide an activated code with complete non-frozen provenance"
            await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
            scan_id = uuid.uuid4()
            await runtime_pg_conn.fetchrow(
                "SELECT * FROM record_public_code_scan($1,$2,$3,$4,'registry authority','browser',NULL)",
                tenant_id,
                item["public_id"],
                scan_id,
                ip_hash,
            )
            scan_time = await runtime_pg_conn.fetchval(
                "SELECT scan_time FROM scan_events WHERE tenant_id=$1 AND id=$2",
                tenant_id,
                scan_id,
            )
            observation_id = uuid.uuid4()
            receipt = await runtime_pg_conn.fetchrow(
                "SELECT * FROM record_diversion_observation("
                "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'ip_inference','medium',NULL,NULL,NULL,'cross_region_ip','medium')",
                tenant_id,
                observation_id,
                scan_id,
                scan_time,
                f"registry-diversion-{scan_id}",
                item["public_id"],
                item["id"],
                ip_hash,
                detected_city,
                "北京",
            )
            diversion_facts[tenant_id] = (receipt["clue_id"], observation_id)

            accounts = await owner.fetch(
                "SELECT DISTINCT account.id,account.auth_version FROM accounts account "
                "JOIN account_roles ar ON ar.tenant_id=account.tenant_id AND ar.account_id=account.id "
                "JOIN roles role ON role.tenant_id=ar.tenant_id AND role.id=ar.role_id "
                "JOIN role_permissions rp ON rp.tenant_id=role.tenant_id AND rp.role_id=role.id "
                "JOIN permissions permission ON permission.tenant_id=rp.tenant_id AND permission.id=rp.permission_id "
                "WHERE account.tenant_id=$1 AND account.is_active AND role.name='admin' "
                "AND permission.code='risk:manage' ORDER BY account.id",
                tenant_id,
            )
            assert len(accounts) == 1, "seed must expose one canonical active admin risk:manage actor per tenant"
            account = accounts[0]
            auth_session_id = uuid.uuid4()
            owned_auth_session_ids.append(auth_session_id)
            await owner.execute(
                "INSERT INTO auth_sessions "
                "(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,$5,now()+interval '1 hour',now(),now())",
                auth_session_id,
                tenant_id,
                account["id"],
                account["auth_version"],
                uuid.uuid4().hex,
            )
            product_id = await owner.fetchval(
                "SELECT cb.product_id FROM code_items ci JOIN code_batches cb "
                "ON cb.tenant_id=ci.tenant_id AND cb.id=ci.code_batch_id "
                "WHERE ci.tenant_id=$1 AND ci.id=$2",
                tenant_id,
                item["id"],
            )
            campaign_id = uuid.uuid4()
            await owner.execute(
                "INSERT INTO campaigns "
                "(id,tenant_id,name,campaign_type,status,product_id,start_at,end_at,rules_json,created_at,updated_at) "
                "VALUES($1,$2,$3,'lottery','active',$4,now()-interval '1 day',"
                "now()+interval '1 day','{}'::json,now(),now())",
                campaign_id,
                tenant_id,
                f"registry-risk-{tenant_id}",
                product_id,
            )
            rule_id = uuid.uuid4()
            await runtime_pg_conn.fetchrow(
                "SELECT * FROM mutate_risk_rule($1,$2,$3,'create',$4,NULL,$5,$6,'manual','block',$7,true)",
                tenant_id,
                auth_session_id,
                uuid.uuid4(),
                rule_id,
                f"registry-rule-{rule_id}",
                "registry authority rule",
                json.dumps({"always_trigger": True}),
            )
            link = await runtime_pg_conn.fetchrow(
                "SELECT * FROM set_campaign_risk_rule($1,$2,$3,$4,$5,true,$6)",
                tenant_id,
                auth_session_id,
                uuid.uuid4(),
                rule_id,
                campaign_id,
                f"registry-link-{campaign_id}",
            )
            risk_scan_id = uuid.uuid4()
            await runtime_pg_conn.fetchrow(
                "SELECT * FROM record_public_code_scan($1,$2,$3,$4,'registry risk authority','browser',NULL)",
                tenant_id,
                item["public_id"],
                risk_scan_id,
                ip_hash,
            )
            risk_receipt_id = uuid.uuid4()
            decision = await runtime_pg_conn.fetchrow(
                "SELECT * FROM evaluate_execute_scan_risk($1,$2,$3,$4,$5,$6)",
                tenant_id,
                risk_scan_id,
                rule_id,
                risk_receipt_id,
                f"registry-risk-{risk_scan_id}",
                json.dumps({"request_source": "registry acceptance"}),
            )
            assert decision["triggered"] is True
            assert decision["paused_campaign_ids"] == [campaign_id]
            pause_id = await runtime_pg_conn.fetchval(
                "SELECT id FROM risk_campaign_pauses WHERE tenant_id=$1 AND receipt_id=$2",
                tenant_id,
                risk_receipt_id,
            )
            outbox_id = await runtime_pg_conn.fetchval(
                "SELECT id FROM risk_action_outbox WHERE tenant_id=$1 AND receipt_id=$2",
                tenant_id,
                risk_receipt_id,
            )
            assert link["attached"] is True and pause_id is not None and outbox_id is not None
            risk_facts[tenant_id] = {
                "campaign_risk_rules": link["link_id"],
                "interception_records": decision["interception_id"],
                "risk_action_outbox": outbox_id,
                "risk_action_receipts": risk_receipt_id,
                "risk_alerts": decision["alert_id"],
                "risk_campaign_pauses": pause_id,
                "risk_rules": rule_id,
            }

        await runtime_pg_conn.execute("SELECT set_config('app.bypass_rls', 'false', true)")
        await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_a))
        own_clue_id, own_observation_id = diversion_facts[tenant_a]
        foreign_clue_id, foreign_observation_id = diversion_facts[tenant_b]
        assert await runtime_pg_conn.fetchval("SELECT count(*) FROM diversion_clues WHERE id=$1", own_clue_id) == 1
        assert (
            await runtime_pg_conn.fetchval(
                "SELECT count(*) FROM diversion_observations WHERE id=$1", own_observation_id
            )
            == 1
        )
        assert await runtime_pg_conn.fetchval("SELECT count(*) FROM diversion_clues WHERE id=$1", foreign_clue_id) == 0
        assert (
            await runtime_pg_conn.fetchval(
                "SELECT count(*) FROM diversion_observations WHERE id=$1", foreign_observation_id
            )
            == 0
        )
        for table in DIVERSION_RESTRICTED_TABLES:
            await _assert_statement_privilege_denied(
                runtime_pg_conn,
                f"INSERT INTO {table} DEFAULT VALUES",
            )
            await _assert_statement_privilege_denied(
                runtime_pg_conn,
                f"UPDATE {table} SET tenant_id=tenant_id WHERE false",
            )
            await _assert_statement_privilege_denied(
                runtime_pg_conn,
                f"DELETE FROM {table} WHERE false",
            )
        for table in RISK_RESTRICTED_TABLES:
            await _assert_statement_privilege_denied(runtime_pg_conn, f"INSERT INTO {table} DEFAULT VALUES")
            await _assert_statement_privilege_denied(
                runtime_pg_conn,
                f"UPDATE {table} SET tenant_id=tenant_id WHERE false",
            )
            await _assert_statement_privilege_denied(runtime_pg_conn, f"DELETE FROM {table} WHERE false")
        for table, own_id in risk_facts[tenant_a].items():
            assert await runtime_pg_conn.fetchval(f"SELECT count(*) FROM {table} WHERE id=$1", own_id) == 1
            assert (
                await runtime_pg_conn.fetchval(
                    f"SELECT count(*) FROM {table} WHERE id=$1",
                    risk_facts[tenant_b][table],
                )
                == 0
            )
        # Notification facts may already exist when the authority suite runs first.
        # Prove exact tenant visibility against the owner snapshot without assuming
        # global emptiness or deleting legitimate authority-owned facts.
        own_notification_ids = {
            row["id"]
            for row in await owner.fetch(
                "SELECT id FROM risk_notifications WHERE tenant_id=$1 ORDER BY id",
                tenant_a,
            )
        }
        foreign_notification_ids = {
            row["id"]
            for row in await owner.fetch(
                "SELECT id FROM risk_notifications WHERE tenant_id=$1 ORDER BY id",
                tenant_b,
            )
        }
        visible_notification_ids = {
            row["id"] for row in await runtime_pg_conn.fetch("SELECT id FROM risk_notifications ORDER BY id")
        }
        assert visible_notification_ids == own_notification_ids
        assert visible_notification_ids.isdisjoint(foreign_notification_ids)
        assert await runtime_pg_conn.fetchval("SELECT count(*) FROM code_allocations WHERE id=$1", allocation_a) == 1
        assert await runtime_pg_conn.fetchval("SELECT count(*) FROM code_allocations WHERE id=$1", allocation_b) == 0
        await _assert_statement_privilege_denied(
            runtime_pg_conn,
            "INSERT INTO code_allocations "
            "(id,tenant_id,batch_id,distributor_id,quantity,effective_from,version,change_reason,"
            "allocation_root_id,target_type,target_id,action,status,actor_id,actor_tenant_id,audit_id) "
            "VALUES($1,$2,$3,$4,1,now(),1,'direct runtime denied',$1,'distributor',$4,"
            "'allocate','active',$5,$2,$6)",
            uuid.uuid4(),
            tenant_a,
            batch_a,
            distributor_a,
            account_a,
            uuid.uuid4(),
        )
        await _assert_statement_privilege_denied(
            runtime_pg_conn,
            "UPDATE code_allocations SET quantity=2 WHERE id=$1",
            allocation_a,
        )
        await _assert_statement_privilege_denied(
            runtime_pg_conn,
            "DELETE FROM code_allocations WHERE id=$1",
            allocation_a,
        )
        own_coupon_code = uuid.uuid4()
        foreign_coupon_code = uuid.uuid4()
        await owner.execute(
            "INSERT INTO coupon_codes(id,tenant_id,pool_id,code,distributed) VALUES($1,$2,$3,$4,false)",
            foreign_coupon_code,
            tenant_b,
            pool_b1,
            f"B-{foreign_coupon_code}",
        )
        assert await runtime_pg_conn.fetchval("SELECT count(*) FROM coupon_codes WHERE id=$1", foreign_coupon_code) == 0
        await _assert_insert_denied(
            runtime_pg_conn,
            "INSERT INTO coupon_codes(id,tenant_id,pool_id,code,distributed) VALUES($1,$2,$3,$4,false)",
            uuid.uuid4(),
            tenant_b,
            pool_b1,
            f"CROSS-{uuid.uuid4()}",
        )
        await runtime_pg_conn.execute(
            "INSERT INTO coupon_codes(id,tenant_id,pool_id,code,distributed) VALUES($1,$2,$3,$4,false)",
            own_coupon_code,
            tenant_a,
            pool_a,
            f"A-{own_coupon_code}",
        )
        await _assert_statement_privilege_denied(
            runtime_pg_conn, "UPDATE coupon_codes SET distributed=true WHERE id=$1", own_coupon_code
        )
        await _assert_statement_privilege_denied(
            runtime_pg_conn, "DELETE FROM coupon_codes WHERE id=$1", own_coupon_code
        )
        for table, key_col, _unused, insert_sql, own_args, foreign_args, update_clause in cases:
            foreign_id = foreign_keys[table]
            assert await runtime_pg_conn.fetchval(f"SELECT count(*) FROM {table} WHERE {key_col}=$1", foreign_id) == 0
            cross_args = (uuid.uuid4(), *foreign_args[1:])
            await _assert_insert_denied(runtime_pg_conn, insert_sql, *cross_args)
            await runtime_pg_conn.execute(insert_sql, *own_args)
            own_id = own_args[0]
            assert (
                await runtime_pg_conn.execute(f"UPDATE {table} SET {update_clause} WHERE {key_col}=$1", own_id)
                == "UPDATE 1"
            )
            assert (
                await runtime_pg_conn.execute(f"UPDATE {table} SET {update_clause} WHERE {key_col}=$1", foreign_id)
                == "UPDATE 0"
            )
            assert await runtime_pg_conn.execute(f"DELETE FROM {table} WHERE {key_col}=$1", foreign_id) == "DELETE 0"
            assert await runtime_pg_conn.execute(f"DELETE FROM {table} WHERE {key_col}=$1", own_id) == "DELETE 1"

        association_cases = (
            (
                "account_roles",
                "account_id",
                "role_id",
                (account_a, role_a),
                (account_b1, role_b1),
                (account_b2, role_b2),
            ),
            (
                "role_permissions",
                "role_id",
                "permission_id",
                (role_a, permission_a),
                (role_b1, permission_b1),
                (role_b2, permission_b2),
            ),
        )
        for table, left, right, own_pair, foreign_pair, cross_pair in association_cases:
            await owner.execute(f"INSERT INTO {table} ({left},{right}) VALUES ($1,$2)", *foreign_pair)
            assert (
                await runtime_pg_conn.fetchval(
                    f"SELECT count(*) FROM {table} WHERE {left}=$1 AND {right}=$2", *foreign_pair
                )
                == 0
            )
            await _assert_association_insert_denied(
                runtime_pg_conn, f"INSERT INTO {table} ({left},{right}) VALUES ($1,$2)", *cross_pair
            )
            await runtime_pg_conn.execute(f"INSERT INTO {table} ({left},{right}) VALUES ($1,$2)", *own_pair)
            assert (
                await runtime_pg_conn.execute(
                    f"UPDATE {table} SET {right}={right} WHERE {left}=$1 AND {right}=$2", *own_pair
                )
                == "UPDATE 1"
            )
            assert (
                await runtime_pg_conn.execute(f"DELETE FROM {table} WHERE {left}=$1 AND {right}=$2", *foreign_pair)
                == "DELETE 0"
            )
            assert (
                await runtime_pg_conn.execute(f"DELETE FROM {table} WHERE {left}=$1 AND {right}=$2", *own_pair)
                == "DELETE 1"
            )

        whitelabel_foreign = uuid.uuid4()
        await owner.execute(
            "INSERT INTO whitelabel_configs (id,org_id,brand_name,hide_yimatong,primary_color,font_family) "
            "VALUES ($1,$2,'foreign',false,'#000000','')",
            whitelabel_foreign,
            regional_b1,
        )
        assert (
            await runtime_pg_conn.fetchval("SELECT count(*) FROM whitelabel_configs WHERE id=$1", whitelabel_foreign)
            == 0
        )
        await _assert_insert_denied(
            runtime_pg_conn,
            "INSERT INTO whitelabel_configs (id,org_id,brand_name,hide_yimatong,primary_color,font_family) "
            "VALUES ($1,$2,'cross',false,'#000000','')",
            uuid.uuid4(),
            regional_b2,
        )
        whitelabel_own = uuid.uuid4()
        await runtime_pg_conn.execute(
            "INSERT INTO whitelabel_configs (id,org_id,brand_name,hide_yimatong,primary_color,font_family) "
            "VALUES ($1,$2,'own',false,'#000000','')",
            whitelabel_own,
            regional_a,
        )
        assert (
            await runtime_pg_conn.execute(
                "UPDATE whitelabel_configs SET brand_name='updated' WHERE id=$1", whitelabel_own
            )
            == "UPDATE 1"
        )
        assert (
            await runtime_pg_conn.execute("DELETE FROM whitelabel_configs WHERE id=$1", whitelabel_foreign)
            == "DELETE 0"
        )
        assert await runtime_pg_conn.execute("DELETE FROM whitelabel_configs WHERE id=$1", whitelabel_own) == "DELETE 1"
    finally:
        if owned_auth_session_ids:
            await owner.execute("DELETE FROM auth_sessions WHERE id=ANY($1::uuid[])", owned_auth_session_ids)
        if allocation_fixture_ids is not None:
            await owner.execute("DELETE FROM code_allocations WHERE id=ANY($1::uuid[])", allocation_fixture_ids)
        if distributor_fixture_ids is not None:
            await owner.execute("DELETE FROM distributors WHERE id=ANY($1::uuid[])", distributor_fixture_ids)
        await owner.close()


async def test_scan_partition_lifecycle_and_replay_keep_child_acl_closed(
    migrated_pg_url: str,
    runtime_pg_conn: asyncpg.Connection,
) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    partition_name = "scan_events_2099_01"
    try:
        assert (
            await owner.fetchval("SELECT public.create_secure_scan_events_partition('2099-01-01'::date)")
            == partition_name
        )
        assert not await owner.fetchval(
            "SELECT has_table_privilege('yimatong_app', $1, 'SELECT')", f"public.{partition_name}"
        )
        init_sql = (Path(__file__).resolve().parents[2] / "scripts" / "init_runtime_role.sql").read_text()
        await owner.execute(init_sql)
        assert not await owner.fetchval(
            "SELECT has_table_privilege('yimatong_app', $1, 'SELECT')", f"public.{partition_name}"
        )
        state = await owner.fetchrow(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=to_regclass($1)",
            f"public.{partition_name}",
        )
        assert state["relrowsecurity"] and state["relforcerowsecurity"]
    finally:
        await owner.close()

    tenant_id = uuid.uuid4()
    # Parent access remains the only runtime contract and routes normally.
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await owner.execute(
            "INSERT INTO tenants (id,name,slug,status,plan,tenant_type,created_at,updated_at) "
            "VALUES ($1,'partition tenant',$2,'active','free','brand',now(),now())",
            tenant_id,
            f"partition-{tenant_id.hex[:8]}",
        )
    finally:
        await owner.close()
    await runtime_pg_conn.execute("SELECT set_config('app.bypass_rls', 'false', true)")
    await runtime_pg_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
    for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert not await runtime_pg_conn.fetchval(
            "SELECT has_table_privilege('yimatong_app','public.scan_events',$1)", privilege
        )
    await _assert_statement_privilege_denied(
        runtime_pg_conn,
        "INSERT INTO scan_events (id,tenant_id,public_id,scan_time,is_first_scan,is_valid_visit) "
        "VALUES ($1,$2,'PARTITION-TEST','2099-01-15T00:00:00Z',false,false)",
        uuid.uuid4(),
        tenant_id,
    )
    await _assert_statement_privilege_denied(
        runtime_pg_conn,
        f"SELECT count(*) FROM public.{partition_name}",
    )


async def test_runtime_registry_grants_wecom_authority_when_callback_role_is_created_after_head(
    migrated_pg_url: str,
) -> None:
    async with _isolated_head_database(migrated_pg_url) as database_url:
        owner_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        owner = await asyncpg.connect(owner_dsn)
        callback = None
        callback_role = f"acceptance_callback_{uuid.uuid4().hex[:12]}"
        signature = (
            "public.apply_verified_wecom_contact_event"
            "(uuid,uuid,uuid,text,text,text,text,text,timestamp with time zone,bigint,text)"
        )
        try:
            assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", callback_role)
            assert await owner.fetchval("SELECT to_regprocedure($1) IS NOT NULL", signature)
            registry_sql = (
                (BACKEND_DIR / "scripts" / "init_runtime_role.sql")
                .read_text()
                .replace("yimatong_callback", callback_role)
            )
            await owner.execute(registry_sql)
            assert await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", callback_role, signature)
            assert not await owner.fetchval("SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')", signature)
            assert not await owner.fetchval("SELECT has_function_privilege('public',$1,'EXECUTE')", signature)
            callback = await asyncpg.connect(
                owner_dsn.replace("yimatong:yimatong@", f"{callback_role}:{callback_role}@")
            )
            for table in WECOM_RESTRICTED_TABLES:
                for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                    assert not await callback.fetchval(
                        "SELECT has_table_privilege($1,$2,$3)", callback_role, f"public.{table}", privilege
                    )
                    assert not await owner.fetchval(
                        "SELECT has_table_privilege('yimatong_app',$1,$2)", f"public.{table}", privilege
                    )
        finally:
            if callback is not None:
                await callback.close()
            if await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", callback_role):
                await owner.execute(f'DROP OWNED BY "{callback_role}"')
                await owner.execute(f'DROP ROLE "{callback_role}"')
            await owner.close()
