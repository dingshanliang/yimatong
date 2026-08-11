"""Focused regressions for takeover import worker entitlement handling."""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core import database
from app.models.takeover import (
    TakeoverImportJob,
    TakeoverImportStatus,
    TakeoverMode,
    TakeoverProject,
    TakeoverProjectStatus,
)
from app.models.tenant import Tenant
from app.services import takeover as takeover_service
from app.services.takeover import (
    TAKEOVER_IMPORT_PLAN_EXPIRED_ERROR,
    poll_pending_takeover_imports,
    process_import_job,
    queue_import,
    serialize_import,
)
from app.tasks.worker import worker_loop
from tests.conftest import TestSessionLocal


async def _seed_job(*, expired: bool, error_detail: str | None = None) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    project_id = uuid.uuid4()
    job_id = uuid.uuid4()
    async with TestSessionLocal() as db, db.begin():
        db.add(
            Tenant(
                id=tenant_id,
                name="接管 worker 套餐租户",
                slug=f"takeover-worker-{tenant_id.hex[:10]}",
                plan_expires_at=datetime.now(UTC) + (-timedelta(days=1) if expired else timedelta(days=1)),
            )
        )
        db.add(
            TakeoverProject(
                id=project_id,
                tenant_id=tenant_id,
                name="接管 worker 项目",
                source_system="legacy-test",
                mode=TakeoverMode.legacy_redirect,
                source_domain="legacy.example.com",
                sample_url="https://legacy.example.com/OLD-001",
                url_rule={"kind": "path_tail"},
                code_scope={},
                control_facts={},
                responsible_person="运营",
                rollback_contact="回退人",
                fallback_url="https://fallback.example.com",
                status=TakeoverProjectStatus.draft,
                created_by=account_id,
            )
        )
        db.add(
            TakeoverImportJob(
                id=job_id,
                tenant_id=tenant_id,
                project_id=project_id,
                file_name="legacy.csv",
                file_sha256=uuid.uuid4().hex,
                status=TakeoverImportStatus.failed if error_detail else TakeoverImportStatus.pending,
                source_rows=[],
                counts={"total": 0, "valid": 0, "failed": 0, "succeeded": 0},
                error_detail=error_detail,
                created_by=account_id,
            )
        )
    return tenant_id, project_id, job_id


@pytest.mark.anyio
async def test_expired_worker_records_recoverable_failure_and_retry_after_renewal(monkeypatch) -> None:
    tenant_id, project_id, job_id = await _seed_job(expired=True)
    monkeypatch.setattr(database, "async_session_factory", TestSessionLocal)
    monkeypatch.setattr(database, "control_session_factory", TestSessionLocal)

    await process_import_job(job_id)

    async with TestSessionLocal() as db:
        job = await db.get(TakeoverImportJob, job_id)
        project = await db.get(TakeoverProject, project_id)
        assert job is not None
        assert job.status == TakeoverImportStatus.failed
        assert job.error_detail == TAKEOVER_IMPORT_PLAN_EXPIRED_ERROR
        assert serialize_import(job, [])["error_detail"] == TAKEOVER_IMPORT_PLAN_EXPIRED_ERROR
        assert job.counts == {"total": 0, "valid": 0, "failed": 0, "succeeded": 0}
        assert project is not None
        assert project.status == TakeoverProjectStatus.draft

    async with TestSessionLocal() as db, db.begin():
        tenant = await db.get(Tenant, tenant_id)
        assert tenant is not None
        tenant.plan_expires_at = datetime.now(UTC) + timedelta(days=1)

    async with TestSessionLocal() as db, db.begin():
        project = await db.get(TakeoverProject, project_id)
        job = await db.get(TakeoverImportJob, job_id)
        assert project is not None and job is not None
        retried = await queue_import(db, project, job, job.created_by)
        assert retried.status == TakeoverImportStatus.completed
        assert retried.error_detail is None


@pytest.mark.anyio
async def test_queue_import_does_not_reopen_unrelated_failed_job() -> None:
    _, project_id, job_id = await _seed_job(expired=False, error_detail="后台导入任务执行失败")

    async with TestSessionLocal() as db:
        project = await db.scalar(select(TakeoverProject).where(TakeoverProject.id == project_id))
        job = await db.scalar(select(TakeoverImportJob).where(TakeoverImportJob.id == job_id))
        assert project is not None and job is not None
        with pytest.raises(HTTPException) as exc_info:
            await queue_import(db, project, job, job.created_by)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "当前导入任务不能提交"


def test_takeover_import_delivery_uses_durable_database_polling_only() -> None:
    queue_source = inspect.getsource(queue_import)
    worker_source = inspect.getsource(worker_loop)

    assert "redis" not in queue_source
    assert "lpush" not in queue_source
    assert "TAKEOVER_IMPORT_QUEUE_KEY" not in worker_source
    assert "poll_pending_takeover_imports" in worker_source


@pytest.mark.anyio
async def test_durable_poll_rediscovers_stale_processing_claim(monkeypatch) -> None:
    _, _, job_id = await _seed_job(expired=False)
    async with TestSessionLocal() as db, db.begin():
        job = await db.get(TakeoverImportJob, job_id)
        assert job is not None
        job.status = TakeoverImportStatus.processing
        job.attempt_count = 1
        job.claim_token = uuid.uuid4()
        job.claimed_at = datetime.now(UTC) - timedelta(minutes=6)

    observed: list[uuid.UUID] = []

    async def fake_process(candidate_id: uuid.UUID) -> None:
        observed.append(candidate_id)

    monkeypatch.setattr(database, "control_session_factory", TestSessionLocal)
    monkeypatch.setattr(takeover_service, "process_import_job", fake_process)

    assert await poll_pending_takeover_imports() == 1
    assert observed == [job_id]
