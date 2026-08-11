"""PostgreSQL authority boundary for campaign, benefit, and claim mutations."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7


def _auth_session_id() -> uuid.UUID:
    from app.core.database import get_request_security_credential

    credential = get_request_security_credential()
    if credential is None or credential[0] != "auth_session":
        raise HTTPException(status_code=401, detail="Live login session required for campaign changes")
    try:
        return uuid.UUID(credential[1])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid login session") from exc


def map_campaign_db_error(exc: DBAPIError) -> HTTPException | None:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if sqlstate == "42501":
        return HTTPException(status_code=403, detail="Campaign authority denied")
    if sqlstate == "23503":
        return HTTPException(status_code=404, detail="Campaign resource not found")
    if sqlstate in {"22023", "23514", "23505"}:
        return HTTPException(status_code=409, detail="Campaign contract conflict")
    if sqlstate == "55P03":
        return HTTPException(status_code=409, detail="Campaign is busy; retry", headers={"Retry-After": "1"})
    return None


async def _one(
    db: AsyncSession,
    sql: str,
    params: dict,
    *,
    json_params: tuple[str, ...] = (),
) -> dict:
    statement = text(sql)
    if json_params:
        statement = statement.bindparams(*(bindparam(name, type_=JSONB) for name in json_params))
    try:
        row = (await db.execute(statement, params)).mappings().one()
    except DBAPIError as exc:
        mapped = map_campaign_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    return dict(row)


def _actor_params(tenant_id: uuid.UUID) -> dict:
    return {"tenant_id": tenant_id, "auth_session_id": _auth_session_id(), "audit_id": uuid7()}


async def create_campaign_authority(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    product_id: uuid.UUID | None,
    name: str,
    campaign_type: str,
    start_at: datetime,
    end_at: datetime,
    rules_json: dict,
    description: str | None,
) -> dict:
    return await _one(
        db,
        "SELECT * FROM public.create_campaign(:tenant_id,:auth_session_id,:audit_id,:campaign_id,"
        ":product_id,:name,:campaign_type,:start_at,:end_at,:rules_json,:description)",
        _actor_params(tenant_id)
        | {
            "campaign_id": campaign_id,
            "product_id": product_id,
            "name": name,
            "campaign_type": campaign_type,
            "start_at": start_at,
            "end_at": end_at,
            "rules_json": rules_json,
            "description": description,
        },
        json_params=("rules_json",),
    )


async def update_campaign_authority(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    fields: dict,
) -> dict:
    return await _one(
        db,
        "SELECT * FROM public.update_campaign(:tenant_id,:auth_session_id,:audit_id,:campaign_id,"
        ":product_present,:product_id,:name,:campaign_type,:start_at,:end_at,:rules_json,:description)",
        _actor_params(tenant_id)
        | {
            "campaign_id": campaign_id,
            "product_present": "product_id" in fields,
            "product_id": fields.get("product_id"),
            "name": fields.get("name"),
            "campaign_type": fields.get("campaign_type"),
            "start_at": fields.get("start_at"),
            "end_at": fields.get("end_at"),
            "rules_json": fields.get("rules_json"),
            "description": fields.get("description"),
        },
        json_params=("rules_json",),
    )


async def transition_campaign_authority(
    db: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID, target_status: str
) -> dict:
    return await _one(
        db,
        "SELECT * FROM public.transition_campaign(:tenant_id,:auth_session_id,:audit_id,:campaign_id,:target_status)",
        _actor_params(tenant_id) | {"campaign_id": campaign_id, "target_status": target_status},
    )


async def delete_campaign_authority(db: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> dict:
    return await _one(
        db,
        "SELECT * FROM public.delete_campaign(:tenant_id,:auth_session_id,:audit_id,:campaign_id)",
        _actor_params(tenant_id) | {"campaign_id": campaign_id},
    )


async def create_benefit_authority(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    benefit_id: uuid.UUID,
    campaign_id: uuid.UUID | None,
    name: str,
    benefit_type: str,
    config_json: dict,
    stock_total: int,
    per_person_limit: int,
    connector_id: uuid.UUID | None,
) -> dict:
    return await _one(
        db,
        "SELECT * FROM public.create_benefit(:tenant_id,:auth_session_id,:audit_id,:benefit_id,:campaign_id,"
        ":name,:benefit_type,:config_json,:stock_total,:per_person_limit,:connector_id)",
        _actor_params(tenant_id)
        | {
            "benefit_id": benefit_id,
            "campaign_id": campaign_id,
            "name": name,
            "benefit_type": benefit_type,
            "config_json": config_json,
            "stock_total": stock_total,
            "per_person_limit": per_person_limit,
            "connector_id": connector_id,
        },
        json_params=("config_json",),
    )


async def update_benefit_authority(db: AsyncSession, tenant_id: uuid.UUID, benefit_id: uuid.UUID, fields: dict) -> dict:
    return await _one(
        db,
        "SELECT * FROM public.update_benefit(:tenant_id,:auth_session_id,:audit_id,:benefit_id,:name,"
        ":benefit_type,:config_json,:stock_total,:per_person_limit,:connector_present,:connector_id,:status)",
        _actor_params(tenant_id)
        | {
            "benefit_id": benefit_id,
            "name": fields.get("name"),
            "benefit_type": fields.get("benefit_type"),
            "config_json": fields.get("config_json"),
            "stock_total": fields.get("stock_total"),
            "per_person_limit": fields.get("per_person_limit"),
            "connector_present": "connector_id" in fields,
            "connector_id": fields.get("connector_id"),
            "status": fields.get("status"),
        },
        json_params=("config_json",),
    )


async def relate_benefit_authority(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    benefit_id: uuid.UUID,
    *,
    attach: bool,
) -> dict:
    function = "attach_benefit" if attach else "detach_benefit"
    return await _one(
        db,
        f"SELECT * FROM public.{function}(:tenant_id,:auth_session_id,:audit_id,:benefit_id,:campaign_id)",
        _actor_params(tenant_id) | {"campaign_id": campaign_id, "benefit_id": benefit_id},
    )


async def delete_benefit_authority(db: AsyncSession, tenant_id: uuid.UUID, benefit_id: uuid.UUID) -> dict:
    return await _one(
        db,
        "SELECT * FROM public.delete_benefit(:tenant_id,:auth_session_id,:audit_id,:benefit_id)",
        _actor_params(tenant_id) | {"benefit_id": benefit_id},
    )


async def claim_campaign_benefit_authority(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    benefit_id: uuid.UUID,
    scan_event_id: uuid.UUID,
    scanned_product_id: uuid.UUID,
    public_id: str,
    consumer_id: str,
    idempotency_key: str,
) -> dict:
    claim_id = uuid7()
    return await _one(
        db,
        "SELECT * FROM public.claim_campaign_benefit(:tenant_id,:claim_id,:benefit_id,:scan_event_id,"
        ":scanned_product_id,:public_id,:consumer_id,:idempotency_key)",
        {
            "tenant_id": tenant_id,
            "claim_id": claim_id,
            "benefit_id": benefit_id,
            "scan_event_id": scan_event_id,
            "scanned_product_id": scanned_product_id,
            "public_id": public_id,
            "consumer_id": consumer_id,
            "idempotency_key": idempotency_key,
        },
    )


async def redeem_campaign_benefit_claim_authority(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    api_key_id: uuid.UUID,
    claim_id: uuid.UUID,
) -> dict:
    """Redeem one claim through the API-key-bound PostgreSQL authority."""

    return await _one(
        db,
        "SELECT * FROM public.redeem_campaign_benefit_claim(:tenant_id,:api_key_id,:audit_id,:claim_id)",
        {
            "tenant_id": tenant_id,
            "api_key_id": api_key_id,
            "audit_id": uuid7(),
            "claim_id": claim_id,
        },
    )
