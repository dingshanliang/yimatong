"""Focused PostgreSQL contracts for durable risk decisions."""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url

from tests.test_acceptance.conftest import (
    ADMIN_DSN,
    AcceptanceDatabaseLease,
    _create_owned_database,
    _drop_database_with_retry,
    seed_baseline,
)

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]
BACKEND_DIR = Path(__file__).resolve().parents[2]


def _alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["database_url"] = database_url
    env["migration_database_url"] = database_url
    env["control_database_url"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


@pytest_asyncio.fixture
async def isolated_risk_roundtrip_pg_url(migrated_pg_url: str):
    """Lease a fact-free database for destructive risk migration round trips."""

    database_name = f"yimatong_acceptance_risk_{uuid.uuid4().hex[:12]}"
    database_url = make_url(migrated_pg_url).set(database=database_name).render_as_string(hide_password=False)
    lease = AcceptanceDatabaseLease(database_name, database_url, uuid.uuid4().hex)
    try:
        await _create_owned_database(lease, ADMIN_DSN)
        initial = _alembic(database_url, "upgrade", "u7c2a3b4c5d6")
        assert initial.returncode == 0, initial.stderr
        yield database_url
    finally:
        if lease.created:
            await _drop_database_with_retry(
                lease.database_name,
                ADMIN_DSN,
                expected_owner_marker=lease.owner_marker,
                allow_unmarked_created=lease.created and not lease.marker_written,
            )


async def test_risk_migration_clean_roundtrip_uses_isolated_database(isolated_risk_roundtrip_pg_url: str) -> None:
    dsn = isolated_risk_roundtrip_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    catalog_sql = (
        "SELECT jsonb_build_object("
        "'columns',(SELECT jsonb_agg(row_to_json(c) ORDER BY c.column_name) FROM ("
        "SELECT column_name,data_type,is_nullable,column_default FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='risk_alerts') c),"
        "'constraints',(SELECT jsonb_agg(jsonb_build_array(conname,pg_get_constraintdef(oid)) ORDER BY conname) "
        "FROM pg_constraint WHERE conrelid='public.risk_alerts'::regclass),"
        "'indexes',(SELECT jsonb_agg(jsonb_build_array(indexname,indexdef) ORDER BY indexname) FROM pg_indexes "
        "WHERE schemaname='public' AND tablename='risk_alerts'),"
        "'triggers',(SELECT jsonb_agg(pg_get_triggerdef(oid) ORDER BY tgname) FROM pg_trigger "
        "WHERE tgrelid='public.risk_alerts'::regclass AND NOT tgisinternal),"
        "'function',(SELECT pg_get_functiondef("
        "'public.freeze_code_item_with_risk_alert(uuid,uuid,uuid,uuid,uuid,uuid,text,text)'::regprocedure))"
        ")::text"
    )
    catalog_before = await conn.fetchval(catalog_sql)
    await conn.close()
    before = _alembic(isolated_risk_roundtrip_pg_url, "current")
    assert before.returncode == 0 and "u7c2a3b4c5d6" in before.stdout
    down = _alembic(isolated_risk_roundtrip_pg_url, "downgrade", "u7b1d2e3f4a5")
    assert down.returncode == 0, down.stderr
    up = _alembic(isolated_risk_roundtrip_pg_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval(catalog_sql) == catalog_before
    finally:
        await conn.close()


async def test_risk_rule_and_scan_decision_are_actor_bound_durable_and_replay_safe(migrated_pg_url: str) -> None:
    baseline = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(baseline["baseline_tenant"]["id"])
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    rule_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    session_id = uuid.uuid4()
    audit_ids: list[uuid.UUID] = []
    try:
        account = await owner.fetchrow(
            "SELECT id,auth_version FROM accounts WHERE tenant_id=$1 AND email=$2",
            tenant_id,
            baseline["baseline_tenant"]["admin_email"],
        )
        await owner.execute(
            "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,now()+interval '1 hour',now(),now())",
            session_id,
            tenant_id,
            account["id"],
            account["auth_version"],
            uuid.uuid4().hex,
        )
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
        for table in ("risk_rules", "risk_action_receipts", "risk_campaign_pauses", "risk_action_outbox"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with runtime.transaction():
                    await runtime.execute(f"DELETE FROM {table} WHERE tenant_id=$1", tenant_id)

        create_audit_id = uuid.uuid4()
        audit_ids.append(create_audit_id)
        create_args = (
            tenant_id,
            session_id,
            create_audit_id,
            "create",
            rule_id,
            None,
            "rule-create-1",
            "测试预警规则",
            "manual",
            "warn",
            json.dumps({"always_trigger": True, "threshold": 1}),
            True,
        )
        created = await runtime.fetchrow(
            "SELECT * FROM mutate_risk_rule($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)", *create_args
        )
        replay = await runtime.fetchrow(
            "SELECT * FROM mutate_risk_rule($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)", *create_args
        )
        assert created["version"] == 1 and created["replayed"] is False
        assert replay["rule_id"] == rule_id and replay["replayed"] is True

        item = await owner.fetchrow(
            "SELECT id,public_id FROM code_items WHERE tenant_id=$1 AND code_batch_id=$2 ORDER BY id LIMIT 1",
            tenant_id,
            uuid.UUID(baseline["code_batch"]["id"]),
        )
        await owner.execute(
            "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,ip_hash,is_first_scan,is_valid_visit) "
            "VALUES($1,$2,$3,now(),$4,false,true)",
            scan_id,
            tenant_id,
            item["public_id"],
            "a" * 64,
        )
        receipt_id = uuid.uuid4()
        evaluated = await runtime.fetchrow(
            "SELECT * FROM evaluate_execute_scan_risk($1,$2,$3,$4,$5,$6)",
            tenant_id,
            scan_id,
            rule_id,
            receipt_id,
            "scan-rule-1",
            json.dumps({"request_source": "worker"}),
        )
        replayed = await runtime.fetchrow(
            "SELECT * FROM evaluate_execute_scan_risk($1,$2,$3,$4,$5,$6)",
            tenant_id,
            scan_id,
            rule_id,
            receipt_id,
            "scan-rule-1",
            json.dumps({"request_source": "worker"}),
        )
        assert evaluated["triggered"] is True and evaluated["action"] == "warn"
        assert evaluated["alert_id"] is not None and evaluated["paused_campaign_ids"] == []
        assert replayed["receipt_id"] == receipt_id and replayed["replayed"] is True
        facts = await owner.fetchrow(
            "SELECT (SELECT count(*) FROM risk_action_receipts WHERE id=$1) receipts,"
            "(SELECT count(*) FROM interception_records WHERE id=$2) interceptions,"
            "(SELECT count(*) FROM risk_alerts WHERE id=$3 AND risk_rule_id=$4) alerts,"
            "(SELECT count(*) FROM risk_action_outbox WHERE receipt_id=$1) outbox",
            receipt_id,
            evaluated["interception_id"],
            evaluated["alert_id"],
            rule_id,
        )
        assert dict(facts) == {"receipts": 1, "interceptions": 1, "alerts": 1, "outbox": 1}
        snapshot = json.loads(
            await owner.fetchval(
                "SELECT (result->'context_snapshot')::text FROM risk_action_receipts WHERE id=$1", receipt_id
            )
        )
        assert snapshot["scan_event_id"] == str(scan_id)
        assert len(snapshot["config_digest"]) == 64 and snapshot["rule_version"] == 1
        with pytest.raises(asyncpg.UniqueViolationError):
            await runtime.fetchrow(
                "SELECT * FROM evaluate_execute_scan_risk($1,$2,$3,$4,$5,$6)",
                tenant_id,
                scan_id,
                rule_id,
                uuid.uuid4(),
                "scan-rule-1",
                json.dumps({"request_source": "changed"}),
            )

        for action, expected_version, idem, enabled in (
            ("update", 1, "rule-update-1", True),
            ("disable", 2, "rule-disable-1", False),
        ):
            mutation_audit_id = uuid.uuid4()
            audit_ids.append(mutation_audit_id)
            mutation_args = (
                tenant_id,
                session_id,
                mutation_audit_id,
                action,
                rule_id,
                expected_version,
                idem,
                "测试预警规则",
                "manual",
                "warn",
                json.dumps({"always_trigger": True, "threshold": 1}),
                enabled,
            )
            mutation = await runtime.fetchrow(
                "SELECT * FROM mutate_risk_rule($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)", *mutation_args
            )
            mutation_replay = await runtime.fetchrow(
                "SELECT * FROM mutate_risk_rule($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)", *mutation_args
            )
            assert mutation["replayed"] is False and mutation_replay["replayed"] is True

        delivery_rows = await owner.fetch(
            "SELECT receipt.action,receipt.id,outbox.topic,outbox.payload FROM risk_action_receipts receipt "
            "JOIN risk_action_outbox outbox ON outbox.tenant_id=receipt.tenant_id AND outbox.receipt_id=receipt.id "
            "WHERE receipt.tenant_id=$1 AND receipt.risk_rule_id=$2 AND receipt.action=ANY($3::text[]) "
            "ORDER BY receipt.action",
            tenant_id,
            rule_id,
            ["rule:create", "rule:update", "rule:disable"],
        )
        assert [row["action"] for row in delivery_rows] == ["rule:create", "rule:disable", "rule:update"]
        for row in delivery_rows:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            assert row["topic"] == "risk.rule_mutated"
            assert payload["tenant_id"] == str(tenant_id) and payload["receipt_id"] == str(row["id"])
            assert payload["action"] == row["action"] and payload["result_version"] == 1
            assert payload["actor_id"] == str(account["id"]) and "config" not in json.dumps(payload)

        before = await owner.fetchval("SELECT version_num FROM alembic_version")
        downgrade = _alembic(migrated_pg_url, "downgrade", "u7b1d2e3f4a5")
        assert downgrade.returncode != 0 and "immutable facts" in downgrade.stderr
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == before
    finally:
        receipt_ids = await owner.fetch(
            "SELECT id FROM risk_action_receipts WHERE tenant_id=$1 AND risk_rule_id=$2",
            tenant_id,
            rule_id,
        )
        owned_receipts = [row["id"] for row in receipt_ids]
        if owned_receipts:
            await owner.execute("DELETE FROM risk_action_outbox WHERE receipt_id=ANY($1::uuid[])", owned_receipts)
        await owner.execute("DELETE FROM risk_alerts WHERE tenant_id=$1 AND risk_rule_id=$2", tenant_id, rule_id)
        await owner.execute(
            "DELETE FROM interception_records WHERE tenant_id=$1 AND risk_rule_id=$2", tenant_id, rule_id
        )
        if owned_receipts:
            await owner.execute("DELETE FROM risk_action_receipts WHERE id=ANY($1::uuid[])", owned_receipts)
        if audit_ids:
            await owner.execute("DELETE FROM platform_audit_log WHERE id=ANY($1::uuid[])", audit_ids)
        await owner.execute("DELETE FROM risk_rules WHERE tenant_id=$1 AND id=$2", tenant_id, rule_id)
        await owner.execute("DELETE FROM scan_events WHERE tenant_id=$1 AND id=$2", tenant_id, scan_id)
        await owner.execute("DELETE FROM auth_sessions WHERE id=$1", session_id)
        await runtime.close()
        await owner.close()


async def test_risk_tenant_foreign_keys_and_rls_catalog(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        tables = ("risk_action_receipts", "risk_campaign_pauses", "risk_action_outbox")
        rows = await owner.fetch(
            "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=ANY($1::text[])",
            list(tables),
        )
        assert {row["relname"] for row in rows} == set(tables)
        assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in rows)
        constraints = {
            row["conname"]
            for row in await owner.fetch(
                "SELECT conname FROM pg_constraint WHERE conname LIKE 'fk_risk_%' OR conname LIKE 'fk_campaign_risk_%'"
            )
        }
        assert constraints.issuperset(
            {
                "fk_campaign_risk_rules_tenant_campaign",
                "fk_campaign_risk_rules_tenant_rule",
                "fk_risk_alerts_tenant_code_item",
                "fk_risk_alerts_tenant_rule",
                "fk_risk_notifications_tenant_campaign",
                "fk_risk_notifications_tenant_code_item",
                "fk_risk_notifications_tenant_rule",
                "fk_risk_pauses_tenant_campaign",
                "fk_risk_pauses_tenant_receipt",
                "fk_risk_pauses_tenant_rule",
                "fk_risk_receipts_tenant_rule",
            }
        )
    finally:
        await owner.close()


async def test_block_pauses_only_linked_product_campaign_and_resume_is_matching_cas(migrated_pg_url: str) -> None:
    baseline = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(baseline["baseline_tenant"]["id"])
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    session_id = uuid.uuid4()
    rule_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    linked_campaign, unrelated_campaign = uuid.uuid4(), uuid.uuid4()
    audit_ids: list[uuid.UUID] = []
    subject = None
    try:
        account = await owner.fetchrow(
            "SELECT id,auth_version FROM accounts WHERE tenant_id=$1 AND email=$2",
            tenant_id,
            baseline["baseline_tenant"]["admin_email"],
        )
        await owner.execute(
            "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,now()+interval '1 hour',now(),now())",
            session_id,
            tenant_id,
            account["id"],
            account["auth_version"],
            uuid.uuid4().hex,
        )
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
        batch_id = uuid.UUID(baseline["code_batch"]["id"])
        subject = await owner.fetchrow(
            "SELECT ci.id,ci.public_id,cb.product_id FROM code_items ci JOIN code_batches cb "
            "ON cb.tenant_id=ci.tenant_id AND cb.id=ci.code_batch_id "
            "WHERE ci.tenant_id=$1 AND cb.id=$2 AND ci.status='activated' ORDER BY ci.id LIMIT 1",
            tenant_id,
            batch_id,
        )
        assert subject is not None
        for campaign_id, name in (
            (linked_campaign, "linked-risk-campaign"),
            (unrelated_campaign, "unrelated-campaign"),
        ):
            await owner.execute(
                "INSERT INTO campaigns(id,tenant_id,name,campaign_type,status,product_id,start_at,end_at,rules_json,created_at,updated_at) "
                "VALUES($1,$2,$3,'lottery','active',$4,now()-interval '1 day',now()+interval '1 day','{}',now(),now())",
                campaign_id,
                tenant_id,
                name,
                subject["product_id"],
            )
        create_audit_id = uuid.uuid4()
        audit_ids.append(create_audit_id)
        await runtime.fetchrow(
            "SELECT * FROM mutate_risk_rule($1,$2,$3,'create',$4,NULL,$5,$6,'manual','block',$7,true)",
            tenant_id,
            session_id,
            create_audit_id,
            rule_id,
            "block-rule-create",
            "阻断规则",
            json.dumps({"always_trigger": True}),
        )
        attach_audit_id = uuid.uuid4()
        audit_ids.append(attach_audit_id)
        link = await runtime.fetchrow(
            "SELECT * FROM set_campaign_risk_rule($1,$2,$3,$4,$5,true,$6)",
            tenant_id,
            session_id,
            attach_audit_id,
            rule_id,
            linked_campaign,
            "attach-linked",
        )
        assert link["attached"] is True
        link_replay = await runtime.fetchrow(
            "SELECT * FROM set_campaign_risk_rule($1,$2,$3,$4,$5,true,$6)",
            tenant_id,
            session_id,
            uuid.uuid4(),
            rule_id,
            linked_campaign,
            "attach-linked",
        )
        assert link_replay["replayed"] is True and link_replay["link_id"] == link["link_id"]
        await owner.execute(
            "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,ip_hash,is_first_scan,is_valid_visit) "
            "VALUES($1,$2,$3,now(),$4,false,true)",
            scan_id,
            tenant_id,
            subject["public_id"],
            "b" * 64,
        )
        decision_receipt_id = uuid.uuid4()
        decision = await runtime.fetchrow(
            "SELECT * FROM evaluate_execute_scan_risk($1,$2,$3,$4,$5,$6)",
            tenant_id,
            scan_id,
            rule_id,
            decision_receipt_id,
            "block-decision-1",
            json.dumps({"request_source": "worker"}),
        )
        assert decision["triggered"] is True and decision["action"] == "block"
        assert decision["paused_campaign_ids"] == [linked_campaign]
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM risk_alerts WHERE tenant_id=$1 AND risk_action_receipt_id=$2 "
                "AND risk_rule_id=$3 AND source='worker_evaluation' AND prior_code_status='activated' "
                "AND current_code_status='frozen'",
                tenant_id,
                decision_receipt_id,
                rule_id,
            )
            == 1
        )
        states = await owner.fetch(
            "SELECT id,status FROM campaigns WHERE id=ANY($1::uuid[]) ORDER BY id",
            [linked_campaign, unrelated_campaign],
        )
        assert {row["id"]: row["status"] for row in states} == {
            linked_campaign: "paused",
            unrelated_campaign: "active",
        }
        pause = await owner.fetchrow(
            "SELECT id,version,status FROM risk_campaign_pauses WHERE tenant_id=$1 AND campaign_id=$2",
            tenant_id,
            linked_campaign,
        )
        resume_audit_id = uuid.uuid4()
        audit_ids.append(resume_audit_id)
        resumed = await runtime.fetchrow(
            "SELECT * FROM resume_risk_campaign_pause($1,$2,$3,$4,$5,$6,$7)",
            tenant_id,
            session_id,
            resume_audit_id,
            pause["id"],
            pause["version"],
            "resume-linked-1",
            "人工复核通过",
        )
        assert resumed["status"] == "resumed" and resumed["campaign_status"] == "active"
        resumed_replay = await runtime.fetchrow(
            "SELECT * FROM resume_risk_campaign_pause($1,$2,$3,$4,$5,$6,$7)",
            tenant_id,
            session_id,
            uuid.uuid4(),
            pause["id"],
            pause["version"],
            "resume-linked-1",
            "人工复核通过",
        )
        assert resumed_replay["replayed"] is True and resumed_replay["pause_id"] == pause["id"]
        with pytest.raises(asyncpg.CheckViolationError):
            await runtime.fetchrow(
                "SELECT * FROM resume_risk_campaign_pause($1,$2,$3,$4,$5,$6,$7)",
                tenant_id,
                session_id,
                uuid.uuid4(),
                pause["id"],
                pause["version"],
                "resume-stale",
                "重复恢复",
            )
        detach_audit_id = uuid.uuid4()
        audit_ids.append(detach_audit_id)
        detached = await runtime.fetchrow(
            "SELECT * FROM set_campaign_risk_rule($1,$2,$3,$4,$5,false,$6)",
            tenant_id,
            session_id,
            detach_audit_id,
            rule_id,
            linked_campaign,
            "detach-linked",
        )
        detached_replay = await runtime.fetchrow(
            "SELECT * FROM set_campaign_risk_rule($1,$2,$3,$4,$5,false,$6)",
            tenant_id,
            session_id,
            uuid.uuid4(),
            rule_id,
            linked_campaign,
            "detach-linked",
        )
        assert detached["attached"] is False and detached_replay["replayed"] is True
        deliveries = await owner.fetch(
            "SELECT receipt.action,receipt.id,outbox.topic,outbox.payload FROM risk_action_receipts receipt "
            "JOIN risk_action_outbox outbox ON outbox.tenant_id=receipt.tenant_id AND outbox.receipt_id=receipt.id "
            "WHERE receipt.tenant_id=$1 AND receipt.risk_rule_id=$2 ORDER BY receipt.recorded_at,receipt.id",
            tenant_id,
            rule_id,
        )
        assert len(deliveries) == 5
        assert [row["topic"] for row in deliveries].count("risk.campaign_rule_set") == 2
        assert [row["topic"] for row in deliveries].count("risk.campaign_resumed") == 1
        assert [row["topic"] for row in deliveries].count("risk.action_evaluated") == 1
        for row in deliveries:
            if row["topic"] not in {"risk.campaign_rule_set", "risk.campaign_resumed"}:
                continue
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            assert payload["tenant_id"] == str(tenant_id) and payload["receipt_id"] == str(row["id"])
            assert payload["result_version"] == 1 and payload["actor_id"] == str(account["id"])
            assert "config" not in json.dumps(payload)
    finally:
        if subject is not None and await owner.fetchval(
            "SELECT status='frozen' FROM code_items WHERE tenant_id=$1 AND id=$2", tenant_id, subject["id"]
        ):
            recover_audit_id = uuid.uuid4()
            await runtime.fetchrow(
                "SELECT * FROM transition_code_item_lifecycle($1,$2,$3,$4,'recover',NULL)",
                tenant_id,
                session_id,
                recover_audit_id,
                subject["id"],
            )
            audit_ids.append(recover_audit_id)
        receipt_rows = await owner.fetch(
            "SELECT id FROM risk_action_receipts WHERE tenant_id=$1 AND risk_rule_id=$2", tenant_id, rule_id
        )
        owned_receipts = [row["id"] for row in receipt_rows]
        if owned_receipts:
            await owner.execute("DELETE FROM risk_action_outbox WHERE receipt_id=ANY($1::uuid[])", owned_receipts)
        await owner.execute("DELETE FROM risk_alerts WHERE tenant_id=$1 AND risk_rule_id=$2", tenant_id, rule_id)
        await owner.execute(
            "DELETE FROM risk_campaign_pauses WHERE tenant_id=$1 AND risk_rule_id=$2", tenant_id, rule_id
        )
        await owner.execute(
            "DELETE FROM interception_records WHERE tenant_id=$1 AND risk_rule_id=$2", tenant_id, rule_id
        )
        await owner.execute(
            "DELETE FROM campaign_risk_rules WHERE tenant_id=$1 AND risk_rule_id=$2", tenant_id, rule_id
        )
        if owned_receipts:
            await owner.execute("DELETE FROM risk_action_receipts WHERE id=ANY($1::uuid[])", owned_receipts)
        if subject is not None:
            freeze_audits = await owner.fetch(
                "SELECT id FROM platform_audit_log WHERE target_tenant_id=$1 AND action='code_freeze' "
                "AND resource=$2 AND details->>'reason' LIKE 'risk auto block rule=%'",
                str(tenant_id),
                f"code_item:{subject['public_id']}",
            )
            audit_ids.extend(row["id"] for row in freeze_audits)
        if audit_ids:
            await owner.execute("DELETE FROM platform_audit_log WHERE id=ANY($1::uuid[])", audit_ids)
        await owner.execute("DELETE FROM risk_rules WHERE tenant_id=$1 AND id=$2", tenant_id, rule_id)
        await owner.execute(
            "DELETE FROM campaigns WHERE tenant_id=$1 AND id=ANY($2::uuid[])",
            tenant_id,
            [linked_campaign, unrelated_campaign],
        )
        await owner.execute("DELETE FROM scan_events WHERE tenant_id=$1 AND id=$2", tenant_id, scan_id)
        await owner.execute("DELETE FROM auth_sessions WHERE id=$1", session_id)
        await runtime.close()
        await owner.close()


async def test_risk_notification_read_authority_is_actor_bound_and_replay_safe(migrated_pg_url: str) -> None:
    baseline = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(baseline["baseline_tenant"]["id"])
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    session_id, expired_session = uuid.uuid4(), uuid.uuid4()
    notification_ids = [uuid.uuid4() for _ in range(3)]
    audit_ids = [uuid.uuid4(), uuid.uuid4()]
    removed_grants: list[asyncpg.Record] = []
    try:
        account = await owner.fetchrow(
            "SELECT id,auth_version FROM accounts WHERE tenant_id=$1 AND email=$2",
            tenant_id,
            baseline["baseline_tenant"]["admin_email"],
        )
        await owner.execute(
            "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,now()+interval '1 hour',now(),now()),"
            "($6,$2,$3,$4,$7,now()-interval '1 hour',now(),now())",
            session_id,
            tenant_id,
            account["id"],
            account["auth_version"],
            uuid.uuid4().hex,
            expired_session,
            uuid.uuid4().hex,
        )
        await owner.executemany(
            "INSERT INTO risk_notifications(id,tenant_id,notification_type,title,detail,read,created_at,updated_at) "
            "VALUES($1,$2,'risk','owned notification','owned detail',false,now(),now())",
            [(notification_id, tenant_id) for notification_id in notification_ids],
        )
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
        one = await runtime.fetchrow(
            "SELECT * FROM mark_risk_notification_read($1,$2,$3,$4,$5)",
            tenant_id,
            session_id,
            audit_ids[0],
            notification_ids[0],
            "notification-one",
        )
        replay = await runtime.fetchrow(
            "SELECT * FROM mark_risk_notification_read($1,$2,$3,$4,$5)",
            tenant_id,
            session_id,
            uuid.uuid4(),
            notification_ids[0],
            "notification-one",
        )
        assert one["changed"] is True and one["replayed"] is False
        assert replay["receipt_id"] == one["receipt_id"] and replay["replayed"] is True
        before_conflict = await owner.fetchval(
            "SELECT count(*) FROM risk_action_receipts WHERE id=$1", one["receipt_id"]
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await runtime.fetchrow(
                "SELECT * FROM mark_risk_notification_read($1,$2,$3,$4,$5)",
                tenant_id,
                session_id,
                uuid.uuid4(),
                notification_ids[1],
                "notification-one",
            )
        assert (
            await owner.fetchval("SELECT count(*) FROM risk_action_receipts WHERE id=$1", one["receipt_id"])
            == before_conflict
        )

        all_read = await runtime.fetchrow(
            "SELECT * FROM mark_all_risk_notifications_read($1,$2,$3,$4)",
            tenant_id,
            session_id,
            audit_ids[1],
            "notification-all",
        )
        all_replay = await runtime.fetchrow(
            "SELECT * FROM mark_all_risk_notifications_read($1,$2,$3,$4)",
            tenant_id,
            session_id,
            uuid.uuid4(),
            "notification-all",
        )
        assert all_read["updated_count"] == 2 and all_read["replayed"] is False
        assert all_replay["receipt_id"] == all_read["receipt_id"] and all_replay["replayed"] is True
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM risk_notifications WHERE id=ANY($1::uuid[]) AND read", notification_ids
            )
            == 3
        )
        receipts = [one["receipt_id"], all_read["receipt_id"]]
        facts = await owner.fetchrow(
            "SELECT (SELECT count(*) FROM risk_action_receipts WHERE id=ANY($1::uuid[])) receipts,"
            "(SELECT count(*) FROM risk_action_outbox WHERE receipt_id=ANY($1::uuid[])) outbox,"
            "(SELECT count(*) FROM platform_audit_log WHERE id=ANY($2::uuid[])) audits",
            receipts,
            audit_ids,
        )
        assert dict(facts) == {"receipts": 2, "outbox": 2, "audits": 2}
        for receipt_id in receipts:
            payload = await owner.fetchval("SELECT payload FROM risk_action_outbox WHERE receipt_id=$1", receipt_id)
            payload = json.loads(payload) if isinstance(payload, str) else payload
            assert "owned notification" not in json.dumps(payload) and "owned detail" not in json.dumps(payload)

        for denied_tenant, denied_session in (
            (uuid.uuid4(), session_id),
            (tenant_id, expired_session),
            (tenant_id, uuid.uuid4()),
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime.fetchrow(
                    "SELECT * FROM mark_risk_notification_read($1,$2,$3,$4,$5)",
                    denied_tenant,
                    denied_session,
                    uuid.uuid4(),
                    notification_ids[0],
                    f"denied-{uuid.uuid4()}",
                )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.execute(
                "UPDATE risk_notifications SET read=false WHERE tenant_id=$1 AND id=$2", tenant_id, notification_ids[0]
            )
        removed_grants = await owner.fetch(
            "DELETE FROM role_permissions rp USING permissions p,account_roles ar "
            "WHERE p.tenant_id=rp.tenant_id AND p.id=rp.permission_id AND p.code='risk:manage' "
            "AND ar.tenant_id=rp.tenant_id AND ar.role_id=rp.role_id AND ar.account_id=$1 "
            "RETURNING rp.tenant_id,rp.role_id,rp.permission_id",
            account["id"],
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.fetchrow(
                "SELECT * FROM mark_all_risk_notifications_read($1,$2,$3,$4)",
                tenant_id,
                session_id,
                uuid.uuid4(),
                "notification-all",
            )
        head = await owner.fetchval("SELECT version_num FROM alembic_version")
        blocked = _alembic(migrated_pg_url, "downgrade", "-1")
        assert blocked.returncode != 0 and "immutable facts" in blocked.stderr
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == head
    finally:
        if removed_grants:
            await owner.executemany(
                "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
                [(row["tenant_id"], row["role_id"], row["permission_id"]) for row in removed_grants],
            )
        receipt_rows = await owner.fetch(
            "SELECT id FROM risk_action_receipts WHERE tenant_id=$1 "
            "AND action IN ('notification:read','notifications:read_all') "
            "AND idempotency_key IN ('notification-one','notification-all')",
            tenant_id,
        )
        receipt_ids = [row["id"] for row in receipt_rows]
        if receipt_ids:
            await owner.execute("DELETE FROM risk_action_outbox WHERE receipt_id=ANY($1::uuid[])", receipt_ids)
        await owner.execute("DELETE FROM platform_audit_log WHERE id=ANY($1::uuid[])", audit_ids)
        if receipt_ids:
            await owner.execute("DELETE FROM risk_action_receipts WHERE id=ANY($1::uuid[])", receipt_ids)
        await owner.execute("DELETE FROM risk_notifications WHERE id=ANY($1::uuid[])", notification_ids)
        await owner.execute("DELETE FROM auth_sessions WHERE id=ANY($1::uuid[])", [session_id, expired_session])
        await runtime.close()
        await owner.close()


@pytest.mark.parametrize("reverse_order", [False, True])
async def test_overlapping_risk_pauses_restore_first_status_without_overwriting_operator_change(
    migrated_pg_url: str, reverse_order: bool
) -> None:
    baseline = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(baseline["baseline_tenant"]["id"])
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner, runtime = await asyncpg.connect(owner_dsn), await asyncpg.connect(runtime_dsn)
    session_id, rule_id = uuid.uuid4(), uuid.uuid4()
    campaign_ids = [uuid.uuid4(), uuid.uuid4()]
    scan_ids = [uuid.uuid4(), uuid.uuid4()]
    receipt_ids = [uuid.uuid4(), uuid.uuid4()]
    audit_ids: list[uuid.UUID] = []
    items: list[asyncpg.Record] = []
    try:
        account = await owner.fetchrow(
            "SELECT id,auth_version FROM accounts WHERE tenant_id=$1 AND email=$2",
            tenant_id,
            baseline["baseline_tenant"]["admin_email"],
        )
        await owner.execute(
            "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,now()+interval '1 hour',now(),now())",
            session_id,
            tenant_id,
            account["id"],
            account["auth_version"],
            uuid.uuid4().hex,
        )
        items = await owner.fetch(
            "SELECT ci.id,ci.public_id,cb.product_id FROM code_items ci JOIN code_batches cb "
            "ON cb.tenant_id=ci.tenant_id AND cb.id=ci.code_batch_id "
            "WHERE ci.tenant_id=$1 AND ci.status='activated' AND cb.product_id IS NOT NULL "
            "ORDER BY ci.id LIMIT 2",
            tenant_id,
        )
        assert len(items) == 2 and items[0]["product_id"] == items[1]["product_id"]
        await owner.executemany(
            "INSERT INTO campaigns(id,tenant_id,name,campaign_type,status,product_id,start_at,end_at,rules_json,created_at,updated_at) "
            "VALUES($1,$2,$3,'lottery','active',$4,now()-interval '1 day',now()+interval '1 day','{}',now(),now())",
            [
                (campaign_ids[0], tenant_id, f"overlap-normal-{uuid.uuid4()}", items[0]["product_id"]),
                (campaign_ids[1], tenant_id, f"overlap-operator-{uuid.uuid4()}", items[0]["product_id"]),
            ],
        )
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
        create_audit = uuid.uuid4()
        audit_ids.append(create_audit)
        await runtime.fetchrow(
            "SELECT * FROM mutate_risk_rule($1,$2,$3,'create',$4,NULL,$5,$6,'manual','block',$7,true)",
            tenant_id,
            session_id,
            create_audit,
            rule_id,
            f"overlap-rule-{rule_id}",
            "重叠暂停规则",
            json.dumps({"always_trigger": True}),
        )
        for index, campaign_id in enumerate(campaign_ids):
            link_audit = uuid.uuid4()
            audit_ids.append(link_audit)
            await runtime.fetchrow(
                "SELECT * FROM set_campaign_risk_rule($1,$2,$3,$4,$5,true,$6)",
                tenant_id,
                session_id,
                link_audit,
                rule_id,
                campaign_id,
                f"overlap-link-{index}-{rule_id}",
            )
        for index, item in enumerate(items):
            await owner.execute(
                "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,ip_hash,is_first_scan,is_valid_visit) "
                "VALUES($1,$2,$3,now()+($4*interval '1 second'),$5,false,true)",
                scan_ids[index],
                tenant_id,
                item["public_id"],
                index,
                str(index + 1) * 64,
            )
            decision = await runtime.fetchrow(
                "SELECT * FROM evaluate_execute_scan_risk($1,$2,$3,$4,$5,$6)",
                tenant_id,
                scan_ids[index],
                rule_id,
                receipt_ids[index],
                f"overlap-hit-{index}-{rule_id}",
                json.dumps({"request_source": "overlap acceptance"}),
            )
            assert set(decision["paused_campaign_ids"]) == set(campaign_ids)
        pause_rows = await owner.fetch(
            "SELECT id,campaign_id,prior_status,authority_updated_at,version FROM risk_campaign_pauses "
            "WHERE receipt_id=ANY($1::uuid[]) ORDER BY campaign_id,paused_at,id",
            receipt_ids,
        )
        assert len(pause_rows) == 4 and all(row["prior_status"] == "active" for row in pause_rows)
        for campaign_id in campaign_ids:
            tokens = {row["authority_updated_at"] for row in pause_rows if row["campaign_id"] == campaign_id}
            assert len(tokens) == 1
        await owner.execute(
            "UPDATE campaigns SET status='ended',updated_at=now()+interval '1 minute' WHERE id=$1", campaign_ids[1]
        )
        for campaign_id in campaign_ids:
            campaign_pauses = [row for row in pause_rows if row["campaign_id"] == campaign_id]
            if reverse_order:
                campaign_pauses.reverse()
            for index, pause in enumerate(campaign_pauses):
                resume_audit = uuid.uuid4()
                audit_ids.append(resume_audit)
                result = await runtime.fetchrow(
                    "SELECT * FROM resume_risk_campaign_pause($1,$2,$3,$4,$5,$6,$7)",
                    tenant_id,
                    session_id,
                    resume_audit,
                    pause["id"],
                    pause["version"],
                    f"overlap-resume-{campaign_id}-{index}",
                    "重叠暂停复核完成",
                )
                assert result["replayed"] is False
                replay = await runtime.fetchrow(
                    "SELECT * FROM resume_risk_campaign_pause($1,$2,$3,$4,$5,$6,$7)",
                    tenant_id,
                    session_id,
                    uuid.uuid4(),
                    pause["id"],
                    pause["version"],
                    f"overlap-resume-{campaign_id}-{index}",
                    "重叠暂停复核完成",
                )
                assert replay["replayed"] is True and replay["pause_id"] == result["pause_id"]
                assert replay["recorded_at"] == result["recorded_at"]
                if index == 0:
                    expected_intermediate = "paused" if campaign_id == campaign_ids[0] else "ended"
                    assert (
                        await owner.fetchval("SELECT status FROM campaigns WHERE id=$1", campaign_id)
                        == expected_intermediate
                    )
        states = await owner.fetch("SELECT id,status FROM campaigns WHERE id=ANY($1::uuid[]) ORDER BY id", campaign_ids)
        assert {row["id"]: row["status"] for row in states} == {
            campaign_ids[0]: "active",
            campaign_ids[1]: "ended",
        }
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM risk_campaign_pauses WHERE receipt_id=ANY($1::uuid[]) AND status='active'",
                receipt_ids,
            )
            == 0
        )
        delivery = await owner.fetchrow(
            "SELECT count(*) receipts,count(outbox.id) outbox,count(*) FILTER (WHERE outbox.id IS NULL) orphan "
            "FROM risk_action_receipts receipt LEFT JOIN risk_action_outbox outbox "
            "ON outbox.tenant_id=receipt.tenant_id AND outbox.receipt_id=receipt.id "
            "WHERE receipt.tenant_id=$1 AND receipt.risk_rule_id=$2",
            tenant_id,
            rule_id,
        )
        assert delivery["receipts"] == delivery["outbox"] and delivery["orphan"] == 0
    finally:
        for item in items:
            if await owner.fetchval("SELECT status='frozen' FROM code_items WHERE id=$1", item["id"]):
                recover_audit = uuid.uuid4()
                audit_ids.append(recover_audit)
                await runtime.fetchrow(
                    "SELECT * FROM transition_code_item_lifecycle($1,$2,$3,$4,'recover',NULL)",
                    tenant_id,
                    session_id,
                    recover_audit,
                    item["id"],
                )
        all_receipts = await owner.fetch(
            "SELECT id FROM risk_action_receipts WHERE tenant_id=$1 AND risk_rule_id=$2", tenant_id, rule_id
        )
        owned_receipts = [row["id"] for row in all_receipts]
        if owned_receipts:
            await owner.execute("DELETE FROM risk_action_outbox WHERE receipt_id=ANY($1::uuid[])", owned_receipts)
        await owner.execute("DELETE FROM risk_alerts WHERE tenant_id=$1 AND risk_rule_id=$2", tenant_id, rule_id)
        await owner.execute(
            "DELETE FROM risk_campaign_pauses WHERE tenant_id=$1 AND risk_rule_id=$2", tenant_id, rule_id
        )
        await owner.execute(
            "DELETE FROM interception_records WHERE tenant_id=$1 AND risk_rule_id=$2", tenant_id, rule_id
        )
        await owner.execute(
            "DELETE FROM campaign_risk_rules WHERE tenant_id=$1 AND risk_rule_id=$2", tenant_id, rule_id
        )
        if owned_receipts:
            await owner.execute("DELETE FROM risk_action_receipts WHERE id=ANY($1::uuid[])", owned_receipts)
        for item in items:
            rows = await owner.fetch(
                "SELECT id FROM platform_audit_log WHERE target_tenant_id=$1 AND resource=$2 "
                "AND action IN ('code_freeze','code_recover')",
                str(tenant_id),
                f"code_item:{item['public_id']}",
            )
            audit_ids.extend(row["id"] for row in rows)
        if audit_ids:
            await owner.execute("DELETE FROM platform_audit_log WHERE id=ANY($1::uuid[])", audit_ids)
        await owner.execute("DELETE FROM risk_rules WHERE tenant_id=$1 AND id=$2", tenant_id, rule_id)
        await owner.execute("DELETE FROM campaigns WHERE id=ANY($1::uuid[])", campaign_ids)
        await owner.execute("DELETE FROM scan_events WHERE id=ANY($1::uuid[])", scan_ids)
        await owner.execute("DELETE FROM auth_sessions WHERE id=$1", session_id)
        await runtime.close()
        await owner.close()


async def test_manual_freeze_is_actor_bound_atomic_and_replay_safe(migrated_pg_url: str) -> None:
    baseline = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(baseline["baseline_tenant"]["id"])
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = owner_dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    runtime = await asyncpg.connect(runtime_dsn)
    session_id = uuid.uuid4()
    expired_session = uuid.uuid4()
    ids = {"receipt": uuid.uuid4(), "audit": uuid.uuid4(), "alert": uuid.uuid4()}
    item = None
    removed_grants: list[asyncpg.Record] = []
    try:
        account = await owner.fetchrow(
            "SELECT id,auth_version FROM accounts WHERE tenant_id=$1 AND email=$2",
            tenant_id,
            baseline["baseline_tenant"]["admin_email"],
        )
        await owner.execute(
            "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,now()+interval '1 hour',now(),now())",
            session_id,
            tenant_id,
            account["id"],
            account["auth_version"],
            uuid.uuid4().hex,
        )
        item = await owner.fetchrow(
            "SELECT id,public_id FROM code_items WHERE tenant_id=$1 AND status='activated' ORDER BY id DESC LIMIT 1",
            tenant_id,
        )
        assert item is not None
        await runtime.execute("SELECT set_config('app.tenant_id',$1,false)", str(tenant_id))
        first = await runtime.fetchrow(
            "SELECT * FROM freeze_code_item_with_risk_alert($1,$2,$3,$4,$5,$6,$7,$8)",
            tenant_id,
            session_id,
            ids["receipt"],
            ids["audit"],
            ids["alert"],
            item["id"],
            "manual-freeze-1",
            "人工风控冻结",
        )
        assert first["prior_status"] == "activated" and first["current_status"] == "frozen"
        assert first["replayed"] is False and first["actor_id"] == account["id"]
        replay = await runtime.fetchrow(
            "SELECT * FROM freeze_code_item_with_risk_alert($1,$2,$3,$4,$5,$6,$7,$8)",
            tenant_id,
            session_id,
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            item["id"],
            "manual-freeze-1",
            "人工风控冻结",
        )
        assert replay["replayed"] is True and replay["receipt_id"] == ids["receipt"]
        assert replay["risk_alert_id"] == ids["alert"]
        facts = await owner.fetchrow(
            "SELECT (SELECT status::text FROM code_items WHERE id=$1) item_status,"
            "(SELECT count(*) FROM risk_action_receipts WHERE id=$2) receipts,"
            "(SELECT count(*) FROM risk_alerts WHERE id=$3 AND risk_action_receipt_id=$2 AND actor_id=$4 "
            "AND source='manual_freeze' AND reason_snapshot='人工风控冻结' AND prior_code_status='activated' "
            "AND current_code_status='frozen') alerts,"
            "(SELECT count(*) FROM platform_audit_log WHERE id=$5) audits,"
            "(SELECT count(*) FROM risk_action_outbox WHERE receipt_id=$2) outbox",
            item["id"],
            ids["receipt"],
            ids["alert"],
            account["id"],
            ids["audit"],
        )
        assert dict(facts) == {"item_status": "frozen", "receipts": 1, "alerts": 1, "audits": 1, "outbox": 1}
        initial_head = await owner.fetchval("SELECT version_num FROM alembic_version")
        blocked = _alembic(migrated_pg_url, "downgrade", "-1")
        assert blocked.returncode != 0 and "immutable facts" in blocked.stderr
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == initial_head
        with pytest.raises(asyncpg.UniqueViolationError):
            await runtime.fetchrow(
                "SELECT * FROM freeze_code_item_with_risk_alert($1,$2,$3,$4,$5,$6,$7,$8)",
                tenant_id,
                session_id,
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                item["id"],
                "manual-freeze-1",
                "changed reason",
            )
        failure_ids = {"receipt": uuid.uuid4(), "audit": uuid.uuid4(), "alert": uuid.uuid4()}
        with pytest.raises(asyncpg.InvalidParameterValueError):
            await runtime.fetchrow(
                "SELECT * FROM freeze_code_item_with_risk_alert($1,$2,$3,$4,$5,$6,$7,$8)",
                tenant_id,
                session_id,
                failure_ids["receipt"],
                failure_ids["audit"],
                failure_ids["alert"],
                item["id"],
                "manual-freeze-state-failure",
                "already frozen",
            )
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM risk_action_receipts WHERE id=$1) "
            "OR EXISTS(SELECT 1 FROM platform_audit_log WHERE id=$2) "
            "OR EXISTS(SELECT 1 FROM risk_alerts WHERE id=$3) "
            "OR EXISTS(SELECT 1 FROM risk_action_outbox WHERE receipt_id=$1)",
            failure_ids["receipt"],
            failure_ids["audit"],
            failure_ids["alert"],
        )
        await owner.execute(
            "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,now()-interval '1 minute',now(),now())",
            expired_session,
            tenant_id,
            account["id"],
            account["auth_version"],
            uuid.uuid4().hex,
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.fetchrow(
                "SELECT * FROM freeze_code_item_with_risk_alert($1,$2,$3,$4,$5,$6,$7,$8)",
                tenant_id,
                expired_session,
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                item["id"],
                "expired-session-freeze",
                "must fail auth",
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.fetchrow(
                "SELECT * FROM freeze_code_item_with_risk_alert($1,$2,$3,$4,$5,$6,$7,$8)",
                uuid.uuid4(),
                session_id,
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                item["id"],
                "cross-tenant-freeze",
                "must fail tenant",
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.execute(
                "INSERT INTO risk_alerts(id,tenant_id,alert_type,public_id,code_item_id,detail,resolved) "
                "VALUES($1,$2,'risk_frozen',$3,$4,'forbidden',false)",
                uuid.uuid4(),
                tenant_id,
                item["public_id"],
                item["id"],
            )
        removed_grants = await owner.fetch(
            "DELETE FROM role_permissions rp USING permissions p,account_roles ar "
            "WHERE p.tenant_id=rp.tenant_id AND p.id=rp.permission_id AND p.code='risk:manage' "
            "AND ar.tenant_id=rp.tenant_id AND ar.role_id=rp.role_id AND ar.account_id=$1 "
            "RETURNING rp.tenant_id,rp.role_id,rp.permission_id",
            account["id"],
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.fetchrow(
                "SELECT * FROM freeze_code_item_with_risk_alert($1,$2,$3,$4,$5,$6,$7,$8)",
                tenant_id,
                session_id,
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                item["id"],
                "manual-freeze-1",
                "人工风控冻结",
            )
        assert await owner.fetchval("SELECT count(*) FROM risk_action_receipts WHERE id=$1", ids["receipt"]) == 1
    finally:
        if removed_grants:
            await owner.executemany(
                "INSERT INTO role_permissions(tenant_id,role_id,permission_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
                [(row["tenant_id"], row["role_id"], row["permission_id"]) for row in removed_grants],
            )
        recover_audit_id = None
        if item is not None and await owner.fetchval(
            "SELECT status='frozen' FROM code_items WHERE tenant_id=$1 AND id=$2", tenant_id, item["id"]
        ):
            recover_audit_id = uuid.uuid4()
            await runtime.fetchrow(
                "SELECT * FROM transition_code_item_lifecycle($1,$2,$3,$4,'recover',NULL)",
                tenant_id,
                session_id,
                recover_audit_id,
                item["id"],
            )
        await owner.execute("DELETE FROM risk_action_outbox WHERE receipt_id=$1", ids["receipt"])
        await owner.execute("DELETE FROM risk_alerts WHERE id=$1", ids["alert"])
        await owner.execute("DELETE FROM risk_action_receipts WHERE id=$1", ids["receipt"])
        audit_ids = [ids["audit"]] + ([recover_audit_id] if recover_audit_id else [])
        await owner.execute("DELETE FROM platform_audit_log WHERE id=ANY($1::uuid[])", audit_ids)
        await owner.execute("DELETE FROM auth_sessions WHERE id=ANY($1::uuid[])", [session_id, expired_session])
        await runtime.close()
        await owner.close()
