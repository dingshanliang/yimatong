"""Public resolver and scan telemetry under a real NOBYPASSRLS runtime role."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import asyncpg
import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import get_db
from app.main import app

pytestmark = pytest.mark.acceptance


@pytest.fixture
async def runtime_client(migrated_pg_url: str) -> AsyncGenerator[AsyncClient, None]:
    """Serve HTTP requests with PostgreSQL's non-owner, NOBYPASSRLS role."""

    from app.middleware.rate_limit import rate_limiter

    rate_limiter._cache._mem_store.clear()  # type: ignore[attr-defined]
    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    runtime_engine = create_async_engine(runtime_url)
    runtime_factory = async_sessionmaker(runtime_engine, expire_on_commit=False)
    control_engine = create_async_engine(migrated_pg_url)
    control_factory = async_sessionmaker(control_engine, expire_on_commit=False)

    async def override_get_db():
        async with runtime_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    with patch("app.core.database.control_session_factory", control_factory):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    app.dependency_overrides.clear()
    await runtime_engine.dispose()
    await control_engine.dispose()


async def _reset_public_scan_state(bypass_session, summary: dict) -> None:
    tenant_ids = (summary["baseline_tenant"]["id"], summary["control_tenant"]["id"])
    public_ids = (summary["first_public_id"], summary["control_first_public_id"])
    await bypass_session.rollback()
    await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
    await bypass_session.execute(
        text(
            "UPDATE tenants SET plan_expires_at=:future, quota='{}'::json "
            "WHERE id IN (:baseline_tenant, :control_tenant)"
        ),
        {
            "future": datetime.now(UTC) + timedelta(days=30),
            "baseline_tenant": tenant_ids[0],
            "control_tenant": tenant_ids[1],
        },
    )
    await bypass_session.execute(
        text(
            "UPDATE code_items SET status='activated', revoked_at=NULL, first_scanned_at=NULL "
            "WHERE tenant_id IN (:baseline_tenant, :control_tenant) "
            "AND public_id IN (:baseline_public_id, :control_public_id)"
        ),
        {
            "baseline_tenant": tenant_ids[0],
            "control_tenant": tenant_ids[1],
            "baseline_public_id": public_ids[0],
            "control_public_id": public_ids[1],
        },
    )
    await bypass_session.execute(
        text("DELETE FROM scan_events WHERE tenant_id IN (:baseline_tenant, :control_tenant)"),
        {"baseline_tenant": tenant_ids[0], "control_tenant": tenant_ids[1]},
    )
    await bypass_session.execute(
        text("DELETE FROM intent_events WHERE tenant_id IN (:baseline_tenant, :control_tenant)"),
        {"baseline_tenant": tenant_ids[0], "control_tenant": tenant_ids[1]},
    )
    await bypass_session.execute(
        text("DELETE FROM anonymous_visitors WHERE tenant_id IN (:baseline_tenant, :control_tenant)"),
        {"baseline_tenant": tenant_ids[0], "control_tenant": tenant_ids[1]},
    )
    await bypass_session.commit()

    from app.services.resolve_cache import resolve_cache

    for public_id in public_ids:
        await resolve_cache.invalidate(f"resolve:{public_id}")


class TestPublicScanRuntimeRLS:
    async def test_public_benefit_claim_uses_scan_token_tenant_rls(
        self,
        runtime_client: AsyncClient,
        bypass_session,
        asyncpg_conn,
        migrated_pg_url: str,
    ):
        """A public claim must establish RLS context before any business query."""

        from app.utils.client_ip import compute_ip_hash
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]
        control_tenant_id = summary["control_tenant"]["id"]
        public_id = summary["first_public_id"]
        benefit_id = summary["benefit"]["id"]
        fixed_ip = "203.0.113.209"

        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await bypass_session.execute(
            text("DELETE FROM benefit_deliveries WHERE tenant_id=:tenant_id AND benefit_id=:benefit_id"),
            {"tenant_id": tenant_id, "benefit_id": benefit_id},
        )
        await bypass_session.execute(
            text("DELETE FROM benefit_claims WHERE tenant_id=:tenant_id AND benefit_id=:benefit_id"),
            {"tenant_id": tenant_id, "benefit_id": benefit_id},
        )
        await bypass_session.execute(
            text("UPDATE benefits SET status='active', stock_used=0 WHERE tenant_id=:tenant_id AND id=:benefit_id"),
            {"tenant_id": tenant_id, "benefit_id": benefit_id},
        )
        await bypass_session.execute(
            text("UPDATE tenants SET plan_expires_at=:future WHERE id=:tenant_id"),
            {"future": datetime.now(UTC) + timedelta(days=30), "tenant_id": tenant_id},
        )
        await bypass_session.commit()

        assert await asyncpg_conn.fetchval("SELECT current_user") == "acceptance_tester"
        assert await asyncpg_conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user") is False

        def scan_token(*, for_tenant: str, expires_in: int = 300) -> str:
            return jwt.encode(
                {
                    "public_id": public_id,
                    "ip_hash": compute_ip_hash(fixed_ip),
                    "tenant_id": for_tenant,
                    "consumer_id": f"runtime-claim-{uuid.uuid4()}",
                    "type": "scan_token",
                    "jti": str(uuid.uuid4()),
                    "exp": int(time.time()) + expires_in,
                },
                settings.secret_key,
                algorithm="HS256",
            )

        success = await runtime_client.post(
            "/api/v1/benefit-claims",
            json={"benefit_id": benefit_id, "scan_token": scan_token(for_tenant=tenant_id)},
            headers={"X-Real-IP": fixed_ip},
        )
        assert success.status_code == 201, success.text
        assert success.json() == {"status": "claimed", "benefit_id": benefit_id}

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        successful_claims = await bypass_session.scalar(
            text("SELECT count(*) FROM benefit_claims WHERE tenant_id=:tenant_id AND benefit_id=:benefit_id"),
            {"tenant_id": tenant_id, "benefit_id": benefit_id},
        )
        stock_used = await bypass_session.scalar(
            text("SELECT stock_used FROM benefits WHERE tenant_id=:tenant_id AND id=:benefit_id"),
            {"tenant_id": tenant_id, "benefit_id": benefit_id},
        )
        assert successful_claims == 1
        assert stock_used == 1

        expired = await runtime_client.post(
            "/api/v1/benefit-claims",
            json={"benefit_id": benefit_id, "scan_token": scan_token(for_tenant=tenant_id, expires_in=-1)},
            headers={"X-Real-IP": fixed_ip},
        )
        assert expired.status_code == 401

        forged_token = scan_token(for_tenant=tenant_id)
        parts = forged_token.split(".")
        parts[2] = f"{'a' if parts[2][0] != 'a' else 'b'}{parts[2][1:]}"
        forged = await runtime_client.post(
            "/api/v1/benefit-claims",
            json={"benefit_id": benefit_id, "scan_token": ".".join(parts)},
            headers={"X-Real-IP": fixed_ip},
        )
        assert forged.status_code == 401

        cross_tenant = await runtime_client.post(
            "/api/v1/benefit-claims",
            json={"benefit_id": benefit_id, "scan_token": scan_token(for_tenant=control_tenant_id)},
            headers={"X-Real-IP": fixed_ip},
        )
        assert cross_tenant.status_code == 404

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        assert (
            await bypass_session.scalar(
                text("SELECT count(*) FROM benefit_claims WHERE benefit_id=:benefit_id"),
                {"benefit_id": benefit_id},
            )
            == successful_claims
        )
        assert (
            await bypass_session.scalar(
                text("SELECT stock_used FROM benefits WHERE tenant_id=:tenant_id AND id=:benefit_id"),
                {"tenant_id": tenant_id, "benefit_id": benefit_id},
            )
            == stock_used
        )

    async def test_resolve_and_telemetry_use_trusted_tenant_context(
        self,
        runtime_client: AsyncClient,
        bypass_session,
        asyncpg_conn,
        migrated_pg_url: str,
    ):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        await _reset_public_scan_state(bypass_session, summary)
        tenant_id = summary["baseline_tenant"]["id"]
        public_id = summary["first_public_id"]
        control_public_id = summary["control_first_public_id"]

        assert await asyncpg_conn.fetchval("SELECT current_user") == "acceptance_tester"
        assert await asyncpg_conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user") is False

        fixed_ip = "203.0.113.201"
        resolved = await runtime_client.get(
            f"/c/{public_id}",
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0", "X-Real-IP": fixed_ip},
        )
        assert resolved.status_code == 200, resolved.text
        payload = resolved.json()
        assert payload["code_data"]["public_id"] == public_id
        scan_token = payload["scan_token"]
        visitor_id = payload["scan_info"]["visitor_id"]

        telemetry = await runtime_client.post(
            "/scan-events",
            json={"event_type": "view", "public_id": public_id, "client_event_id": "runtime-rls-evt-1"},
            headers={
                "Authorization": f"Bearer {scan_token}",
                "X-Visitor-ID": visitor_id,
                "X-Real-IP": fixed_ip,
            },
        )
        assert telemetry.status_code == 201, telemetry.text
        assert telemetry.json()["persisted"] is True

        concurrent_event_id = "runtime-rls-concurrent-1"

        async def post_duplicate():
            return await runtime_client.post(
                "/scan-events",
                json={"event_type": "view", "public_id": public_id, "client_event_id": concurrent_event_id},
                headers={
                    "Authorization": f"Bearer {scan_token}",
                    "X-Visitor-ID": visitor_id,
                    "X-Real-IP": fixed_ip,
                },
            )

        duplicate_responses = await asyncio.gather(post_duplicate(), post_duplicate())
        assert [response.status_code for response in duplicate_responses] == [201, 201]
        assert sorted(response.json()["deduplicated"] for response in duplicate_responses) == [False, True]

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        scan_count = await bypass_session.scalar(
            text("SELECT count(*) FROM scan_events WHERE tenant_id=:tenant_id AND public_id=:public_id"),
            {"tenant_id": tenant_id, "public_id": public_id},
        )
        intent_count = await bypass_session.scalar(
            text(
                "SELECT count(*) FROM intent_events "
                "WHERE tenant_id=:tenant_id AND public_id=:public_id AND client_event_id='runtime-rls-evt-1'"
            ),
            {"tenant_id": tenant_id, "public_id": public_id},
        )
        assert scan_count == 1
        assert intent_count == 1
        concurrent_count = await bypass_session.scalar(
            text("SELECT count(*) FROM intent_events WHERE tenant_id=:tenant_id AND client_event_id=:event_id"),
            {"tenant_id": tenant_id, "event_id": concurrent_event_id},
        )
        assert concurrent_count == 1

        # A valid token is bound to its public code. Reusing it for the control
        # tenant must fail before any database context or write is established.
        cross_tenant = await runtime_client.post(
            "/scan-events",
            json={
                "event_type": "view",
                "public_id": control_public_id,
                "client_event_id": "runtime-rls-cross-tenant",
            },
            headers={"Authorization": f"Bearer {scan_token}", "X-Real-IP": fixed_ip},
        )
        assert cross_tenant.status_code == 201
        assert cross_tenant.json() == {"status": "ignored", "reason": "invalid_token"}

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        cross_tenant_events = await bypass_session.scalar(
            text("SELECT count(*) FROM intent_events WHERE client_event_id='runtime-rls-cross-tenant'")
        )
        assert cross_tenant_events == 0

        token_parts = scan_token.split(".")
        token_parts[2] = f"{'a' if token_parts[2][0] != 'a' else 'b'}{token_parts[2][1:]}"
        tampered_token = ".".join(token_parts)
        forged = await runtime_client.post(
            "/scan-events",
            json={"event_type": "view", "public_id": public_id, "client_event_id": "forged-token-view"},
            headers={"Authorization": f"Bearer {tampered_token}", "X-Real-IP": fixed_ip},
        )
        assert forged.status_code == 201
        assert forged.json() == {"status": "ignored", "reason": "invalid_token"}

        from app.services.public_id import generate_public_id

        invalid_code = await runtime_client.get(f"/c/{generate_public_id()}", headers={"Accept": "application/json"})
        assert invalid_code.status_code == 404
        assert "tenant" not in invalid_code.text.lower()

    async def test_expired_plan_and_max_scans_fail_closed_under_runtime_rls(
        self,
        runtime_client: AsyncClient,
        bypass_session,
        migrated_pg_url: str,
    ):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        await _reset_public_scan_state(bypass_session, summary)
        tenant_id = summary["baseline_tenant"]["id"]
        public_id = summary["first_public_id"]

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await bypass_session.execute(
            text("UPDATE tenants SET plan_expires_at=:expired WHERE id=:tenant_id"),
            {"expired": datetime.now(UTC) - timedelta(seconds=1), "tenant_id": tenant_id},
        )
        await bypass_session.commit()

        expired = await runtime_client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        assert expired.status_code == 403
        assert expired.json()["code"] == "TENANT_PLAN_EXPIRED"

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        assert (
            await bypass_session.scalar(
                text("SELECT count(*) FROM scan_events WHERE tenant_id=:tenant_id AND public_id=:public_id"),
                {"tenant_id": tenant_id, "public_id": public_id},
            )
            == 0
        )
        await bypass_session.execute(
            text("UPDATE tenants SET plan_expires_at=:future, quota=:quota WHERE id=:tenant_id"),
            {
                "future": datetime.now(UTC) + timedelta(days=30),
                "quota": '{"max_scans": 1}',
                "tenant_id": tenant_id,
            },
        )
        await bypass_session.commit()

        first = await runtime_client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        second = await runtime_client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        assert first.status_code == 200, first.text
        assert second.status_code == 429
        assert second.json()["code"] == "QUOTA_EXCEEDED"

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        persisted = await bypass_session.scalar(
            text("SELECT count(*) FROM scan_events WHERE tenant_id=:tenant_id AND public_id=:public_id"),
            {"tenant_id": tenant_id, "public_id": public_id},
        )
        assert persisted == 1

    async def test_public_wecom_contact_way_uses_scan_token_tenant_rls(
        self,
        runtime_client: AsyncClient,
        bypass_session,
        migrated_pg_url: str,
    ):
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]
        control_tenant_id = summary["control_tenant"]["id"]
        public_id = summary["first_public_id"]
        benefit_id = summary["benefit"]["id"]
        connector_id = uuid.uuid4()

        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await bypass_session.execute(
            text(
                "INSERT INTO connectors (id, tenant_id, name, connector_type, config, enabled) "
                "VALUES (:id, :tenant_id, 'runtime-wecom', 'wecom_customer_contact', "
                "CAST(:config AS jsonb), true)"
            ),
            {
                "id": connector_id,
                "tenant_id": tenant_id,
                "config": '{"status":"connected","mock_mode":true,"customer_service_user_ids":["demo-member"]}',
            },
        )
        await bypass_session.commit()

        forged_callback = await runtime_client.post(
            f"/api/v1/integrations/wecom/callback/{connector_id}",
            json={
                "ChangeType": "add_external_contact",
                "ExternalUserID": "forged-runtime-contact",
                "State": "forged-state",
            },
        )
        assert forged_callback.status_code == 400
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        assert (
            await bypass_session.scalar(
                text("SELECT count(*) FROM wecom_external_contacts WHERE external_userid='forged-runtime-contact'")
            )
            == 0
        )

        def scan_token(for_tenant: str) -> str:
            return jwt.encode(
                {
                    "public_id": public_id,
                    "tenant_id": for_tenant,
                    "visitor_id": "runtime-wecom-visitor",
                    "type": "scan_token",
                    "jti": str(uuid.uuid4()),
                    "exp": int(time.time()) + 300,
                },
                settings.secret_key,
                algorithm="HS256",
            )

        valid = await runtime_client.post(
            "/api/v1/integrations/wecom/contact-way",
            json={"benefit_id": benefit_id, "scan_token": scan_token(tenant_id)},
        )
        assert valid.status_code == 200, valid.text
        assert valid.json()["qr_code"].startswith("data:image/svg+xml")

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        intent_count = await bypass_session.scalar(
            text(
                "SELECT count(*) FROM intent_events "
                "WHERE tenant_id=:tenant_id AND event_type='wecom_click' AND public_id=:public_id"
            ),
            {"tenant_id": tenant_id, "public_id": public_id},
        )
        assert intent_count == 1

        cross_tenant = await runtime_client.post(
            "/api/v1/integrations/wecom/contact-way",
            json={"benefit_id": benefit_id, "scan_token": scan_token(control_tenant_id)},
        )
        assert cross_tenant.status_code == 404

        forged_token = scan_token(tenant_id)
        parts = forged_token.split(".")
        parts[2] = f"{'a' if parts[2][0] != 'a' else 'b'}{parts[2][1:]}"
        forged = await runtime_client.post(
            "/api/v1/integrations/wecom/contact-way",
            json={"benefit_id": benefit_id, "scan_token": ".".join(parts)},
        )
        assert forged.status_code == 401


class TestIntentEventRLSMatrix:
    async def test_tenant_no_context_and_explicit_bypass_crud(
        self, runtime_pg_conn, bypass_session, migrated_pg_url: str
    ):
        from tests.test_acceptance.conftest import seed_baseline

        asyncpg_conn = runtime_pg_conn

        summary = await seed_baseline(migrated_pg_url)
        tenant_a = uuid.UUID(summary["baseline_tenant"]["id"])
        tenant_b = uuid.UUID(summary["control_tenant"]["id"])
        event_a = uuid.uuid4()
        event_b = uuid.uuid4()

        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await bypass_session.execute(
            text(
                "INSERT INTO intent_events (id, tenant_id, event_type, public_id, client_event_id) VALUES "
                "(:event_a, :tenant_a, 'page_view', :public_a, :client_a), "
                "(:event_b, :tenant_b, 'page_view', :public_b, :client_b)"
            ),
            {
                "event_a": event_a,
                "tenant_a": tenant_a,
                "public_a": summary["first_public_id"],
                "client_a": f"rls-a-{event_a}",
                "event_b": event_b,
                "tenant_b": tenant_b,
                "public_b": summary["control_first_public_id"],
                "client_b": f"rls-b-{event_b}",
            },
        )
        await bypass_session.commit()

        policy = await asyncpg_conn.fetchrow(
            "SELECT policyname, qual, with_check FROM pg_policies "
            "WHERE schemaname='public' AND tablename='intent_events'"
        )
        assert policy is not None
        assert policy["policyname"] == "intent_events_tenant_isolation"
        assert "app.bypass_rls" in policy["qual"]
        assert "app.bypass_rls" in policy["with_check"]
        index_valid = await asyncpg_conn.fetchval(
            "SELECT i.indisvalid AND i.indisunique FROM pg_index i "
            "JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname='uq_intent_events_tenant_client_event'"
        )
        assert index_valid is True

        await asyncpg_conn.execute("SELECT set_config('app.bypass_rls', 'false', true)")
        await asyncpg_conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_a))
        visible = await asyncpg_conn.fetch(
            "SELECT id FROM intent_events WHERE id IN ($1, $2) ORDER BY id", event_a, event_b
        )
        assert [row["id"] for row in visible] == [event_a]
        own_insert = uuid.uuid4()
        await asyncpg_conn.execute(
            "INSERT INTO intent_events (id, tenant_id, event_type, client_event_id) VALUES ($1, $2, 'page_view', $3)",
            own_insert,
            tenant_a,
            f"rls-own-{own_insert}",
        )
        assert (
            await asyncpg_conn.fetchval(
                "UPDATE intent_events SET event_type='benefit_click' WHERE id=$1 RETURNING id", event_a
            )
            == event_a
        )
        assert (
            await asyncpg_conn.fetchval(
                "UPDATE intent_events SET event_type='lead_click' WHERE id=$1 RETURNING id", event_b
            )
            is None
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="row-level security"):
            async with asyncpg_conn.transaction():
                await asyncpg_conn.execute(
                    "INSERT INTO intent_events (id, tenant_id, event_type, client_event_id) "
                    "VALUES ($1, $2, 'page_view', $3)",
                    uuid.uuid4(),
                    tenant_b,
                    f"rls-cross-{uuid.uuid4()}",
                )
        assert await asyncpg_conn.fetchval("DELETE FROM intent_events WHERE id=$1 RETURNING id", event_b) is None
        assert (
            await asyncpg_conn.fetchval("DELETE FROM intent_events WHERE id=$1 RETURNING id", own_insert) == own_insert
        )

        await asyncpg_conn.execute("SELECT set_config('app.tenant_id', '', true)")
        await asyncpg_conn.execute("SELECT set_config('app.bypass_rls', 'false', true)")
        assert (
            await asyncpg_conn.fetchval("SELECT count(*) FROM intent_events WHERE id IN ($1, $2)", event_a, event_b)
            == 0
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="row-level security"):
            async with asyncpg_conn.transaction():
                await asyncpg_conn.execute(
                    "INSERT INTO intent_events (id, tenant_id, event_type, client_event_id) "
                    "VALUES ($1, $2, 'page_view', $3)",
                    uuid.uuid4(),
                    tenant_a,
                    f"rls-none-{uuid.uuid4()}",
                )

        await asyncpg_conn.execute("SELECT set_config('app.bypass_rls', 'true', true)")
        assert (
            await asyncpg_conn.fetchval("SELECT count(*) FROM intent_events WHERE id IN ($1, $2)", event_a, event_b)
            == 0
        )
        bypass_insert = uuid.uuid4()
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="row-level security"):
            async with asyncpg_conn.transaction():
                await asyncpg_conn.execute(
                    "INSERT INTO intent_events (id, tenant_id, event_type, client_event_id) "
                    "VALUES ($1, $2, 'page_view', $3)",
                    bypass_insert,
                    tenant_b,
                    f"rls-bypass-{bypass_insert}",
                )

        await bypass_session.rollback()
        await bypass_session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        await bypass_session.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
        assert (
            await bypass_session.scalar(
                text("SELECT count(*) FROM intent_events WHERE id IN (:event_a, :event_b)"),
                {"event_a": event_a, "event_b": event_b},
            )
            == 2
        )
