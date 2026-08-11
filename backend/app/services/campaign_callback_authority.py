"""Dedicated PostgreSQL authority for verified connector callbacks."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from uuid6 import uuid7

from app.core.database import callback_session_factory, set_session_tenant_context


def _map_callback_db_error(exc: DBAPIError) -> HTTPException | None:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if sqlstate == "42501":
        return HTTPException(status_code=503, detail="Callback authority unavailable")
    if sqlstate == "23503":
        return HTTPException(status_code=404, detail="Callback delivery not found")
    if sqlstate in {"22023", "23514", "23505"}:
        return HTTPException(status_code=409, detail="Callback settlement conflict")
    if sqlstate == "55P03":
        return HTTPException(status_code=409, detail="Callback settlement is busy", headers={"Retry-After": "1"})
    return None


async def settle_campaign_claim_callback(
    tenant_id: uuid.UUID,
    connector_id: uuid.UUID,
    delivery_id: uuid.UUID,
    claim_id: uuid.UUID,
    external_id: str,
    callback_status: str,
    external_data: dict,
) -> dict:
    """Atomically settle a verified callback using the callback-only DB role."""

    try:
        async with callback_session_factory() as callback_db:
            await set_session_tenant_context(callback_db, tenant_id)
            row = (
                (
                    await callback_db.execute(
                        text(
                            "SELECT * FROM public.settle_campaign_claim_callback("
                            ":tenant_id,:audit_id,:connector_id,:delivery_id,:claim_id,"
                            ":external_id,:callback_status,:external_data)"
                        ).bindparams(bindparam("external_data", type_=JSONB)),
                        {
                            "tenant_id": tenant_id,
                            "audit_id": uuid7(),
                            "connector_id": connector_id,
                            "delivery_id": delivery_id,
                            "claim_id": claim_id,
                            "external_id": external_id,
                            "callback_status": callback_status,
                            "external_data": external_data,
                        },
                    )
                )
                .mappings()
                .one()
            )
            await callback_db.commit()
            return dict(row)
    except DBAPIError as exc:
        mapped = _map_callback_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
