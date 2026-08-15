"""validate and finalize export ledger authority

Revision ID: u8c4d5e6f7a8
Revises: u8c3c4d5e6f7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u8c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "u8c3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REQUIRED_CHECK = "ck_export_logs_required_u8c_stage"
_REQUIRED_COLUMNS = (
    "reason",
    "scope_snapshot",
    "idempotency_key",
    "payload_digest",
    "content_type",
    "authority_version",
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute("SET LOCAL search_path=public,pg_catalog")
    # VALIDATE uses a lower lock level and remains compatible with inserts made
    # through record_prepared_export. The validated temporary check lets the
    # following SET NOT NULL avoid a second table scan.
    for constraint in (
        _REQUIRED_CHECK,
        "ck_export_logs_authority_version_u8c",
        "ck_export_logs_authoritative_shape_u8c",
        "fk_export_logs_tenant_account_u8c",
        "fk_export_logs_tenant_auth_session_u8c",
    ):
        op.execute(f"ALTER TABLE public.export_logs VALIDATE CONSTRAINT {constraint}")
    for column in _REQUIRED_COLUMNS:
        op.alter_column("export_logs", column, nullable=False)
    op.execute(f"ALTER TABLE public.export_logs DROP CONSTRAINT {_REQUIRED_CHECK}")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    facts = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM public.export_logs WHERE authority_version IN (1,2)"))
        .scalar_one()
    )
    if facts:
        raise RuntimeError("u8c4 downgrade blocked: immutable prepared export facts exist")
    op.execute(
        f"""ALTER TABLE public.export_logs ADD CONSTRAINT {_REQUIRED_CHECK} CHECK (
        reason IS NOT NULL AND scope_snapshot IS NOT NULL AND idempotency_key IS NOT NULL
        AND payload_digest IS NOT NULL AND content_type IS NOT NULL AND authority_version IS NOT NULL
        ) NOT VALID"""
    )
    for column in reversed(_REQUIRED_COLUMNS):
        op.alter_column("export_logs", column, nullable=True)
