"""Public resolver and scan telemetry under a real NOBYPASSRLS runtime role."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import asyncpg
import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.plan import QuotaRolloutPhase, TenantQuotaUsage
from app.models.tenant import Tenant, TenantPlan
from app.services.quota import (
    QUOTA_RECONCILIATION_SOURCE_REVISION,
    lock_quota_rollout_state,
)

pytestmark = pytest.mark.acceptance


@dataclass(frozen=True, slots=True)
class _TenantPlanState:
    plan: TenantPlan
    plan_expires_at: datetime | None
    quota: dict | None


@dataclass(frozen=True, slots=True)
class _TenantUsageState:
    codes: int
    scans: int
    campaigns: int
    products: int
    accounts: int
    enforcement_ready: bool
    source_revision: str | None
    reconciled_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class _TenantState:
    plan: _TenantPlanState
    usage: _TenantUsageState | None


@dataclass(frozen=True, slots=True)
class _QuotaEpochState:
    source_revision: str
    phase: QuotaRolloutPhase
    started_at: datetime
    drained_at: datetime | None
    activated_at: datetime | None
    drained_by: str | None
    activated_by: str | None


@pytest.fixture
async def current_quota_epoch(migrated_pg_url: str):
    """Activate only the global gate, then restore its exact provenance."""

    owner_engine = create_async_engine(migrated_pg_url)
    owner_factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with owner_factory() as db, db.begin():
        state = await lock_quota_rollout_state(db, write=True)
        assert state is not None
        prior = _QuotaEpochState(
            state.source_revision,
            state.phase,
            state.started_at,
            state.drained_at,
            state.activated_at,
            state.drained_by,
            state.activated_by,
        )
        now = datetime.now(UTC)
        state.source_revision = QUOTA_RECONCILIATION_SOURCE_REVISION
        state.phase = QuotaRolloutPhase.active
        state.started_at = now
        state.drained_at = now
        state.activated_at = now
        state.drained_by = "public-scan-acceptance"
        state.activated_by = "public-scan-acceptance"
        await db.flush()
    try:
        yield
    finally:
        try:
            async with owner_factory() as db, db.begin():
                state = await lock_quota_rollout_state(db, write=True)
                assert state is not None
                state.source_revision = prior.source_revision
                state.phase = prior.phase
                state.started_at = prior.started_at
                state.drained_at = prior.drained_at
                state.activated_at = prior.activated_at
                state.drained_by = prior.drained_by
                state.activated_by = prior.activated_by
        finally:
            await owner_engine.dispose()


class _TenantStateGuard:
    def __init__(self, owner_factory: async_sessionmaker[AsyncSession]):
        self._owner_factory = owner_factory
        self._captured: dict[uuid.UUID, _TenantState] = {}

    @staticmethod
    def _usage_state(usage: TenantQuotaUsage | None) -> _TenantUsageState | None:
        if usage is None:
            return None
        return _TenantUsageState(
            codes=usage.codes,
            scans=usage.scans,
            campaigns=usage.campaigns,
            products=usage.products,
            accounts=usage.accounts,
            enforcement_ready=usage.enforcement_ready,
            source_revision=usage.source_revision,
            reconciled_at=usage.reconciled_at,
            created_at=usage.created_at,
            updated_at=usage.updated_at,
        )

    async def read(self, tenant_id: str | uuid.UUID) -> _TenantState:
        normalized_id = uuid.UUID(str(tenant_id))
        async with self._owner_factory() as db:
            tenant = await db.get(Tenant, normalized_id)
            assert tenant is not None
            usage = await db.get(TenantQuotaUsage, normalized_id)
            return _TenantState(
                plan=_TenantPlanState(
                    tenant.plan,
                    tenant.plan_expires_at,
                    dict(tenant.quota) if tenant.quota is not None else None,
                ),
                usage=self._usage_state(usage),
            )

    async def __call__(self, *tenant_ids: str | uuid.UUID) -> None:
        async with self._owner_factory() as db, db.begin():
            await lock_quota_rollout_state(db)
            for raw_tenant_id in tenant_ids:
                tenant_id = uuid.UUID(str(raw_tenant_id))
                if tenant_id in self._captured:
                    continue
                tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
                assert tenant is not None
                usage = await db.get(TenantQuotaUsage, tenant_id)
                self._captured[tenant_id] = _TenantState(
                    plan=_TenantPlanState(
                        tenant.plan,
                        tenant.plan_expires_at,
                        dict(tenant.quota) if tenant.quota is not None else None,
                    ),
                    usage=self._usage_state(usage),
                )

    async def restore(self) -> None:
        if not self._captured:
            return
        async with self._owner_factory() as db, db.begin():
            await lock_quota_rollout_state(db)
            for tenant_id, prior in self._captured.items():
                tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
                assert tenant is not None
                tenant.plan = prior.plan.plan
                tenant.plan_expires_at = prior.plan.plan_expires_at
                tenant.quota = dict(prior.plan.quota) if prior.plan.quota is not None else None

                usage = await db.get(TenantQuotaUsage, tenant_id)
                if prior.usage is None:
                    if usage is not None:
                        await db.delete(usage)
                    continue
                if usage is None:
                    usage = TenantQuotaUsage(tenant_id=tenant_id)
                    db.add(usage)
                usage.codes = prior.usage.codes
                usage.scans = prior.usage.scans
                usage.campaigns = prior.usage.campaigns
                usage.products = prior.usage.products
                usage.accounts = prior.usage.accounts
                usage.enforcement_ready = prior.usage.enforcement_ready
                usage.source_revision = prior.usage.source_revision
                usage.reconciled_at = prior.usage.reconciled_at
                usage.created_at = prior.usage.created_at
                usage.updated_at = prior.usage.updated_at
        self._captured.clear()


@pytest.fixture
async def preserve_tenant_plan_state(migrated_pg_url: str):
    """Opt-in exact plan/quota/usage guard for touched shared tenants."""

    owner_engine = create_async_engine(migrated_pg_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    guard = _TenantStateGuard(owner_factory)
    try:
        yield guard
    finally:
        try:
            await guard.restore()
        finally:
            await owner_engine.dispose()


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


async def _create_owned_tenant(db: AsyncSession, marker: str) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO tenants "
            "(id, name, slug, status, plan, plan_expires_at, tenant_type, quota, enabled_features, "
            "created_at, updated_at) "
            "VALUES (:id, :name, :slug, 'active', 'free', :expires_at, 'brand', '{}'::json, '{}'::json, "
            "now(), now())"
        ),
        {
            "id": tenant_id,
            "name": f"Public scan acceptance {marker}",
            "slug": f"public-scan-{marker}-{tenant_id.hex[:8]}",
            "expires_at": datetime.now(UTC) + timedelta(days=30),
        },
    )
    return tenant_id


async def _owned_benefit_fact_sentinel(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    benefit_id: uuid.UUID,
) -> tuple[int, int, int, int, int]:
    row = (
        await db.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM benefits WHERE tenant_id=:tenant_id AND id=:benefit_id), "
                "(SELECT count(*) FROM benefit_claims WHERE tenant_id=:tenant_id AND benefit_id=:benefit_id), "
                "(SELECT count(*) FROM benefit_deliveries WHERE tenant_id=:tenant_id AND benefit_id=:benefit_id), "
                "(SELECT count(*) FROM wecom_contact_ways WHERE tenant_id=:tenant_id AND benefit_id=:benefit_id), "
                "(SELECT count(*) FROM wecom_external_contacts WHERE tenant_id=:tenant_id AND benefit_id=:benefit_id)"
            ),
            {"tenant_id": tenant_id, "benefit_id": benefit_id},
        )
    ).one()
    return tuple(int(value) for value in row)


async def _shared_fact_counts(db: AsyncSession) -> tuple[int, ...]:
    row = (
        await db.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM tenants), "
                "(SELECT count(*) FROM code_items), "
                "(SELECT count(*) FROM scan_events), "
                "(SELECT count(*) FROM intent_events), "
                "(SELECT count(*) FROM anonymous_visitors), "
                "(SELECT count(*) FROM benefits), "
                "(SELECT count(*) FROM benefit_claims), "
                "(SELECT count(*) FROM benefit_deliveries), "
                "(SELECT count(*) FROM connectors), "
                "(SELECT count(*) FROM wecom_contact_ways), "
                "(SELECT count(*) FROM wecom_external_contacts)"
            )
        )
    ).one()
    return tuple(int(value) for value in row)


async def _shared_code_state(db: AsyncSession, summary: dict) -> tuple[tuple, ...]:
    rows = (
        await db.execute(
            text(
                "SELECT tenant_id, public_id, status, revoked_at, first_scanned_at, activated_at, updated_at "
                "FROM code_items "
                "WHERE (tenant_id=:baseline_tenant AND public_id=:baseline_public_id) "
                "OR (tenant_id=:control_tenant AND public_id=:control_public_id) "
                "ORDER BY tenant_id, public_id"
            ),
            {
                "baseline_tenant": summary["baseline_tenant"]["id"],
                "baseline_public_id": summary["first_public_id"],
                "control_tenant": summary["control_tenant"]["id"],
                "control_public_id": summary["control_first_public_id"],
            },
        )
    ).all()
    return tuple(tuple(row) for row in rows)


async def _owned_scan_fact_sentinel(
    db: AsyncSession,
    *,
    tenant_id: str | uuid.UUID,
    code_item_id: uuid.UUID,
    public_id: str,
    visitor_id: str,
) -> tuple[int, int, int, int]:
    row = (
        await db.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM code_items WHERE tenant_id=:tenant_id AND id=:code_item_id), "
                "(SELECT count(*) FROM scan_events WHERE tenant_id=:tenant_id AND public_id=:public_id), "
                "(SELECT count(*) FROM intent_events WHERE tenant_id=:tenant_id AND public_id=:public_id), "
                "(SELECT count(*) FROM anonymous_visitors "
                " WHERE tenant_id=:tenant_id AND visitor_id=:visitor_id)"
            ),
            {
                "tenant_id": tenant_id,
                "code_item_id": code_item_id,
                "public_id": public_id,
                "visitor_id": visitor_id,
            },
        )
    ).one()
    return int(row[0]), int(row[1]), int(row[2]), int(row[3])


async def _insert_owned_activated_item(
    migrated_pg_url: str,
    *,
    item_id: uuid.UUID,
    tenant_id: str,
    code_batch_id: uuid.UUID,
    public_id: str,
) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        async with owner.transaction():
            await owner.execute("ALTER TABLE code_items DISABLE TRIGGER USER")
            await owner.execute(
                "INSERT INTO code_items "
                "(id,tenant_id,code_batch_id,public_id,status,code_type,activated_at) "
                "VALUES($1,$2,$3,$4,'activated','single',now())",
                item_id,
                uuid.UUID(tenant_id),
                code_batch_id,
                public_id,
            )
            await owner.execute("ALTER TABLE code_items ENABLE TRIGGER USER")
    finally:
        await owner.close()


async def _delete_owned_code_item(migrated_pg_url: str, tenant_id: str, item_id: uuid.UUID) -> None:
    owner = await asyncpg.connect(migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        async with owner.transaction():
            await owner.execute("ALTER TABLE code_items DISABLE TRIGGER USER")
            assert (
                await owner.execute(
                    "DELETE FROM code_items WHERE tenant_id=$1 AND id=$2",
                    uuid.UUID(tenant_id),
                    item_id,
                )
                == "DELETE 1"
            )
            await owner.execute("ALTER TABLE code_items ENABLE TRIGGER USER")
    finally:
        await owner.close()


class TestPublicScanRuntimeRLS:
    @pytest.mark.parametrize("_repeat", (0, 1), ids=("first-pass", "same-db-repeat"))
    async def test_public_benefit_claim_uses_scan_token_tenant_rls(
        self,
        runtime_client: AsyncClient,
        bypass_session,
        asyncpg_conn,
        _repeat: int,
    ):
        """A public claim must establish RLS context before any business query."""

        from app.utils.client_ip import compute_ip_hash

        tenant_id = uuid.uuid4()
        control_tenant_id = uuid.uuid4()
        public_id = f"C15{uuid.uuid4().hex[:13]}"
        benefit_id = uuid.uuid4()
        fixed_ip = "203.0.113.209"
        claim_created = False

        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        shared_before = await _shared_fact_counts(bypass_session)
        tenant_id = await _create_owned_tenant(bypass_session, "claim")
        control_tenant_id = await _create_owned_tenant(bypass_session, "claim-control")
        await bypass_session.execute(
            text(
                "INSERT INTO benefits "
                "(id, tenant_id, name, benefit_type, config_json, stock_total, stock_used, per_person_limit, status) "
                "VALUES (:id, :tenant_id, :name, 'platform_coupon', '{}'::json, 2, 0, 1, 'active')"
            ),
            {"id": benefit_id, "tenant_id": tenant_id, "name": f"cycle15-claim-{benefit_id}"},
        )
        await bypass_session.commit()

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

        try:
            assert await asyncpg_conn.fetchval("SELECT current_user") == "acceptance_tester"
            assert await asyncpg_conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user") is False

            success = await runtime_client.post(
                "/api/v1/benefit-claims",
                json={"benefit_id": str(benefit_id), "scan_token": scan_token(for_tenant=str(tenant_id))},
                headers={"X-Forwarded-For": fixed_ip},
            )
            assert success.status_code == 201, success.text
            assert success.json() == {"status": "claimed", "benefit_id": str(benefit_id)}
            claim_created = True

            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            assert await _owned_benefit_fact_sentinel(bypass_session, tenant_id=tenant_id, benefit_id=benefit_id) == (
                1,
                1,
                0,
                0,
                0,
            )
            assert (
                await bypass_session.scalar(
                    text("SELECT stock_used FROM benefits WHERE tenant_id=:tenant_id AND id=:benefit_id"),
                    {"tenant_id": tenant_id, "benefit_id": benefit_id},
                )
                == 1
            )

            expired = await runtime_client.post(
                "/api/v1/benefit-claims",
                json={
                    "benefit_id": str(benefit_id),
                    "scan_token": scan_token(for_tenant=str(tenant_id), expires_in=-1),
                },
                headers={"X-Forwarded-For": fixed_ip},
            )
            assert expired.status_code == 401

            forged_token = scan_token(for_tenant=str(tenant_id))
            parts = forged_token.split(".")
            parts[2] = f"{'a' if parts[2][0] != 'a' else 'b'}{parts[2][1:]}"
            forged = await runtime_client.post(
                "/api/v1/benefit-claims",
                json={"benefit_id": str(benefit_id), "scan_token": ".".join(parts)},
                headers={"X-Forwarded-For": fixed_ip},
            )
            assert forged.status_code == 401

            cross_tenant = await runtime_client.post(
                "/api/v1/benefit-claims",
                json={
                    "benefit_id": str(benefit_id),
                    "scan_token": scan_token(for_tenant=str(control_tenant_id)),
                },
                headers={"X-Forwarded-For": fixed_ip},
            )
            assert cross_tenant.status_code == 404
            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            assert await _owned_benefit_fact_sentinel(bypass_session, tenant_id=tenant_id, benefit_id=benefit_id) == (
                1,
                1,
                0,
                0,
                0,
            )
        finally:
            await bypass_session.rollback()
            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            deleted_deliveries = await bypass_session.execute(
                text("DELETE FROM benefit_deliveries WHERE tenant_id=:tenant_id AND benefit_id=:benefit_id"),
                {"tenant_id": tenant_id, "benefit_id": benefit_id},
            )
            assert deleted_deliveries.rowcount == 0
            deleted_claims = await bypass_session.execute(
                text("DELETE FROM benefit_claims WHERE tenant_id=:tenant_id AND benefit_id=:benefit_id"),
                {"tenant_id": tenant_id, "benefit_id": benefit_id},
            )
            assert deleted_claims.rowcount == int(claim_created)
            deleted_benefit = await bypass_session.execute(
                text("DELETE FROM benefits WHERE tenant_id=:tenant_id AND id=:benefit_id"),
                {"tenant_id": tenant_id, "benefit_id": benefit_id},
            )
            assert deleted_benefit.rowcount == 1
            assert await _owned_benefit_fact_sentinel(bypass_session, tenant_id=tenant_id, benefit_id=benefit_id) == (
                0,
                0,
                0,
                0,
                0,
            )
            for owned_tenant_id in (tenant_id, control_tenant_id):
                deleted_tenant = await bypass_session.execute(
                    text("DELETE FROM tenants WHERE id=:tenant_id"), {"tenant_id": owned_tenant_id}
                )
                assert deleted_tenant.rowcount == 1
            assert await _shared_fact_counts(bypass_session) == shared_before
            await bypass_session.commit()

    @pytest.mark.parametrize("_repeat", (0, 1), ids=("first-pass", "same-db-repeat"))
    async def test_resolve_and_telemetry_use_trusted_tenant_context(
        self,
        runtime_client: AsyncClient,
        bypass_session,
        asyncpg_conn,
        migrated_pg_url: str,
        preserve_tenant_plan_state,
        _repeat: int,
    ):
        from app.services.public_id import generate_public_id
        from app.services.resolve_cache import resolve_cache
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]
        source_public_id = summary["first_public_id"]
        control_public_id = summary["control_first_public_id"]
        public_id = generate_public_id()
        code_item_id = uuid.uuid4()
        visitor_pk = uuid.uuid4()
        visitor_id = f"cycle15-resolve-{uuid.uuid4()}"
        event_prefix = f"cycle15-{uuid.uuid4().hex}"
        fixed_ip = "203.0.113.201"
        primary_event_id = f"{event_prefix}:primary"
        concurrent_event_id = f"{event_prefix}:concurrent"
        cross_event_id = f"{event_prefix}:cross"
        forged_event_id = f"{event_prefix}:forged"
        await preserve_tenant_plan_state(tenant_id)
        tenant_state_before = await preserve_tenant_plan_state.read(tenant_id)

        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        shared_counts_before = await _shared_fact_counts(bypass_session)
        shared_code_before = await _shared_code_state(bypass_session, summary)
        owned_before = await _owned_scan_fact_sentinel(
            bypass_session,
            tenant_id=tenant_id,
            code_item_id=code_item_id,
            public_id=public_id,
            visitor_id=visitor_id,
        )
        assert owned_before == (0, 0, 0, 0)
        code_batch_id = await bypass_session.scalar(
            text("SELECT code_batch_id FROM code_items WHERE tenant_id=:tenant_id AND public_id=:public_id"),
            {"tenant_id": tenant_id, "public_id": source_public_id},
        )
        assert code_batch_id is not None
        await bypass_session.commit()
        # This node owns only the public resolver/RLS boundary. Build one extra
        # activated item in the already-authoritative baseline batch without
        # pretending it traversed the delivery lifecycle (covered separately).
        await _insert_owned_activated_item(
            migrated_pg_url,
            item_id=code_item_id,
            tenant_id=tenant_id,
            code_batch_id=code_batch_id,
            public_id=public_id,
        )
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await bypass_session.execute(
            text(
                "INSERT INTO anonymous_visitors (id, tenant_id, visitor_id, first_environment) "
                "VALUES (:id, :tenant_id, :visitor_id, 'browser')"
            ),
            {"id": visitor_pk, "tenant_id": tenant_id, "visitor_id": visitor_id},
        )
        await bypass_session.execute(
            text("UPDATE tenants SET plan_expires_at=:future WHERE id=:tenant_id"),
            {"future": datetime.now(UTC) + timedelta(days=30), "tenant_id": tenant_id},
        )
        await bypass_session.commit()
        await resolve_cache.invalidate(f"resolve:{public_id}")

        try:
            assert await asyncpg_conn.fetchval("SELECT current_user") == "acceptance_tester"
            assert await asyncpg_conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user") is False

            resolved = await runtime_client.get(
                f"/c/{public_id}",
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0",
                    "X-Forwarded-For": fixed_ip,
                    "X-Visitor-ID": visitor_id,
                },
            )
            assert resolved.status_code == 200, resolved.text
            payload = resolved.json()
            assert payload["code_data"]["public_id"] == public_id
            assert payload["scan_info"]["visitor_id"] == visitor_id
            scan_token = payload["scan_token"]

            telemetry = await runtime_client.post(
                "/api/v1/scan-events",
                json={"event_type": "view", "public_id": public_id, "client_event_id": primary_event_id},
                headers={
                    "Authorization": f"Bearer {scan_token}",
                    "X-Visitor-ID": visitor_id,
                    "X-Forwarded-For": fixed_ip,
                },
            )
            assert telemetry.status_code == 201, telemetry.text
            assert telemetry.json()["persisted"] is True

            async def post_duplicate():
                return await runtime_client.post(
                    "/api/v1/scan-events",
                    json={"event_type": "view", "public_id": public_id, "client_event_id": concurrent_event_id},
                    headers={
                        "Authorization": f"Bearer {scan_token}",
                        "X-Visitor-ID": visitor_id,
                        "X-Forwarded-For": fixed_ip,
                    },
                )

            duplicate_responses = await asyncio.gather(post_duplicate(), post_duplicate())
            assert [response.status_code for response in duplicate_responses] == [201, 201]
            assert sorted(response.json()["deduplicated"] for response in duplicate_responses) == [False, True]

            cross_tenant = await runtime_client.post(
                "/api/v1/scan-events",
                json={"event_type": "view", "public_id": control_public_id, "client_event_id": cross_event_id},
                headers={"Authorization": f"Bearer {scan_token}", "X-Forwarded-For": fixed_ip},
            )
            assert cross_tenant.status_code == 201
            assert cross_tenant.json() == {"status": "ignored", "reason": "invalid_token"}

            token_parts = scan_token.split(".")
            token_parts[2] = f"{'a' if token_parts[2][0] != 'a' else 'b'}{token_parts[2][1:]}"
            forged = await runtime_client.post(
                "/api/v1/scan-events",
                json={"event_type": "view", "public_id": public_id, "client_event_id": forged_event_id},
                headers={"Authorization": f"Bearer {'.'.join(token_parts)}", "X-Forwarded-For": fixed_ip},
            )
            assert forged.status_code == 201
            assert forged.json() == {"status": "ignored", "reason": "invalid_token"}

            invalid_code = await runtime_client.get(
                f"/c/{generate_public_id()}", headers={"Accept": "application/json"}
            )
            assert invalid_code.status_code == 404
            assert "tenant" not in invalid_code.text.lower()

            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            assert await _owned_scan_fact_sentinel(
                bypass_session,
                tenant_id=tenant_id,
                code_item_id=code_item_id,
                public_id=public_id,
                visitor_id=visitor_id,
            ) == (1, 1, 2, 1)
            assert (
                await bypass_session.scalar(
                    text("SELECT count(*) FROM intent_events WHERE client_event_id IN (:cross, :forged)"),
                    {"cross": cross_event_id, "forged": forged_event_id},
                )
                == 0
            )
        finally:
            await resolve_cache.invalidate(f"resolve:{public_id}")
            await bypass_session.rollback()
            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            deleted_intents = await bypass_session.execute(
                text("DELETE FROM intent_events WHERE tenant_id=:tenant_id AND public_id=:public_id"),
                {"tenant_id": tenant_id, "public_id": public_id},
            )
            assert deleted_intents.rowcount == 2
            deleted_scans = await bypass_session.execute(
                text("DELETE FROM scan_events WHERE tenant_id=:tenant_id AND public_id=:public_id"),
                {"tenant_id": tenant_id, "public_id": public_id},
            )
            assert deleted_scans.rowcount == 1
            deleted_visitor = await bypass_session.execute(
                text(
                    "DELETE FROM anonymous_visitors "
                    "WHERE tenant_id=:tenant_id AND id=:visitor_pk AND visitor_id=:visitor_id"
                ),
                {"tenant_id": tenant_id, "visitor_pk": visitor_pk, "visitor_id": visitor_id},
            )
            assert deleted_visitor.rowcount == 1
            await bypass_session.commit()
            await _delete_owned_code_item(migrated_pg_url, tenant_id, code_item_id)
            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            assert (
                await _owned_scan_fact_sentinel(
                    bypass_session,
                    tenant_id=tenant_id,
                    code_item_id=code_item_id,
                    public_id=public_id,
                    visitor_id=visitor_id,
                )
                == owned_before
            )
            await bypass_session.commit()
            await preserve_tenant_plan_state.restore()

        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        assert await preserve_tenant_plan_state.read(tenant_id) == tenant_state_before
        assert await _shared_fact_counts(bypass_session) == shared_counts_before
        assert await _shared_code_state(bypass_session, summary) == shared_code_before

    @pytest.mark.parametrize("_repeat", (0, 1), ids=("first-pass", "same-db-repeat"))
    async def test_expired_plan_and_max_scans_fail_closed_under_runtime_rls(
        self,
        runtime_client: AsyncClient,
        bypass_session,
        migrated_pg_url: str,
        preserve_tenant_plan_state,
        current_quota_epoch,
        _repeat: int,
    ):
        from app.services.public_id import generate_public_id
        from tests.test_acceptance.conftest import seed_baseline

        summary = await seed_baseline(migrated_pg_url)
        tenant_id = summary["baseline_tenant"]["id"]
        source_public_id = summary["first_public_id"]
        public_id = generate_public_id()
        code_item_id = uuid.uuid4()
        visitor_id = f"cycle15-quota-{uuid.uuid4()}"
        visitor_pk = uuid.uuid4()
        await preserve_tenant_plan_state(tenant_id)
        sentinel_before = await preserve_tenant_plan_state.read(tenant_id)

        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        shared_counts_before = await _shared_fact_counts(bypass_session)
        shared_code_before = await _shared_code_state(bypass_session, summary)
        fact_sentinel_before = await _owned_scan_fact_sentinel(
            bypass_session,
            tenant_id=tenant_id,
            code_item_id=code_item_id,
            public_id=public_id,
            visitor_id=visitor_id,
        )
        assert fact_sentinel_before == (0, 0, 0, 0)
        code_batch_id = await bypass_session.scalar(
            text("SELECT code_batch_id FROM code_items WHERE tenant_id=:tenant_id AND public_id=:public_id"),
            {"tenant_id": tenant_id, "public_id": source_public_id},
        )
        assert code_batch_id is not None
        await bypass_session.commit()
        await _insert_owned_activated_item(
            migrated_pg_url,
            item_id=code_item_id,
            tenant_id=tenant_id,
            code_batch_id=code_batch_id,
            public_id=public_id,
        )
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        await bypass_session.execute(
            text(
                "INSERT INTO anonymous_visitors (id, tenant_id, visitor_id, first_environment) "
                "VALUES (:id, :tenant_id, :visitor_id, 'browser')"
            ),
            {"id": visitor_pk, "tenant_id": tenant_id, "visitor_id": visitor_id},
        )
        await bypass_session.commit()

        from app.services.resolve_cache import resolve_cache

        await resolve_cache.invalidate(f"resolve:{public_id}")
        try:
            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            await bypass_session.execute(
                text("UPDATE tenants SET plan_expires_at=:expired WHERE id=:tenant_id"),
                {"expired": datetime.now(UTC) - timedelta(seconds=1), "tenant_id": tenant_id},
            )
            await bypass_session.commit()

            expired = await runtime_client.get(
                f"/c/{public_id}",
                headers={"Accept": "application/json", "X-Visitor-ID": visitor_id},
            )
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
                text(
                    "INSERT INTO tenant_quota_usage "
                    "(tenant_id, reconciled_at, source_revision, enforcement_ready) "
                    "VALUES (:tenant_id, :reconciled_at, :source_revision, true) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET "
                    "reconciled_at=EXCLUDED.reconciled_at, source_revision=EXCLUDED.source_revision, "
                    "enforcement_ready=true"
                ),
                {
                    "tenant_id": tenant_id,
                    "reconciled_at": datetime.now(UTC),
                    "source_revision": QUOTA_RECONCILIATION_SOURCE_REVISION,
                },
            )
            current_scans = int(
                await bypass_session.scalar(
                    text("SELECT scans FROM tenant_quota_usage WHERE tenant_id=:tenant_id"),
                    {"tenant_id": tenant_id},
                )
                or 0
            )
            await bypass_session.execute(
                text("UPDATE tenants SET plan_expires_at=:future, quota=:quota WHERE id=:tenant_id"),
                {
                    "future": datetime.now(UTC) + timedelta(days=30),
                    "quota": f'{{"max_scans": {current_scans + 1}}}',
                    "tenant_id": tenant_id,
                },
            )
            await bypass_session.commit()

            first = await runtime_client.get(
                f"/c/{public_id}",
                headers={"Accept": "application/json", "X-Visitor-ID": visitor_id},
            )
            second = await runtime_client.get(
                f"/c/{public_id}",
                headers={"Accept": "application/json", "X-Visitor-ID": visitor_id},
            )
            assert first.status_code == 200, first.text
            assert first.json()["scan_info"]["visitor_id"] == visitor_id
            assert second.status_code == 429
            assert second.json()["code"] == "QUOTA_EXCEEDED"

            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            persisted = await bypass_session.scalar(
                text("SELECT count(*) FROM scan_events WHERE tenant_id=:tenant_id AND public_id=:public_id"),
                {"tenant_id": tenant_id, "public_id": public_id},
            )
            assert persisted == 1
            assert await _owned_scan_fact_sentinel(
                bypass_session,
                tenant_id=tenant_id,
                code_item_id=code_item_id,
                public_id=public_id,
                visitor_id=visitor_id,
            ) == (1, 1, 0, 1)
        finally:
            await resolve_cache.invalidate(f"resolve:{public_id}")
            await bypass_session.rollback()
            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            deleted_intents = await bypass_session.execute(
                text("DELETE FROM intent_events WHERE tenant_id=:tenant_id AND public_id=:public_id"),
                {"tenant_id": tenant_id, "public_id": public_id},
            )
            assert deleted_intents.rowcount == 0
            deleted_scans = await bypass_session.execute(
                text("DELETE FROM scan_events WHERE tenant_id=:tenant_id AND public_id=:public_id"),
                {"tenant_id": tenant_id, "public_id": public_id},
            )
            assert deleted_scans.rowcount == 1
            deleted_visitor = await bypass_session.execute(
                text(
                    "DELETE FROM anonymous_visitors "
                    "WHERE tenant_id=:tenant_id AND id=:visitor_pk AND visitor_id=:visitor_id"
                ),
                {"tenant_id": tenant_id, "visitor_pk": visitor_pk, "visitor_id": visitor_id},
            )
            assert deleted_visitor.rowcount == 1
            await bypass_session.commit()
            await _delete_owned_code_item(migrated_pg_url, tenant_id, code_item_id)
            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            assert (
                await _owned_scan_fact_sentinel(
                    bypass_session,
                    tenant_id=tenant_id,
                    code_item_id=code_item_id,
                    public_id=public_id,
                    visitor_id=visitor_id,
                )
                == fact_sentinel_before
            )
            await bypass_session.commit()
            await preserve_tenant_plan_state.restore()

        sentinel_after = await preserve_tenant_plan_state.read(tenant_id)
        assert sentinel_after == sentinel_before
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        assert await _shared_fact_counts(bypass_session) == shared_counts_before
        assert await _shared_code_state(bypass_session, summary) == shared_code_before

    @pytest.mark.parametrize("_repeat", (0, 1), ids=("first-pass", "same-db-repeat"))
    async def test_public_wecom_contact_way_uses_scan_token_tenant_rls(
        self,
        runtime_client: AsyncClient,
        bypass_session,
        _repeat: int,
    ):
        tenant_id = uuid.uuid4()
        control_tenant_id = uuid.uuid4()
        public_id = f"C15{uuid.uuid4().hex[:13]}"
        benefit_id = uuid.uuid4()
        connector_id = uuid.uuid4()
        visitor_id = f"cycle15-wecom-{uuid.uuid4()}"
        forged_external_userid = f"cycle15-forged-{uuid.uuid4()}"
        contact_way_created = False

        await bypass_session.rollback()
        await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        shared_before = await _shared_fact_counts(bypass_session)
        tenant_id = await _create_owned_tenant(bypass_session, "wecom")
        control_tenant_id = await _create_owned_tenant(bypass_session, "wecom-control")
        await bypass_session.execute(
            text(
                "INSERT INTO connectors (id, tenant_id, name, connector_type, config, enabled) "
                "VALUES (:id, :tenant_id, :name, 'wecom_customer_contact', "
                "CAST(:config AS jsonb), true)"
            ),
            {
                "id": connector_id,
                "tenant_id": tenant_id,
                "name": f"cycle15-wecom-{connector_id}",
                "config": '{"status":"connected","mock_mode":true,"customer_service_user_ids":["demo-member"]}',
            },
        )
        await bypass_session.execute(
            text(
                "INSERT INTO benefits "
                "(id, tenant_id, name, benefit_type, config_json, stock_total, stock_used, per_person_limit, status) "
                "VALUES (:id, :tenant_id, :name, 'platform_coupon', '{}'::json, 2, 0, 1, 'active')"
            ),
            {"id": benefit_id, "tenant_id": tenant_id, "name": f"cycle15-wecom-{benefit_id}"},
        )
        await bypass_session.commit()

        def scan_token(for_tenant: str) -> str:
            return jwt.encode(
                {
                    "public_id": public_id,
                    "tenant_id": for_tenant,
                    "visitor_id": visitor_id,
                    "type": "scan_token",
                    "jti": str(uuid.uuid4()),
                    "exp": int(time.time()) + 300,
                },
                settings.secret_key,
                algorithm="HS256",
            )

        try:
            forged_callback = await runtime_client.post(
                f"/api/v1/integrations/wecom/callback/{connector_id}",
                json={
                    "ChangeType": "add_external_contact",
                    "ExternalUserID": forged_external_userid,
                    "State": f"cycle15-forged-{uuid.uuid4()}",
                },
            )
            assert forged_callback.status_code == 400
            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            assert (
                await bypass_session.scalar(
                    text("SELECT count(*) FROM wecom_external_contacts WHERE external_userid=:external_userid"),
                    {"external_userid": forged_external_userid},
                )
                == 0
            )

            valid = await runtime_client.post(
                "/api/v1/integrations/wecom/contact-way",
                json={"benefit_id": str(benefit_id), "scan_token": scan_token(str(tenant_id))},
            )
            assert valid.status_code == 200, valid.text
            assert valid.json()["qr_code"].startswith("data:image/svg+xml")
            contact_way_created = True

            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            assert await _owned_benefit_fact_sentinel(bypass_session, tenant_id=tenant_id, benefit_id=benefit_id) == (
                1,
                0,
                0,
                1,
                0,
            )
            assert (
                await bypass_session.scalar(
                    text(
                        "SELECT count(*) FROM intent_events "
                        "WHERE tenant_id=:tenant_id AND event_type='wecom_click' AND public_id=:public_id"
                    ),
                    {"tenant_id": tenant_id, "public_id": public_id},
                )
                == 1
            )

            cross_tenant = await runtime_client.post(
                "/api/v1/integrations/wecom/contact-way",
                json={"benefit_id": str(benefit_id), "scan_token": scan_token(str(control_tenant_id))},
            )
            assert cross_tenant.status_code == 404

            forged_token = scan_token(str(tenant_id))
            parts = forged_token.split(".")
            parts[2] = f"{'a' if parts[2][0] != 'a' else 'b'}{parts[2][1:]}"
            forged = await runtime_client.post(
                "/api/v1/integrations/wecom/contact-way",
                json={"benefit_id": str(benefit_id), "scan_token": ".".join(parts)},
            )
            assert forged.status_code == 401
        finally:
            await bypass_session.rollback()
            await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            deleted_external = await bypass_session.execute(
                text(
                    "DELETE FROM wecom_external_contacts "
                    "WHERE tenant_id=:tenant_id AND connector_id=:connector_id AND benefit_id=:benefit_id"
                ),
                {"tenant_id": tenant_id, "connector_id": connector_id, "benefit_id": benefit_id},
            )
            assert deleted_external.rowcount == 0
            deleted_intents = await bypass_session.execute(
                text("DELETE FROM intent_events WHERE tenant_id=:tenant_id AND public_id=:public_id"),
                {"tenant_id": tenant_id, "public_id": public_id},
            )
            assert deleted_intents.rowcount == int(contact_way_created)
            deleted_contact_ways = await bypass_session.execute(
                text(
                    "DELETE FROM wecom_contact_ways "
                    "WHERE tenant_id=:tenant_id AND connector_id=:connector_id AND benefit_id=:benefit_id"
                ),
                {"tenant_id": tenant_id, "connector_id": connector_id, "benefit_id": benefit_id},
            )
            assert deleted_contact_ways.rowcount == int(contact_way_created)
            deleted_benefit = await bypass_session.execute(
                text("DELETE FROM benefits WHERE tenant_id=:tenant_id AND id=:benefit_id"),
                {"tenant_id": tenant_id, "benefit_id": benefit_id},
            )
            assert deleted_benefit.rowcount == 1
            deleted_connector = await bypass_session.execute(
                text("DELETE FROM connectors WHERE tenant_id=:tenant_id AND id=:connector_id"),
                {"tenant_id": tenant_id, "connector_id": connector_id},
            )
            assert deleted_connector.rowcount == 1
            assert await _owned_benefit_fact_sentinel(bypass_session, tenant_id=tenant_id, benefit_id=benefit_id) == (
                0,
                0,
                0,
                0,
                0,
            )
            for owned_tenant_id in (tenant_id, control_tenant_id):
                deleted_tenant = await bypass_session.execute(
                    text("DELETE FROM tenants WHERE id=:tenant_id"), {"tenant_id": owned_tenant_id}
                )
                assert deleted_tenant.rowcount == 1
            assert await _shared_fact_counts(bypass_session) == shared_before
            await bypass_session.commit()


class TestIntentEventRLSMatrix:
    async def test_tenant_no_context_and_explicit_bypass_crud(self, bypass_session, migrated_pg_url: str):
        from tests.test_acceptance.conftest import seed_baseline

        runtime_dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://").replace(
            "yimatong:yimatong@", "yimatong_app:yimatong_app@"
        )
        asyncpg_conn = await asyncpg.connect(runtime_dsn)
        runtime_transaction = asyncpg_conn.transaction()
        await runtime_transaction.start()

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

        await runtime_transaction.rollback()
        await asyncpg_conn.close()

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
        deleted_seed_events = await bypass_session.execute(
            text("DELETE FROM intent_events WHERE id IN (:event_a, :event_b)"),
            {"event_a": event_a, "event_b": event_b},
        )
        assert deleted_seed_events.rowcount == 2
        assert (
            await bypass_session.scalar(
                text("SELECT count(*) FROM intent_events WHERE id IN (:event_a, :event_b)"),
                {"event_a": event_a, "event_b": event_b},
            )
            == 0
        )
        await bypass_session.commit()
