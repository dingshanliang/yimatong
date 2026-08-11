"""Expand nullable takeover integrity metadata transactionally.

Revision ID: t3a1c0d4e5f6
Revises: d297eb0518da
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

revision: str = "t3a1c0d4e5f6"
down_revision: str | None = "d297eb0518da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TAKEOVER_ROOT_REVISION = "s0b1c2d3e4f5"


def _protect_root_downgrade() -> None:
    destination = context.get_revision_argument()
    if isinstance(destination, tuple):
        raise RuntimeError("takeover downgrade requires a single linear destination")
    below_root = destination is None
    if destination is not None:
        try:
            below_root = bool(
                tuple(ScriptDirectory.from_config(context.config).iterate_revisions(_TAKEOVER_ROOT_REVISION, destination))
            )
        except RangeNotAncestorError:
            below_root = False
    if below_root:
        op.execute(
            """
            DO $block$
            BEGIN
                IF EXISTS (SELECT 1 FROM public.takeover_projects)
                   OR EXISTS (SELECT 1 FROM public.takeover_import_jobs)
                   OR EXISTS (SELECT 1 FROM public.takeover_import_errors)
                   OR EXISTS (SELECT 1 FROM public.takeover_aliases)
                   OR EXISTS (SELECT 1 FROM public.takeover_domain_checks)
                   OR EXISTS (SELECT 1 FROM public.takeover_route_versions)
                   OR EXISTS (SELECT 1 FROM public.takeover_cutover_events)
                   OR EXISTS (SELECT 1 FROM public.takeover_observations) THEN
                    RAISE EXCEPTION USING ERRCODE='55000',
                        MESSAGE='takeover control-plane downgrade blocked: durable takeover data exists';
                END IF;
            END
            $block$
            """
        )


def _protect_domain_authority_downgrade() -> None:
    op.execute(
        """
        DO $block$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.takeover_projects WHERE domain_verification_token IS NOT NULL)
               OR EXISTS (
                   SELECT 1 FROM public.takeover_domain_checks
                   WHERE ownership_verified OR observed_ownership_tokens::jsonb <> '[]'::jsonb
               ) THEN
                RAISE EXCEPTION USING ERRCODE='55000',
                    MESSAGE='takeover domain ownership downgrade blocked: verification facts exist';
            END IF;
        END
        $block$
        """
    )

def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.add_column(
        "takeover_import_jobs",
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("takeover_import_jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("takeover_import_jobs", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("takeover_import_jobs", sa.Column("claim_token", sa.Uuid(), nullable=True))
    op.add_column("takeover_import_jobs", sa.Column("last_error_code", sa.String(length=50), nullable=True))
    op.add_column("takeover_route_versions", sa.Column("current_event_id", sa.Uuid(), nullable=True))
    op.add_column(
        "takeover_projects",
        sa.Column("domain_verification_token", sa.String(length=36), nullable=True),
    )
    op.execute(
        """
        CREATE FUNCTION public.populate_takeover_domain_verification_token() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public
        AS $function$
        BEGIN
            IF NEW.domain_verification_token IS NULL THEN
                NEW.domain_verification_token:=gen_random_uuid()::text;
            END IF;
            RETURN NEW;
        END;
        $function$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_populate_takeover_domain_verification_token "
        "BEFORE INSERT OR UPDATE OF domain_verification_token ON public.takeover_projects "
        "FOR EACH ROW EXECUTE FUNCTION public.populate_takeover_domain_verification_token()"
    )
    op.execute("REVOKE ALL ON FUNCTION public.populate_takeover_domain_verification_token() FROM PUBLIC")
    op.add_column(
        "takeover_domain_checks",
        sa.Column("observed_ownership_tokens", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
    )
    op.add_column(
        "takeover_domain_checks",
        sa.Column("ownership_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("takeover_observations", sa.Column("transition_event_id", sa.Uuid(), nullable=True))
    op.add_column("takeover_observations", sa.Column("observed_target_url", sa.Text(), nullable=True))
    op.add_column(
        "takeover_observations",
        sa.Column("evidence_source", sa.String(length=30), server_default=sa.text("'legacy_client'"), nullable=False),
    )
    op.add_column(
        "takeover_observations",
        sa.Column("evidence_purpose", sa.String(length=30), server_default=sa.text("'legacy_client'"), nullable=False),
    )
    op.add_column("takeover_observations", sa.Column("evidence_digest", sa.String(length=64), nullable=True))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _protect_domain_authority_downgrade()
    _protect_root_downgrade()
    op.execute("SET LOCAL lock_timeout = '5s'")
    for column in (
        "evidence_digest",
        "evidence_purpose",
        "evidence_source",
        "observed_target_url",
        "transition_event_id",
    ):
        op.drop_column("takeover_observations", column)
    op.drop_column("takeover_route_versions", "current_event_id")
    op.drop_column("takeover_domain_checks", "ownership_verified")
    op.drop_column("takeover_domain_checks", "observed_ownership_tokens")
    op.execute("DROP TRIGGER trg_populate_takeover_domain_verification_token ON public.takeover_projects")
    op.execute("DROP FUNCTION public.populate_takeover_domain_verification_token()")
    op.drop_column("takeover_projects", "domain_verification_token")
    for column in ("last_error_code", "claim_token", "claimed_at", "next_attempt_at", "attempt_count"):
        op.drop_column("takeover_import_jobs", column)
