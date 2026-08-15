"""品牌方正式上线门禁 API。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant, get_current_tenant_type
from app.models.launch import LaunchRelease
from app.schemas.common import PaginatedResponse
from app.schemas.launch import (
    LaunchActionRequest,
    LaunchReleaseActionResponse,
    LaunchReleaseCreateRequest,
    LaunchSuspendRequest,
)
from app.services.launch import (
    confirm_launch_release,
    create_launch_release,
    get_launch_release,
    launch_confirmed_release,
    resume_launch_release,
    serialize_launch_release,
    suspend_launch_release,
)
from app.utils.auth_rbac import require_durable_session, require_permission, require_role

router = APIRouter(prefix="/api/v1/launch-releases", tags=["launch-releases"])


def _ensure_brand(tenant_type: str) -> None:
    if tenant_type != "brand":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有品牌方可以确认或执行正式上线")


@router.post("", response_model=LaunchReleaseActionResponse, status_code=status.HTTP_201_CREATED)
async def create_launch_release_endpoint(
    body: LaunchReleaseCreateRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    tenant_type: str = Depends(get_current_tenant_type),
    _role: str = Depends(require_role("admin", "operator")),
    _session: None = Depends(require_durable_session),
    _permission: None = Depends(require_permission("page:publish")),
):
    _ensure_brand(tenant_type)
    release = await create_launch_release(
        db,
        tenant_id,
        account_id,
        page_version_id=body.page_version_id,
        campaign_id=body.campaign_id,
        code_batch_id=body.code_batch_id,
        idempotency_key=body.idempotency_key,
    )
    return serialize_launch_release(release)


@router.get("", response_model=PaginatedResponse)
async def list_launch_releases_endpoint(
    release_status: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    tenant_type: str = Depends(get_current_tenant_type),
    _role: str = Depends(require_role("admin", "operator")),
):
    _ensure_brand(tenant_type)
    filters = [LaunchRelease.tenant_id == tenant_id]
    if release_status:
        filters.append(LaunchRelease.status == release_status)
    total = int(await db.scalar(select(func.count()).select_from(LaunchRelease).where(*filters)) or 0)
    releases = list(
        (
            await db.scalars(
                select(LaunchRelease)
                .where(*filters)
                .order_by(LaunchRelease.created_at.desc(), LaunchRelease.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return PaginatedResponse(
        items=[serialize_launch_release(release) for release in releases],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{release_id}", response_model=LaunchReleaseActionResponse)
async def get_launch_release_endpoint(
    release_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    tenant_type: str = Depends(get_current_tenant_type),
    _role: str = Depends(require_role("admin", "operator")),
):
    _ensure_brand(tenant_type)
    release = await get_launch_release(db, tenant_id, release_id)
    if not release:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="上线版本不存在")
    return serialize_launch_release(release)


@router.post("/{release_id}/confirm", response_model=LaunchReleaseActionResponse)
async def confirm_launch_release_endpoint(
    release_id: uuid.UUID,
    body: LaunchActionRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    tenant_type: str = Depends(get_current_tenant_type),
    _role: str = Depends(require_role("admin")),
    _session: None = Depends(require_durable_session),
    _permission: None = Depends(require_permission("page:publish")),
):
    _ensure_brand(tenant_type)
    release = await get_launch_release(db, tenant_id, release_id)
    if not release:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="上线版本不存在")
    return serialize_launch_release(await confirm_launch_release(db, release, account_id, body.idempotency_key))


@router.post("/{release_id}/confirm-and-launch", response_model=LaunchReleaseActionResponse)
async def confirm_and_launch_endpoint(
    release_id: uuid.UUID,
    body: LaunchActionRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    tenant_type: str = Depends(get_current_tenant_type),
    _role: str = Depends(require_role("admin")),
    _session: None = Depends(require_durable_session),
    _permission: None = Depends(require_permission("page:publish")),
):
    _ensure_brand(tenant_type)
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="请先确认版本，再执行上线")


@router.post("/{release_id}/launch", response_model=LaunchReleaseActionResponse)
async def launch_release_endpoint(
    release_id: uuid.UUID,
    body: LaunchActionRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    tenant_type: str = Depends(get_current_tenant_type),
    _role: str = Depends(require_role("admin")),
    _session: None = Depends(require_durable_session),
    _permission: None = Depends(require_permission("page:publish")),
):
    _ensure_brand(tenant_type)
    release = await get_launch_release(db, tenant_id, release_id)
    if not release:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="上线版本不存在")
    return serialize_launch_release(await launch_confirmed_release(db, release, account_id, body.idempotency_key))


@router.post("/{release_id}/suspend", response_model=LaunchReleaseActionResponse)
async def suspend_launch_release_endpoint(
    release_id: uuid.UUID,
    body: LaunchSuspendRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    tenant_type: str = Depends(get_current_tenant_type),
    _role: str = Depends(require_role("admin")),
    _session: None = Depends(require_durable_session),
    _permission: None = Depends(require_permission("page:publish")),
):
    _ensure_brand(tenant_type)
    release = await get_launch_release(db, tenant_id, release_id)
    if not release:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="上线版本不存在")
    return serialize_launch_release(
        await suspend_launch_release(db, release, account_id, body.reason, body.idempotency_key)
    )


@router.post("/{release_id}/resume", response_model=LaunchReleaseActionResponse)
async def resume_launch_release_endpoint(
    release_id: uuid.UUID,
    body: LaunchActionRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    tenant_type: str = Depends(get_current_tenant_type),
    _role: str = Depends(require_role("admin")),
    _session: None = Depends(require_durable_session),
    _permission: None = Depends(require_permission("page:publish")),
):
    _ensure_brand(tenant_type)
    release = await get_launch_release(db, tenant_id, release_id)
    if not release:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="上线版本不存在")
    return serialize_launch_release(await resume_launch_release(db, release, account_id, body.idempotency_key))
