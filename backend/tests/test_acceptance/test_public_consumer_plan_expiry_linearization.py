"""Real PostgreSQL races between platform plan expiry and public mutations."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import get_db, get_db_for_consumer
from app.main import app
from app.models.tenant import Tenant, TenantPlan
from app.services.scan_token import create_scan_token
from app.utils.client_ip import compute_ip_hash
from app.utils.crypto import hash_phone

pytestmark = [pytest.mark.acceptance, pytest.mark.anyio]


@dataclass(frozen=True, slots=True)
class _PlanState:
    plan: TenantPlan
    plan_expires_at: datetime | None


async def _capture_plan_state(
    owner_factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
) -> _PlanState:
    async with owner_factory() as db:
        tenant = await db.get(Tenant, tenant_id)
        assert tenant is not None
        return _PlanState(tenant.plan, tenant.plan_expires_at)


async def _restore_plan_state(
    owner_factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    prior: _PlanState,
) -> None:
    async with owner_factory() as db, db.begin():
        tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        assert tenant is not None
        tenant.plan = prior.plan
        tenant.plan_expires_at = prior.plan_expires_at


@pytest.fixture
async def runtime_client(migrated_pg_url: str) -> AsyncGenerator[AsyncClient, None]:
    """Run public endpoints as the production NOBYPASSRLS runtime role."""

    runtime_url = migrated_pg_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    runtime_engine = create_async_engine(runtime_url)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    control_engine = create_async_engine(migrated_pg_url)
    control_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with runtime_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_for_consumer] = override_get_db
    transport = ASGITransport(app=app)
    try:
        with patch("app.core.database.control_session_factory", control_factory):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield client
    finally:
        app.dependency_overrides.clear()
        await runtime_engine.dispose()
        await control_engine.dispose()


async def _expire_then_request(
    owner_factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    request: Callable[[], Awaitable[Response]],
) -> Response:
    """Commit expiry first while proving the public request waits on its row lock."""

    async with owner_factory() as db, db.begin():
        await db.execute(
            text("UPDATE tenants SET plan_expires_at=:future WHERE id=:tenant_id"),
            {"future": datetime.now(UTC) + timedelta(days=1), "tenant_id": tenant_id},
        )

    async with owner_factory() as db, db.begin():
        await db.execute(
            text("UPDATE tenants SET plan_expires_at=:expired WHERE id=:tenant_id"),
            {"expired": datetime.now(UTC) - timedelta(seconds=1), "tenant_id": tenant_id},
        )
        request_task = asyncio.create_task(request())
        await asyncio.sleep(0.15)
        assert not request_task.done(), "public mutation must wait for the platform expiry row lock"

    response = await asyncio.wait_for(request_task, timeout=10)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "TENANT_PLAN_EXPIRED"
    return response


async def test_expiry_commit_blocks_lead_exchange_and_benefit_delivery(
    migrated_pg_url: str,
    runtime_client: AsyncClient,
) -> None:
    from tests.test_acceptance.conftest import seed_baseline

    summary = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])
    public_id = summary["first_public_id"]
    benefit_id = uuid.UUID(summary["benefit"]["id"])
    consumer_id = uuid.uuid4()
    point_product_id = uuid.uuid4()
    consent_id = uuid.uuid4()
    phone = f"138{int(consumer_id.hex[:8], 16) % 100_000_000:08d}"
    phone_digest = hash_phone(phone)
    fixed_ip = "203.0.113.217"

    owner_engine = create_async_engine(migrated_pg_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    prior_plan = await _capture_plan_state(owner_factory, tenant_id)
    try:
        async with owner_factory() as db, db.begin():
            await db.execute(
                text("DELETE FROM benefit_deliveries WHERE tenant_id=:tenant_id AND benefit_id=:benefit_id"),
                {"tenant_id": tenant_id, "benefit_id": benefit_id},
            )
            await db.execute(
                text("DELETE FROM benefit_claims WHERE tenant_id=:tenant_id AND benefit_id=:benefit_id"),
                {"tenant_id": tenant_id, "benefit_id": benefit_id},
            )
            await db.execute(
                text("UPDATE benefits SET status='active', stock_used=0 WHERE tenant_id=:tenant_id AND id=:benefit_id"),
                {"tenant_id": tenant_id, "benefit_id": benefit_id},
            )
            await db.execute(
                text(
                    "INSERT INTO consumer_profiles (id, tenant_id, nickname, member_level, total_points) "
                    "VALUES (:id, :tenant_id, '到期竞态消费者', 'normal', 100)"
                ),
                {"id": consumer_id, "tenant_id": tenant_id},
            )
            await db.execute(
                text(
                    "INSERT INTO point_products "
                    "(id, tenant_id, name, points_cost, stock, total_claimed, enabled, per_consumer_limit, sort_order) "
                    "VALUES (:id, :tenant_id, '到期竞态商品', 10, 2, 0, true, 1, 0)"
                ),
                {"id": point_product_id, "tenant_id": tenant_id},
            )
            await db.execute(
                text(
                    "INSERT INTO consent_records "
                    "(id, tenant_id, consent_type, status, public_id, scenario, policy_version) "
                    "VALUES (:id, :tenant_id, 'privacy', 'granted', :public_id, 'lead_capture', 'race-v1')"
                ),
                {"id": consent_id, "tenant_id": tenant_id, "public_id": public_id},
            )

        lead_token = create_scan_token(
            public_id=public_id,
            ip_hash=compute_ip_hash(fixed_ip),
            tenant_id=str(tenant_id),
        )
        await _expire_then_request(
            owner_factory,
            tenant_id,
            lambda: runtime_client.post(
                "/api/v1/consumers/lead-capture",
                json={"public_id": public_id, "name": "不得写入", "phone": phone},
                headers={"Authorization": f"Bearer {lead_token}", "X-Forwarded-For": fixed_ip},
            ),
        )
        async with owner_factory() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM consumer_profiles WHERE tenant_id=:tenant_id AND phone_hash=:hash"),
                    {"tenant_id": tenant_id, "hash": phone_digest},
                )
                == 0
            )

        exchange_token = create_scan_token(
            public_id=public_id,
            ip_hash=compute_ip_hash(fixed_ip),
            tenant_id=str(tenant_id),
            consumer_id=str(consumer_id),
        )
        await _expire_then_request(
            owner_factory,
            tenant_id,
            lambda: runtime_client.post(
                "/api/v1/consumers/points/exchanges",
                json={"consumer_id": str(consumer_id), "product_id": str(point_product_id)},
                headers={"Authorization": f"Bearer {exchange_token}", "X-Forwarded-For": fixed_ip},
            ),
        )
        async with owner_factory() as db:
            exchange_state = (
                await db.execute(
                    text(
                        "SELECT p.stock, p.total_claimed, c.total_points, "
                        "(SELECT count(*) FROM point_redemptions r WHERE r.product_id=p.id), "
                        "(SELECT count(*) FROM point_transactions t WHERE t.consumer_id=c.id) "
                        "FROM point_products p CROSS JOIN consumer_profiles c "
                        "WHERE p.id=:product_id AND c.id=:consumer_id"
                    ),
                    {"product_id": point_product_id, "consumer_id": consumer_id},
                )
            ).one()
            assert tuple(exchange_state) == (2, 0, 100, 0, 0)

        claim_token = create_scan_token(
            public_id=public_id,
            ip_hash=compute_ip_hash(fixed_ip),
            tenant_id=str(tenant_id),
            consumer_id=str(consumer_id),
        )
        await _expire_then_request(
            owner_factory,
            tenant_id,
            lambda: runtime_client.post(
                "/api/v1/benefit-claims",
                json={"benefit_id": str(benefit_id), "scan_token": claim_token},
                headers={"X-Forwarded-For": fixed_ip},
            ),
        )
        async with owner_factory() as db:
            claim_state = (
                await db.execute(
                    text(
                        "SELECT b.stock_used, "
                        "(SELECT count(*) FROM benefit_claims c WHERE c.tenant_id=b.tenant_id AND c.benefit_id=b.id), "
                        "(SELECT count(*) FROM benefit_deliveries d WHERE d.tenant_id=b.tenant_id AND d.benefit_id=b.id) "
                        "FROM benefits b WHERE b.tenant_id=:tenant_id AND b.id=:benefit_id"
                    ),
                    {"tenant_id": tenant_id, "benefit_id": benefit_id},
                )
            ).one()
            assert tuple(claim_state) == (0, 0, 0)

        # Expiry blocks commercial mutations, not consumer reads or the
        # compliance right to withdraw previously granted consent.
        points_read = await runtime_client.get(
            "/api/v1/consumers/points/me",
            headers={"Authorization": f"Bearer {exchange_token}"},
        )
        assert points_read.status_code == 200, points_read.text
        assert points_read.json()["total_points"] == 100

        withdrawal = await runtime_client.post(
            f"/api/v1/public/consents/{consent_id}/withdraw",
            headers={"Authorization": f"Bearer {exchange_token}", "X-Forwarded-For": fixed_ip},
        )
        assert withdrawal.status_code == 200, withdrawal.text
        assert withdrawal.json()["status"] == "withdrawn"
    finally:
        try:
            await _restore_plan_state(owner_factory, tenant_id, prior_plan)
        finally:
            await owner_engine.dispose()


async def test_expiry_commit_blocks_scan_contact_and_external_preparation(
    migrated_pg_url: str,
    runtime_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import benefit_claims
    from app.services import wecom_integration
    from app.services.resolve_cache import resolve_cache
    from tests.test_acceptance.conftest import seed_baseline

    summary = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])
    public_id = summary["first_public_id"]
    benefit_id = uuid.UUID(summary["benefit"]["id"])
    campaign_id = uuid.UUID(summary["campaign"]["id"])
    consumer_id = uuid.uuid4()
    fixed_ip = "203.0.113.218"
    token = create_scan_token(
        public_id=public_id,
        ip_hash=compute_ip_hash(fixed_ip),
        tenant_id=str(tenant_id),
        consumer_id=str(consumer_id),
    )
    owner_engine = create_async_engine(migrated_pg_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    prior_plan = await _capture_plan_state(owner_factory, tenant_id)
    cash_preparations = 0
    wecom_preparations = 0

    async def cash_preparation_spy(*args, **kwargs):
        nonlocal cash_preparations
        cash_preparations += 1
        return {"status": "unexpected"}

    async def wecom_preparation_spy(*args, **kwargs):
        nonlocal wecom_preparations
        wecom_preparations += 1
        raise AssertionError("expired plan reached WeCom external-effect preparation")

    monkeypatch.setattr(benefit_claims, "_handle_cash_red_packet_claim", cash_preparation_spy)
    monkeypatch.setattr(wecom_integration, "get_or_create_claim_contact_way", wecom_preparation_spy)

    try:
        async with owner_factory() as db, db.begin():
            await db.execute(
                text("DELETE FROM scan_events WHERE tenant_id=:tenant_id AND public_id=:public_id"),
                {"tenant_id": tenant_id, "public_id": public_id},
            )
            await db.execute(
                text("DELETE FROM intent_events WHERE tenant_id=:tenant_id AND public_id=:public_id"),
                {"tenant_id": tenant_id, "public_id": public_id},
            )
            await db.execute(
                text("DELETE FROM anonymous_visitors WHERE tenant_id=:tenant_id"),
                {"tenant_id": tenant_id},
            )
            await db.execute(
                text("DELETE FROM wecom_contact_ways WHERE tenant_id=:tenant_id AND benefit_id=:benefit_id"),
                {"tenant_id": tenant_id, "benefit_id": benefit_id},
            )
            await db.execute(
                text(
                    "UPDATE code_items SET status='activated', first_scanned_at=NULL "
                    "WHERE tenant_id=:tenant_id AND public_id=:public_id"
                ),
                {"tenant_id": tenant_id, "public_id": public_id},
            )
            await db.execute(
                text(
                    "INSERT INTO tenant_quota_usage (tenant_id, scans) VALUES (:tenant_id, 0) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET scans=0"
                ),
                {"tenant_id": tenant_id},
            )
            await db.execute(
                text("UPDATE benefits SET status='active', stock_used=0 WHERE tenant_id=:tenant_id AND id=:benefit_id"),
                {"tenant_id": tenant_id, "benefit_id": benefit_id},
            )
        await resolve_cache.invalidate(f"resolve:{public_id}")

        await _expire_then_request(
            owner_factory,
            tenant_id,
            lambda: runtime_client.get(
                f"/c/{public_id}",
                headers={"Accept": "application/json", "X-Forwarded-For": fixed_ip},
            ),
        )
        async with owner_factory() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM scan_events WHERE tenant_id=:tenant_id AND public_id=:public_id"),
                    {"tenant_id": tenant_id, "public_id": public_id},
                )
                == 0
            )
            assert (
                await db.scalar(
                    text("SELECT scans FROM tenant_quota_usage WHERE tenant_id=:tenant_id"),
                    {"tenant_id": tenant_id},
                )
                == 0
            )

        intent_id = f"expired-intent-{uuid.uuid4()}"
        await _expire_then_request(
            owner_factory,
            tenant_id,
            lambda: runtime_client.post(
                "/api/v1/scan-events",
                json={"event_type": "view", "public_id": public_id, "client_event_id": intent_id},
                headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": fixed_ip},
            ),
        )
        async with owner_factory() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM intent_events WHERE tenant_id=:tenant_id AND client_event_id=:event_id"),
                    {"tenant_id": tenant_id, "event_id": intent_id},
                )
                == 0
            )

        await _expire_then_request(
            owner_factory,
            tenant_id,
            lambda: runtime_client.post(
                "/api/v1/integrations/wecom/contact-way",
                json={"benefit_id": str(benefit_id), "scan_token": token},
            ),
        )
        assert wecom_preparations == 0

        async with owner_factory() as db, db.begin():
            await db.execute(
                text("UPDATE benefits SET benefit_type='cash_red_packet' WHERE id=:benefit_id"),
                {"benefit_id": benefit_id},
            )
        await _expire_then_request(
            owner_factory,
            tenant_id,
            lambda: runtime_client.post(
                "/api/v1/benefit-claims",
                json={"benefit_id": str(benefit_id), "scan_token": token},
                headers={"X-Forwarded-For": fixed_ip},
            ),
        )
        assert cash_preparations == 0

        async with owner_factory() as db, db.begin():
            await db.execute(
                text("UPDATE benefits SET benefit_type='platform_coupon' WHERE id=:benefit_id"),
                {"benefit_id": benefit_id},
            )
            await db.execute(
                text("UPDATE campaigns SET rules_json=CAST(:rules AS jsonb) WHERE id=:id"),
                {"id": campaign_id, "rules": '{"wecom_mode":"required"}'},
            )
        await _expire_then_request(
            owner_factory,
            tenant_id,
            lambda: runtime_client.post(
                "/api/v1/benefit-claims",
                json={"benefit_id": str(benefit_id), "scan_token": token},
                headers={"X-Forwarded-For": fixed_ip},
            ),
        )
        assert wecom_preparations == 0
        async with owner_factory() as db:
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM wecom_contact_ways WHERE tenant_id=:tenant_id AND benefit_id=:benefit_id"
                    ),
                    {"tenant_id": tenant_id, "benefit_id": benefit_id},
                )
                == 0
            )
    finally:
        try:
            async with owner_factory() as db, db.begin():
                await db.execute(
                    text("UPDATE benefits SET benefit_type='platform_coupon' WHERE id=:benefit_id"),
                    {"benefit_id": benefit_id},
                )
                await db.execute(
                    text("UPDATE campaigns SET rules_json='{}'::jsonb WHERE id=:id"),
                    {"id": campaign_id},
                )
            await _restore_plan_state(owner_factory, tenant_id, prior_plan)
        finally:
            await owner_engine.dispose()


async def test_public_writer_first_holds_expiry_until_scan_intent_commit(
    migrated_pg_url: str,
    runtime_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import scan_events
    from tests.test_acceptance.conftest import seed_baseline

    summary = await seed_baseline(migrated_pg_url)
    tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])
    public_id = summary["first_public_id"]
    fixed_ip = "203.0.113.219"
    event_id = f"writer-first-{uuid.uuid4()}"
    token = create_scan_token(
        public_id=public_id,
        ip_hash=compute_ip_hash(fixed_ip),
        tenant_id=str(tenant_id),
    )
    owner_engine = create_async_engine(migrated_pg_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    prior_plan = await _capture_plan_state(owner_factory, tenant_id)
    effect_prepared = asyncio.Event()
    allow_business_commit = asyncio.Event()
    original_insert = scan_events.insert_intent_event_idempotent

    async def gated_insert(*args, **kwargs):
        result = await original_insert(*args, **kwargs)
        effect_prepared.set()
        await allow_business_commit.wait()
        return result

    monkeypatch.setattr(scan_events, "insert_intent_event_idempotent", gated_insert)
    try:
        async with owner_factory() as db, db.begin():
            await db.execute(
                text("UPDATE tenants SET plan_expires_at=:future WHERE id=:tenant_id"),
                {"future": datetime.now(UTC) + timedelta(days=1), "tenant_id": tenant_id},
            )
            await db.execute(
                text("DELETE FROM intent_events WHERE tenant_id=:tenant_id AND client_event_id=:event_id"),
                {"tenant_id": tenant_id, "event_id": event_id},
            )

        request_task = asyncio.create_task(
            runtime_client.post(
                "/api/v1/scan-events",
                json={"event_type": "view", "public_id": public_id, "client_event_id": event_id},
                headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": fixed_ip},
            )
        )
        await asyncio.wait_for(effect_prepared.wait(), timeout=10)

        async def expire_plan() -> None:
            async with owner_factory() as db, db.begin():
                await db.execute(
                    text("UPDATE tenants SET plan_expires_at=:expired WHERE id=:tenant_id"),
                    {"expired": datetime.now(UTC) - timedelta(seconds=1), "tenant_id": tenant_id},
                )

        expiry_task = asyncio.create_task(expire_plan())
        await asyncio.sleep(0.15)
        assert not expiry_task.done(), "writer-first public mutation must hold expiry through effect preparation"
        allow_business_commit.set()
        response = await asyncio.wait_for(request_task, timeout=10)
        await asyncio.wait_for(expiry_task, timeout=10)
        assert response.status_code == 201, response.text
        assert response.json()["persisted"] is True
        async with owner_factory() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM intent_events WHERE tenant_id=:tenant_id AND client_event_id=:event_id"),
                    {"tenant_id": tenant_id, "event_id": event_id},
                )
                == 1
            )
    finally:
        allow_business_commit.set()
        try:
            await _restore_plan_state(owner_factory, tenant_id, prior_plan)
        finally:
            await owner_engine.dispose()
