import asyncio
import os
import subprocess
from pathlib import Path

import asyncpg
import pytest

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]
BACKEND_DIR = Path(__file__).resolve().parents[2]


async def test_official_and_rich_seed_replay_exact_diversion_authority_facts(migrated_pg_url):
    environment = os.environ.copy()
    environment.update(
        {
            "database_url": migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"),
            "control_database_url": migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@"),
            "migration_database_url": migrated_pg_url,
            "environment": "test",
        }
    )

    def run(*arguments: str, rich: bool = False) -> None:
        command = (
            ["uv", "run", "python", "scripts/seed_demo.py", *arguments]
            if rich
            else ["uv", "run", "python", "-m", "app.cli", *arguments]
        )
        result = subprocess.run(
            command,
            cwd=BACKEND_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    await asyncio.to_thread(run, "all")
    await asyncio.to_thread(run, "all")
    await asyncio.to_thread(run, "reset", "--target", "demo", rich=True)

    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        tenant_id = await owner.fetchval("SELECT id FROM tenants WHERE slug='demo'")

        async def snapshot():
            return await owner.fetchrow(
                """
                SELECT
                  (SELECT count(*) FROM diversion_observations WHERE tenant_id=$1) observations,
                  (SELECT count(*) FROM diversion_clues WHERE tenant_id=$1) clues,
                  (SELECT coalesce(sum(observation_count),0) FROM diversion_clues WHERE tenant_id=$1) clue_total,
                  (SELECT count(*) FROM diversion_observations observation
                   LEFT JOIN scan_events event ON event.tenant_id=observation.tenant_id
                     AND event.id=observation.scan_event_id AND event.scan_time=observation.observed_at
                     AND event.public_id=observation.public_id
                     AND event.ip_hash IS NOT DISTINCT FROM observation.ip_hash
                   LEFT JOIN code_items item ON item.tenant_id=observation.tenant_id
                     AND item.id=observation.code_item_id AND item.public_id=observation.public_id
                   WHERE observation.tenant_id=$1
                     AND (event.id IS NULL OR NOT event.is_valid_visit OR item.id IS NULL)) invalid,
                  (SELECT count(*) FROM diversion_evidence WHERE tenant_id=$1) evidence,
                  (SELECT count(*) FROM diversion_investigation_history WHERE tenant_id=$1) history,
                  (SELECT count(*) FROM diversion_action_receipts WHERE tenant_id=$1) action_receipts
                """,
                tenant_id,
            )

        initial = await snapshot()
        assert dict(initial) == {
            "observations": 5,
            "clues": 5,
            "clue_total": 5,
            "invalid": 0,
            "evidence": 0,
            "history": 0,
            "action_receipts": 0,
        }

        async def risk_snapshot():
            return await owner.fetchrow(
                """
                SELECT
                  (SELECT count(*) FROM risk_rules WHERE tenant_id=$1) rules,
                  (SELECT count(*) FROM interception_records WHERE tenant_id=$1) interceptions,
                  (SELECT count(*) FROM risk_alerts WHERE tenant_id=$1) alerts,
                  (SELECT count(*) FROM risk_action_receipts WHERE tenant_id=$1) receipts,
                  (SELECT count(*) FROM risk_action_outbox WHERE tenant_id=$1) outbox,
                  (SELECT count(*) FROM risk_action_receipts WHERE tenant_id=$1
                     AND action='rule:create') rule_create_receipts,
                  (SELECT count(*) FROM risk_action_receipts WHERE tenant_id=$1
                     AND action='evaluate') evaluate_receipts,
                  (SELECT count(*) FROM risk_action_receipts WHERE tenant_id=$1
                     AND action='manual_freeze') manual_freeze_receipts,
                  (SELECT count(*) FROM interception_records WHERE tenant_id=$1
                     AND action='warn') warn_interceptions,
                  (SELECT count(*) FROM interception_records WHERE tenant_id=$1
                     AND action='block') block_interceptions,
                  (SELECT count(*) FROM risk_action_receipts receipt
                   LEFT JOIN risk_action_outbox outbox ON outbox.tenant_id=receipt.tenant_id
                     AND outbox.receipt_id=receipt.id
                   WHERE receipt.tenant_id=$1 AND outbox.id IS NULL) receipts_without_outbox,
                  (SELECT count(*) FROM risk_alerts alert
                   JOIN risk_action_receipts receipt ON receipt.tenant_id=alert.tenant_id
                     AND receipt.id=alert.risk_action_receipt_id
                   WHERE alert.tenant_id=$1 AND receipt.action='rule:create') rule_create_alerts,
                  (SELECT count(*) FROM risk_action_receipts receipt
                   LEFT JOIN risk_alerts alert ON alert.tenant_id=receipt.tenant_id
                     AND alert.risk_action_receipt_id=receipt.id
                   WHERE receipt.tenant_id=$1 AND receipt.action IN ('evaluate','manual_freeze')
                     AND alert.id IS NULL) action_receipts_without_alert,
                  (SELECT count(*) FROM risk_action_receipts receipt
                   LEFT JOIN scan_events event ON event.tenant_id=receipt.tenant_id
                     AND event.id=receipt.scan_event_id
                   LEFT JOIN risk_rules rule ON rule.tenant_id=receipt.tenant_id
                     AND rule.id=receipt.risk_rule_id
                   WHERE receipt.tenant_id=$1 AND receipt.action='evaluate'
                     AND (event.id IS NULL OR rule.id IS NULL
                       OR (receipt.result->>'triggered')::boolean IS NOT TRUE)) invalid_evaluations,
                  (SELECT count(*) FROM risk_alerts alert
                   LEFT JOIN risk_action_receipts receipt ON receipt.tenant_id=alert.tenant_id
                     AND receipt.id=alert.risk_action_receipt_id
                   LEFT JOIN code_items item ON item.tenant_id=alert.tenant_id
                     AND item.id=alert.code_item_id AND item.public_id=alert.public_id
                   WHERE alert.tenant_id=$1 AND (receipt.id IS NULL OR item.id IS NULL)) invalid_alerts,
                  (SELECT count(*) FROM interception_records interception
                   LEFT JOIN risk_rules rule ON rule.tenant_id=interception.tenant_id
                     AND rule.id=interception.risk_rule_id
                   LEFT JOIN code_items item ON item.tenant_id=interception.tenant_id
                     AND item.id=interception.code_item_id
                   WHERE interception.tenant_id=$1 AND (rule.id IS NULL OR item.id IS NULL)) invalid_interceptions
                """,
                tenant_id,
            )

        initial_risk = await risk_snapshot()
        assert initial_risk["rules"] == 2
        assert initial_risk["interceptions"] == 2
        assert initial_risk["receipts"] == initial_risk["outbox"]
        assert initial_risk["rule_create_receipts"] == 2
        assert initial_risk["evaluate_receipts"] == 2
        assert initial_risk["manual_freeze_receipts"] >= 1
        assert initial_risk["warn_interceptions"] == initial_risk["block_interceptions"] == 1
        assert initial_risk["receipts_without_outbox"] == 0
        assert initial_risk["rule_create_alerts"] == 0
        assert initial_risk["action_receipts_without_alert"] == 0
        assert initial_risk["invalid_evaluations"] == 0
        assert initial_risk["invalid_alerts"] == 0
        assert initial_risk["invalid_interceptions"] == 0
        for table in (
            "risk_rules",
            "risk_alerts",
            "interception_records",
            "risk_action_receipts",
            "risk_action_outbox",
        ):
            for privilege in ("INSERT", "UPDATE", "DELETE"):
                assert not await owner.fetchval(
                    "SELECT has_table_privilege('yimatong_app',$1,$2)",
                    f"public.{table}",
                    privilege,
                )
        await asyncio.to_thread(run, "generate", "--target", "demo", rich=True)
        assert await snapshot() == initial
        assert await risk_snapshot() == initial_risk
        await asyncio.to_thread(run, "generate", "--target", "demo", rich=True)
        assert await snapshot() == initial
        assert await risk_snapshot() == initial_risk
    finally:
        await owner.close()
