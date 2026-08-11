"""既有码接管项目 API 与消费者域名网关。"""

from __future__ import annotations

import csv
import io
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.models.takeover import (
    TakeoverCutoverEvent,
    TakeoverDomainCheck,
    TakeoverDomainClaim,
    TakeoverImportError,
    TakeoverImportJob,
    TakeoverObservation,
    TakeoverProject,
    TakeoverRouteVersion,
)
from app.schemas.takeover import (
    ExternalExecutionRequest,
    ObservationRequest,
    RollbackRequest,
    TakeoverProjectCreate,
    TakeoverProjectUpdate,
    TakeoverRouteCreate,
    validate_takeover_domain_name,
)
from app.services.audit import write_audit_log
from app.services.code_export import spreadsheet_safe
from app.services.takeover import (
    MAX_IMPORT_BYTES,
    complete_route,
    confirm_project,
    confirm_route,
    create_project,
    create_route,
    cutover_route,
    dry_run_import,
    gateway_resolve,
    get_project,
    get_route,
    inspect_domain,
    preview_alias,
    probe_route_observation,
    queue_import,
    record_external_execution,
    refresh_takeover_readiness,
    retry_failed_import,
    rollback_route,
    serialize_domain_check,
    serialize_import,
    serialize_project,
    serialize_route,
    update_project,
    verify_rollback_route,
)
from app.services.takeover_admission import enforce_takeover_probe_rate_limit
from app.utils.auth_rbac import require_permission

router = APIRouter(prefix="/api/v1/takeovers", tags=["takeovers"])
gateway_router = APIRouter(tags=["takeover-gateway"])


async def read_takeover_csv_upload(file: UploadFile) -> bytes:
    """Read one bounded CSV upload without trusting a caller-controlled MIME alone."""
    filename = (file.filename or "").lower()
    allowed_content_types = {"text/csv", "application/csv", "application/vnd.ms-excel"}
    if not filename.endswith(".csv") or file.content_type not in allowed_content_types:
        raise HTTPException(status_code=415, detail="Unsupported import file type")
    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Import file is too large")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Import file is empty")
    return content


def _auth_session_id(request: Request) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(request.state.session_id))
    except (AttributeError, TypeError, ValueError):
        return None


async def _require_project(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
) -> TakeoverProject:
    project = await get_project(db, tenant_id, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="既有码接管项目不存在")
    return project


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_takeover_project(
    body: TakeoverProjectCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await create_project(db, tenant_id, account_id, body.model_dump())
    return serialize_project(project)


@router.get("")
async def list_takeover_projects(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project_filter = TakeoverProject.tenant_id == tenant_id
    total = int(await db.scalar(select(func.count()).select_from(TakeoverProject).where(project_filter)) or 0)
    projects = list(
        (
            await db.scalars(
                select(TakeoverProject)
                .where(project_filter)
                .order_by(TakeoverProject.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return {
        "items": [serialize_project(project) for project in projects],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{project_id}")
async def get_takeover_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:prepare")),
):
    return serialize_project(await _require_project(db, tenant_id, project_id))


@router.patch("/{project_id}")
async def update_takeover_project(
    project_id: uuid.UUID,
    body: TakeoverProjectUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    return serialize_project(await update_project(db, project, account_id, body.model_dump(exclude_unset=True)))


@router.post("/{project_id}/assess")
async def assess_takeover_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    return serialize_project(await refresh_takeover_readiness(db, project))


@router.post("/{project_id}/imports/dry-run", status_code=status.HTTP_201_CREATED)
async def dry_run_takeover_import(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    content = await read_takeover_csv_upload(file)
    job = await dry_run_import(db, project, account_id, file.filename or "takeover.csv", content)
    errors = list((await db.scalars(select(TakeoverImportError).where(TakeoverImportError.job_id == job.id))).all())
    return serialize_import(job, errors)


@router.get("/{project_id}/imports")
async def list_takeover_imports(
    project_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    job_filter = (TakeoverImportJob.project_id == project.id, TakeoverImportJob.tenant_id == tenant_id)
    total = int(await db.scalar(select(func.count()).select_from(TakeoverImportJob).where(*job_filter)) or 0)
    jobs = list(
        (
            await db.scalars(
                select(TakeoverImportJob)
                .options(
                    load_only(
                        TakeoverImportJob.id,
                        TakeoverImportJob.project_id,
                        TakeoverImportJob.status,
                        TakeoverImportJob.file_name,
                        TakeoverImportJob.file_sha256,
                        TakeoverImportJob.counts,
                        TakeoverImportJob.error_detail,
                        TakeoverImportJob.created_at,
                        TakeoverImportJob.completed_at,
                    )
                )
                .where(*job_filter)
                .order_by(TakeoverImportJob.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return {
        "items": [serialize_import(job, []) for job in jobs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/{project_id}/imports/{job_id}/submit")
async def submit_takeover_import(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    job = await db.scalar(
        select(TakeoverImportJob).where(TakeoverImportJob.id == job_id, TakeoverImportJob.project_id == project.id)
    )
    if not job:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    submitted_job = await queue_import(db, project, job, account_id)
    import_errors = list(
        (await db.scalars(select(TakeoverImportError).where(TakeoverImportError.job_id == job.id))).all()
    )
    return serialize_import(submitted_job, import_errors)


@router.post("/{project_id}/imports/{job_id}/retry-failed", status_code=status.HTTP_201_CREATED)
async def retry_takeover_import(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    job = await db.scalar(
        select(TakeoverImportJob).where(TakeoverImportJob.id == job_id, TakeoverImportJob.project_id == project.id)
    )
    if not job:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    retry_job = await retry_failed_import(db, project, job, account_id)
    return serialize_import(retry_job, [])


@router.get("/{project_id}/imports/{job_id}/errors.csv")
async def download_takeover_import_errors(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    job = await db.scalar(
        select(TakeoverImportJob).where(TakeoverImportJob.id == job_id, TakeoverImportJob.project_id == project.id)
    )
    if not job:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    from app.models.takeover import TakeoverImportError

    errors = list(
        (
            await db.scalars(
                select(TakeoverImportError)
                .where(TakeoverImportError.job_id == job.id)
                .order_by(TakeoverImportError.row_number)
            )
        ).all()
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["row_number", "legacy_code", "error_code", "message", "retryable"])
    for error in errors:
        writer.writerow(
            [
                error.row_number,
                spreadsheet_safe(error.legacy_code),
                spreadsheet_safe(error.error_code),
                spreadsheet_safe(error.message),
                error.retryable,
            ]
        )
    await write_audit_log(
        db,
        str(account_id),
        str(tenant_id),
        "takeover_import_errors_exported",
        f"takeover_import:{job.id}",
        {"project_id": str(project.id), "row_count": len(errors)},
    )
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="takeover-errors-{job.id}.csv"'},
    )


@router.get("/{project_id}/aliases")
async def list_takeover_aliases(
    project_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:prepare")),
):
    from app.models.takeover import TakeoverAlias
    from app.services.takeover import _attach_alias_target, serialize_alias

    project = await _require_project(db, tenant_id, project_id)
    query = select(TakeoverAlias).where(TakeoverAlias.project_id == project.id, TakeoverAlias.tenant_id == tenant_id)
    if status_filter:
        query = query.where(TakeoverAlias.status == status_filter)
    total = int(await db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    aliases = list(
        (
            await db.scalars(
                query.order_by(TakeoverAlias.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        ).all()
    )
    return {
        "items": [serialize_alias(await _attach_alias_target(db, alias)) for alias in aliases],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{project_id}/aliases/preview")
async def preview_takeover_alias(
    project_id: uuid.UUID,
    url: str = Query(..., min_length=8),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    return await preview_alias(db, project, url)


@router.post("/{project_id}/domains/check")
async def check_takeover_domain(
    project_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:prepare")),
    admission: None = Depends(enforce_takeover_probe_rate_limit),
):
    project = await _require_project(db, tenant_id, project_id)
    return serialize_domain_check(await inspect_domain(db, project, account_id, _auth_session_id(request)))


@router.get("/{project_id}/domains/checks")
async def list_takeover_domain_checks(
    project_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    check_filter = (
        TakeoverDomainCheck.project_id == project.id,
        TakeoverDomainCheck.tenant_id == tenant_id,
    )
    total = int(await db.scalar(select(func.count()).select_from(TakeoverDomainCheck).where(*check_filter)) or 0)
    checks = list(
        (
            await db.scalars(
                select(TakeoverDomainCheck)
                .where(*check_filter)
                .order_by(TakeoverDomainCheck.checked_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return {
        "items": [serialize_domain_check(check) for check in checks],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{project_id}/readiness")
async def get_takeover_readiness(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    project = await refresh_takeover_readiness(db, project)
    return project.readiness_snapshot


@router.post("/{project_id}/confirm")
async def confirm_takeover_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:approve")),
):
    project = await _require_project(db, tenant_id, project_id)
    return serialize_project(await confirm_project(db, project, account_id))


@router.post("/{project_id}/routes", status_code=status.HTTP_201_CREATED)
async def create_takeover_route(
    project_id: uuid.UUID,
    body: TakeoverRouteCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    return serialize_route(await create_route(db, project, account_id, body.model_dump()))


@router.get("/{project_id}/routes")
async def list_takeover_routes(
    project_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    route_filter = (
        TakeoverRouteVersion.project_id == project.id,
        TakeoverRouteVersion.tenant_id == tenant_id,
    )
    total = int(await db.scalar(select(func.count()).select_from(TakeoverRouteVersion).where(*route_filter)) or 0)
    routes = list(
        (
            await db.scalars(
                select(TakeoverRouteVersion)
                .where(*route_filter)
                .order_by(TakeoverRouteVersion.version.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return {
        "items": [serialize_route(route) for route in routes],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/{project_id}/routes/{route_id}/confirm")
async def confirm_takeover_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    request: Request,
    idempotency_key: str = Query(..., min_length=1, max_length=100),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:approve")),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    return serialize_route(
        await confirm_route(db, project, route, account_id, idempotency_key, _auth_session_id(request))
    )


@router.post("/{project_id}/routes/{route_id}/cutover")
async def cutover_takeover_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    request: Request,
    idempotency_key: str = Query(..., min_length=1, max_length=100),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:execute")),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    return serialize_route(
        await cutover_route(db, project, route, account_id, idempotency_key, _auth_session_id(request))
    )


@router.post("/{project_id}/routes/{route_id}/external-execution")
async def report_takeover_external_execution(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    request: Request,
    body: ExternalExecutionRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:execute")),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    event = await record_external_execution(
        db, project, route, account_id, body.model_dump(), _auth_session_id(request)
    )
    return {"id": event.id, "status": event.state, "message": "已记录外部执行，等待真实链接探测验证"}


@router.post("/{project_id}/routes/{route_id}/probe")
async def probe_takeover_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    request: Request,
    body: ObservationRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:execute")),
    admission: None = Depends(enforce_takeover_probe_rate_limit),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    observation = await probe_route_observation(
        db, project, route, account_id, str(body.checked_url), _auth_session_id(request)
    )
    return {"route": serialize_route(route), "observation": _serialize_observation(observation)}


@router.post("/{project_id}/routes/{route_id}/observe")
async def observe_takeover_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    request: Request,
    body: ObservationRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:execute")),
    admission: None = Depends(enforce_takeover_probe_rate_limit),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    return _serialize_observation(
        await probe_route_observation(db, project, route, account_id, str(body.checked_url), _auth_session_id(request))
    )


@router.post("/{project_id}/routes/{route_id}/complete")
async def complete_takeover_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    request: Request,
    idempotency_key: str = Query(..., min_length=1, max_length=100),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:execute")),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    return serialize_route(
        await complete_route(db, project, route, account_id, idempotency_key, _auth_session_id(request))
    )


@router.post("/{project_id}/routes/{route_id}/rollback")
async def rollback_takeover_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    request: Request,
    body: RollbackRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:rollback")),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    return serialize_route(
        await rollback_route(
            db,
            project,
            route,
            account_id,
            body.reason,
            body.idempotency_key,
            _auth_session_id(request),
        )
    )


@router.post("/{project_id}/routes/{route_id}/rollback/verify")
async def verify_takeover_route_rollback(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    request: Request,
    idempotency_key: str = Query(..., min_length=1, max_length=80),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:rollback")),
    admission: None = Depends(enforce_takeover_probe_rate_limit),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    verified_route, observation = await verify_rollback_route(
        db,
        project,
        route,
        account_id,
        idempotency_key,
        _auth_session_id(request),
    )
    return {"route": serialize_route(verified_route), "observation": _serialize_observation(observation)}


def _serialize_observation(observation: TakeoverObservation) -> dict:
    return {
        "id": observation.id,
        "transition_event_id": observation.transition_event_id,
        "checked_url": observation.checked_url,
        "observed_target_url": observation.observed_target_url,
        "evidence_source": observation.evidence_source,
        "evidence_purpose": observation.evidence_purpose,
        "evidence_digest": observation.evidence_digest,
        "status": observation.status,
        "success_rate": observation.success_rate,
        "error_rate": observation.error_rate,
        "latency_ms": observation.latency_ms,
        "h5_reach_rate": observation.h5_reach_rate,
        "target_match": observation.target_match,
        "metrics": observation.metrics,
        "recommendation": observation.recommendation,
        "created_at": observation.created_at,
    }


@router.get("/{project_id}/events")
async def list_takeover_events(
    project_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:audit")),
):
    project = await _require_project(db, tenant_id, project_id)
    event_filter = TakeoverCutoverEvent.project_id == project.id
    observation_filter = TakeoverObservation.project_id == project.id
    events_total = int(await db.scalar(select(func.count()).where(event_filter).select_from(TakeoverCutoverEvent)) or 0)
    observations_total = int(
        await db.scalar(select(func.count()).where(observation_filter).select_from(TakeoverObservation)) or 0
    )
    offset = (page - 1) * page_size
    events = list(
        (
            await db.scalars(
                select(TakeoverCutoverEvent)
                .where(event_filter)
                .order_by(TakeoverCutoverEvent.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
        ).all()
    )
    observations = list(
        (
            await db.scalars(
                select(TakeoverObservation)
                .where(observation_filter)
                .order_by(TakeoverObservation.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
        ).all()
    )
    return {
        "events": [
            {
                "id": event.id,
                "action": event.action,
                "state": event.state,
                "details": event.details,
                "created_at": event.created_at,
            }
            for event in events
        ],
        "observations": [_serialize_observation(observation) for observation in observations],
        "events_total": events_total,
        "observations_total": observations_total,
        "page": page,
        "page_size": page_size,
    }


async def _load_public_gateway_authority(db: AsyncSession, host: str) -> tuple[TakeoverProject, TakeoverRouteVersion]:
    from app.core.database import set_session_tenant_context

    try:
        canonical_host = validate_takeover_domain_name(host)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="未找到已激活的接管域名") from exc
    if not canonical_host:
        raise HTTPException(status_code=404, detail="未找到已激活的接管域名")
    if db.get_bind().dialect.name == "postgresql":
        try:
            authority = (
                (
                    await db.execute(
                        text("SELECT * FROM public.resolve_takeover_public_route(:domain)"),
                        {"domain": canonical_host},
                    )
                )
                .mappings()
                .one_or_none()
            )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) != "22023":
                raise
            raise HTTPException(status_code=404, detail="未找到已激活的接管域名") from exc
    else:
        claim = await db.get(TakeoverDomainClaim, canonical_host)
        authority = (
            {
                "tenant_id": claim.tenant_id,
                "project_id": claim.project_id,
                "route_version_id": claim.route_version_id,
            }
            if claim is not None
            else None
        )
    if authority is None:
        raise HTTPException(status_code=404, detail="未找到已激活的接管域名")
    await set_session_tenant_context(db, authority["tenant_id"])
    project = await db.scalar(
        select(TakeoverProject).where(
            TakeoverProject.tenant_id == authority["tenant_id"],
            TakeoverProject.id == authority["project_id"],
        )
    )
    active_route = await db.scalar(
        select(TakeoverRouteVersion).where(
            TakeoverRouteVersion.project_id == authority["project_id"],
            TakeoverRouteVersion.tenant_id == authority["tenant_id"],
            TakeoverRouteVersion.id == authority["route_version_id"],
            TakeoverRouteVersion.domain == canonical_host,
        )
    )
    if project is None or active_route is None:
        raise HTTPException(status_code=404, detail="当前请求域名未绑定该接管项目")
    return project, active_route


@gateway_router.get("/api/v1/takeover/gateway")
async def resolve_takeover_gateway(
    request: Request,
    legacy_url: str | None = Query(default=None),
    response_mode: str = Query(default="json", pattern="^(json|redirect)$"),
    db: AsyncSession = Depends(get_db),
):
    host = (request.headers.get("host") or "").split(":", 1)[0].lower().rstrip(".")
    project, route = await _load_public_gateway_authority(db, host)
    raw_url = legacy_url or str(request.url)
    result = await gateway_resolve(db, project, raw_url, route=route)
    if response_mode == "redirect":
        return RedirectResponse(url=result["redirect_to"], status_code=307)
    return result


@gateway_router.get("/api/v1/takeover/gateway/{legacy_path:path}")
async def redirect_takeover_gateway_path(
    request: Request,
    legacy_path: str,
    db: AsyncSession = Depends(get_db),
):
    """域名网关转发到的真实旧路径；浏览器请求直接得到 307。"""
    host = (request.headers.get("host") or "").split(":", 1)[0].lower().rstrip(".")
    project, route = await _load_public_gateway_authority(db, host)
    query = f"?{request.url.query}" if request.url.query else ""
    raw_url = f"https://{host}/{legacy_path.lstrip('/')}{query}"
    result = await gateway_resolve(db, project, raw_url, route=route)
    return RedirectResponse(url=result["redirect_to"], status_code=307)
