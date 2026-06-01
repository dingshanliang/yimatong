"""Enterprise WeChat integration APIs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.models.campaign import Benefit
from app.models.connector import Connector
from app.models.wecom import WeComContactWay
from app.services.wecom_integration import (
    WeComIntegrationError,
    decrypt_wecom_echo,
    get_wecom_status,
    list_wecom_members,
    parse_wecom_callback_body,
    process_wecom_callback_event,
    upsert_wecom_connector,
    verify_wecom_connector,
)

wecom_integration_router = APIRouter(prefix="/api/v1/integrations/wecom", tags=["wecom-integrations"])


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
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_wecom_status(db, tenant_id)


@wecom_integration_router.post("", summary="保存企业微信接入配置")
async def save_wecom_integration_endpoint(
    body: WeComConfigRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
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
):
    try:
        return await verify_wecom_connector(db, tenant_id)
    except WeComIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@wecom_integration_router.get("/members", summary="读取可添加客户成员")
async def list_wecom_members_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        return {"items": await list_wecom_members(db, tenant_id)}
    except WeComIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@wecom_integration_router.post("/contact-way", summary="获取企业微信添加入口")
async def get_contact_way_endpoint(
    body: ContactWayRequest,
    db: AsyncSession = Depends(get_db),
):
    import jwt

    from app.core.config import settings
    from app.services.wecom_integration import get_or_create_claim_contact_way

    try:
        payload = jwt.decode(body.scan_token, settings.secret_key, algorithms=["HS256"])
    except jwt.exceptions.DecodeError:
        raise HTTPException(status_code=401, detail="invalid token")
    except jwt.exceptions.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    if payload.get("type") != "scan_token":
        raise HTTPException(status_code=401, detail="invalid token type")

    benefit_result = await db.execute(select(Benefit).where(Benefit.id == body.benefit_id))
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
        await db.commit()
    except WeComIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    connector_result = await db.execute(select(Connector).where(Connector.id == connector_id))
    connector = connector_result.scalar_one_or_none()
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
    connector_result = await db.execute(select(Connector).where(Connector.id == connector_id))
    connector = connector_result.scalar_one_or_none()
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


@wecom_integration_router.get("/mock-added", summary="本地模拟添加企业微信")
async def mock_wecom_added_endpoint(
    state: str,
    db: AsyncSession = Depends(get_db),
):
    way_result = await db.execute(select(WeComContactWay).where(WeComContactWay.state == state))
    way = way_result.scalar_one_or_none()
    if not way:
        raise HTTPException(status_code=404, detail="Contact entry not found")
    result = await process_wecom_callback_event(
        db,
        connector_id=way.connector_id,
        event={
            "ChangeType": "add_external_contact",
            "ExternalUserID": f"mock-{state[-10:]}",
            "UserID": (way.user_ids or ["demo-member"])[0],
            "State": state,
        },
    )
    await db.commit()
    return {"status": result.get("status", "recorded"), "message": "已确认添加，可返回领取页面继续领取"}
