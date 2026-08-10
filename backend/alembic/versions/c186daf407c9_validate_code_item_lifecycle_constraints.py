"""Validate lifecycle constraints after the online backfill.

Revision ID: c186daf407c9
Revises: b075c9e3f6b8
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c186daf407c9"
down_revision: str | None = "b075c9e3f6b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_SQL = (
    "(status = 'frozen' AND frozen_from_status IS NOT NULL "
    "AND frozen_from_status IN ('activated', 'bound') AND frozen_at IS NOT NULL "
    "AND freeze_provenance_version IS NOT NULL AND freeze_provenance_version IN (0, 1) "
    "AND ((freeze_provenance_version = 0 AND frozen_by IS NULL AND freeze_reason IS NULL) OR "
    "(freeze_provenance_version = 1 AND NULLIF(trim(frozen_by), '') IS NOT NULL "
    "AND NULLIF(trim(freeze_reason), '') IS NOT NULL))) OR "
    "(status <> 'frozen' AND frozen_from_status IS NULL AND frozen_at IS NULL "
    "AND frozen_by IS NULL AND freeze_reason IS NULL AND freeze_provenance_version IS NULL)"
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '10min'")
    op.execute("ALTER TABLE public.code_items VALIDATE CONSTRAINT ck_code_items_frozen_provenance")
    op.execute(
        "ALTER TABLE public.interception_records "
        "VALIDATE CONSTRAINT fk_interception_records_tenant_code_item"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.drop_constraint("fk_interception_records_tenant_code_item", "interception_records", type_="foreignkey")
    op.drop_constraint("ck_code_items_frozen_provenance", "code_items", type_="check")
    op.execute(
        "ALTER TABLE public.interception_records "
        "ADD CONSTRAINT fk_interception_records_tenant_code_item "
        "FOREIGN KEY (tenant_id,code_item_id) REFERENCES public.code_items(tenant_id,id) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.code_items ADD CONSTRAINT ck_code_items_frozen_provenance "
        f"CHECK ({_CHECK_SQL}) NOT VALID"
    )
