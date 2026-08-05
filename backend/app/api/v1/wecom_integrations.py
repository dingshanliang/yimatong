"""Enterprise WeChat integration APIs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import _session_uses_postgresql, get_db, set_session_tenant_context
from app.core.dependencies import get_current_tenant
from app.models.campaign import Benefit
from app.models.connector import Connector
from app.models.wecom import WeComContactWay
from app.services.entitlement import (
    PLAN_EXPIRED_CODE,
    PLAN_EXPIRED_DETAIL,
    TenantPlanExpiredError,
    require_active_plan,
)
from app.services.wecom_integration import (
    WeComIntegrationError,
    decrypt_wecom_echo,
    get_wecom_status,
    list_configured_wecom_members,
    list_wecom_members,
    parse_wecom_callback_body,
    process_wecom_callback_event,
    upsert_wecom_connector,
    verify_wecom_connector,
)
from app.utils.auth_rbac import require_permission

wecom_integration_router = APIRouter(prefix="/api/v1/integrations/wecom", tags=["wecom-integrations"])


async def _scope_public_connector_tenant(db: AsyncSession, connector_id: uuid.UUID) -> Connector | None:
    """Locate exactly one callback connector, then lock the session to its tenant."""

    if _session_uses_postgresql(db):
        from app.core.database import control_session_factory

        async with control_session_factory() as control_db:
            await control_db.execute(text("SELECT set_config('app.tenant_id', '', true)"))
            await control_db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
            tenant_id = await control_db.scalar(
                select(Connector.tenant_id).where(Connector.id == connector_id).limit(1)
            )
    else:
        tenant_id = await db.scalar(select(Connector.tenant_id).where(Connector.id == connector_id).limit(1))
    if tenant_id is None:
        return None
    await set_session_tenant_context(db, tenant_id)
    return await db.scalar(select(Connector).where(Connector.id == connector_id, Connector.tenant_id == tenant_id))


class WeComConfigRequest(BaseModel):
    corp_id: str = Field(..., min_length=2)
    secret: str | None = None
    customer_service_user_ids: list[str] = Field(default_factory=list)
    mock_mode: bool = False


class ContactWayRequest(BaseModel):
    benefit_id: uuid.UUID
    scan_token: str


@wecom_integration_router.get("", summary="读取企业微信接入状态")
async def get_wecom_integration_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("campaign:manage")),
):
    return await get_wecom_status(
        db,
        tenant_id,
        include_callback_credentials=getattr(request.state, "acting_tenant_id", None) is None,
    )


@wecom_integration_router.post("", summary="保存企业微信接入配置")
async def save_wecom_integration_endpoint(
    body: WeComConfigRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("campaign:manage")),
):
    if settings.environment == "production" and body.mock_mode:
        raise HTTPException(status_code=400, detail="生产环境不允许启用企业微信模拟模式")
    return await upsert_wecom_connector(
        db,
        tenant_id,
        corp_id=body.corp_id,
        secret=body.secret,
        customer_service_user_ids=body.customer_service_user_ids,
        mock_mode=body.mock_mode,
    )


@wecom_integration_router.post("/verify", summary="检测企业微信接入")
async def verify_wecom_integration_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("campaign:manage")),
):
    try:
        return await verify_wecom_connector(db, tenant_id)
    except WeComIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@wecom_integration_router.get("/members", summary="读取可添加客户成员")
async def list_wecom_members_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("campaign:manage")),
):
    try:
        if getattr(request.state, "acting_tenant_id", None) is not None:
            return {"items": await list_configured_wecom_members(db, tenant_id)}
        return {"items": await list_wecom_members(db, tenant_id)}
    except WeComIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@wecom_integration_router.post("/contact-way", summary="获取企业微信添加入口")
async def get_contact_way_endpoint(
    body: ContactWayRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.services.scan_token import verify_scan_token
    from app.services.wecom_integration import get_or_create_claim_contact_way

    payload = verify_scan_token(body.scan_token)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid or expired scan token")

    token_tenant_id = payload.get("tenant_id")
    if not token_tenant_id:
        raise HTTPException(status_code=401, detail="scan token tenant missing")
    try:
        parsed_tenant_id = uuid.UUID(token_tenant_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="invalid scan token tenant")
    await set_session_tenant_context(db, parsed_tenant_id)
    try:
        await require_active_plan(db, parsed_tenant_id)
    except TenantPlanExpiredError:
        return JSONResponse(
            status_code=403,
            content={"code": PLAN_EXPIRED_CODE, "detail": PLAN_EXPIRED_DETAIL},
        )
    benefit_result = await db.execute(
        select(Benefit).where(
            Benefit.id == body.benefit_id,
            Benefit.tenant_id == parsed_tenant_id,
        )
    )
    benefit = benefit_result.scalar_one_or_none()
    if not benefit:
        raise HTTPException(status_code=404, detail="Benefit not found")
    try:
        contact_way = await get_or_create_claim_contact_way(
            db,
            tenant_id=benefit.tenant_id,
            benefit=benefit,
            scan_token=body.scan_token,
        )
    except WeComIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # yimatong-zgb1.12 Decision 19+26：记录 wecom_click 意图事件（QR 展示 = 意图，非确认转化）
    visitor_id = payload.get("visitor_id") or None
    public_id = payload.get("public_id")
    if visitor_id and public_id:
        from app.services.intent_event import insert_intent_event_idempotent

        await insert_intent_event_idempotent(
            db,
            tenant_id=benefit.tenant_id,
            event_type="wecom_click",
            public_id=public_id,
            visitor_id=visitor_id,
            client_event_id=f"wecom_click:{contact_way.state}",
            page_version_id=str(benefit.campaign_id) if benefit.campaign_id else None,
        )
    # Contact-way and its intent evidence commit atomically while the
    # transaction-local tenant RLS context is still active.
    await db.commit()

    return {"qr_code": contact_way.qr_code, "state": contact_way.state}


@wecom_integration_router.get("/callback/{connector_id}", summary="企业微信 URL 验证")
async def verify_wecom_callback_endpoint(
    connector_id: uuid.UUID,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    connector = await _scope_public_connector_tenant(db, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="Enterprise WeChat integration not found")
    try:
        plain = decrypt_wecom_echo(connector, msg_signature, timestamp, nonce, echostr)
    except WeComIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PlainTextResponse(plain)


@wecom_integration_router.post("/callback/{connector_id}", summary="接收企业微信客户事件")
async def receive_wecom_callback_endpoint(
    connector_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    connector = await _scope_public_connector_tenant(db, connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="Enterprise WeChat integration not found")
    try:
        event = parse_wecom_callback_body(
            await request.body(),
            connector,
            {k: v for k, v in request.query_params.items()},
        )
        result = await process_wecom_callback_event(db, connector_id=connector_id, event=event)
        await db.commit()
    except WeComIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PlainTextResponse("success" if result.get("status") in {"recorded", "duplicate", "ignored"} else "fail")


@wecom_integration_router.post("/mock-added", summary="本地受控模拟添加企业微信")
async def mock_wecom_added_endpoint(
    state: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _permission: None = Depends(require_permission("campaign:manage")),
):
    if settings.environment == "production":
        raise HTTPException(status_code=404, detail="Not found")
    way_result = await db.execute(
        select(WeComContactWay).where(WeComContactWay.state == state, WeComContactWay.tenant_id == tenant_id)
    )
    way = way_result.scalar_one_or_none()
    if not way:
        raise HTTPException(status_code=404, detail="Contact entry not found")
    connector = await db.scalar(
        select(Connector).where(Connector.id == way.connector_id, Connector.tenant_id == tenant_id)
    )
    if connector is None or not connector.config.get("mock_mode"):
        raise HTTPException(status_code=403, detail="Mock mode is not enabled for this tenant")
    result = await process_wecom_callback_event(
        db,
        connector_id=way.connector_id,
        event={
            "Event": "change_external_contact",
            "ChangeType": "add_external_contact",
            "ExternalUserID": f"mock-{state[-10:]}",
            "UserID": (way.user_ids or ["demo-member"])[0],
            "State": state,
        },
        # yimatong-zgb1.12：标记为 mock_added（非验签，仅本地演示）
        verification_source="mock_added",
    )
    await db.commit()
    return {"status": result.get("status", "recorded"), "message": "已确认添加，可返回领取页面继续领取"}
