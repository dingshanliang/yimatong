"""Database-authoritative prepared-export ledger adapter."""

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import _session_uses_postgresql
from app.models.auth_security import AuthSession
from app.models.export_log import ExportLog


@dataclass(frozen=True)
class PreparedExportRecord:
    export_id: uuid.UUID
    account_id: uuid.UUID
    replayed: bool
    checksum_sha256: str
    file_size_bytes: int
    row_count: int
    status: str


@dataclass(frozen=True)
class SeedCodeExportRecord:
    export_id: uuid.UUID
    replayed: bool


async def record_seed_code_export_manifest(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    export_id: uuid.UUID,
    code_batch_id: uuid.UUID,
    file_name: str,
    row_count: int,
    checksum_sha256: str,
    file_size_bytes: int,
    artifact_ciphertext: bytes,
    artifact_nonce: bytes,
    artifact_scheme: str,
    artifact_key_id: str,
) -> SeedCodeExportRecord:
    """Call the owner-only seed capability; this seam is never HTTP-facing."""

    if not _session_uses_postgresql(db):
        raise RuntimeError("The trusted seed export authority requires PostgreSQL")
    row = (
        (
            await db.execute(
                text(
                    "SELECT * FROM public.record_seed_code_export_manifest("
                    ":tenant_id,:export_id,:code_batch_id,:file_name,:row_count,:checksum_sha256,"
                    ":file_size_bytes,:artifact_ciphertext,:artifact_nonce,:artifact_scheme,:artifact_key_id)"
                ),
                {
                    "tenant_id": tenant_id,
                    "export_id": export_id,
                    "code_batch_id": code_batch_id,
                    "file_name": file_name,
                    "row_count": row_count,
                    "checksum_sha256": checksum_sha256,
                    "file_size_bytes": file_size_bytes,
                    "artifact_ciphertext": artifact_ciphertext,
                    "artifact_nonce": artifact_nonce,
                    "artifact_scheme": artifact_scheme,
                    "artifact_key_id": artifact_key_id,
                },
            )
        )
        .mappings()
        .one()
    )
    return SeedCodeExportRecord(export_id=row["export_id"], replayed=bool(row["replayed"]))


async def record_prepared_export(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    export_id: uuid.UUID,
    export_type: str,
    reason: str,
    scope_snapshot: dict[str, object],
    idempotency_key: str,
    file_name: str,
    content_type: str,
    row_count: int,
    checksum_sha256: str,
    file_size_bytes: int,
    resource_id: uuid.UUID | None = None,
    code_batch_id: uuid.UUID | None = None,
    manifest_version: int | None = None,
    artifact_ciphertext: bytes | None = None,
    artifact_nonce: bytes | None = None,
    artifact_scheme: str | None = None,
    artifact_key_id: str | None = None,
) -> PreparedExportRecord:
    """Record one prepared file, deriving its actor in the database authority."""

    parameters = {
        "tenant_id": tenant_id,
        "auth_session_id": auth_session_id,
        "export_id": export_id,
        "export_type": export_type,
        "reason": reason,
        "scope_snapshot": json.dumps(scope_snapshot, sort_keys=True, separators=(",", ":")),
        "idempotency_key": idempotency_key,
        "file_name": file_name,
        "content_type": content_type,
        "row_count": row_count,
        "checksum_sha256": checksum_sha256,
        "file_size_bytes": file_size_bytes,
        "resource_id": resource_id,
        "code_batch_id": code_batch_id,
        "manifest_version": manifest_version,
        "artifact_ciphertext": artifact_ciphertext,
        "artifact_nonce": artifact_nonce,
        "artifact_scheme": artifact_scheme,
        "artifact_key_id": artifact_key_id,
    }
    if _session_uses_postgresql(db):
        row = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM public.record_prepared_export("
                        ":tenant_id,:auth_session_id,:export_id,:export_type,:reason,CAST(:scope_snapshot AS jsonb),"
                        ":idempotency_key,:file_name,:content_type,:row_count,:checksum_sha256,:file_size_bytes,"
                        ":resource_id,:code_batch_id,:manifest_version,:artifact_ciphertext,:artifact_nonce,"
                        ":artifact_scheme,:artifact_key_id)"
                    ),
                    parameters,
                )
            )
            .mappings()
            .one()
        )
        return PreparedExportRecord(
            export_id=row["export_id"],
            account_id=row["account_id"],
            replayed=bool(row["replayed"]),
            checksum_sha256=row["checksum_sha256"],
            file_size_bytes=row["file_size_bytes"],
            row_count=row["row_count"],
            status=row["status"],
        )

    # SQLite is used only by isolated tests. It preserves actor derivation and
    # exact replay semantics while PostgreSQL owns the permission capability.
    session = await db.scalar(
        select(AuthSession).where(
            AuthSession.id == auth_session_id,
            AuthSession.tenant_id == tenant_id,
        )
    )
    # Existing isolated request fixtures predate durable sessions and use the
    # synthetic account id as sid. This exception is deliberately non-PG.
    account_id = session.account_id if session is not None else auth_session_id
    normalized_scope = json.loads(parameters["scope_snapshot"])
    payload_material = json.dumps(
        {
            key: value.hex() if isinstance(value, bytes) else str(value) if isinstance(value, uuid.UUID) else value
            for key, value in parameters.items()
            if key not in {"export_id", "idempotency_key", "scope_snapshot"}
        }
        | {"scope_snapshot": normalized_scope},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    payload_digest = hashlib.sha256(payload_material).hexdigest()
    prior = await db.scalar(
        select(ExportLog).where(
            ExportLog.tenant_id == tenant_id,
            ExportLog.idempotency_key == idempotency_key.strip(),
        )
    )
    if prior is not None:
        if prior.payload_digest != payload_digest:
            raise ValueError("Prepared export idempotency payload conflicts")
        return PreparedExportRecord(
            export_id=prior.id,
            account_id=prior.account_id,
            replayed=True,
            checksum_sha256=prior.checksum_sha256 or "",
            file_size_bytes=prior.artifact_size_bytes or 0,
            row_count=prior.row_count,
            status=prior.status,
        )
    status = "completed" if export_type == "code_csv" else "prepared"
    entry = ExportLog(
        id=export_id,
        tenant_id=tenant_id,
        account_id=account_id,
        auth_session_id=auth_session_id,
        export_type=export_type,
        resource_id=resource_id,
        file_name=file_name.strip(),
        content_type=content_type.strip(),
        row_count=row_count,
        status=status,
        reason=reason.strip(),
        scope_snapshot=normalized_scope,
        idempotency_key=idempotency_key.strip(),
        payload_digest=payload_digest,
        authority_version=1,
        code_batch_id=code_batch_id,
        manifest_version=manifest_version,
        checksum_sha256=checksum_sha256,
        artifact_size_bytes=file_size_bytes,
        artifact_ciphertext=artifact_ciphertext,
        artifact_nonce=artifact_nonce,
        artifact_scheme=artifact_scheme,
        artifact_key_id=artifact_key_id,
    )
    db.add(entry)
    await db.flush()
    return PreparedExportRecord(
        export_id=entry.id,
        account_id=entry.account_id,
        replayed=False,
        checksum_sha256=checksum_sha256,
        file_size_bytes=file_size_bytes,
        row_count=row_count,
        status=status,
    )
