"""Callback-role authority for binding a verified WeChat identity."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from uuid6 import uuid7

from app.core.database import callback_session_factory, set_session_tenant_context
from app.utils.crypto import encrypt_wechat_openid, hash_wechat_openid


def _map_callback_error(exc: DBAPIError) -> HTTPException | None:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    if sqlstate == "42501":
        return HTTPException(status_code=403, detail="consent_required")
    if sqlstate == "23503":
        return HTTPException(status_code=404, detail="oauth_subject_not_found")
    if sqlstate in {"22023", "23514", "23505"}:
        return HTTPException(status_code=409, detail="oauth_binding_conflict")
    if sqlstate == "55P03":
        return HTTPException(status_code=409, detail="oauth_binding_retry", headers={"Retry-After": "1"})
    return None


async def bind_wechat_oauth_consumer_authority(
    *,
    tenant_id: uuid.UUID,
    consent_id: uuid.UUID,
    scan_event_id: uuid.UUID,
    scan_time: datetime,
    public_id: str,
    visitor_id: str,
    token_consumer_id: uuid.UUID | None,
    openid: str,
    benefit_id: uuid.UUID,
) -> dict:
    """Bind one verified OAuth result without granting callback-role table DML."""

    requested_consumer_id = token_consumer_id or uuid7()
    openid_hash = hash_wechat_openid(tenant_id, openid)
    ciphertext, nonce, key_id = encrypt_wechat_openid(tenant_id, requested_consumer_id, openid)
    try:
        async with callback_session_factory() as callback_db:
            await set_session_tenant_context(callback_db, tenant_id)
            row = (
                (
                    await callback_db.execute(
                        text(
                            "SELECT * FROM public.bind_wechat_oauth_consumer("
                            ":tenant_id,:consent_id,:scan_event_id,:scan_time,:public_id,:visitor_id,"
                            ":token_consumer_id,:requested_consumer,:openid_hash,:openid_ciphertext,"
                            ":openid_nonce,:openid_key_id,:audit_id,:benefit_id)"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "consent_id": consent_id,
                            "scan_event_id": scan_event_id,
                            "scan_time": scan_time,
                            "public_id": public_id,
                            "visitor_id": visitor_id,
                            "token_consumer_id": token_consumer_id,
                            "requested_consumer": requested_consumer_id,
                            "openid_hash": openid_hash,
                            "openid_ciphertext": ciphertext,
                            "openid_nonce": nonce,
                            "openid_key_id": key_id,
                            "audit_id": uuid7(),
                            "benefit_id": benefit_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
            result = dict(row)
            if result.get("outcome") != "bound":
                raise HTTPException(status_code=409, detail="oauth_binding_conflict")
            result_consumer_id = uuid.UUID(str(result.get("consumer_id")))
            if token_consumer_id is not None and result_consumer_id != token_consumer_id:
                raise HTTPException(status_code=409, detail="oauth_binding_conflict")
            if token_consumer_id is None and not result.get("replayed") and result_consumer_id != requested_consumer_id:
                raise HTTPException(status_code=409, detail="oauth_binding_conflict")
            if uuid.UUID(str(result.get("consent_id"))) != consent_id:
                raise HTTPException(status_code=409, detail="oauth_binding_conflict")
            await callback_db.commit()
            return result
    except DBAPIError as exc:
        mapped = _map_callback_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
