"""Actor-bound channel mutations with a SQLite semantic adapter for tests."""

import hashlib
import json
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.database import _session_uses_postgresql, get_request_security_credential
from app.models.channel import AccountChannelScope, CodeAllocation, Distributor, Region, Store
from app.services import channel as legacy
from app.utils import utcnow
from app.utils.crypto import encrypt_phone, hash_phone


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


async def _sqlite_replay(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    action: str,
    idempotency_key: str,
    payload: dict[str, Any],
    model,
):
    """Match PostgreSQL idempotency semantics in the isolated SQLite adapter."""

    receipts = db.sync_session.info.setdefault("channel_authority_receipts", {})
    key = (tenant_id, action, idempotency_key)
    existing = receipts.get(key)
    digest = _canonical_digest(payload)
    if existing is None:
        return None, digest
    if existing["payload_digest"] != digest:
        raise HTTPException(status_code=409, detail="Channel authority conflict")
    resource = await _reload(db, model, tenant_id, existing["resource_id"])
    if resource is None:  # pragma: no cover - an adapter transaction must retain its first write
        raise HTTPException(status_code=409, detail="Channel authority conflict")
    return resource, digest


def _sqlite_record(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    action: str,
    idempotency_key: str,
    payload_digest: str,
    resource_id: uuid.UUID,
) -> None:
    receipts = db.sync_session.info.setdefault("channel_authority_receipts", {})
    receipts[(tenant_id, action, idempotency_key)] = {
        "payload_digest": payload_digest,
        "resource_id": resource_id,
    }


def _auth_session_id() -> uuid.UUID:
    credential = get_request_security_credential()
    if credential is None or credential[0] != "auth_session":
        raise HTTPException(status_code=403, detail="A live login session is required")
    try:
        return uuid.UUID(credential[1])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Invalid login session") from exc


def _map_authority_error(exc: DBAPIError) -> None:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    if sqlstate == "42501":
        raise HTTPException(status_code=403, detail="Channel authority denied") from exc
    if sqlstate == "23503":
        raise HTTPException(status_code=404, detail="Channel resource not found") from exc
    if sqlstate == "55P03":
        raise HTTPException(status_code=409, detail="Channel resource busy", headers={"Retry-After": "1"}) from exc
    if sqlstate in {"22023", "23514", "23505"}:
        raise HTTPException(status_code=409, detail="Channel authority conflict") from exc
    raise exc


def _json_or_none(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


async def _call(db: AsyncSession, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        row = (await db.execute(text(sql), params)).mappings().one()
    except DBAPIError as exc:
        _map_authority_error(exc)
        raise AssertionError("unreachable")
    return dict(row)


async def _reload(db: AsyncSession, model, tenant_id: uuid.UUID, resource_id: uuid.UUID):
    return await db.scalar(
        select(model)
        .where(model.tenant_id == tenant_id, model.id == resource_id)
        .execution_options(populate_existing=True)
    )


async def create_distributor(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    idempotency_key: str,
    name: str,
    code: str | None,
    contact_name: str | None,
    contact_phone: str | None,
    status: str,
) -> Distributor:
    if not _session_uses_postgresql(db):
        payload = {
            "name": name,
            "code": code,
            "contact_name": contact_name,
            "phone_hash": hash_phone(contact_phone) if contact_phone else None,
            "status": status,
        }
        replay, payload_digest = await _sqlite_replay(
            db,
            tenant_id,
            action="create_distributor",
            idempotency_key=idempotency_key,
            payload=payload,
            model=Distributor,
        )
        if replay is not None:
            return replay
        distributor = await legacy.create_distributor(db, tenant_id, name, code, contact_name, contact_phone, status)
        _sqlite_record(
            db,
            tenant_id,
            action="create_distributor",
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            resource_id=distributor.id,
        )
        return distributor
    resource_id = uuid7()
    ciphertext = encrypt_phone(contact_phone) if contact_phone else None
    digest = hash_phone(contact_phone) if contact_phone else None
    row = await _call(
        db,
        "SELECT * FROM public.create_channel_distributor("
        ":tenant,:sid,:audit,:id,:idem,:name,:code,:contact,:cipher,:hash,:status)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "id": resource_id,
            "idem": idempotency_key,
            "name": name,
            "code": code,
            "contact": contact_name,
            "cipher": ciphertext,
            "hash": digest,
            "status": status,
        },
    )
    return await _reload(db, Distributor, tenant_id, row["resource_id"])


async def update_distributor(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    resource_id: uuid.UUID,
    *,
    expected_version: int,
    idempotency_key: str,
    changes: dict[str, Any],
) -> Distributor | None:
    current = await _reload(db, Distributor, tenant_id, resource_id)
    if current is None:
        return None
    contact_name = changes.get("contact_name", current.contact_name)
    if "contact_phone" in changes:
        contact_phone = changes["contact_phone"]
        contact_phone_encrypted = encrypt_phone(contact_phone) if contact_phone else None
        contact_phone_hash = hash_phone(contact_phone) if contact_phone else None
    else:
        contact_phone_encrypted = current.contact_phone_encrypted
        contact_phone_hash = current.contact_phone_hash
    if not _session_uses_postgresql(db):
        payload = {
            "resource_id": resource_id,
            "expected_version": expected_version,
            "name": changes.get("name", current.name),
            "contact_name": contact_name,
            "phone_hash": contact_phone_hash,
            "status": changes.get("status", current.status),
        }
        replay, payload_digest = await _sqlite_replay(
            db,
            tenant_id,
            action="update_distributor",
            idempotency_key=idempotency_key,
            payload=payload,
            model=Distributor,
        )
        if replay is not None:
            return replay
        if "name" in changes:
            current.name = changes["name"]
        current.contact_name = contact_name
        current.contact_phone_encrypted = contact_phone_encrypted
        current.contact_phone_hash = contact_phone_hash
        if "status" in changes:
            current.status = changes["status"]
        current.version += 1
        await db.flush()
        await db.refresh(current)
        _sqlite_record(
            db,
            tenant_id,
            action="update_distributor",
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            resource_id=current.id,
        )
        return current
    row = await _call(
        db,
        "SELECT * FROM public.update_channel_distributor("
        ":tenant,:sid,:audit,:id,:version,:idem,:name,:contact,:cipher,:hash,:status)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "id": resource_id,
            "version": expected_version,
            "idem": idempotency_key,
            "name": changes.get("name", current.name),
            "contact": contact_name,
            "cipher": contact_phone_encrypted,
            "hash": contact_phone_hash,
            "status": changes.get("status", current.status),
        },
    )
    return await _reload(db, Distributor, tenant_id, row["resource_id"])


async def create_region(
    db: AsyncSession, tenant_id: uuid.UUID, *, idempotency_key: str, payload: dict[str, Any]
) -> Region:
    if not _session_uses_postgresql(db):
        return await legacy.create_region(db, tenant_id, **payload)
    resource_id = uuid7()
    row = await _call(
        db,
        "SELECT * FROM public.create_channel_region("
        ":tenant,:sid,:audit,:id,:idem,:name,:code,:province,:city,:coverage_type,"
        "CAST(:areas AS jsonb),:distributor,:status)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "id": resource_id,
            "idem": idempotency_key,
            "name": payload["name"],
            "code": payload.get("code"),
            "province": payload.get("province"),
            "city": payload.get("city"),
            "coverage_type": payload.get("coverage_type"),
            "areas": _json_or_none(payload.get("coverage_areas")),
            "distributor": payload.get("distributor_id"),
            "status": payload["status"],
        },
    )
    return await _reload(db, Region, tenant_id, row["resource_id"])


async def update_region(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    resource_id: uuid.UUID,
    *,
    expected_version: int,
    idempotency_key: str,
    changes: dict[str, Any],
) -> Region | None:
    current = await _reload(db, Region, tenant_id, resource_id)
    if current is None:
        return None
    values = {
        name: changes.get(name, getattr(current, name))
        for name in ("name", "province", "city", "coverage_type", "coverage_areas", "distributor_id", "status")
    }
    if not _session_uses_postgresql(db):
        for name, value in values.items():
            setattr(current, name, value)
        current.version += 1
        await db.flush()
        await db.refresh(current)
        return current
    row = await _call(
        db,
        "SELECT * FROM public.update_channel_region("
        ":tenant,:sid,:audit,:id,:version,:idem,:name,:province,:city,:coverage_type,"
        "CAST(:areas AS jsonb),:distributor,:status)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "id": resource_id,
            "version": expected_version,
            "idem": idempotency_key,
            "name": values["name"],
            "province": values["province"],
            "city": values["city"],
            "coverage_type": values["coverage_type"],
            "areas": _json_or_none(values["coverage_areas"]),
            "distributor": values["distributor_id"],
            "status": values["status"],
        },
    )
    return await _reload(db, Region, tenant_id, row["resource_id"])


async def create_store(
    db: AsyncSession, tenant_id: uuid.UUID, *, idempotency_key: str, payload: dict[str, Any]
) -> Store:
    if not _session_uses_postgresql(db):
        status = payload.get("status", "active")
        store = await legacy.create_store(db, tenant_id, **{k: v for k, v in payload.items() if k != "status"})
        store.status = status
        await db.flush()
        await db.refresh(store)
        return store
    resource_id = uuid7()
    row = await _call(
        db,
        "SELECT * FROM public.create_channel_store("
        ":tenant,:sid,:audit,:id,:idem,:name,:code,:region,:distributor,:address,:status)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "id": resource_id,
            "idem": idempotency_key,
            "name": payload["name"],
            "code": payload.get("code"),
            "region": payload.get("region_id"),
            "distributor": payload.get("distributor_id"),
            "address": payload.get("address"),
            "status": payload.get("status", "active"),
        },
    )
    return await _reload(db, Store, tenant_id, row["resource_id"])


async def update_store(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    resource_id: uuid.UUID,
    *,
    expected_version: int,
    idempotency_key: str,
    changes: dict[str, Any],
) -> Store | None:
    current = await _reload(db, Store, tenant_id, resource_id)
    if current is None:
        return None
    values = {
        name: changes.get(name, getattr(current, name))
        for name in ("name", "region_id", "distributor_id", "address", "status")
    }
    if not _session_uses_postgresql(db):
        for name, value in values.items():
            setattr(current, name, value)
        current.version += 1
        await db.flush()
        await db.refresh(current)
        return current
    row = await _call(
        db,
        "SELECT * FROM public.update_channel_store("
        ":tenant,:sid,:audit,:id,:version,:idem,:name,:region,:distributor,:address,:status)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "id": resource_id,
            "version": expected_version,
            "idem": idempotency_key,
            "name": values["name"],
            "region": values["region_id"],
            "distributor": values["distributor_id"],
            "address": values["address"],
            "status": values["status"],
        },
    )
    return await _reload(db, Store, tenant_id, row["resource_id"])


async def archive_store(
    db: AsyncSession, tenant_id: uuid.UUID, resource_id: uuid.UUID, *, expected_version: int, idempotency_key: str
) -> bool:
    if not _session_uses_postgresql(db):
        current = await _reload(db, Store, tenant_id, resource_id)
        if current is None:
            return False
        current.status = "inactive"
        current.version += 1
        await db.flush()
        return True
    await _call(
        db,
        "SELECT * FROM public.archive_channel_store(:tenant,:sid,:audit,:id,:version,:idem)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "id": resource_id,
            "version": expected_version,
            "idem": idempotency_key,
        },
    )
    return True


async def assign_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    *,
    idempotency_key: str,
    distributor_id: uuid.UUID | None,
    region_id: uuid.UUID | None,
) -> dict | None:
    if not _session_uses_postgresql(db):
        return await legacy.assign_batch_to_channel(db, tenant_id, batch_id, distributor_id, region_id)
    row = await _call(
        db,
        "SELECT * FROM public.assign_code_batch_channel(:tenant,:sid,:audit,:batch,:idem,:distributor,:region)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "batch": batch_id,
            "idem": idempotency_key,
            "distributor": distributor_id,
            "region": region_id,
        },
    )
    batch = await db.scalar(
        select(legacy.CodeBatch)
        .where(legacy.CodeBatch.tenant_id == tenant_id, legacy.CodeBatch.id == row["resource_id"])
        .execution_options(populate_existing=True)
    )
    return (
        None
        if batch is None
        else {
            "id": str(batch.id),
            "distributor_id": str(batch.distributor_id) if batch.distributor_id else None,
            "region_id": str(batch.region_id) if batch.region_id else None,
            "version": row["version"],
        }
    )


async def allocate(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    idempotency_key: str,
    batch_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    quantity: int,
    reason: str,
) -> CodeAllocation | None:
    if not _session_uses_postgresql(db):
        allocation = await legacy.allocate_codes_to_store(
            db,
            tenant_id,
            batch_id,
            target_id if target_type == "store" else None,
            quantity,
            distributor_id=target_id if target_type == "distributor" else None,
            region_id=target_id if target_type == "region" else None,
        )
        if allocation is not None:
            allocation.change_reason = reason
            await db.flush()
            await db.refresh(allocation)
        return allocation
    allocation_id = uuid7()
    row = await _call(
        db,
        "SELECT * FROM public.allocate_code_batch_channel("
        ":tenant,:sid,:audit,:id,:idem,:batch,:target_type,:target_id,:quantity,:reason)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "id": allocation_id,
            "idem": idempotency_key,
            "batch": batch_id,
            "target_type": target_type,
            "target_id": target_id,
            "quantity": quantity,
            "reason": reason,
        },
    )
    return await _reload(db, CodeAllocation, tenant_id, row["allocation_id"])


async def reassign_allocation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    current_id: uuid.UUID,
    *,
    expected_version: int,
    idempotency_key: str,
    target_type: str,
    target_id: uuid.UUID,
    quantity: int,
    reason: str,
) -> CodeAllocation | None:
    if not _session_uses_postgresql(db):
        current = await db.scalar(
            select(CodeAllocation).where(
                CodeAllocation.tenant_id == tenant_id,
                CodeAllocation.id == current_id,
            )
        )
        if current is None:
            return None
        if current.effective_to is not None or current.status != "active" or current.version != expected_version:
            raise HTTPException(status_code=409, detail="Channel authority conflict")
        now = utcnow().replace(microsecond=0)
        current.effective_to = now
        await db.flush()
        target = await legacy.allocate_codes_to_store(
            db,
            tenant_id,
            current.batch_id,
            target_id if target_type == "store" else None,
            quantity,
            distributor_id=target_id if target_type == "distributor" else None,
            region_id=target_id if target_type == "region" else None,
        )
        if target is None:
            return None
        target.allocation_root_id = current.allocation_root_id
        target.version = current.version + 1
        target.action = "reassign"
        target.change_reason = reason
        target.effective_from = now
        await db.flush()
        await db.refresh(target)
        return target
    new_id = uuid7()
    row = await _call(
        db,
        "SELECT * FROM public.reassign_code_batch_channel("
        ":tenant,:sid,:audit,:new_id,:idem,:current_id,:version,:target_type,:target_id,:quantity,:reason)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "new_id": new_id,
            "idem": idempotency_key,
            "current_id": current_id,
            "version": expected_version,
            "target_type": target_type,
            "target_id": target_id,
            "quantity": quantity,
            "reason": reason,
        },
    )
    return await _reload(db, CodeAllocation, tenant_id, row["allocation_id"])


async def archive_allocation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    current_id: uuid.UUID,
    *,
    expected_version: int,
    idempotency_key: str,
    reason: str,
) -> CodeAllocation | None:
    if not _session_uses_postgresql(db):
        current = await db.scalar(
            select(CodeAllocation).where(
                CodeAllocation.tenant_id == tenant_id,
                CodeAllocation.id == current_id,
            )
        )
        if current is None:
            return None
        if current.effective_to is not None or current.status != "active" or current.version != expected_version:
            raise HTTPException(status_code=409, detail="Channel authority conflict")
        now = utcnow().replace(microsecond=0)
        current.effective_to = now
        archived = CodeAllocation(
            id=uuid7(),
            tenant_id=tenant_id,
            batch_id=current.batch_id,
            allocation_root_id=current.allocation_root_id,
            action="archive",
            status="archived",
            quantity=0,
            allocated_at=now.isoformat(),
            effective_from=now,
            version=current.version + 1,
            change_reason=reason,
        )
        db.add(archived)
        await db.flush()
        await db.refresh(archived)
        return archived
    new_id = uuid7()
    row = await _call(
        db,
        "SELECT * FROM public.archive_code_batch_allocation("
        ":tenant,:sid,:audit,:new_id,:idem,:current_id,:version,:reason)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "new_id": new_id,
            "idem": idempotency_key,
            "current_id": current_id,
            "version": expected_version,
            "reason": reason,
        },
    )
    return await _reload(db, CodeAllocation, tenant_id, row["allocation_id"])


async def set_scope(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    idempotency_key: str,
    account_id: uuid.UUID,
    scope_type: str,
    target_id: uuid.UUID,
) -> AccountChannelScope:
    if not _session_uses_postgresql(db):
        return await legacy.create_account_scope(
            db,
            tenant_id,
            account_id,
            scope_type,
            distributor_id=target_id if scope_type == "distributor" else None,
            region_id=target_id if scope_type == "region" else None,
            store_id=target_id if scope_type == "store" else None,
        )
    scope_id = uuid7()
    row = await _call(
        db,
        "SELECT * FROM public.set_account_channel_scope(:tenant,:sid,:audit,:id,:idem,:account,:scope_type,:target_id)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "id": scope_id,
            "idem": idempotency_key,
            "account": account_id,
            "scope_type": scope_type,
            "target_id": target_id,
        },
    )
    return await _reload(db, AccountChannelScope, tenant_id, row["resource_id"])


async def delete_scope(
    db: AsyncSession, tenant_id: uuid.UUID, scope_id: uuid.UUID, *, expected_version: int, idempotency_key: str
) -> bool:
    if not _session_uses_postgresql(db):
        return await legacy.delete_account_scope(db, tenant_id, scope_id)
    await _call(
        db,
        "SELECT * FROM public.delete_account_channel_scope(:tenant,:sid,:audit,:id,:version,:idem)",
        {
            "tenant": tenant_id,
            "sid": _auth_session_id(),
            "audit": uuid7(),
            "id": scope_id,
            "version": expected_version,
            "idem": idempotency_key,
        },
    )
    return True
