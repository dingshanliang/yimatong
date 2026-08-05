"""既有码接管项目 API 与消费者域名网关。"""

from __future__ import annotations

import csv
import io
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.models.takeover import (
    TakeoverCutoverEvent,
    TakeoverDomainCheck,
    TakeoverImportError,
    TakeoverImportJob,
    TakeoverObservation,
    TakeoverProject,
    TakeoverRouteStatus,
    TakeoverRouteVersion,
)
from app.schemas.takeover import (
    ExternalExecutionRequest,
    ObservationRequest,
    RollbackRequest,
    TakeoverProjectCreate,
    TakeoverProjectUpdate,
    TakeoverRouteCreate,
)
from app.services.takeover import (
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
    queue_import,
    record_external_execution,
    record_observation,
    refresh_takeover_readiness,
    retry_failed_import,
    rollback_route,
    serialize_domain_check,
    serialize_import,
    serialize_project,
    serialize_route,
    update_project,
)
from app.utils.auth_rbac import require_permission

router = APIRouter(prefix="/api/v1/takeovers", tags=["takeovers"])
gateway_router = APIRouter(tags=["takeover-gateway"])
PUBLIC_ROUTE_STATUSES = (
    TakeoverRouteStatus.active,
    TakeoverRouteStatus.paused,
    TakeoverRouteStatus.rolled_back,
)


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
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await create_project(db, tenant_id, account_id, body.model_dump())
    return serialize_project(project)


@router.get("")
async def list_takeover_projects(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:prepare")),
):
    projects = list(
        (
            await db.scalars(
                select(TakeoverProject)
                .where(TakeoverProject.tenant_id == tenant_id)
                .order_by(TakeoverProject.created_at.desc())
            )
        ).all()
    )
    return {"items": [serialize_project(project) for project in projects], "total": len(projects)}


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
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    return serialize_project(await update_project(db, project, account_id, body.model_dump(exclude_unset=True)))


@router.post("/{project_id}/assess")
async def assess_takeover_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    return serialize_project(await refresh_takeover_readiness(db, project))


@router.post("/{project_id}/imports/dry-run", status_code=status.HTTP_201_CREATED)
async def dry_run_takeover_import(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    content = await file.read()
    job = await dry_run_import(db, project, account_id, file.filename or "takeover.csv", content)
    errors = list((await db.scalars(select(TakeoverImportError).where(TakeoverImportError.job_id == job.id))).all())
    return serialize_import(job, errors)


@router.get("/{project_id}/imports")
async def list_takeover_imports(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    jobs = list(
        (
            await db.scalars(
                select(TakeoverImportJob)
                .where(TakeoverImportJob.project_id == project.id, TakeoverImportJob.tenant_id == tenant_id)
                .order_by(TakeoverImportJob.created_at.desc())
            )
        ).all()
    )
    return {"items": [serialize_import(job, []) for job in jobs], "total": len(jobs)}


@router.post("/{project_id}/imports/{job_id}/submit")
async def submit_takeover_import(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
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
        writer.writerow([error.row_number, error.legacy_code or "", error.error_code, error.message, error.retryable])
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="takeover-errors-{job.id}.csv"'},
    )


@router.get("/{project_id}/aliases")
async def list_takeover_aliases(
    project_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
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
    aliases = list((await db.scalars(query.order_by(TakeoverAlias.created_at.desc()))).all())
    return {
        "items": [serialize_alias(await _attach_alias_target(db, alias)) for alias in aliases],
        "total": len(aliases),
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
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    return serialize_domain_check(await inspect_domain(db, project, account_id))


@router.get("/{project_id}/domains/checks")
async def list_takeover_domain_checks(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    checks = list(
        (
            await db.scalars(
                select(TakeoverDomainCheck)
                .where(TakeoverDomainCheck.project_id == project.id)
                .order_by(TakeoverDomainCheck.checked_at.desc())
            )
        ).all()
    )
    return {"items": [serialize_domain_check(check) for check in checks], "total": len(checks)}


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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    return serialize_route(await create_route(db, project, account_id, body.model_dump()))


@router.get("/{project_id}/routes")
async def list_takeover_routes(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:prepare")),
):
    project = await _require_project(db, tenant_id, project_id)
    routes = list(
        (
            await db.scalars(
                select(TakeoverRouteVersion)
                .where(TakeoverRouteVersion.project_id == project.id)
                .order_by(TakeoverRouteVersion.version.desc())
            )
        ).all()
    )
    return {"items": [serialize_route(route) for route in routes], "total": len(routes)}


@router.post("/{project_id}/routes/{route_id}/confirm")
async def confirm_takeover_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:approve")),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    return serialize_route(await confirm_route(db, project, route, account_id))


@router.post("/{project_id}/routes/{route_id}/cutover")
async def cutover_takeover_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    idempotency_key: str = Query(..., min_length=1, max_length=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:execute")),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    return serialize_route(await cutover_route(db, project, route, account_id, idempotency_key))


@router.post("/{project_id}/routes/{route_id}/external-execution")
async def report_takeover_external_execution(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    body: ExternalExecutionRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:execute")),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    event = await record_external_execution(db, project, route, account_id, body.model_dump())
    return {"id": event.id, "status": event.state, "message": "已记录外部执行，等待真实链接探测验证"}


@router.post("/{project_id}/routes/{route_id}/probe")
async def probe_takeover_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    body: ObservationRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:execute")),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    if route.status != "active":
        route = await cutover_route(db, project, route, account_id, f"probe-{route.id}")
    observation = await record_observation(db, project, route, account_id, body.model_dump())
    return {"route": serialize_route(route), "observation": _serialize_observation(observation)}


@router.post("/{project_id}/routes/{route_id}/observe")
async def observe_takeover_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    body: ObservationRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:execute")),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    return _serialize_observation(await record_observation(db, project, route, account_id, body.model_dump()))


@router.post("/{project_id}/routes/{route_id}/complete")
async def complete_takeover_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:execute")),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    return serialize_route(await complete_route(db, project, route, account_id))


@router.post("/{project_id}/routes/{route_id}/rollback")
async def rollback_takeover_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    body: RollbackRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_permission("takeover:rollback")),
):
    project = await _require_project(db, tenant_id, project_id)
    route = await get_route(db, project, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由版本不存在")
    return serialize_route(await rollback_route(db, project, route, account_id, body.reason, body.idempotency_key))


def _serialize_observation(observation: TakeoverObservation) -> dict:
    return {
        "id": observation.id,
        "checked_url": observation.checked_url,
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
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("takeover:audit")),
):
    project = await _require_project(db, tenant_id, project_id)
    events = list(
        (
            await db.scalars(
                select(TakeoverCutoverEvent)
                .where(TakeoverCutoverEvent.project_id == project.id)
                .order_by(TakeoverCutoverEvent.created_at.desc())
            )
        ).all()
    )
    observations = list(
        (
            await db.scalars(
                select(TakeoverObservation)
                .where(TakeoverObservation.project_id == project.id)
                .order_by(TakeoverObservation.created_at.desc())
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
    }


@gateway_router.get("/api/v1/takeover/gateway")
async def resolve_takeover_gateway(
    request: Request,
    project_id: uuid.UUID | None = Query(default=None),
    legacy_url: str | None = Query(default=None),
    response_mode: str = Query(default="json", pattern="^(json|redirect)$"),
    db: AsyncSession = Depends(get_db),
):
    from app.core.database import bootstrap_tenant_row

    project = None
    host = (request.headers.get("host") or "").split(":", 1)[0].lower().rstrip(".")
    if project_id:
        project = await bootstrap_tenant_row(
            db,
            select(TakeoverProject).where(TakeoverProject.id == project_id).limit(1),
        )
    else:
        project = await bootstrap_tenant_row(
            db,
            select(TakeoverProject)
            .join(TakeoverRouteVersion, TakeoverRouteVersion.project_id == TakeoverProject.id)
            .where(
                TakeoverRouteVersion.domain == host,
                TakeoverRouteVersion.status.in_(PUBLIC_ROUTE_STATUSES),
            )
            .order_by(TakeoverRouteVersion.created_at.desc())
            .limit(1),
        )
    if not project:
        raise HTTPException(status_code=404, detail="未找到已激活的接管域名")
    active_route = await db.scalar(
        select(TakeoverRouteVersion)
        .where(
            TakeoverRouteVersion.project_id == project.id,
            TakeoverRouteVersion.tenant_id == project.tenant_id,
            TakeoverRouteVersion.domain == host,
            TakeoverRouteVersion.status.in_(PUBLIC_ROUTE_STATUSES),
        )
        .order_by(TakeoverRouteVersion.version.desc(), TakeoverRouteVersion.created_at.desc())
    )
    if not active_route:
        raise HTTPException(status_code=404, detail="当前请求域名未绑定该接管项目")
    raw_url = legacy_url or str(request.url)
    result = await gateway_resolve(db, project, raw_url)
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
    from app.core.database import bootstrap_tenant_row

    host = (request.headers.get("host") or "").split(":", 1)[0].lower().rstrip(".")
    project = await bootstrap_tenant_row(
        db,
        select(TakeoverProject)
        .join(TakeoverRouteVersion, TakeoverRouteVersion.project_id == TakeoverProject.id)
        .where(
            TakeoverRouteVersion.domain == host,
            TakeoverRouteVersion.status.in_(PUBLIC_ROUTE_STATUSES),
        )
        .order_by(TakeoverRouteVersion.created_at.desc())
        .limit(1),
    )
    if not project:
        raise HTTPException(status_code=404, detail="未找到已激活的接管域名")
    query = f"?{request.url.query}" if request.url.query else ""
    raw_url = f"https://{host}/{legacy_path.lstrip('/')}{query}"
    result = await gateway_resolve(db, project, raw_url)
    return RedirectResponse(url=result["redirect_to"], status_code=307)
