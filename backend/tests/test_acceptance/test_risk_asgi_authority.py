"""Real ASGI coverage for the tenant risk authority boundary."""

import asyncio
import uuid

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.test_acceptance.conftest import seed_baseline

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def test_risk_asgi_crud_link_exact_evaluate_resume_and_roles(migrated_pg_url: str, monkeypatch) -> None:
    from app.core import database
    from app.main import app
    from app.middleware.rate_limit import rate_limiter
    from app.services.redis_cache import AsyncRedisCache
    from app.utils.security import create_access_token

    baseline = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(baseline["baseline_tenant"]["id"])
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    account = await owner.fetchrow(
        "SELECT id,auth_version FROM accounts WHERE tenant_id=$1 AND email=$2",
        tenant_id,
        baseline["baseline_tenant"]["admin_email"],
    )
    operator_id, viewer_id = uuid.uuid4(), uuid.uuid4()
    await owner.execute(
        "UPDATE tenants SET enabled_features=(coalesce(enabled_features,'{}'::json)::jsonb"
        "||'{\"risk_module\":true}'::jsonb)::json WHERE id=$1",
        tenant_id,
    )
    for role in ("operator", "viewer"):
        await owner.execute(
            "INSERT INTO roles(id,tenant_id,name,description,created_at,updated_at) "
            "VALUES($1,$2,$3,$3,now(),now()) ON CONFLICT(tenant_id,name) DO NOTHING",
            uuid.uuid4(),
            tenant_id,
            role,
        )
    for identity_id, email, role in (
        (operator_id, "risk-operator@acceptance.local", "operator"),
        (viewer_id, "risk-viewer@acceptance.local", "viewer"),
    ):
        await owner.execute(
            "INSERT INTO accounts(id,tenant_id,organization_id,email,hashed_password,name,is_active,auth_version,"
            "must_change_password,failed_login_attempts,created_at,updated_at) "
            "SELECT $1,tenant_id,organization_id,$2,hashed_password,$3,true,0,false,0,now(),now() FROM accounts WHERE id=$4",
            identity_id,
            email,
            role,
            account["id"],
        )
        await owner.execute(
            "INSERT INTO account_roles(tenant_id,account_id,role_id) "
            "SELECT $1,$2,id FROM roles WHERE tenant_id=$1 AND name=$3",
            tenant_id,
            identity_id,
            role,
        )
    await owner.execute(
        "INSERT INTO role_permissions(tenant_id,role_id,permission_id) "
        "SELECT $1,r.id,p.id FROM roles r JOIN permissions p ON p.tenant_id=r.tenant_id "
        "WHERE r.tenant_id=$1 AND r.name='operator' AND p.code IN ('risk:read','risk:manage','risk:evaluate') "
        "ON CONFLICT DO NOTHING",
        tenant_id,
    )
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM role_permissions rp JOIN roles r ON r.id=rp.role_id "
            "JOIN permissions p ON p.id=rp.permission_id WHERE rp.tenant_id=$1 AND r.name='operator' "
            "AND p.code LIKE 'risk:%'",
            tenant_id,
        )
        == 3
    )
    identity = {
        "admin": (account["id"], account["auth_version"], uuid.uuid4()),
        "operator": (operator_id, 0, uuid.uuid4()),
        "viewer": (viewer_id, 0, uuid.uuid4()),
    }
    for identity_id, auth_version, session_id in identity.values():
        await owner.execute(
            "INSERT INTO auth_sessions(id,tenant_id,account_id,auth_version,current_refresh_jti,expires_at,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,now()+interval '1 hour',now(),now())",
            session_id,
            tenant_id,
            identity_id,
            auth_version,
            uuid.uuid4().hex,
        )
    subject = await owner.fetchrow(
        "SELECT ci.id,ci.public_id,cb.product_id FROM code_items ci JOIN code_batches cb "
        "ON cb.tenant_id=ci.tenant_id AND cb.id=ci.code_batch_id "
        "WHERE ci.tenant_id=$1 AND cb.id=$2 ORDER BY ci.id LIMIT 1",
        tenant_id,
        uuid.UUID(baseline["code_batch"]["id"]),
    )
    await owner.execute(
        "UPDATE code_items SET status='activated',frozen_from_status=NULL,frozen_at=NULL,frozen_by=NULL,"
        "freeze_reason=NULL,freeze_provenance_version=NULL WHERE tenant_id=$1 AND id=$2",
        tenant_id,
        subject["id"],
    )
    campaign_id = uuid.uuid4()
    await owner.execute(
        "INSERT INTO campaigns(id,tenant_id,name,campaign_type,status,product_id,start_at,end_at,rules_json,created_at,updated_at) "
        "VALUES($1,$2,'ASGI risk campaign','lottery','active',$3,now()-interval '1 day',"
        "now()+interval '1 day','{}',now(),now())",
        campaign_id,
        tenant_id,
        subject["product_id"],
    )
    notification_ids = [uuid.uuid4(), uuid.uuid4()]
    await owner.executemany(
        "INSERT INTO risk_notifications(id,tenant_id,notification_type,title,detail,read,created_at,updated_at) "
        "VALUES($1,$2,'risk','ASGI owned notification','ASGI owned detail',false,now(),now())",
        [(notification_id, tenant_id) for notification_id in notification_ids],
    )

    runtime_engine = create_async_engine(runtime_url)
    owner_engine = create_async_engine(migrated_pg_url)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    monkeypatch.setattr(database, "_is_pg", True)

    async def cache_is_not_revoked(self, key: str) -> bool:
        return False

    monkeypatch.setattr(AsyncRedisCache, "is_token_revoked", cache_is_not_revoked)
    rate_limiter._cache._mem_store.clear()  # type: ignore[attr-defined]

    def headers(role: str) -> dict[str, str]:
        identity_id, auth_version, session_id = identity[role]
        token = create_access_token(
            str(tenant_id),
            str(identity_id),
            role,
            "brand",
            extra={"sid": str(session_id), "auth_version": auth_version},
        )
        return {"Authorization": f"Bearer {token}"}

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            viewer = await client.get("/api/v1/risk-rules", headers=headers("viewer"))
            assert viewer.status_code == 403, viewer.text
            operator_read = await client.get("/api/v1/risk-rules", headers=headers("operator"))
            assert operator_read.status_code == 200, operator_read.text
            assert (await client.get("/api/v1/risk-rules", headers={"X-Api-Key": "not-authority"})).status_code == 401
            api_key_notification = await client.post(
                "/api/v1/risk-notifications/mark-all-read",
                headers={"X-Api-Key": "not-authority", "Idempotency-Key": "asgi-notification-api-key"},
            )
            assert api_key_notification.status_code == 401, api_key_notification.text
            cross_tenant_token = create_access_token(
                str(uuid.uuid4()),
                str(identity["admin"][0]),
                "admin",
                "brand",
                extra={"sid": str(identity["admin"][2]), "auth_version": identity["admin"][1]},
            )
            cross_tenant = await client.post(
                "/api/v1/risk-notifications/mark-all-read",
                headers={
                    "Authorization": f"Bearer {cross_tenant_token}",
                    "Idempotency-Key": "asgi-notification-cross-tenant",
                },
            )
            assert cross_tenant.status_code == 401, cross_tenant.text
            acting_token = create_access_token(
                str(tenant_id),
                str(identity["admin"][0]),
                "admin",
                "brand",
                extra={
                    "sid": str(identity["admin"][2]),
                    "auth_version": identity["admin"][1],
                    "acting_tenant_id": str(uuid.uuid4()),
                },
            )
            acting = await client.post(
                "/api/v1/risk-notifications/mark-all-read",
                headers={
                    "Authorization": f"Bearer {acting_token}",
                    "Idempotency-Key": "asgi-notification-acting",
                },
            )
            assert acting.status_code == 403, acting.text

            denied_notification = await client.post(
                "/api/v1/risk-notifications/mark-all-read",
                headers={**headers("viewer"), "Idempotency-Key": "asgi-notification-viewer"},
            )
            assert denied_notification.status_code == 403, denied_notification.text
            missing_idempotency = await client.post(
                f"/api/v1/risk-notifications/{notification_ids[0]}/read",
                headers=headers("admin"),
            )
            assert missing_idempotency.status_code == 422, missing_idempotency.text
            missing_notification = await client.post(
                f"/api/v1/risk-notifications/{uuid.uuid4()}/read",
                headers={**headers("admin"), "Idempotency-Key": "asgi-notification-missing"},
            )
            assert missing_notification.status_code == 404, missing_notification.text
            notification_headers = {**headers("admin"), "Idempotency-Key": "asgi-notification-one"}
            notification_one = await client.post(
                f"/api/v1/risk-notifications/{notification_ids[0]}/read",
                headers=notification_headers,
            )
            notification_one_replay = await client.post(
                f"/api/v1/risk-notifications/{notification_ids[0]}/read",
                headers=notification_headers,
            )
            assert notification_one.status_code == 200, notification_one.text
            assert notification_one_replay.status_code == 200, notification_one_replay.text
            assert (
                notification_one.json()
                == notification_one_replay.json()
                == {
                    "id": str(notification_ids[0]),
                    "read": True,
                }
            )
            notification_conflict = await client.post(
                f"/api/v1/risk-notifications/{notification_ids[1]}/read",
                headers=notification_headers,
            )
            assert notification_conflict.status_code == 409, notification_conflict.text
            notification_all_headers = {**headers("operator"), "Idempotency-Key": "asgi-notification-all"}
            notification_all = await client.post(
                "/api/v1/risk-notifications/mark-all-read",
                headers=notification_all_headers,
            )
            notification_all_replay = await client.post(
                "/api/v1/risk-notifications/mark-all-read",
                headers=notification_all_headers,
            )
            assert notification_all.status_code == 200, notification_all.text
            assert notification_all_replay.status_code == 200, notification_all_replay.text
            assert notification_all.json() == notification_all_replay.json() == {"updated": 1}
            notification_facts = await owner.fetchrow(
                "SELECT (SELECT count(*) FROM risk_action_receipts WHERE tenant_id=$1 "
                "AND idempotency_key IN ('asgi-notification-one','asgi-notification-all')) receipts,"
                "(SELECT count(*) FROM risk_action_outbox o JOIN risk_action_receipts r ON r.id=o.receipt_id "
                "WHERE r.tenant_id=$1 AND r.idempotency_key IN ('asgi-notification-one','asgi-notification-all')) outbox,"
                "(SELECT count(*) FROM platform_audit_log WHERE target_tenant_id=$2 "
                "AND action IN ('risk_notification_read','risk_notifications_read_all') "
                "AND resource IN ('risk_notification:'||$3,'risk_notifications:all')) audits",
                tenant_id,
                str(tenant_id),
                str(notification_ids[0]),
            )
            assert dict(notification_facts) == {"receipts": 2, "outbox": 2, "audits": 2}

            created = await asyncio.wait_for(
                client.post(
                    "/api/v1/risk-rules",
                    json={
                        "name": "ASGI scan frequency block",
                        "rule_type": "scan_frequency",
                        "action": "block",
                        "config": {"window_minutes": 1440, "max_requests": 1},
                    },
                    headers={**headers("operator"), "Idempotency-Key": "asgi-risk-create"},
                ),
                5,
            )
            assert created.status_code == 201, created.text
            rule_id = uuid.UUID(created.json()["id"])
            assert created.json()["version"] == 1

            updated = await client.patch(
                f"/api/v1/risk-rules/{rule_id}",
                json={"expected_version": 1, "name": "ASGI exact scan block"},
                headers={**headers("admin"), "Idempotency-Key": "asgi-risk-update"},
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["version"] == 2

            linked = await client.post(
                f"/api/v1/risk-rules/{rule_id}/campaigns/{campaign_id}",
                headers={**headers("admin"), "Idempotency-Key": "asgi-risk-link"},
            )
            assert linked.status_code == 200 and linked.json()["attached"] is True, linked.text

            scan_id = uuid.uuid4()
            await owner.execute(
                "INSERT INTO scan_events(id,tenant_id,public_id,scan_time,ip_hash,is_first_scan,is_valid_visit) "
                "VALUES($1,$2,$3,now(),$4,false,true)",
                scan_id,
                tenant_id,
                subject["public_id"],
                "c" * 64,
            )
            evaluated = await client.post(
                "/api/v1/risk-rules/evaluate/scan",
                json={"scan_event_id": str(scan_id), "rule_id": str(rule_id)},
                headers={**headers("operator"), "Idempotency-Key": "asgi-risk-evaluate"},
            )
            assert evaluated.status_code == 200, evaluated.text
            assert evaluated.json()["triggered"] is True
            assert evaluated.json()["paused_campaign_ids"] == [str(campaign_id)]

            pauses = await client.get("/api/v1/risk-rules/pauses?status=active", headers=headers("operator"))
            assert pauses.status_code == 200 and pauses.json()["total"] == 1, pauses.text
            pause = pauses.json()["items"][0]
            resumed = await client.post(
                f"/api/v1/risk-rules/pauses/{pause['id']}/resume",
                json={"expected_version": pause["version"], "reason": "ASGI review completed"},
                headers={**headers("operator"), "Idempotency-Key": "asgi-risk-resume"},
            )
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["status"] == "resumed"
            assert resumed.json()["campaign_status"] == "active"
            await owner.execute("UPDATE auth_sessions SET revoked_at=now() WHERE id=$1", identity["operator"][2])
            revoked_replay = await client.post(
                "/api/v1/risk-notifications/mark-all-read",
                headers=notification_all_headers,
            )
            assert revoked_replay.status_code == 401, revoked_replay.text
    finally:
        await owner.close()
        await runtime_engine.dispose()
        await owner_engine.dispose()
