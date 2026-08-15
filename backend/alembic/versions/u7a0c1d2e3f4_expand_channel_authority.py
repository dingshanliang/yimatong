"""expand tenant-safe channel authority

Revision ID: u7a0c1d2e3f4
Revises: u6j0b1c2d3e4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "u7a0c1d2e3f4"
down_revision: str | Sequence[str] | None = "u6j0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _force_tenant_policy(table: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON public.{table}")
    op.execute(
        f"CREATE POLICY tenant_isolation ON public.{table} "
        "USING (tenant_id = public.current_tenant_id()) "
        "WITH CHECK (tenant_id = public.current_tenant_id())"
    )


def upgrade() -> None:
    # 0006 dropped these values without a backfill.  A child migration cannot
    # reconstruct executions which already happened.  If a pre-0006-shaped
    # database reaches this target, stop before any new DDL rather than delete
    # residual plaintext.
    op.execute(
        r"""
        DO $guard$
        DECLARE populated bigint;
        DECLARE kyc_relation regclass:=to_regclass('public.kyc_records');
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_attribute
            WHERE attrelid='public.distributors'::regclass AND attname='contact_phone' AND NOT attisdropped
          ) THEN
            EXECUTE 'SELECT count(*) FROM public.distributors WHERE contact_phone IS NOT NULL'
              INTO populated;
            IF populated > 0 THEN
              RAISE EXCEPTION USING ERRCODE='23514',
                MESSAGE='legacy distributors.contact_phone requires operator encryption recovery';
            END IF;
          END IF;
          IF kyc_relation IS NOT NULL THEN
            IF EXISTS (
              SELECT 1 FROM pg_attribute
              WHERE attrelid=kyc_relation AND attname='phone' AND NOT attisdropped
            ) THEN
              EXECUTE 'SELECT count(*) FROM public.kyc_records WHERE phone IS NOT NULL'
                INTO populated;
              IF populated > 0 THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                  MESSAGE='legacy kyc_records.phone requires operator encryption recovery';
              END IF;
            END IF;
          END IF;
        END
        $guard$
        """
    )
    op.create_table(
        "legacy_pii_recovery_markers",
        sa.Column("source_revision", sa.String(20), nullable=False),
        sa.Column("source_table", sa.String(63), nullable=False),
        sa.Column("source_column", sa.String(63), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("observed_rows", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("source_revision", "source_table", "source_column", name="pk_legacy_pii_recovery"),
        sa.CheckConstraint(
            "state IN ('encrypted_present','plaintext_absent','legacy_unknown','operator_recovered')",
            name="ck_legacy_pii_recovery_state",
        ),
    )
    op.execute("REVOKE ALL ON public.legacy_pii_recovery_markers FROM PUBLIC")
    op.execute(
        r"""
        INSERT INTO public.legacy_pii_recovery_markers
          (source_revision,source_table,source_column,state,observed_rows,note)
        SELECT '0006','distributors','contact_phone',
          CASE WHEN count(*) FILTER (WHERE contact_phone_encrypted IS NOT NULL
                                      OR contact_phone_hash IS NOT NULL)>0
               THEN 'encrypted_present' ELSE 'legacy_unknown' END,
          count(*),
          '0006 removed plaintext without a recoverable backfill; null rows cannot be classified as absent or lost'
        FROM public.distributors
        """
    )
    for column in ("real_name", "id_number", "phone"):
        op.execute(
            sa.text(
                "INSERT INTO public.legacy_pii_recovery_markers "
                "(source_revision,source_table,source_column,state,observed_rows,note) "
                "VALUES ('0006','kyc_records',:column,'legacy_unknown',NULL," 
                "'kyc_records was later removed; historic PII cannot be reconstructed from current catalog')"
            ).bindparams(column=column)
        )

    op.add_column(
        "distributors",
        sa.Column("contact_phone_recovery_state", sa.String(20), server_default="legacy_unknown", nullable=False),
    )
    op.execute(
        "UPDATE public.distributors SET contact_phone_recovery_state = CASE "
        "WHEN contact_phone_encrypted IS NOT NULL OR contact_phone_hash IS NOT NULL THEN 'encrypted' "
        "ELSE 'legacy_unknown' END"
    )
    for table in ("distributors", "regions", "stores"):
        op.add_column(table, sa.Column("version", sa.BigInteger(), server_default="1", nullable=False))

    op.add_column("account_channel_scopes", sa.Column("target_id", sa.Uuid(), nullable=True))
    op.add_column("account_channel_scopes", sa.Column("version", sa.BigInteger(), server_default="1", nullable=False))

    op.add_column("code_allocations", sa.Column("allocation_root_id", sa.Uuid(), nullable=True))
    op.add_column("code_allocations", sa.Column("target_type", sa.String(20), nullable=True))
    op.add_column("code_allocations", sa.Column("target_id", sa.Uuid(), nullable=True))
    op.add_column("code_allocations", sa.Column("action", sa.String(20), server_default="allocate", nullable=False))
    op.add_column("code_allocations", sa.Column("status", sa.String(20), server_default="active", nullable=False))
    op.add_column("code_allocations", sa.Column("actor_id", sa.Uuid(), nullable=True))
    op.add_column("code_allocations", sa.Column("actor_tenant_id", sa.Uuid(), nullable=True))
    op.add_column("code_allocations", sa.Column("audit_id", sa.Uuid(), nullable=True))

    op.create_table(
        "channel_action_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(30), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("resource_version", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("actor_tenant_id", sa.Uuid(), nullable=False),
        sa.Column("audit_id", sa.Uuid(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_channel_action_receipts"),
    )
    op.create_index("ix_channel_action_receipts_tenant_id", "channel_action_receipts", ["tenant_id"])
    op.create_index(
        "ix_channel_action_receipts_resource",
        "channel_action_receipts",
        ["tenant_id", "resource_type", "resource_id"],
    )
    for table in ("distributors", "regions", "stores", "account_channel_scopes", "code_allocations", "channel_action_receipts"):
        _force_tenant_policy(table)


def downgrade() -> None:
    bind = op.get_bind()
    facts = bind.execute(sa.text("SELECT count(*) FROM public.channel_action_receipts")).scalar_one()
    if facts:
        raise RuntimeError("u7a0 downgrade blocked: channel action receipts are immutable facts")
    unresolved_pii = bind.execute(
        sa.text(
            "SELECT count(*) FROM public.legacy_pii_recovery_markers "
            "WHERE state NOT IN ('plaintext_absent','operator_recovered')"
        )
    ).scalar_one()
    if unresolved_pii:
        raise RuntimeError(
            "u7a0 downgrade blocked: legacy PII recovery markers remain unresolved"
        )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.channel_action_receipts")
    op.drop_index("ix_channel_action_receipts_resource", table_name="channel_action_receipts")
    op.drop_index("ix_channel_action_receipts_tenant_id", table_name="channel_action_receipts")
    op.drop_table("channel_action_receipts")
    for column in ("audit_id", "actor_tenant_id", "actor_id", "status", "action", "target_id", "target_type", "allocation_root_id"):
        op.drop_column("code_allocations", column)
    op.drop_column("account_channel_scopes", "version")
    op.drop_column("account_channel_scopes", "target_id")
    for table in ("stores", "regions", "distributors"):
        op.drop_column(table, "version")
    op.drop_column("distributors", "contact_phone_recovery_state")
    op.drop_table("legacy_pii_recovery_markers")
