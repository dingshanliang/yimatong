"""Real PostgreSQL ASGI proof for the platform-only milestone correction boundary."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import database
from app.main import app
from app.utils.security import create_access_token

pytestmark = [pytest.mark.acceptance, pytest.mark.asyncio]


def _platform_headers(session_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        "platform",
        "platform-admin",
        "platform_admin",
        tenant_type="platform",
        extra={"sid": str(session_id)},
    )
    return {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=pilot-asgi-csrf",
        "Origin": "http://localhost:3002",
        "X-Platform-CSRF": "pilot-asgi-csrf",
        "Idempotency-Key": str(uuid.uuid4()),
    }


def _body() -> dict:
    return {
        "milestone_type": "launched",
        "corrected_at": "2026-08-01T00:00:00Z",
        "source": "verified launch receipt",
        "reason": "correct source timestamp",
    }


async def test_platform_correction_real_session_boundary_and_negative_zero_writes(migrated_pg_url: str) -> None:
    owner_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    owner = await asyncpg.connect(owner_dsn)
    runtime_engine = create_async_engine(migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@"))
    control_engine = create_async_engine(migrated_pg_url)
    runtime_factory = async_sessionmaker(runtime_engine, expire_on_commit=False)
    control_factory = async_sessionmaker(control_engine, expire_on_commit=False)
    target_tenant = uuid.uuid4()
    other_tenant = uuid.uuid4()
    active_session = uuid.uuid4()
    revoked_session = uuid.uuid4()
    expired_session = uuid.uuid4()
    nonadmin_session = uuid.uuid4()
    try:
        for tenant_id, suffix in ((target_tenant, "target"), (other_tenant, "other")):
            await owner.execute(
                "INSERT INTO tenants(id,name,slug,status,plan,tenant_type,created_at,updated_at) "
                "VALUES($1,$2,$3,'active','free','brand',now(),now())",
                tenant_id,
                f"pilot {suffix}",
                f"pilot-{suffix}-{tenant_id.hex[:8]}",
            )
            await owner.execute(
                "INSERT INTO pilot_milestones(id,tenant_id,milestone_type,achieved_at,source,fact_digest,"
                "authority_version,created_at,updated_at) VALUES($1,$2,'launched',$3,'launch receipt',$4,1,now(),now())",
                uuid.uuid4(),
                tenant_id,
                datetime(2026, 7, 31, tzinfo=UTC),
                tenant_id.hex * 2,
            )
        await owner.executemany(
            "INSERT INTO platform_auth_sessions(id,principal,expires_at,revoked_at,created_at) VALUES($1,$2,$3,$4,now())",
            [
                (active_session, "platform-admin", datetime.now(UTC) + timedelta(hours=1), None),
                (
                    revoked_session,
                    "platform-admin",
                    datetime.now(UTC) + timedelta(hours=1),
                    datetime.now(UTC),
                ),
                (expired_session, "platform-admin", datetime.now(UTC) - timedelta(minutes=1), None),
                (nonadmin_session, "other-principal", datetime.now(UTC) + timedelta(hours=1), None),
            ],
        )

        with (
            patch.object(database, "_is_pg", True),
            patch.object(database, "_control_is_pg", True),
            patch.object(database, "async_session_factory", runtime_factory),
            patch.object(database, "control_session_factory", control_factory),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                positive = await client.post(
                    f"/api/v1/platform/tenants/{target_tenant}/pilot-milestones/corrections",
                    headers=_platform_headers(active_session),
                    json=_body(),
                )
                negative_responses = []
                for session_id in (revoked_session, expired_session, nonadmin_session, uuid.uuid4()):
                    negative_responses.append(
                        await client.post(
                            f"/api/v1/platform/tenants/{target_tenant}/pilot-milestones/corrections",
                            headers=_platform_headers(session_id),
                            json=_body(),
                        )
                    )
                tenant_token = create_access_token(str(target_tenant), str(uuid.uuid4()), "admin", tenant_type="brand")
                negative_responses.append(
                    await client.post(
                        f"/api/v1/platform/tenants/{target_tenant}/pilot-milestones/corrections",
                        headers={
                            "Authorization": f"Bearer {tenant_token}",
                            "Idempotency-Key": str(uuid.uuid4()),
                        },
                        json=_body(),
                    )
                )
                acting_token = create_access_token(
                    str(uuid.uuid4()),
                    str(uuid.uuid4()),
                    "admin",
                    tenant_type="agency",
                    extra={"acting_tenant_id": str(target_tenant), "sid": str(uuid.uuid4())},
                )
                negative_responses.append(
                    await client.post(
                        f"/api/v1/platform/tenants/{target_tenant}/pilot-milestones/corrections",
                        headers={
                            "Authorization": f"Bearer {acting_token}",
                            "Idempotency-Key": str(uuid.uuid4()),
                        },
                        json=_body(),
                    )
                )
                negative_responses.append(
                    await client.post(
                        f"/api/v1/platform/tenants/{target_tenant}/pilot-milestones/corrections",
                        headers={"X-Api-Key": "not-a-control-plane-cookie", "Idempotency-Key": str(uuid.uuid4())},
                        json=_body(),
                    )
                )
                negative_responses.append(
                    await client.post(
                        f"/api/v1/platform/tenants/{uuid.uuid4()}/pilot-milestones/corrections",
                        headers=_platform_headers(active_session),
                        json=_body(),
                    )
                )

        assert positive.status_code == 200, positive.text
        assert all(response.status_code in {401, 403, 404} for response in negative_responses)
        assert (
            await owner.fetchval("SELECT count(*) FROM pilot_milestone_corrections WHERE tenant_id=$1", target_tenant)
            == 1
        )
        assert (
            await owner.fetchval("SELECT count(*) FROM pilot_milestone_corrections WHERE tenant_id=$1", other_tenant)
            == 0
        )
        assert await owner.fetchval("SELECT count(*) FROM pilot_milestone_corrections") == 1
    finally:
        app.dependency_overrides.clear()
        await runtime_engine.dispose()
        await control_engine.dispose()
        await owner.close()
