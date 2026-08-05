"""代运营协作上线 API：可准备、可请求确认，默认不可发布。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id
from app.schemas.launch import LaunchActionRequest, LaunchReleaseActionResponse, LaunchReleaseCreateRequest
from app.services.agency_auth import verify_authorization
from app.services.audit import write_audit_log
from app.services.launch import (
    create_launch_release,
    get_launch_release,
    launch_confirmed_release,
    refresh_launch_release,
    serialize_launch_release,
)
from app.utils.auth_rbac import require_role, require_tenant_type

router = APIRouter(
    prefix="/api/v1/ops/launch-releases",
    tags=["ops-launch-releases"],
    dependencies=[Depends(require_tenant_type("agency"))],
)


def _agency_context(request: Request) -> tuple[uuid.UUID, uuid.UUID]:
    original = getattr(request.state, "original_tenant_id", None)
    acting = getattr(request.state, "acting_tenant_id", None)
    if not original or not acting:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="请先切换到要协作的品牌客户")
    return uuid.UUID(original), uuid.UUID(acting)


async def _require_scope(db: AsyncSession, request: Request, required_scope: str) -> tuple[uuid.UUID, uuid.UUID]:
    agency_tenant_id, client_tenant_id = _agency_context(request)
    authorization = await verify_authorization(db, agency_tenant_id, client_tenant_id, required_scope)
    if not authorization:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前授权范围不包含该上线操作")
    return agency_tenant_id, client_tenant_id


@router.post("", response_model=LaunchReleaseActionResponse, status_code=status.HTTP_201_CREATED)
async def create_ops_launch_release(
    body: LaunchReleaseCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
):
    _agency_tenant_id, client_tenant_id = await _require_scope(db, request, "pages")
    release = await create_launch_release(
        db,
        client_tenant_id,
        account_id,
        page_version_id=body.page_version_id,
        campaign_id=body.campaign_id,
        code_batch_id=body.code_batch_id,
    )
    return serialize_launch_release(release)


@router.get("/{release_id}", response_model=LaunchReleaseActionResponse)
async def get_ops_launch_release(
    release_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _role: str = Depends(require_role("admin", "operator")),
):
    _agency_tenant_id, client_tenant_id = await _require_scope(db, request, "pages")
    release = await get_launch_release(db, client_tenant_id, release_id)
    if not release:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="上线版本不存在")
    await refresh_launch_release(db, release)
    return serialize_launch_release(release)


@router.post("/{release_id}/request-confirmation", response_model=LaunchReleaseActionResponse)
async def request_brand_confirmation(
    release_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
):
    agency_tenant_id, client_tenant_id = await _require_scope(db, request, "pages")
    release = await get_launch_release(db, client_tenant_id, release_id)
    if not release:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="上线版本不存在")
    await refresh_launch_release(db, release)
    if not release.readiness_snapshot.get("ready"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="上线准备度未通过，暂时不能提交品牌方确认")
    await write_audit_log(
        db,
        str(account_id),
        str(client_tenant_id),
        "launch_release_confirmation_requested",
        f"launch_release:{release.id}",
        {"agency_tenant_id": str(agency_tenant_id)},
    )
    return serialize_launch_release(release)


@router.post("/{release_id}/publish", response_model=LaunchReleaseActionResponse)
async def publish_ops_launch_release(
    release_id: uuid.UUID,
    body: LaunchActionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
):
    _agency_tenant_id, client_tenant_id = await _require_scope(db, request, "release:execute")
    release = await get_launch_release(db, client_tenant_id, release_id)
    if not release:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="上线版本不存在")
    return serialize_launch_release(await launch_confirmed_release(db, release, account_id, body.idempotency_key))
