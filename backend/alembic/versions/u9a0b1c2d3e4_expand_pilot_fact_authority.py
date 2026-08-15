"""expand pilot fact authority

Revision ID: u9a0b1c2d3e4
Revises: u8d4c5d6e7f8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u9a0b1c2d3e4"
down_revision: str | Sequence[str] | None = "u8d4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.add_column("pilot_milestones", sa.Column("fact_digest", sa.String(64), nullable=True))
    op.add_column("pilot_milestones", sa.Column("authority_version", sa.SmallInteger(), nullable=True))

    for column in (
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.String(20), nullable=True),
        sa.Column("actor_principal", sa.String(64), nullable=True),
        sa.Column("platform_auth_session_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("payload_digest", sa.String(64), nullable=True),
        sa.Column("authority_version", sa.SmallInteger(), nullable=True),
    ):
        op.add_column("pilot_milestone_corrections", column)

    for column in (
        sa.Column("version", sa.BigInteger(), nullable=True),
        sa.Column("snapshot_digest", sa.String(64), nullable=True),
        sa.Column("authority_version", sa.SmallInteger(), nullable=True),
        sa.Column("completed_actor_tenant_id", sa.Uuid(), nullable=True),
        sa.Column("completed_auth_session_id", sa.Uuid(), nullable=True),
        sa.Column("completed_agency_authorization_id", sa.Uuid(), nullable=True),
        sa.Column("completion_request_id", sa.Uuid(), nullable=True),
    ):
        op.add_column("retrospectives", column)

    op.create_unique_constraint(
        "uq_retrospectives_tenant_id_id_u09",
        "retrospectives",
        ["tenant_id", "id"],
    )
    op.create_table(
        "pilot_authority_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("resource_version", sa.BigInteger(), nullable=True),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_tenant_id", sa.Uuid(), nullable=True),
        sa.Column("actor_account_id", sa.Uuid(), nullable=True),
        sa.Column("auth_session_id", sa.Uuid(), nullable=True),
        sa.Column("platform_auth_session_id", sa.Uuid(), nullable=True),
        sa.Column("agency_authorization_id", sa.Uuid(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_pilot_receipts_tenant"),
        sa.PrimaryKeyConstraint("id", name="pk_pilot_authority_receipts"),
        sa.UniqueConstraint(
            "tenant_id", "operation", "idempotency_key", name="uq_pilot_receipts_tenant_operation_idem"
        ),
    )
    op.execute("REVOKE ALL PRIVILEGES ON public.pilot_authority_receipts FROM PUBLIC")
    op.execute("ALTER TABLE public.pilot_authority_receipts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.pilot_authority_receipts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON public.pilot_authority_receipts "
        "USING (tenant_id=public.current_tenant_id() OR (public.current_tenant_id() IS NULL "
        "AND current_setting('app.bypass_rls',true)='true' "
        "AND has_parameter_privilege(session_user,'app.bypass_rls','SET'))) "
        "WITH CHECK (tenant_id=public.current_tenant_id() OR (public.current_tenant_id() IS NULL "
        "AND current_setting('app.bypass_rls',true)='true' "
        "AND has_parameter_privilege(session_user,'app.bypass_rls','SET')))"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.drop_table("pilot_authority_receipts")
    op.drop_constraint("uq_retrospectives_tenant_id_id_u09", "retrospectives", type_="unique")
    for column in (
        "completion_request_id",
        "completed_agency_authorization_id",
        "completed_auth_session_id",
        "completed_actor_tenant_id",
        "authority_version",
        "snapshot_digest",
        "version",
    ):
        op.drop_column("retrospectives", column)
    for column in (
        "authority_version",
        "payload_digest",
        "idempotency_key",
        "platform_auth_session_id",
        "actor_principal",
        "actor_type",
        "request_id",
    ):
        op.drop_column("pilot_milestone_corrections", column)
    op.drop_column("pilot_milestones", "authority_version")
    op.drop_column("pilot_milestones", "fact_digest")
