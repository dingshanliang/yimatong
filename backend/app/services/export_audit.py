"""导出审计日志服务"""

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.export_log import ExportLog
from app.services.audit import write_audit_log


async def log_export(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    export_type: str,
    resource_id: str | None = None,
    file_name: str | None = None,
    row_count: int = 0,
    code_batch_id: uuid.UUID | None = None,
    manifest_version: int | None = None,
    checksum_sha256: str | None = None,
    artifact_size_bytes: int | None = None,
    artifact_ciphertext: bytes | None = None,
    artifact_nonce: bytes | None = None,
    artifact_scheme: str | None = None,
    artifact_key_id: str | None = None,
) -> ExportLog:
    resource_uuid = uuid.UUID(resource_id) if resource_id else None
    if export_type == "code_csv" and (
        code_batch_id is None
        or resource_uuid != code_batch_id
        or manifest_version is None
        or manifest_version < 1
        or row_count < 1
        or checksum_sha256 is None
        or re.fullmatch(r"[0-9a-f]{64}", checksum_sha256) is None
        or artifact_size_bytes is None
        or artifact_size_bytes < 1
        or artifact_size_bytes > 16_777_216
        or artifact_ciphertext is None
        or len(artifact_ciphertext) != artifact_size_bytes + 16
        or artifact_nonce is None
        or len(artifact_nonce) != 12
        or artifact_scheme != "aes-256-gcm-v1"
        or artifact_key_id is None
        or re.fullmatch(r"aes-master-v[1-9][0-9]*", artifact_key_id) is None
    ):
        raise ValueError("Code CSV export requires a complete manifest")
    entry = ExportLog(
        tenant_id=tenant_id,
        account_id=account_id,
        export_type=export_type,
        resource_id=resource_uuid,
        file_name=file_name,
        row_count=row_count,
        code_batch_id=code_batch_id,
        manifest_version=manifest_version,
        checksum_sha256=checksum_sha256,
        artifact_size_bytes=artifact_size_bytes,
        artifact_ciphertext=artifact_ciphertext,
        artifact_nonce=artifact_nonce,
        artifact_scheme=artifact_scheme,
        artifact_key_id=artifact_key_id,
    )
    db.add(entry)
    await db.flush()
    await write_audit_log(
        db,
        operator_id=str(account_id),
        target_tenant_id=str(tenant_id),
        action="sensitive_export",
        resource=f"{export_type}:{resource_id or entry.id}",
        details={
            "export_type": export_type,
            "file_name": file_name,
            "row_count": row_count,
            "manifest_version": manifest_version,
            "checksum_sha256": checksum_sha256,
            "artifact_size_bytes": artifact_size_bytes,
            "reason": "用户发起数据导出",
            "result": "success",
        },
    )
    return entry
