"""Real ASGI journey for repeated current-policy consent grants."""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.services.scan_token import create_scan_token
from app.utils.client_ip import compute_ip_hash
from tests.test_acceptance.test_consumer_consent_authority import _owner_dsn, _seed_scan
from tests.test_acceptance.test_wechat_oauth_app_authority import _oauth_client, _Redis

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def test_privacy_and_lead_form_repeat_grants_share_receipt_and_capture_uses_it(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    graph = await _seed_scan(owner, "consent-repeat-app")
    token = create_scan_token(
        str(graph["public_id"]),
        compute_ip_hash("203.0.113.91"),
        tenant_id=str(graph["tenant"]),
        scan_event_id=str(graph["scan_event_id"]),
        scan_time=graph["scan_time"].isoformat(),
        visitor_id=str(graph["visitor_id"]),
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Forwarded-For": "203.0.113.91",
        "User-Agent": "consent-repeat-acceptance",
    }
    try:
        async with _oauth_client(migrated_pg_url, monkeypatch, _Redis()) as client:
            policy_response = await client.get(
                "/api/v1/public/consents/policy",
                params={"purpose": "lead_capture"},
                headers=headers,
            )
            assert policy_response.status_code == 200, policy_response.text
            policy = policy_response.json()
            privacy_grant = await client.post(
                "/api/v1/public/consents",
                json={
                    "purpose": "lead_capture",
                    "policy_version": policy["policy_version"],
                    "policy_digest": policy["policy_digest"],
                    "idempotency_key": f"privacy-policy-{uuid.uuid4()}",
                },
                headers=headers,
            )
            assert privacy_grant.status_code == 201, privacy_grant.text
            lead_form_grant = await client.post(
                "/api/v1/public/consents",
                json={
                    "purpose": "lead_capture",
                    "policy_version": policy["policy_version"],
                    "policy_digest": policy["policy_digest"],
                    "idempotency_key": f"lead-form-{uuid.uuid4()}",
                },
                headers=headers,
            )
            assert lead_form_grant.status_code == 201, lead_form_grant.text
            assert lead_form_grant.json()["consent_id"] == privacy_grant.json()["consent_id"]
            assert lead_form_grant.json()["replayed"] is True

            receipt_id = privacy_grant.json()["consent_id"]
            captured = await client.post(
                "/api/v1/consumers/lead-capture",
                json={
                    "consent_id": receipt_id,
                    "idempotency_key": f"lead-capture-{uuid.uuid4()}",
                    "name": "重复授权用户",
                    "phone": "13900139000",
                    "region": "上海",
                    "intention": "了解产品",
                },
                headers=headers,
            )
            assert captured.status_code == 201, captured.text
            consumer_id = uuid.UUID(captured.json()["consumer_id"])
            assert await owner.fetchval(
                "SELECT lead_consent_id=$1 AND lead_contact_suppressed=false "
                "FROM consumer_profiles WHERE tenant_id=$2 AND id=$3",
                uuid.UUID(receipt_id),
                graph["tenant"],
                consumer_id,
            )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM consent_records WHERE tenant_id=$1 AND purpose='lead_capture' "
                    "AND status='granted'",
                    graph["tenant"],
                )
                == 1
            )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM consumer_consent_actions WHERE tenant_id=$1 "
                    "AND consent_id=$2 AND action='grant'",
                    graph["tenant"],
                    uuid.UUID(receipt_id),
                )
                == 2
            )
    finally:
        await owner.close()
