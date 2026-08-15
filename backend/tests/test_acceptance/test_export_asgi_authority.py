"""Real ASGI coverage for prepared-export authorization and file evidence."""

import hashlib
import json
import uuid

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.test_acceptance.test_code_batch_delivery_contract import _seed_catalog
from tests.test_acceptance.test_export_ledger_authority import _grant_permission, _owner_dsn, _session

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


async def test_export_asgi_requires_reason_and_live_direct_brand_session_and_replays_exact_bytes(
    migrated_pg_url: str, monkeypatch
) -> None:
    from app.core import database
    from app.main import app
    from app.services import export_admission
    from app.services.redis_cache import AsyncRedisCache
    from app.utils.security import create_access_token

    owner = await asyncpg.connect(_owner_dsn(migrated_pg_url))
    ids = await _seed_catalog(owner, "u08c-export-asgi")
    await _grant_permission(owner, ids, "analytics:view")
    session_id = await _session(owner, ids)

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

    async def cache_is_not_revoked(self, key: str) -> bool:
        return False

    async def allow_rate(*args, **kwargs):
        return True, 1

    monkeypatch.setattr(AsyncRedisCache, "is_token_revoked", cache_is_not_revoked)
    monkeypatch.setattr(export_admission._export_rate_cache, "rate_limit_check_shared", allow_rate)

    token = create_access_token(
        str(ids["tenant"]),
        str(ids["account"]),
        "admin",
        "brand",
        extra={"sid": str(session_id), "auth_version": 0},
    )
    idem = uuid.uuid4()
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(idem)}
    body = {"export_type": "scan_stats", "reason": "  real ASGI operating review  "}
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            invalid = await client.post(
                "/api/v1/analytics/exports",
                json={"export_type": "scan_stats", "reason": "   "},
                headers=headers,
            )
            assert invalid.status_code == 422, invalid.text
            assert await owner.fetchval("SELECT count(*) FROM export_logs WHERE tenant_id=$1", ids["tenant"]) == 0

            created = await client.post("/api/v1/analytics/exports", json=body, headers=headers)
            assert created.status_code == 200, created.text
            assert created.headers["content-type"].startswith(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            assert int(created.headers["content-length"]) == len(created.content)
            assert created.headers["x-content-sha256"] == hashlib.sha256(created.content).hexdigest()

            replay = await client.post("/api/v1/analytics/exports", json=body, headers=headers)
            assert replay.status_code == 200, replay.text
            assert replay.content == created.content
            assert replay.headers["x-export-id"] == created.headers["x-export-id"]

            conflict = await client.post(
                "/api/v1/analytics/exports",
                json={"export_type": "scan_stats", "reason": "different purpose"},
                headers=headers,
            )
            assert conflict.status_code == 409, conflict.text

            acting_token = create_access_token(
                str(ids["tenant"]),
                str(ids["account"]),
                "admin",
                "brand",
                extra={
                    "sid": str(session_id),
                    "auth_version": 0,
                    "acting_tenant_id": str(uuid.uuid4()),
                },
            )
            acting = await client.post(
                "/api/v1/analytics/exports",
                json=body,
                headers={"Authorization": f"Bearer {acting_token}", "Idempotency-Key": str(uuid.uuid4())},
            )
            assert acting.status_code == 403, acting.text

            platform_token = create_access_token("platform", "platform-admin", "platform_admin", "platform")
            platform = await client.post(
                "/api/v1/analytics/exports",
                json=body,
                headers={"Authorization": f"Bearer {platform_token}", "Idempotency-Key": str(uuid.uuid4())},
            )
            assert platform.status_code == 401, platform.text

        row = await owner.fetchrow(
            "SELECT reason,scope_snapshot,checksum_sha256,artifact_size_bytes,row_count,status,auth_session_id "
            "FROM export_logs WHERE tenant_id=$1 AND idempotency_key=$2",
            ids["tenant"],
            str(idem),
        )
        assert row is not None
        assert row["reason"] == "real ASGI operating review"
        assert json.loads(row["scope_snapshot"]) == {
            "end_date": None,
            "row_limit": 50_000,
            "start_date": None,
        }
        assert row["checksum_sha256"] == hashlib.sha256(created.content).hexdigest()
        assert row["artifact_size_bytes"] == len(created.content)
        assert row["row_count"] == 0
        assert row["status"] == "prepared"
        assert row["auth_session_id"] == session_id
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM export_logs WHERE tenant_id=$1 AND idempotency_key=$2",
                ids["tenant"],
                str(idem),
            )
            == 1
        )
    finally:
        await owner.close()
        await runtime_engine.dispose()
        await owner_engine.dispose()
