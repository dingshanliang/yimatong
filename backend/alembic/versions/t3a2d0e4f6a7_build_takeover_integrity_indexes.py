"""Build takeover parent and durable-queue indexes without blocking writers.

Revision ID: t3a2d0e4f6a7
Revises: t3a1c0d4e5f6
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

revision: str = "t3a2d0e4f6a7"
down_revision: str | None = "t3a1c0d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TAKEOVER_ROOT_REVISION = "s0b1c2d3e4f5"

UNIQUE_INDEXES = {
    "uq_production_batches_tenant_id_id": (
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_production_batches_tenant_id_id "
        "ON public.production_batches (tenant_id,id)"
    ),
    "uq_takeover_projects_tenant_id_id": (
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_takeover_projects_tenant_id_id "
        "ON public.takeover_projects (tenant_id,id)"
    ),
    "uq_takeover_import_jobs_tenant_id_id": (
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_takeover_import_jobs_tenant_id_id "
        "ON public.takeover_import_jobs (tenant_id,id)"
    ),
    "uq_takeover_import_jobs_tenant_project_id": (
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_takeover_import_jobs_tenant_project_id "
        "ON public.takeover_import_jobs (tenant_id,project_id,id)"
    ),
    "uq_takeover_domain_checks_tenant_project_id": (
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_takeover_domain_checks_tenant_project_id "
        "ON public.takeover_domain_checks (tenant_id,project_id,id)"
    ),
    "uq_takeover_routes_tenant_project_id": (
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_takeover_routes_tenant_project_id "
        "ON public.takeover_route_versions (tenant_id,project_id,id)"
    ),
    "uq_takeover_events_tenant_project_route_id": (
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_takeover_events_tenant_project_route_id "
        "ON public.takeover_cutover_events (tenant_id,project_id,route_version_id,id)"
    ),
}

SUPPORTING_INDEXES = {
    "ix_takeover_import_jobs_pending_poll": (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_takeover_import_jobs_pending_poll "
        "ON public.takeover_import_jobs (tenant_id,next_attempt_at,created_at) "
        "WHERE status IN ('pending','failed')"
    ),
    "ix_takeover_import_jobs_stale_claim": (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_takeover_import_jobs_stale_claim "
        "ON public.takeover_import_jobs (claimed_at) WHERE status='processing'"
    ),
}

EXPECTED_INDEX_DEFINITIONS = {
    "uq_production_batches_tenant_id_id": (
        "CREATE UNIQUE INDEX uq_production_batches_tenant_id_id ON public.production_batches "
        "USING btree (tenant_id, id)"
    ),
    "uq_takeover_projects_tenant_id_id": (
        "CREATE UNIQUE INDEX uq_takeover_projects_tenant_id_id ON public.takeover_projects "
        "USING btree (tenant_id, id)"
    ),
    "uq_takeover_import_jobs_tenant_id_id": (
        "CREATE UNIQUE INDEX uq_takeover_import_jobs_tenant_id_id ON public.takeover_import_jobs "
        "USING btree (tenant_id, id)"
    ),
    "uq_takeover_import_jobs_tenant_project_id": (
        "CREATE UNIQUE INDEX uq_takeover_import_jobs_tenant_project_id ON public.takeover_import_jobs "
        "USING btree (tenant_id, project_id, id)"
    ),
    "uq_takeover_domain_checks_tenant_project_id": (
        "CREATE UNIQUE INDEX uq_takeover_domain_checks_tenant_project_id ON public.takeover_domain_checks "
        "USING btree (tenant_id, project_id, id)"
    ),
    "uq_takeover_routes_tenant_project_id": (
        "CREATE UNIQUE INDEX uq_takeover_routes_tenant_project_id ON public.takeover_route_versions "
        "USING btree (tenant_id, project_id, id)"
    ),
    "uq_takeover_events_tenant_project_route_id": (
        "CREATE UNIQUE INDEX uq_takeover_events_tenant_project_route_id ON public.takeover_cutover_events "
        "USING btree (tenant_id, project_id, route_version_id, id)"
    ),
    "ix_takeover_import_jobs_pending_poll": (
        "CREATE INDEX ix_takeover_import_jobs_pending_poll ON public.takeover_import_jobs "
        "USING btree (tenant_id, next_attempt_at, created_at) "
        "WHERE ((status)::text = ANY ((ARRAY['pending'::character varying, "
        "'failed'::character varying])::text[]))"
    ),
    "ix_takeover_import_jobs_stale_claim": (
        "CREATE INDEX ix_takeover_import_jobs_stale_claim ON public.takeover_import_jobs "
        "USING btree (claimed_at) WHERE ((status)::text = 'processing'::text)"
    ),
}


def _index_state(name: str) -> tuple[bool, str] | None:
    row = op.get_bind().execute(
        sa.text(
            "SELECT idx.indisvalid,pg_get_indexdef(idx.indexrelid) AS definition FROM pg_index AS idx "
            "WHERE idx.indexrelid=to_regclass('public.' || :name)"
        ),
        {"name": name},
    ).mappings().first()
    return None if row is None else (bool(row["indisvalid"]), str(row["definition"]))


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


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute("SET statement_timeout = '10min'")
        while True:
            result = op.get_bind().execute(
                sa.text(
                    "UPDATE public.takeover_projects SET domain_verification_token=gen_random_uuid()::text "
                    "WHERE ctid IN (SELECT ctid FROM public.takeover_projects "
                    "WHERE domain_verification_token IS NULL LIMIT 1000)"
                )
            )
            if result.rowcount == 0:
                break
        for name, create_sql in {**UNIQUE_INDEXES, **SUPPORTING_INDEXES}.items():
            state = _index_state(name)
            if state is not None and state[1] != EXPECTED_INDEX_DEFINITIONS[name]:
                raise RuntimeError(f"existing takeover index {name} has an unexpected definition")
            if state is not None and not state[0]:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
            op.execute(create_sql)
            final_state = _index_state(name)
            if final_state != (True, EXPECTED_INDEX_DEFINITIONS[name]):
                raise RuntimeError(f"takeover index {name} was not built with its authoritative definition")
        op.execute("RESET lock_timeout")
        op.execute("RESET statement_timeout")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _protect_root_downgrade()
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        for name in reversed((*UNIQUE_INDEXES, *SUPPORTING_INDEXES)):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
        op.execute("RESET lock_timeout")
