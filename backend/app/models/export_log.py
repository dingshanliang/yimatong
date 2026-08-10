"""导出审计日志模型"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.models.base import Base


def _contains_only(column: str, allowed: str) -> str:
    """Build a PostgreSQL/SQLite-compatible exact character whitelist."""

    remainder = column
    for character in allowed:
        remainder = f"replace({remainder}, '{character}', '')"
    return f"{remainder} = ''"


class ExportLog(Base):
    __tablename__ = "export_logs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_export_logs_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "code_batch_id"],
            ["code_batches.tenant_id", "code_batches.id"],
            name="fk_export_logs_tenant_code_batch",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_export_logs_tenant_id_id"),
        CheckConstraint(
            "(manifest_version IS NULL AND artifact_ciphertext IS NULL AND artifact_nonce IS NULL "
            "AND artifact_scheme IS NULL AND artifact_key_id IS NULL) OR "
            "(manifest_version > 0 AND row_count BETWEEN 1 AND 10000 "
            "AND length(checksum_sha256) = 64 AND checksum_sha256 = lower(checksum_sha256) AND "
            + _contains_only("checksum_sha256", "0123456789abcdef")
            + " "
            "AND artifact_size_bytes BETWEEN 1 AND 16777216 "
            "AND artifact_ciphertext IS NOT NULL "
            "AND length(artifact_ciphertext) = artifact_size_bytes + 16 "
            "AND artifact_nonce IS NOT NULL AND length(artifact_nonce) = 12 "
            "AND artifact_scheme = 'aes-256-gcm-v1' "
            "AND length(artifact_key_id) BETWEEN 1 AND 64 AND "
            + _contains_only(
                "artifact_key_id",
                "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ._:-",
            )
            + ")",
            name="ck_export_logs_manifest_shape",
        ),
        CheckConstraint(
            "export_type <> 'code_csv' OR manifest_version IS NULL OR "
            "(code_batch_id IS NOT NULL AND resource_id = code_batch_id AND status = 'completed')",
            name="ck_export_logs_code_csv_manifest",
        ),
        Index(
            "uq_export_logs_tenant_code_batch_version",
            "tenant_id",
            "code_batch_id",
            "manifest_version",
            unique=True,
            postgresql_where=text("code_batch_id IS NOT NULL AND manifest_version IS NOT NULL"),
            sqlite_where=text("code_batch_id IS NOT NULL AND manifest_version IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    export_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    row_count: Mapped[int] = mapped_column(default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    code_batch_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    manifest_version: Mapped[int | None] = mapped_column(nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    artifact_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    artifact_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    artifact_scheme: Mapped[str | None] = mapped_column(String(32), nullable=True)
    artifact_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
