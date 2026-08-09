"""Add transactionally reserved tenant quota usage.

Revision ID: 263a267db08c
Revises: b9e2c3d4f5a6
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "263a267db08c"
down_revision: str | None = "b9e2c3d4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tenant_quota_usage"
_RUNTIME_ROLE = "yimatong_app"


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": _RUNTIME_ROLE})
        .scalar_one()
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
        op.execute("SET LOCAL search_path = public, pg_catalog")

    op.create_table(
        _TABLE,
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("codes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("scans", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("campaigns", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("products", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("accounts", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("accounts >= 0", name="ck_tenant_quota_usage_accounts_nonnegative"),
        sa.CheckConstraint("campaigns >= 0", name="ck_tenant_quota_usage_campaigns_nonnegative"),
        sa.CheckConstraint("codes >= 0", name="ck_tenant_quota_usage_codes_nonnegative"),
        sa.CheckConstraint("products >= 0", name="ck_tenant_quota_usage_products_nonnegative"),
        sa.CheckConstraint("scans >= 0", name="ck_tenant_quota_usage_scans_nonnegative"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )

    if bind.dialect.name != "postgresql":
        return

    # Canonicalize the historical feature alias without overwriting tenants
    # that already carry an explicit channel_portal decision.
    op.execute(
        """
        UPDATE public.tenants
        SET enabled_features = (
            jsonb_set(
                COALESCE(enabled_features::jsonb, '{}'::jsonb) - 'channel_store',
                '{channel_portal}',
                COALESCE(enabled_features::jsonb -> 'channel_portal', enabled_features::jsonb -> 'channel_store'),
                true
            )
        )::json
        WHERE enabled_features::jsonb ? 'channel_store'
        """
    )

    # One pass over each authoritative relation. Existing tenants start from
    # their committed rows; subsequent reservations stay constant-time.
    op.execute(
        """
        WITH code_totals AS (
            SELECT tenant_id, count(*)::bigint AS value FROM public.code_items GROUP BY tenant_id
        ), scan_totals AS (
            SELECT tenant_id, count(*)::bigint AS value FROM public.scan_events GROUP BY tenant_id
        ), campaign_totals AS (
            SELECT tenant_id, count(*)::bigint AS value FROM public.campaigns GROUP BY tenant_id
        ), product_totals AS (
            SELECT tenant_id, count(*)::bigint AS value FROM public.products GROUP BY tenant_id
        ), account_totals AS (
            SELECT tenant_id, count(*)::bigint AS value FROM public.accounts GROUP BY tenant_id
        )
        INSERT INTO public.tenant_quota_usage (tenant_id, codes, scans, campaigns, products, accounts)
        SELECT
            tenants.id,
            COALESCE(code_totals.value, 0),
            COALESCE(scan_totals.value, 0),
            COALESCE(campaign_totals.value, 0),
            COALESCE(product_totals.value, 0),
            COALESCE(account_totals.value, 0)
        FROM public.tenants
        LEFT JOIN code_totals ON code_totals.tenant_id = tenants.id
        LEFT JOIN scan_totals ON scan_totals.tenant_id = tenants.id
        LEFT JOIN campaign_totals ON campaign_totals.tenant_id = tenants.id
        LEFT JOIN product_totals ON product_totals.tenant_id = tenants.id
        LEFT JOIN account_totals ON account_totals.tenant_id = tenants.id
        """
    )

    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{_TABLE} FROM PUBLIC")
    op.execute(f"ALTER TABLE public.{_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON public.{_TABLE}
        USING (
            tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
                AND has_parameter_privilege(session_user, 'app.bypass_rls', 'SET')
            )
        )
        WITH CHECK (
            tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
                AND has_parameter_privilege(session_user, 'app.bypass_rls', 'SET')
            )
        )
        """
    )
    if _runtime_role_exists():
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{_TABLE} TO "{_RUNTIME_ROLE}"')


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
        op.execute("SET LOCAL search_path = public, pg_catalog")
        op.execute(f"LOCK TABLE public.{_TABLE} IN ACCESS EXCLUSIVE MODE")
        if _runtime_role_exists():
            op.execute(f'REVOKE ALL PRIVILEGES ON TABLE public.{_TABLE} FROM "{_RUNTIME_ROLE}"')
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON public.{_TABLE}")
        op.execute(f"ALTER TABLE public.{_TABLE} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{_TABLE} DISABLE ROW LEVEL SECURITY")
        # The pre-upgrade provenance is no longer distinguishable when both
        # aliases existed. Restore the legacy read alias without deleting the
        # canonical key, which is the lossless best-effort downgrade.
        op.execute(
            """
            UPDATE public.tenants
            SET enabled_features = jsonb_set(
                COALESCE(enabled_features::jsonb, '{}'::jsonb),
                '{channel_store}',
                enabled_features::jsonb -> 'channel_portal',
                true
            )::json
            WHERE enabled_features::jsonb ? 'channel_portal'
              AND NOT (enabled_features::jsonb ? 'channel_store')
            """
        )
    # Counters are derived state and will be rebuilt from authoritative rows on
    # the next upgrade; no business resource rows are removed by downgrade.
    op.drop_table(_TABLE)
