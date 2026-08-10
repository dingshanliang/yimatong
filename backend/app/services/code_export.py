"""Authoritative packaging-code CSV export service."""

import csv
import hashlib
import io
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import _session_uses_postgresql
from app.core.exceptions import ConflictError
from app.models.code import CodeBatchSource, CodeBatchStatus, CodeGenerationMode, CodeItem, CodeItemStatus, CodeType
from app.models.export_log import ExportLog
from app.services.code import _lock_forward_operational_code_batch
from app.services.export_audit import log_export
from app.utils import utcnow
from app.utils.crypto import CryptoError, decrypt_bytes, encrypt_bytes

CODE_CSV_MANIFEST_VERSION = 1
CODE_CSV_ARTIFACT_SCHEME = "aes-256-gcm-v1"
CODE_CSV_MAX_ARTIFACT_BYTES = 16_777_216
_REPLAYABLE_EXPORT_STATES = {
    CodeBatchStatus.exported,
    CodeBatchStatus.printing,
    CodeBatchStatus.delivered,
    CodeBatchStatus.activated,
}
_DELIVERABLE_ITEM_STATES = {CodeItemStatus.created, CodeItemStatus.activated, CodeItemStatus.bound}


@dataclass(frozen=True)
class CodeCSVArtifact:
    content: bytes
    row_count: int
    checksum_sha256: str
    manifest_version: int


def _artifact_aad(tenant_id: uuid.UUID, batch_id: uuid.UUID, manifest_version: int) -> bytes:
    return f"yimatong:code-csv:v1:{tenant_id}:{batch_id}:{manifest_version}".encode()


def _decode_persisted_artifact(
    fields,
    *,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    expected_item_count: int,
) -> CodeCSVArtifact:
    try:
        manifest_version = int(fields["manifest_version"])
        row_count = int(fields["row_count"])
        artifact_size_bytes = int(fields["artifact_size_bytes"])
        checksum_sha256 = str(fields["checksum_sha256"])
        ciphertext = bytes(fields["artifact_ciphertext"])
        nonce = bytes(fields["artifact_nonce"])
        scheme = str(fields["artifact_scheme"])
        key_id = str(fields["artifact_key_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConflictError(
            "Export artifact envelope is invalid",
            error_code="CODE_BATCH_EXPORT_MANIFEST_MISMATCH",
        ) from exc
    if (
        manifest_version != CODE_CSV_MANIFEST_VERSION
        or row_count != expected_item_count
        or not 1 <= row_count <= 10_000
        or not 1 <= artifact_size_bytes <= CODE_CSV_MAX_ARTIFACT_BYTES
        or len(ciphertext) != artifact_size_bytes + 16
        or len(nonce) != 12
        or scheme != CODE_CSV_ARTIFACT_SCHEME
        or len(checksum_sha256) != 64
    ):
        raise ConflictError(
            "Export artifact envelope does not match its manifest",
            error_code="CODE_BATCH_EXPORT_MANIFEST_MISMATCH",
        )
    try:
        content = decrypt_bytes(
            ciphertext,
            nonce=nonce,
            key_id=key_id,
            aad=_artifact_aad(tenant_id, batch_id, manifest_version),
        )
    except CryptoError as exc:
        raise ConflictError(
            "Export artifact could not be verified",
            error_code="CODE_BATCH_EXPORT_MANIFEST_MISMATCH",
        ) from exc
    if len(content) != artifact_size_bytes or hashlib.sha256(content).hexdigest() != checksum_sha256:
        raise ConflictError(
            "Export artifact does not match its manifest",
            error_code="CODE_BATCH_EXPORT_MANIFEST_MISMATCH",
        )
    return CodeCSVArtifact(
        content=content,
        row_count=row_count,
        checksum_sha256=checksum_sha256,
        manifest_version=manifest_version,
    )


async def _load_persisted_artifact(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    manifest_id: uuid.UUID,
    expected_item_count: int,
) -> CodeCSVArtifact:
    if _session_uses_postgresql(db):
        fields = (
            (
                await db.execute(
                    text("SELECT * FROM public.get_code_export_artifact(:tenant_id, :batch_id, :manifest_id)"),
                    {"tenant_id": tenant_id, "batch_id": batch_id, "manifest_id": manifest_id},
                )
            )
            .mappings()
            .one_or_none()
        )
    else:
        manifest = await db.scalar(
            select(ExportLog).where(
                ExportLog.id == manifest_id,
                ExportLog.tenant_id == tenant_id,
                ExportLog.code_batch_id == batch_id,
            )
        )
        fields = (
            None
            if manifest is None
            else {
                "artifact_ciphertext": manifest.artifact_ciphertext,
                "artifact_nonce": manifest.artifact_nonce,
                "artifact_scheme": manifest.artifact_scheme,
                "artifact_key_id": manifest.artifact_key_id,
                "artifact_size_bytes": manifest.artifact_size_bytes,
                "checksum_sha256": manifest.checksum_sha256,
                "manifest_version": manifest.manifest_version,
                "row_count": manifest.row_count,
            }
        )
    if fields is None:
        raise ConflictError("Export manifest is unavailable", error_code="CODE_BATCH_EXPORT_MANIFEST_MISSING")
    return _decode_persisted_artifact(
        fields,
        tenant_id=tenant_id,
        batch_id=batch_id,
        expected_item_count=expected_item_count,
    )


def spreadsheet_safe(value: object) -> str:
    """Neutralize values that spreadsheet programs may interpret as formulas."""

    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def _validate_item_contract(batch, items: list[CodeItem]) -> None:
    if batch.contract_version not in {0, 1} or not 1 <= batch.expected_item_count <= 10_000:
        raise ConflictError("Code batch contract version is invalid", error_code="CODE_BATCH_CONTRACT_INVALID")
    if batch.source == CodeBatchSource.generated:
        if batch.generation_mode == CodeGenerationMode.batch_level:
            shape_is_valid = (
                batch.code_type == CodeType.single and batch.quantity == 1 and batch.expected_item_count == 1
            )
        elif batch.code_type == CodeType.single:
            shape_is_valid = batch.quantity > 0 and batch.expected_item_count == batch.quantity
        elif batch.code_type == CodeType.paired:
            shape_is_valid = batch.quantity > 0 and batch.expected_item_count == batch.quantity * 2
        else:
            shape_is_valid = False
    elif batch.source == CodeBatchSource.imported:
        shape_is_valid = (
            batch.generation_mode == CodeGenerationMode.item_level
            and batch.code_type == CodeType.single
            and batch.quantity > 0
            and batch.expected_item_count == batch.quantity
        )
    else:
        shape_is_valid = False
    if not shape_is_valid:
        raise ConflictError("Code batch generation shape is invalid", error_code="CODE_BATCH_CONTRACT_INVALID")
    if len(items) != batch.expected_item_count:
        raise ConflictError(
            "Code batch item count does not match its authoritative contract",
            error_code="CODE_BATCH_ITEM_COUNT_MISMATCH",
        )
    if any(item.status not in _DELIVERABLE_ITEM_STATES for item in items):
        raise ConflictError(
            "Code batch contains non-deliverable code items",
            error_code="CODE_BATCH_ITEM_NOT_DELIVERABLE",
        )

    if batch.source == CodeBatchSource.imported:
        valid = (
            batch.generation_mode == CodeGenerationMode.item_level
            and batch.code_type == CodeType.single
            and all(item.code_type == CodeType.single and item.pair_id is None for item in items)
        )
        if not valid:
            raise ConflictError("Imported code batch contract is invalid", error_code="CODE_BATCH_SOURCE_MISMATCH")
        return

    if batch.source != CodeBatchSource.generated:
        raise ConflictError("Code batch source is invalid", error_code="CODE_BATCH_SOURCE_MISMATCH")
    if batch.code_type == CodeType.single:
        if any(item.code_type != CodeType.single or item.pair_id is not None for item in items):
            raise ConflictError("Single-code batch item shape is invalid", error_code="CODE_BATCH_ITEM_SHAPE_MISMATCH")
        return
    if batch.code_type != CodeType.paired or batch.generation_mode != CodeGenerationMode.item_level:
        raise ConflictError("Generated code batch contract is invalid", error_code="CODE_BATCH_ITEM_SHAPE_MISMATCH")

    pairs: dict[uuid.UUID | None, Counter] = defaultdict(Counter)
    for item in items:
        pairs[item.pair_id][item.code_type] += 1
    if None in pairs or len(pairs) * 2 != batch.expected_item_count:
        raise ConflictError("Paired-code batch is incomplete", error_code="CODE_BATCH_PAIR_MISMATCH")
    if any(counts != Counter({CodeType.outer: 1, CodeType.inner: 1}) for counts in pairs.values()):
        raise ConflictError("Paired-code batch is incomplete", error_code="CODE_BATCH_PAIR_MISMATCH")


def _build_code_csv(batch, items: list[CodeItem]) -> CodeCSVArtifact:
    production_batch = batch.production_batch
    fieldnames = [
        "public_id",
        "status",
        "code_type",
        "code_url",
        "product_name",
        "sku_name",
        "sku_code",
        "batch_code",
        "production_date",
        "expiry_date",
        "origin",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for item in items:
        writer.writerow(
            {
                "public_id": spreadsheet_safe(item.public_id),
                # Operational item status changes after delivery/activation;
                # the delivery artifact records the immutable issued state.
                "status": CodeItemStatus.created.value,
                "code_type": spreadsheet_safe(item.code_type),
                "code_url": spreadsheet_safe(f"https://qr.yimatong.cn/c/{item.public_id}"),
                "product_name": spreadsheet_safe(batch.product.name if batch.product else ""),
                "sku_name": spreadsheet_safe(batch.sku.name if batch.sku else ""),
                "sku_code": spreadsheet_safe(batch.sku.code if batch.sku else ""),
                "batch_code": spreadsheet_safe(batch.batch_code),
                "production_date": spreadsheet_safe(
                    production_batch.production_date.isoformat()
                    if production_batch and production_batch.production_date
                    else ""
                ),
                "expiry_date": spreadsheet_safe(
                    production_batch.expiry_date.isoformat()
                    if production_batch and production_batch.expiry_date
                    else ""
                ),
                "origin": spreadsheet_safe(production_batch.origin if production_batch else ""),
            }
        )
    content = output.getvalue().encode("utf-8")
    return CodeCSVArtifact(
        content=content,
        row_count=len(items),
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        manifest_version=CODE_CSV_MANIFEST_VERSION,
    )


async def generate_code_csv(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    account_id: uuid.UUID,
) -> CodeCSVArtifact:
    """Generate or replay complete deterministic bytes under one transaction."""

    batch = await _lock_forward_operational_code_batch(db, tenant_id, batch_id)
    if batch.status not in {CodeBatchStatus.completed, *_REPLAYABLE_EXPORT_STATES}:
        raise ConflictError(
            f"Cannot export code batch with status '{batch.status.value}'",
            error_code="CODE_BATCH_NOT_EXPORTABLE",
        )

    if batch.status in _REPLAYABLE_EXPORT_STATES:
        if batch.export_manifest_id is None:
            raise ConflictError("Export manifest is unavailable", error_code="CODE_BATCH_EXPORT_MANIFEST_MISSING")
        return await _load_persisted_artifact(
            db,
            tenant_id=tenant_id,
            batch_id=batch_id,
            manifest_id=batch.export_manifest_id,
            expected_item_count=batch.expected_item_count,
        )

    items = list(
        await db.scalars(
            select(CodeItem)
            .where(CodeItem.tenant_id == tenant_id, CodeItem.code_batch_id == batch_id)
            .order_by(CodeItem.public_id.asc())
        )
    )
    _validate_item_contract(batch, items)
    artifact = _build_code_csv(batch, items)
    if len(artifact.content) > CODE_CSV_MAX_ARTIFACT_BYTES:
        raise ConflictError("Export artifact exceeds the size limit", error_code="CODE_BATCH_EXPORT_TOO_LARGE")
    ciphertext, nonce, key_id = encrypt_bytes(
        artifact.content,
        aad=_artifact_aad(tenant_id, batch_id, artifact.manifest_version),
    )

    manifest = await log_export(
        db,
        tenant_id,
        account_id,
        "code_csv",
        resource_id=str(batch_id),
        file_name=f"codes-{batch_id}.csv",
        row_count=artifact.row_count,
        code_batch_id=batch_id,
        manifest_version=artifact.manifest_version,
        checksum_sha256=artifact.checksum_sha256,
        artifact_size_bytes=len(artifact.content),
        artifact_ciphertext=ciphertext,
        artifact_nonce=nonce,
        artifact_scheme=CODE_CSV_ARTIFACT_SCHEME,
        artifact_key_id=key_id,
    )
    batch.export_manifest_id = manifest.id
    batch.exported_at = utcnow()
    batch.contract_version = 1
    batch.status = CodeBatchStatus.exported
    await db.flush()
    return artifact
