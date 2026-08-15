"""Real ASGI regression for function-scoped diversion feature and mutation authority."""

import asyncio
import logging
import uuid

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.test_acceptance.conftest import seed_baseline

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def test_resolver_owns_diversion_observation_without_precommit_event_race(
    migrated_pg_url: str, monkeypatch, caplog
) -> None:
    """A resolver scan records/reopens once without a second-session event race."""
    from app.core import database
    from app.core.event_bus import event_bus
    from app.main import app
    from app.middleware.rate_limit import rate_limiter
    from app.services.redis_cache import AsyncRedisCache
    from app.services.risk_auto_handler import _handle_scan_created

    baseline = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(baseline["baseline_tenant"]["id"])
    batch_id = uuid.UUID(baseline["code_batch"]["id"])
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner = await asyncpg.connect(owner_dsn)
    item = await owner.fetchrow(
        "SELECT id,public_id FROM code_items WHERE tenant_id=$1 AND code_batch_id=$2 ORDER BY id OFFSET 2 LIMIT 1",
        tenant_id,
        batch_id,
    )
    assert item is not None
    account_id = await owner.fetchval(
        "SELECT id FROM accounts WHERE tenant_id=$1 AND email=$2",
        tenant_id,
        baseline["baseline_tenant"]["admin_email"],
    )
    assert account_id is not None
    region_id = uuid.uuid4()
    clue_id = uuid.uuid4()
    await owner.execute(
        "UPDATE tenants SET enabled_features=(coalesce(enabled_features,'{}'::json)::jsonb"
        "||'{\"risk_module\":true}'::jsonb)::json WHERE id=$1",
        tenant_id,
    )
    await owner.execute(
        "INSERT INTO regions(id,tenant_id,name,code,province,city,coverage_type,coverage_areas,status,version) "
        "VALUES($1,$2,'Resolver race region',$3,'江苏','苏州','city',$4::jsonb,'active',1)",
        region_id,
        tenant_id,
        f"REG-{region_id.hex.upper()}",
        '[{"province":"江苏","city":"苏州"}]',
    )
    await owner.execute(
        "UPDATE code_batches SET region_id=$1 WHERE tenant_id=$2 AND id=$3",
        region_id,
        tenant_id,
        batch_id,
    )
    await owner.execute(
        "UPDATE code_items SET status='activated',first_scanned_at=NULL WHERE tenant_id=$1 AND id=$2",
        tenant_id,
        item["id"],
    )
    await owner.execute(
        "INSERT INTO risk_rules(id,tenant_id,name,rule_type,action,config,enabled,version) "
        "VALUES($1,$2,'Resolver race guard','unsupported_test_rule','warn','{}'::jsonb,true,1)",
        uuid.uuid4(),
        tenant_id,
    )
    await owner.execute(
        "INSERT INTO diversion_clues(id,tenant_id,public_id,code_item_id,expected_region,detected_city,ip_hash,"
        "location_source,location_accuracy,location_authorized,rule_name,confidence,pending_review,observation_count,"
        "investigation_status,resolved,version,resolution_action,resolution_note,resolved_at,resolved_by_account_id,"
        "created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'江苏省苏州市','上海',$5,'ip_inference','medium',NULL,'cross_region_ip',"
        "'medium',false,1,'false_positive',true,1,'false_positive','fixture terminal clue',now(),$6,now(),now())",
        clue_id,
        tenant_id,
        item["public_id"],
        item["id"],
        "a" * 64,
        account_id,
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
    original_handlers = list(event_bus._handlers.get("scan.created", []))  # type: ignore[attr-defined]
    event_bus._handlers["scan.created"] = [_handle_scan_created]  # type: ignore[attr-defined]
    caplog.set_level(logging.WARNING)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            response = await asyncio.wait_for(
                client.get(
                    f"/c/{item['public_id']}",
                    headers={"Accept": "application/json", "X-Forwarded-For": "120.1.2.3"},
                ),
                timeout=3.0,
            )
        assert response.status_code == 200, response.text
        assert not [
            record
            for record in caplog.records
            if record.name == "app.services.risk_auto_handler" and record.exc_info is not None
        ]
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM diversion_observations WHERE tenant_id=$1 AND clue_id=$2",
                tenant_id,
                clue_id,
            )
            == 1
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM diversion_investigation_history "
                "WHERE tenant_id=$1 AND clue_id=$2 AND from_status='false_positive' AND to_status='pending_evidence'",
                tenant_id,
                clue_id,
            )
            == 1
        )
    finally:
        event_bus._handlers["scan.created"] = original_handlers  # type: ignore[attr-defined]
        await owner.close()
        await runtime_engine.dispose()
        await owner_engine.dispose()


async def test_diversion_mutations_share_function_scoped_feature_transaction(migrated_pg_url: str, monkeypatch) -> None:
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
    item = await owner.fetchrow(
        "SELECT id,public_id FROM code_items WHERE tenant_id=$1 AND code_batch_id=$2 ORDER BY id OFFSET 1 LIMIT 1",
        tenant_id,
        uuid.UUID(baseline["code_batch"]["id"]),
    )
    session_id = uuid.uuid4()
    clue_id = uuid.uuid4()
    await owner.execute(
        "UPDATE tenants SET enabled_features=(coalesce(enabled_features,'{}'::json)::jsonb"
        "||'{\"risk_module\":true}'::jsonb)::json WHERE id=$1",
        tenant_id,
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
    await owner.execute(
        "INSERT INTO diversion_clues(id,tenant_id,public_id,code_item_id,expected_region,detected_city,ip_hash,"
        "location_source,location_accuracy,location_authorized,rule_name,confidence,pending_review,observation_count,"
        "investigation_status,resolved,version,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,'北京','上海',$5,'browser_geolocation','high',true,'cross_region_browser',"
        "'medium',true,1,'open',false,1,now(),now())",
        clue_id,
        tenant_id,
        item["public_id"],
        item["id"],
        "a" * 64,
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
    token = create_access_token(
        str(tenant_id),
        str(account["id"]),
        "admin",
        "brand",
        extra={"sid": str(session_id), "auth_version": account["auth_version"]},
    )
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            evidence = await asyncio.wait_for(
                client.post(
                    f"/api/v1/risk-dashboard/diversion-clues/{clue_id}/evidence",
                    json={"expected_version": 1, "evidence_type": "explanation", "description": "ASGI evidence"},
                    headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                ),
                timeout=1.0,
            )
            assert evidence.status_code == 201, evidence.text
            assert evidence.json()["clue_version"] == 2

            transition = await asyncio.wait_for(
                client.post(
                    f"/api/v1/risk-dashboard/diversion-clues/{clue_id}/transition",
                    json={
                        "expected_version": 2,
                        "to_status": "false_positive",
                        "reason": "ASGI investigation complete",
                        "resolution_note": "verified false positive",
                    },
                    headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                ),
                timeout=1.0,
            )
            assert transition.status_code == 200, transition.text
            assert transition.json()["investigation_status"] == "false_positive"

            ticket = await asyncio.wait_for(client.post("/api/v1/risk-dashboard/alerts/ticket", headers=headers), 1.0)
            assert ticket.status_code == 200, ticket.text
            assert ticket.json()["ticket"]

            await owner.execute(
                "UPDATE tenants SET enabled_features=(coalesce(enabled_features,'{}'::json)::jsonb"
                "||'{\"risk_module\":false}'::jsonb)::json WHERE id=$1",
                tenant_id,
            )
            disabled = await asyncio.wait_for(
                client.post(
                    f"/api/v1/risk-dashboard/diversion-clues/{clue_id}/evidence",
                    json={"expected_version": 3, "evidence_type": "explanation", "description": "must deny"},
                    headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                ),
                timeout=1.0,
            )
            assert disabled.status_code == 403, disabled.text
            assert disabled.json()["detail"]["code"] == "TENANT_FEATURE_DISABLED"
    finally:
        await owner.close()
        await runtime_engine.dispose()
        await owner_engine.dispose()
