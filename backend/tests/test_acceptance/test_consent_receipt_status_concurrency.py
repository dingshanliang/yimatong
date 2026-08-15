"""Real PostgreSQL reload race contract for durable consent receipts."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from uuid6 import uuid7

from app.services.scan_token import create_scan_token
from app.utils.client_ip import compute_ip_hash
from tests.test_acceptance.test_code_item_lifecycle_db_contract import _alembic
from tests.test_acceptance.test_consumer_consent_authority import _owner_dsn, _runtime_dsn, _seed_scan
from tests.test_acceptance.test_wechat_oauth_app_authority import _oauth_client, _Redis

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]

PARENT_REVISION = "u6i0a1b2c3d4"
STATUS_SIGNATURE = "public.get_consumer_consent_receipt_status(uuid,uuid,uuid,timestamptz,text,text,uuid)"


def _token(graph: dict[str, object], *, other_subject: bool = False) -> str:
    suffix = "other_" if other_subject else ""
    return create_scan_token(
        str(graph["public_id"]),
        compute_ip_hash("203.0.113.91"),
        tenant_id=str(graph["tenant"]),
        scan_event_id=str(graph[f"{suffix}scan_event_id"]),
        scan_time=graph[f"{suffix}scan_time"].isoformat(),
        visitor_id=str(graph[f"{suffix}visitor_id"]),
    )


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Forwarded-For": "203.0.113.91",
        "User-Agent": "consent-reload-race-acceptance",
    }


async def test_receipt_status_waits_for_concurrent_resolver_scan_then_returns_200(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    resolver = await asyncpg.connect(_runtime_dsn(migrated_pg_url))
    observer = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    graph = await _seed_scan(owner, "consent-receipt-reload-race")
    original_token = _token(graph)
    resolver_tx = resolver.transaction()
    status_task: asyncio.Task | None = None
    try:
        async with _oauth_client(migrated_pg_url, monkeypatch, _Redis()) as client:
            policy_response = await client.get(
                "/api/v1/public/consents/policy",
                params={"purpose": "lead_capture"},
                headers=_headers(original_token),
            )
            assert policy_response.status_code == 200, policy_response.text
            policy = policy_response.json()
            grant = await client.post(
                "/api/v1/public/consents",
                json={
                    "purpose": "lead_capture",
                    "policy_version": policy["policy_version"],
                    "policy_digest": policy["policy_digest"],
                    "idempotency_key": f"reload-race-{uuid.uuid4()}",
                },
                headers=_headers(original_token),
            )
            assert grant.status_code == 201, grant.text
            consent_id = uuid.UUID(grant.json()["consent_id"])
            before = await owner.fetchrow(
                "SELECT status,updated_at,xmin::text AS row_version FROM consent_records WHERE tenant_id=$1 AND id=$2",
                graph["tenant"],
                consent_id,
            )
            action_count = await owner.fetchval(
                "SELECT count(*) FROM consumer_consent_actions WHERE tenant_id=$1 AND consent_id=$2",
                graph["tenant"],
                consent_id,
            )

            # Mirror the real resolver transaction: update the durable visitor,
            # then call the function-only scan writer and keep both locks until
            # the request transaction commits.
            await resolver_tx.start()
            await resolver.execute("SELECT set_config('app.tenant_id',$1,true)", str(graph["tenant"]))
            await resolver.execute(
                "UPDATE anonymous_visitors SET last_seen_at=now(),updated_at=now() "
                "WHERE tenant_id=$1 AND visitor_id=$2",
                graph["tenant"],
                graph["visitor_id"],
            )
            new_scan = await resolver.fetchrow(
                "SELECT * FROM record_public_code_scan($1,$2,$3,NULL,$4,'browser',$5)",
                graph["tenant"],
                graph["public_id"],
                uuid7(),
                "consent-reload-race-acceptance",
                graph["visitor_id"],
            )
            assert new_scan and new_scan["is_valid_visit"] is True

            status_task = asyncio.create_task(
                client.get(
                    f"/api/v1/public/consents/{consent_id}/status",
                    headers=_headers(original_token),
                )
            )
            for _ in range(100):
                waiting = await observer.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                    "WHERE datname=current_database() AND usename='yimatong_app' "
                    "AND query LIKE '%get_consumer_consent_receipt_status%' "
                    "AND wait_event_type='Lock')"
                )
                if waiting:
                    break
                await asyncio.sleep(0.02)
            assert waiting and not status_task.done()

            await resolver_tx.commit()
            status_response = await asyncio.wait_for(status_task, timeout=5)
            assert status_response.status_code == 200, status_response.text
            assert status_response.json()["consent_id"] == str(consent_id)
            assert status_response.json()["status"] == "granted"

            # A different visitor remains non-enumerating even after the
            # wait-safe change; only the exact durable subject can recover it.
            denied = await client.get(
                f"/api/v1/public/consents/{consent_id}/status",
                headers=_headers(_token(graph, other_subject=True)),
            )
            assert denied.status_code == 403, denied.text
            after = await owner.fetchrow(
                "SELECT status,updated_at,xmin::text AS row_version FROM consent_records WHERE tenant_id=$1 AND id=$2",
                graph["tenant"],
                consent_id,
            )
            assert after == before
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM consumer_consent_actions WHERE tenant_id=$1 AND consent_id=$2",
                    graph["tenant"],
                    consent_id,
                )
                == action_count
            )
    finally:
        if status_task is not None and not status_task.done():
            status_task.cancel()
            await asyncio.gather(status_task, return_exceptions=True)
        if resolver.is_in_transaction():
            await resolver_tx.rollback()
        await observer.close()
        await resolver.close()
        await owner.close()


async def test_receipt_status_wait_safe_definition_round_trips(migrated_pg_url: str) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    try:
        wait_safe = await owner.fetchval("SELECT pg_get_functiondef($1::regprocedure)", STATUS_SIGNATURE)
        assert "FOR SHARE OF visitor" in wait_safe
        assert "FOR SHARE OF event,item" in wait_safe
        assert "FOR SHARE OF visitor NOWAIT" not in wait_safe
        assert "FOR SHARE OF event,item NOWAIT" not in wait_safe
        assert "FOR SHARE OF receipt NOWAIT" not in wait_safe
        assert wait_safe.index("FOR SHARE OF visitor") < wait_safe.index("FOR SHARE OF event,item")
        assert await owner.fetchval(
            "SELECT NOT EXISTS(SELECT 1 FROM pg_proc AS procedure, "
            "aclexplode(coalesce(procedure.proacl,acldefault('f',procedure.proowner))) AS acl "
            "WHERE procedure.oid=$1::regprocedure AND acl.grantee=0 AND acl.privilege_type='EXECUTE')",
            STATUS_SIGNATURE,
        )
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')",
            STATUS_SIGNATURE,
        )

        await asyncio.to_thread(_alembic, migrated_pg_url, "downgrade", PARENT_REVISION)
        parent = await owner.fetchval("SELECT pg_get_functiondef($1::regprocedure)", STATUS_SIGNATURE)
        assert "validate_consumer_consent_subject" in parent
        assert "FOR SHARE OF receipt NOWAIT" in parent
        assert await owner.fetchval(
            "SELECT NOT EXISTS(SELECT 1 FROM pg_proc AS procedure, "
            "aclexplode(coalesce(procedure.proacl,acldefault('f',procedure.proowner))) AS acl "
            "WHERE procedure.oid=$1::regprocedure AND acl.grantee=0 AND acl.privilege_type='EXECUTE')",
            STATUS_SIGNATURE,
        )
        assert await owner.fetchval(
            "SELECT has_function_privilege('yimatong_app',$1,'EXECUTE')",
            STATUS_SIGNATURE,
        )

        await asyncio.to_thread(_alembic, migrated_pg_url, "upgrade", "head")
        repaired = await owner.fetchval("SELECT pg_get_functiondef($1::regprocedure)", STATUS_SIGNATURE)
        assert "FOR SHARE OF visitor" in repaired
        assert "FOR SHARE OF visitor NOWAIT" not in repaired
        assert "FOR SHARE OF event,item NOWAIT" not in repaired
        assert "FOR SHARE OF receipt NOWAIT" not in repaired
    finally:
        await owner.close()
