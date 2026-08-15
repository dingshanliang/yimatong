"""Real ASGI authorization and tenant-isolation coverage for webhook management."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from uuid6 import uuid7

from tests.test_acceptance.test_code_batch_delivery_contract import _seed_catalog
from tests.test_acceptance.test_export_ledger_authority import _grant_permission, _owner_dsn, _session

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def _role_principal(
    owner: asyncpg.Connection,
    ids: dict[str, uuid.UUID],
    role_name: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    account_id, role_id = uuid7(), uuid7()
    await owner.execute(
        "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,failed_login_attempts,"
        "is_active,auth_version,must_change_password,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'x',$5,0,true,0,false,now(),now())",
        account_id,
        ids["tenant"],
        ids["organization"],
        f"webhook-{role_name}-{account_id.hex[:8]}@test.local",
        f"Webhook {role_name}",
    )
    await owner.execute(
        "INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES($1,$2,$3,now(),now())",
        role_id,
        ids["tenant"],
        role_name,
    )
    await owner.execute(
        "INSERT INTO account_roles(tenant_id,account_id,role_id) VALUES($1,$2,$3)",
        ids["tenant"],
        account_id,
        role_id,
    )
    session_id = await _session(owner, ids, account_id=account_id)
    return account_id, session_id


async def test_webhook_management_real_asgi_enforces_live_direct_admin_and_secret_once(
    migrated_pg_url: str,
    monkeypatch,
) -> None:
    from app.api.v1 import webhooks as webhook_api
    from app.core import database
    from app.main import app
    from app.services.redis_cache import AsyncRedisCache
    from app.utils.security import create_access_token

    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    tenant_a = await _seed_catalog(owner, "u08d-webhook-asgi-a")
    tenant_b = await _seed_catalog(owner, "u08d-webhook-asgi-b")
    for ids in (tenant_a, tenant_b):
        await _grant_permission(owner, ids, "webhook:read")
        await _grant_permission(owner, ids, "webhook:manage")
    admin_a_session = await _session(owner, tenant_a)
    admin_b_session = await _session(owner, tenant_b)
    denied_principals = {
        role: await _role_principal(owner, tenant_a, role)
        for role in ("operator", "viewer", "distributor", "store_guide")
    }
    agency = await _seed_catalog(owner, "u08d-webhook-asgi-agency")
    await owner.execute("UPDATE tenants SET tenant_type='agency' WHERE id=$1", agency["tenant"])
    agency_session = await _session(owner, agency)
    revoked_session = await _session(owner, tenant_a, revoked=True)
    expired_session = await _session(owner, tenant_a, expires_at=datetime.now(UTC) - timedelta(minutes=1))

    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    runtime_engine = create_async_engine(runtime_url)
    owner_engine = create_async_engine(migrated_pg_url)
    monkeypatch.setattr(
        database,
        "async_session_factory",
        async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False),
    )
    monkeypatch.setattr(
        database,
        "control_session_factory",
        async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False),
    )
    monkeypatch.setattr(database, "_is_pg", True)

    async def not_revoked(_self, _key: str) -> bool:
        return False

    async def allow_rate(*_args, **_kwargs):
        return True, 1

    async def allow_public_destination(url: str) -> str:
        return url

    monkeypatch.setattr(AsyncRedisCache, "is_token_revoked", not_revoked)
    monkeypatch.setattr(webhook_api._webhook_management_cache, "rate_limit_check_shared", allow_rate)
    monkeypatch.setattr(webhook_api, "validate_webhook_destination", allow_public_destination)

    def token(ids, account_id, role, session_id, **extra):
        return create_access_token(
            str(ids["tenant"]),
            str(account_id),
            role,
            "brand" if ids is not agency else "agency",
            extra={"sid": str(session_id), "auth_version": 0, **extra},
        )

    admin_a_token = token(tenant_a, tenant_a["account"], "admin", admin_a_session)
    admin_b_token = token(tenant_b, tenant_b["account"], "admin", admin_b_session)
    admin_a_headers = {"Authorization": f"Bearer {admin_a_token}"}
    admin_b_headers = {"Authorization": f"Bearer {admin_b_token}"}
    body = {"url": "https://hooks.example.com/events", "events": ["scan.created", "risk.alert"]}
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            created = await client.post("/api/v1/webhooks/endpoints", json=body, headers=admin_a_headers)
            assert created.status_code == 201, created.text
            endpoint_id = uuid.UUID(created.json()["id"])
            assert created.json()["secret"].startswith("whsec_")
            assert created.json()["config_version"] == 1

            listed = await client.get("/api/v1/webhooks/endpoints", headers=admin_a_headers)
            assert listed.status_code == 200, listed.text
            assert len(listed.json()) == 1
            assert listed.json()[0]["id"] == str(endpoint_id)
            assert "secret" not in listed.json()[0]

            updated = await client.patch(
                f"/api/v1/webhooks/endpoints/{endpoint_id}",
                json={"enabled": False},
                headers={**admin_a_headers, "If-Match": "1"},
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["config_version"] == 2
            assert "secret" not in updated.json()
            stored_material = await owner.fetchrow(
                "SELECT secret_ciphertext,secret_nonce,secret_key_id FROM webhook_endpoints WHERE id=$1",
                endpoint_id,
            )

            cross_update = await client.patch(
                f"/api/v1/webhooks/endpoints/{endpoint_id}",
                json={"enabled": True},
                headers={**admin_b_headers, "If-Match": "2"},
            )
            cross_delete = await client.delete(
                f"/api/v1/webhooks/endpoints/{endpoint_id}",
                headers={**admin_b_headers, "If-Match": "2"},
            )
            assert cross_update.status_code == cross_delete.status_code == 404

            deleted = await client.delete(
                f"/api/v1/webhooks/endpoints/{endpoint_id}",
                headers={**admin_a_headers, "If-Match": "2"},
            )
            assert deleted.status_code == 200, deleted.text

            before_denied = await owner.fetchval("SELECT count(*) FROM webhook_endpoints")
            for role, (account_id, session_id) in denied_principals.items():
                denied_token = token(tenant_a, account_id, role, session_id)
                denied = await client.post(
                    "/api/v1/webhooks/endpoints",
                    json=body,
                    headers={"Authorization": f"Bearer {denied_token}"},
                )
                assert denied.status_code == 403, (role, denied.text)

            agency_token = token(agency, agency["account"], "admin", agency_session)
            acting_token = token(
                tenant_a,
                tenant_a["account"],
                "admin",
                admin_a_session,
                acting_tenant_id=str(tenant_b["tenant"]),
            )
            revoked_token = token(tenant_a, tenant_a["account"], "admin", revoked_session)
            expired_token = token(tenant_a, tenant_a["account"], "admin", expired_session)
            platform_token = create_access_token("platform", "platform-admin", "platform_admin", "platform")
            denied_tokens = (agency_token, acting_token, revoked_token, expired_token, platform_token)
            for denied_token in denied_tokens:
                denied = await client.post(
                    "/api/v1/webhooks/endpoints",
                    json=body,
                    headers={"Authorization": f"Bearer {denied_token}"},
                )
                assert denied.status_code in {401, 403}, denied.text

            operator_id, operator_session = denied_principals["operator"]
            operator_token = token(tenant_a, operator_id, "operator", operator_session)
            bearer_wins = await client.get(
                "/api/v1/webhooks/endpoints",
                headers={
                    "Authorization": f"Bearer {operator_token}",
                    "Cookie": f"access_token={admin_a_token}",
                },
            )
            assert bearer_wins.status_code == 403, bearer_wins.text

            api_key_only = await client.get(
                "/api/v1/webhooks/endpoints",
                headers={"X-Api-Key": "ymt_" + "a" * 48},
            )
            assert api_key_only.status_code == 401, api_key_only.text
            assert await owner.fetchval("SELECT count(*) FROM webhook_endpoints") == before_denied

            manage_permission_id = await owner.fetchval(
                "SELECT id FROM permissions WHERE tenant_id=$1 AND code='webhook:manage'",
                tenant_a["tenant"],
            )
            await owner.execute(
                "DELETE FROM role_permissions WHERE tenant_id=$1 AND role_id=$2 AND permission_id=$3",
                tenant_a["tenant"],
                tenant_a["admin_role"],
                manage_permission_id,
            )
            stale_permission = await client.post(
                "/api/v1/webhooks/endpoints",
                json=body,
                headers=admin_a_headers,
            )
            assert stale_permission.status_code == 403, stale_permission.text
            assert await owner.fetchval("SELECT count(*) FROM webhook_endpoints") == before_denied

        assert stored_material is not None
        assert len(stored_material["secret_ciphertext"]) >= 17
        assert len(stored_material["secret_nonce"]) == 12
        assert stored_material["secret_key_id"].startswith("aes-master-v")
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name='webhook_endpoints' AND column_name='secret')"
        )
        audits = await owner.fetch(
            "SELECT id,action,details FROM platform_audit_log WHERE target_tenant_id=$1 "
            "AND resource=$2 ORDER BY timestamp ASC,id ASC",
            str(tenant_a["tenant"]),
            f"webhook_endpoint:{endpoint_id}",
        )
        assert [row["action"] for row in audits] == [
            "webhook_endpoint_created",
            "webhook_endpoint_updated",
            "webhook_endpoint_deleted",
        ]
        audit_details = [json.loads(row["details"]) for row in audits]
        assert all("url" not in details and "secret" not in details for details in audit_details)
        assert audit_details[0]["url_digest"] == hashlib.sha256(body["url"].encode()).hexdigest()
    finally:
        await owner.close()
        await runtime_engine.dispose()
        await owner_engine.dispose()
