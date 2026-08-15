"""backfill honest legacy export ledger metadata

Revision ID: u8c1a2b3c4d5
Revises: u8c0f1a2b3c4
"""

from collections.abc import Sequence

from alembic import op

revision: str = "u8c1a2b3c4d5"
down_revision: str | Sequence[str] | None = "u8c0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BACKFILL = r"""
UPDATE public.export_logs AS export
SET reason='legacy export record; original reason unavailable',
    scope_snapshot='{}'::jsonb,
    idempotency_key='legacy:'||export.id::text,
    payload_digest=encode(digest(convert_to(jsonb_build_array(
      export.tenant_id,export.account_id,export.auth_session_id,export.export_type,export.resource_id,
      export.file_name,CASE
        WHEN export.file_name ILIKE '%.xlsx' THEN 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        WHEN export.file_name ILIKE '%.csv' OR export.export_type='code_csv' THEN 'text/csv; charset=utf-8'
        ELSE 'application/octet-stream' END,
      export.row_count,export.status,
      'legacy export record; original reason unavailable','{}'::jsonb,0,
      export.code_batch_id,export.manifest_version,export.checksum_sha256,export.artifact_size_bytes,
      CASE WHEN export.artifact_ciphertext IS NULL THEN NULL
           ELSE encode(digest(export.artifact_ciphertext,'sha256'),'hex') END,
      CASE WHEN export.artifact_nonce IS NULL THEN NULL ELSE encode(export.artifact_nonce,'hex') END,
      export.artifact_scheme,export.artifact_key_id,export.created_at
    )::text,'UTF8'),'sha256'),'hex'),
    content_type=CASE
      WHEN export.file_name ILIKE '%.xlsx' THEN 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
      WHEN export.file_name ILIKE '%.csv' OR export.export_type='code_csv' THEN 'text/csv; charset=utf-8'
      ELSE 'application/octet-stream' END,
    authority_version=0
WHERE export.authority_version IS NULL
"""


def upgrade() -> None:
    # This data-only revision takes row locks, not a schema lock. Concurrent old
    # writers remain valid; the bind revision catches their bounded delta while
    # holding the explicit cutover lock.
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("SET LOCAL search_path=public,pg_catalog")
    op.execute(_BACKFILL)


def downgrade() -> None:
    op.execute(
        """UPDATE public.export_logs SET auth_session_id=NULL,reason=NULL,scope_snapshot=NULL,
        idempotency_key=NULL,payload_digest=NULL,content_type=NULL,authority_version=NULL
        WHERE authority_version=0"""
    )
