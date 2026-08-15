"""expand launch release authority

Revision ID: u5b0c1d2e3f4
Revises: u6c4b5c6d7e8
Create Date: 2026-08-11
"""

from collections.abc import Sequence
import re

import sqlalchemy as sa

from alembic import op

revision: str = "u5b0c1d2e3f4"
down_revision: str | None = "u6c4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COMPAT_SQL = r"""
CREATE OR REPLACE FUNCTION public.bind_launch_release_actor_tenants()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $function$
DECLARE resolved uuid;
BEGIN
    IF TG_OP='INSERT' OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_by_tenant_id IS NULL THEN
        SELECT account.tenant_id INTO resolved FROM public.accounts AS account
        WHERE account.id=NEW.created_by;
        IF resolved IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch release creator is unavailable';
        END IF;
        NEW.created_by_tenant_id:=resolved;
    END IF;
    IF NEW.brand_confirmed_by IS NULL THEN
        NEW.brand_confirmed_by_tenant_id:=NULL;
    ELSIF TG_OP='INSERT' OR NEW.brand_confirmed_by IS DISTINCT FROM OLD.brand_confirmed_by
          OR NEW.brand_confirmed_by_tenant_id IS NULL THEN
        SELECT account.tenant_id INTO resolved FROM public.accounts AS account
        WHERE account.id=NEW.brand_confirmed_by;
        IF resolved IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch release confirmer is unavailable'; END IF;
        NEW.brand_confirmed_by_tenant_id:=resolved;
    END IF;
    IF NEW.launched_by IS NULL THEN
        NEW.launched_by_tenant_id:=NULL;
    ELSIF TG_OP='INSERT' OR NEW.launched_by IS DISTINCT FROM OLD.launched_by
          OR NEW.launched_by_tenant_id IS NULL THEN
        SELECT account.tenant_id INTO resolved FROM public.accounts AS account WHERE account.id=NEW.launched_by;
        IF resolved IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch release launcher is unavailable'; END IF;
        NEW.launched_by_tenant_id:=resolved;
    END IF;
    IF NEW.suspended_by IS NULL THEN
        NEW.suspended_by_tenant_id:=NULL;
    ELSIF TG_OP='INSERT' OR NEW.suspended_by IS DISTINCT FROM OLD.suspended_by
          OR NEW.suspended_by_tenant_id IS NULL THEN
        SELECT account.tenant_id INTO resolved FROM public.accounts AS account WHERE account.id=NEW.suspended_by;
        IF resolved IS NULL THEN RAISE EXCEPTION USING ERRCODE='23503', MESSAGE='launch release suspender is unavailable'; END IF;
        NEW.suspended_by_tenant_id:=resolved;
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.bind_launch_release_actor_tenants() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.bind_launch_release_actor_tenants() FROM yimatong_app;
DROP TRIGGER IF EXISTS trg_bind_launch_release_actor_tenants ON public.launch_releases;
CREATE TRIGGER trg_bind_launch_release_actor_tenants
BEFORE INSERT OR UPDATE OF created_by,brand_confirmed_by,launched_by,suspended_by
ON public.launch_releases FOR EACH ROW EXECUTE FUNCTION public.bind_launch_release_actor_tenants();
"""


def _runtime_role_exists() -> bool:
    return bool(op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')")).scalar_one())


def _execute_sql_batch(sql: str) -> None:
    """Execute a DDL batch without splitting dollar-quoted function bodies."""
    statements: list[str] = []
    start = index = 0
    quote: str | None = None
    while index < len(sql):
        if quote is not None:
            if sql.startswith(quote, index):
                index += len(quote)
                quote = None
            else:
                index += 1
            continue
        if sql[index] in ("'", '"'):
            quote = sql[index]
            index += 1
            continue
        if sql[index] == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[index:])
            if match:
                quote = match.group(0)
                index += len(quote)
                continue
        if sql[index] == ";":
            statement = sql[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1
        index += 1
    if trailing := sql[start:].strip():
        statements.append(trailing)
    for statement in statements:
        op.execute(statement)


def upgrade() -> None:
    op.add_column("launch_releases", sa.Column("readiness_manifest", sa.JSON(), nullable=True))
    op.add_column("launch_releases", sa.Column("readiness_scan_event_id", sa.Uuid(), nullable=True))
    op.add_column("launch_releases", sa.Column("readiness_code_item_id", sa.Uuid(), nullable=True))
    op.add_column("launch_releases", sa.Column("first_valid_scan_event_id", sa.Uuid(), nullable=True))
    op.add_column("launch_releases", sa.Column("first_valid_scan_time", sa.DateTime(timezone=True), nullable=True))
    for name in (
        "created_by_tenant_id",
        "brand_confirmed_by_tenant_id",
        "launched_by_tenant_id",
        "suspended_by_tenant_id",
    ):
        op.add_column("launch_releases", sa.Column(name, sa.Uuid(), nullable=True))
    op.add_column("launch_releases", sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("launch_releases", sa.Column("invalidation_reason", sa.String(100), nullable=True))
    if _runtime_role_exists():
        _execute_sql_batch(_COMPAT_SQL)
    else:
        _execute_sql_batch(
            _COMPAT_SQL.replace(
                "REVOKE ALL ON FUNCTION public.bind_launch_release_actor_tenants() FROM yimatong_app;", ""
            )
        )

    op.create_table(
        "launch_release_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("actor_tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("result_status", sa.String(30), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=False),
        sa.Column("replayed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_launch_release_actions_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_launch_release_actions_tenant_idempotency"),
        sa.CheckConstraint("action IN ('create','confirm','launch','suspend','resume','invalidate')", name="ck_launch_release_actions_action"),
    )
    op.create_index("ix_launch_release_actions_tenant_id", "launch_release_actions", ["tenant_id"])
    op.execute("ALTER TABLE public.launch_release_actions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.launch_release_actions FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON public.launch_release_actions
        USING (tenant_id=public.current_tenant_id() OR (public.current_tenant_id() IS NULL
          AND current_setting('app.bypass_rls',true)='true'
          AND has_parameter_privilege(session_user,'app.bypass_rls','SET')))
        WITH CHECK (tenant_id=public.current_tenant_id() OR (public.current_tenant_id() IS NULL
          AND current_setting('app.bypass_rls',true)='true'
          AND has_parameter_privilege(session_user,'app.bypass_rls','SET')))
    """)
    if _runtime_role_exists():
        op.execute("REVOKE ALL ON public.launch_release_actions FROM yimatong_app")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_bind_launch_release_actor_tenants ON public.launch_releases")
    op.execute("DROP FUNCTION IF EXISTS public.bind_launch_release_actor_tenants()")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.launch_release_actions")
    op.drop_index("ix_launch_release_actions_tenant_id", table_name="launch_release_actions")
    op.drop_table("launch_release_actions")
    for name in (
        "invalidation_reason", "invalidated_at", "suspended_by_tenant_id", "launched_by_tenant_id",
        "brand_confirmed_by_tenant_id", "created_by_tenant_id", "first_valid_scan_time",
        "first_valid_scan_event_id", "readiness_code_item_id", "readiness_scan_event_id", "readiness_manifest",
    ):
        op.drop_column("launch_releases", name)
