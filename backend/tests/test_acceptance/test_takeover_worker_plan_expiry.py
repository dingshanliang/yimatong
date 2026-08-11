"""Real PostgreSQL ordering gates for takeover import workers and plan expiry."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import database
from app.models.code import CodeItem
from app.models.takeover import (
    TakeoverAlias,
    TakeoverImportJob,
    TakeoverImportStatus,
    TakeoverMode,
    TakeoverProject,
    TakeoverProjectStatus,
)
from app.models.tenant import Account, Tenant, TenantPlan
from app.services import takeover as takeover_service
from tests.test_acceptance.conftest import seed_baseline

pytestmark = [pytest.mark.acceptance, pytest.mark.anyio]


@dataclass(frozen=True, slots=True)
class _PlanState:
    plan: TenantPlan
    plan_expires_at: datetime | None


def _factories(database_url: str):
    runtime_url = database_url.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    owner_engine = create_async_engine(database_url)
    runtime_engine = create_async_engine(runtime_url)
    owner_factory = async_sessionmaker(owner_engine, class_=AsyncSession, expire_on_commit=False)
    runtime_factory = async_sessionmaker(runtime_engine, class_=AsyncSession, expire_on_commit=False)
    return owner_engine, runtime_engine, owner_factory, runtime_factory


async def _seed_pending_job(
    database_url: str,
    owner_factory,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, _PlanState]:
    summary = await seed_baseline(database_url)
    tenant_id = uuid.UUID(summary["baseline_tenant"]["id"])
    project_id = uuid.uuid4()
    job_id = uuid.uuid4()
    legacy_code = f"OLD-{job_id.hex[:12]}".upper()

    async with owner_factory() as db, db.begin():
        tenant = await db.get(Tenant, tenant_id)
        assert tenant is not None
        prior_plan = _PlanState(plan=tenant.plan, plan_expires_at=tenant.plan_expires_at)
        await db.execute(
            text("UPDATE tenants SET plan_expires_at=:future WHERE id=:tenant_id"),
            {"future": datetime.now(UTC) + timedelta(days=1), "tenant_id": tenant_id},
        )
        account_id = await db.scalar(
            select(Account.id).where(
                Account.tenant_id == tenant_id,
                Account.email == summary["baseline_tenant"]["admin_email"],
            )
        )
        code_item_id = await db.scalar(
            select(CodeItem.id).where(
                CodeItem.tenant_id == tenant_id,
                CodeItem.public_id == summary["first_public_id"],
            )
        )
        assert account_id is not None and code_item_id is not None
        db.add(
            TakeoverProject(
                id=project_id,
                tenant_id=tenant_id,
                name="接管 worker 到期竞态",
                source_system="legacy-acceptance",
                mode=TakeoverMode.legacy_redirect,
                source_domain=f"legacy-{project_id.hex}.example.com",
                sample_url=f"https://legacy-{project_id.hex}.example.com/{legacy_code}",
                url_rule={"kind": "path_tail"},
                code_scope={},
                control_facts={},
                responsible_person="运营负责人",
                rollback_contact="回退负责人",
                fallback_url=f"https://fallback-{project_id.hex}.example.com",
                status=TakeoverProjectStatus.needs_fix,
                created_by=account_id,
            )
        )
        await db.flush()
        db.add(
            TakeoverImportJob(
                id=job_id,
                tenant_id=tenant_id,
                project_id=project_id,
                file_name="legacy.csv",
                file_sha256=uuid.uuid4().hex,
                status=TakeoverImportStatus.pending,
                source_rows=[
                    {
                        "_normalized_code": legacy_code,
                        "_internal_code_id": str(code_item_id),
                        "_row_number": 2,
                        "_alias_type": "unique",
                        "_capabilities": {},
                        "legacy_code": legacy_code,
                    }
                ],
                counts={"total": 1, "valid": 1, "failed": 0, "succeeded": 0},
                created_by=account_id,
            )
        )
    return tenant_id, project_id, job_id, prior_plan


async def _restore_plan(owner_factory, tenant_id: uuid.UUID, prior_plan: _PlanState) -> None:
    async with owner_factory() as db, db.begin():
        tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        assert tenant is not None
        tenant.plan = prior_plan.plan
        tenant.plan_expires_at = prior_plan.plan_expires_at


async def _assert_job_facts(
    owner_factory,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    expected_status: TakeoverImportStatus,
    expected_project_status: TakeoverProjectStatus,
    expected_aliases: int,
) -> TakeoverImportJob:
    async with owner_factory() as db:
        job = await db.get(TakeoverImportJob, job_id)
        project = await db.get(TakeoverProject, project_id)
        aliases = int(
            await db.scalar(
                select(func.count(TakeoverAlias.id)).where(
                    TakeoverAlias.tenant_id == tenant_id,
                    TakeoverAlias.project_id == project_id,
                )
            )
            or 0
        )
        assert job is not None and project is not None
        assert job.status == expected_status
        assert project.status == expected_project_status
        assert aliases == expected_aliases
        return job


async def test_expiry_first_blocks_worker_without_partial_writes_and_can_retry(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_engine, runtime_engine, owner_factory, runtime_factory = _factories(migrated_pg_url)
    tenant_id, project_id, job_id, prior_plan = await _seed_pending_job(migrated_pg_url, owner_factory)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    expiry_locked = asyncio.Event()
    allow_expiry_commit = asyncio.Event()

    async def expire_plan_first() -> None:
        async with owner_factory() as db, db.begin():
            await db.execute(
                text("UPDATE tenants SET plan_expires_at=:expired WHERE id=:tenant_id"),
                {"expired": datetime.now(UTC) - timedelta(seconds=1), "tenant_id": tenant_id},
            )
            expiry_locked.set()
            await allow_expiry_commit.wait()

    try:
        expiry_task = asyncio.create_task(expire_plan_first())
        await asyncio.wait_for(expiry_locked.wait(), timeout=10)
        worker_task = asyncio.create_task(takeover_service.process_import_job(job_id))
        await asyncio.sleep(0.15)
        assert not worker_task.done(), "worker must wait for the platform expiry transaction"
        allow_expiry_commit.set()
        await asyncio.wait_for(expiry_task, timeout=10)
        await asyncio.wait_for(worker_task, timeout=10)

        failed_job = await _assert_job_facts(
            owner_factory,
            tenant_id=tenant_id,
            project_id=project_id,
            job_id=job_id,
            expected_status=TakeoverImportStatus.failed,
            expected_project_status=TakeoverProjectStatus.needs_fix,
            expected_aliases=0,
        )
        assert failed_job.last_error_code == "tenant_plan_expired"
        assert failed_job.error_detail == "background import failed: tenant_plan_expired"
        assert failed_job.counts == {"total": 1, "valid": 1, "failed": 0, "succeeded": 0}

        async with owner_factory() as db, db.begin():
            await db.execute(
                text("UPDATE tenants SET plan_expires_at=:future WHERE id=:tenant_id"),
                {"future": datetime.now(UTC) + timedelta(days=1), "tenant_id": tenant_id},
            )

        class FakeRedis:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return None

            async def lpush(self, key: str, value: str) -> None:
                assert key == takeover_service.TAKEOVER_IMPORT_QUEUE_KEY
                assert value == str(job_id)

        import redis.asyncio as aioredis

        monkeypatch.setattr(aioredis, "from_url", lambda _url: FakeRedis())
        async with runtime_factory() as db, db.begin():
            await database.lock_active_tenant_context(db, tenant_id)
            project = await db.get(TakeoverProject, project_id)
            job = await db.get(TakeoverImportJob, job_id)
            assert project is not None and job is not None
            queued = await takeover_service.queue_import(db, project, job, job.created_by)
            assert queued.status == TakeoverImportStatus.pending
            assert queued.error_detail is None

        await takeover_service.process_import_job(job_id)
        completed_job = await _assert_job_facts(
            owner_factory,
            tenant_id=tenant_id,
            project_id=project_id,
            job_id=job_id,
            expected_status=TakeoverImportStatus.completed,
            expected_project_status=TakeoverProjectStatus.draft,
            expected_aliases=1,
        )
        assert completed_job.counts["succeeded"] == 1
        assert completed_job.error_detail is None
    finally:
        allow_expiry_commit.set()
        try:
            await _restore_plan(owner_factory, tenant_id, prior_plan)
        finally:
            await runtime_engine.dispose()
            await owner_engine.dispose()


async def test_worker_first_holds_plan_lock_through_full_business_commit(
    migrated_pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_engine, runtime_engine, owner_factory, runtime_factory = _factories(migrated_pg_url)
    tenant_id, project_id, job_id, prior_plan = await _seed_pending_job(migrated_pg_url, owner_factory)
    monkeypatch.setattr(database, "async_session_factory", runtime_factory)
    monkeypatch.setattr(database, "control_session_factory", owner_factory)
    business_flushed = asyncio.Event()
    allow_worker_commit = asyncio.Event()
    original_submit_import = takeover_service.submit_import

    async def paused_submit_import(*args, **kwargs):
        job = await original_submit_import(*args, **kwargs)
        business_flushed.set()
        await allow_worker_commit.wait()
        return job

    monkeypatch.setattr(takeover_service, "submit_import", paused_submit_import)

    async def expire_plan_after_worker() -> None:
        async with owner_factory() as db, db.begin():
            await db.execute(
                text("UPDATE tenants SET plan_expires_at=:expired WHERE id=:tenant_id"),
                {"expired": datetime.now(UTC) - timedelta(seconds=1), "tenant_id": tenant_id},
            )

    try:
        worker_task = asyncio.create_task(takeover_service.process_import_job(job_id))
        await asyncio.wait_for(business_flushed.wait(), timeout=10)
        expiry_task = asyncio.create_task(expire_plan_after_worker())
        await asyncio.sleep(0.15)
        assert not expiry_task.done(), "platform expiry must wait through alias, project, and job commit"
        allow_worker_commit.set()
        await asyncio.wait_for(worker_task, timeout=10)
        await asyncio.wait_for(expiry_task, timeout=10)

        completed_job = await _assert_job_facts(
            owner_factory,
            tenant_id=tenant_id,
            project_id=project_id,
            job_id=job_id,
            expected_status=TakeoverImportStatus.completed,
            expected_project_status=TakeoverProjectStatus.draft,
            expected_aliases=1,
        )
        assert completed_job.counts["succeeded"] == 1
        async with owner_factory() as db:
            expires_at = await db.scalar(
                text("SELECT plan_expires_at FROM tenants WHERE id=:tenant_id"),
                {"tenant_id": tenant_id},
            )
            assert expires_at is not None and expires_at < datetime.now(UTC)
    finally:
        allow_worker_commit.set()
        try:
            await _restore_plan(owner_factory, tenant_id, prior_plan)
        finally:
            await runtime_engine.dispose()
            await owner_engine.dispose()
