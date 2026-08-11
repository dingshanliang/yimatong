"""Attach takeover parent keys and unvalidated tenant contracts.

Revision ID: t3b2c1d5e6f7
Revises: t3a2d0e4f6a7
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import context, op
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

revision: str = "t3b2c1d5e6f7"
down_revision: str | None = "t3a2d0e4f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TAKEOVER_ROOT_REVISION = "s0b1c2d3e4f5"

_UNIQUE_CONSTRAINTS = (
    ("production_batches", "uq_production_batches_tenant_id_id"),
    ("takeover_projects", "uq_takeover_projects_tenant_id_id"),
    ("takeover_import_jobs", "uq_takeover_import_jobs_tenant_id_id"),
    ("takeover_import_jobs", "uq_takeover_import_jobs_tenant_project_id"),
    ("takeover_domain_checks", "uq_takeover_domain_checks_tenant_project_id"),
    ("takeover_route_versions", "uq_takeover_routes_tenant_project_id"),
    ("takeover_cutover_events", "uq_takeover_events_tenant_project_route_id"),
)

_FOREIGN_KEYS = (
    "fk_takeover_import_jobs_tenant_project",
    "fk_takeover_import_jobs_tenant_parent",
    "fk_takeover_import_errors_tenant_job",
    "fk_takeover_aliases_tenant_project",
    "fk_takeover_aliases_tenant_source_job",
    "fk_takeover_aliases_tenant_code_item",
    "fk_takeover_aliases_tenant_product",
    "fk_takeover_aliases_tenant_production_batch",
    "fk_takeover_domain_checks_tenant_project",
    "fk_takeover_routes_tenant_project",
    "fk_takeover_events_tenant_project",
    "fk_takeover_events_tenant_route",
    "fk_takeover_observations_tenant_project",
    "fk_takeover_observations_tenant_route",
    "fk_takeover_observations_tenant_event",
    "fk_takeover_routes_tenant_current_event",
    "fk_takeover_projects_tenant_active_route",
)


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


def _preflight() -> None:
    op.execute(
        """
        DO $block$
        DECLARE invalid_kinds text;
        BEGIN
            SELECT string_agg(kind, ', ' ORDER BY kind) INTO invalid_kinds
            FROM (
                SELECT 'import_job_project' AS kind WHERE EXISTS (
                    SELECT 1 FROM public.takeover_import_jobs child
                    LEFT JOIN public.takeover_projects parent
                      ON parent.tenant_id=child.tenant_id AND parent.id=child.project_id
                    WHERE parent.id IS NULL)
                UNION ALL SELECT 'import_job_parent' WHERE EXISTS (
                    SELECT 1 FROM public.takeover_import_jobs child
                    LEFT JOIN public.takeover_import_jobs parent
                      ON parent.tenant_id=child.tenant_id AND parent.project_id=child.project_id
                     AND parent.id=child.parent_job_id
                    WHERE child.parent_job_id IS NOT NULL AND parent.id IS NULL)
                UNION ALL SELECT 'import_error_job' WHERE EXISTS (
                    SELECT 1 FROM public.takeover_import_errors child
                    LEFT JOIN public.takeover_import_jobs parent
                      ON parent.tenant_id=child.tenant_id AND parent.id=child.job_id
                    WHERE parent.id IS NULL)
                UNION ALL SELECT 'alias_project' WHERE EXISTS (
                    SELECT 1 FROM public.takeover_aliases child
                    LEFT JOIN public.takeover_projects parent
                      ON parent.tenant_id=child.tenant_id AND parent.id=child.project_id
                    WHERE parent.id IS NULL)
                UNION ALL SELECT 'alias_source_job' WHERE EXISTS (
                    SELECT 1 FROM public.takeover_aliases child
                    LEFT JOIN public.takeover_import_jobs parent
                      ON parent.tenant_id=child.tenant_id AND parent.project_id=child.project_id
                     AND parent.id=child.source_job_id
                    WHERE child.source_job_id IS NOT NULL AND parent.id IS NULL)
                UNION ALL SELECT 'alias_code_item' WHERE EXISTS (
                    SELECT 1 FROM public.takeover_aliases child
                    LEFT JOIN public.code_items parent
                      ON parent.tenant_id=child.tenant_id AND parent.id=child.internal_code_id
                    WHERE child.internal_code_id IS NOT NULL AND parent.id IS NULL)
                UNION ALL SELECT 'alias_product' WHERE EXISTS (
                    SELECT 1 FROM public.takeover_aliases child
                    LEFT JOIN public.products parent
                      ON parent.tenant_id=child.tenant_id AND parent.id=child.product_id
                    WHERE child.product_id IS NOT NULL AND parent.id IS NULL)
                UNION ALL SELECT 'alias_production_batch' WHERE EXISTS (
                    SELECT 1 FROM public.takeover_aliases child
                    LEFT JOIN public.production_batches parent
                      ON parent.tenant_id=child.tenant_id AND parent.id=child.production_batch_id
                    WHERE child.production_batch_id IS NOT NULL AND parent.id IS NULL)
                UNION ALL SELECT 'domain_check_project' WHERE EXISTS (
                    SELECT 1 FROM public.takeover_domain_checks child
                    LEFT JOIN public.takeover_projects parent
                      ON parent.tenant_id=child.tenant_id AND parent.id=child.project_id
                    WHERE parent.id IS NULL)
                UNION ALL SELECT 'route_project' WHERE EXISTS (
                    SELECT 1 FROM public.takeover_route_versions child
                    LEFT JOIN public.takeover_projects parent
                      ON parent.tenant_id=child.tenant_id AND parent.id=child.project_id
                    WHERE parent.id IS NULL)
                UNION ALL SELECT 'event_project_route' WHERE EXISTS (
                    SELECT 1 FROM public.takeover_cutover_events child
                    LEFT JOIN public.takeover_projects project
                      ON project.tenant_id=child.tenant_id AND project.id=child.project_id
                    LEFT JOIN public.takeover_route_versions route
                      ON route.tenant_id=child.tenant_id AND route.project_id=child.project_id
                     AND route.id=child.route_version_id
                    WHERE project.id IS NULL OR (child.route_version_id IS NOT NULL AND route.id IS NULL))
                UNION ALL SELECT 'observation_project_route' WHERE EXISTS (
                    SELECT 1 FROM public.takeover_observations child
                    LEFT JOIN public.takeover_projects project
                      ON project.tenant_id=child.tenant_id AND project.id=child.project_id
                    LEFT JOIN public.takeover_route_versions route
                      ON route.tenant_id=child.tenant_id AND route.project_id=child.project_id
                     AND route.id=child.route_version_id
                    WHERE project.id IS NULL OR (child.route_version_id IS NOT NULL AND route.id IS NULL))
                UNION ALL SELECT 'project_active_route' WHERE EXISTS (
                    SELECT 1 FROM public.takeover_projects project
                    LEFT JOIN public.takeover_route_versions route
                      ON route.tenant_id=project.tenant_id AND route.project_id=project.id
                     AND route.id=project.active_route_version_id
                    WHERE project.active_route_version_id IS NOT NULL AND route.id IS NULL)
            ) AS invalid;
            IF invalid_kinds IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE='23514',
                    MESSAGE='takeover tenant-integrity preflight failed: ' || invalid_kinds;
            END IF;
        END
        $block$
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _preflight()
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute(
        "DO $block$ BEGIN IF EXISTS (SELECT 1 FROM public.takeover_projects "
        "WHERE domain_verification_token IS NULL) THEN RAISE EXCEPTION USING ERRCODE='23514', "
        "MESSAGE='takeover domain verification token backfill is incomplete'; END IF; END $block$"
    )
    op.execute("ALTER TABLE public.takeover_projects ALTER COLUMN domain_verification_token SET NOT NULL")
    for table, name in _UNIQUE_CONSTRAINTS:
        op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} UNIQUE USING INDEX {name}")

    statements = (
        "ALTER TABLE public.takeover_import_jobs ADD CONSTRAINT fk_takeover_import_jobs_tenant_project "
        "FOREIGN KEY (tenant_id,project_id) REFERENCES public.takeover_projects(tenant_id,id) NOT VALID",
        "ALTER TABLE public.takeover_import_jobs ADD CONSTRAINT fk_takeover_import_jobs_tenant_parent "
        "FOREIGN KEY (tenant_id,project_id,parent_job_id) "
        "REFERENCES public.takeover_import_jobs(tenant_id,project_id,id) NOT VALID",
        "ALTER TABLE public.takeover_import_errors ADD CONSTRAINT fk_takeover_import_errors_tenant_job "
        "FOREIGN KEY (tenant_id,job_id) REFERENCES public.takeover_import_jobs(tenant_id,id) NOT VALID",
        "ALTER TABLE public.takeover_aliases ADD CONSTRAINT fk_takeover_aliases_tenant_project "
        "FOREIGN KEY (tenant_id,project_id) REFERENCES public.takeover_projects(tenant_id,id) NOT VALID",
        "ALTER TABLE public.takeover_aliases ADD CONSTRAINT fk_takeover_aliases_tenant_source_job "
        "FOREIGN KEY (tenant_id,project_id,source_job_id) "
        "REFERENCES public.takeover_import_jobs(tenant_id,project_id,id) NOT VALID",
        "ALTER TABLE public.takeover_aliases ADD CONSTRAINT fk_takeover_aliases_tenant_code_item "
        "FOREIGN KEY (tenant_id,internal_code_id) REFERENCES public.code_items(tenant_id,id) NOT VALID",
        "ALTER TABLE public.takeover_aliases ADD CONSTRAINT fk_takeover_aliases_tenant_product "
        "FOREIGN KEY (tenant_id,product_id) REFERENCES public.products(tenant_id,id) NOT VALID",
        "ALTER TABLE public.takeover_aliases ADD CONSTRAINT fk_takeover_aliases_tenant_production_batch "
        "FOREIGN KEY (tenant_id,production_batch_id) REFERENCES public.production_batches(tenant_id,id) NOT VALID",
        "ALTER TABLE public.takeover_domain_checks ADD CONSTRAINT fk_takeover_domain_checks_tenant_project "
        "FOREIGN KEY (tenant_id,project_id) REFERENCES public.takeover_projects(tenant_id,id) NOT VALID",
        "ALTER TABLE public.takeover_route_versions ADD CONSTRAINT fk_takeover_routes_tenant_project "
        "FOREIGN KEY (tenant_id,project_id) REFERENCES public.takeover_projects(tenant_id,id) NOT VALID",
        "ALTER TABLE public.takeover_cutover_events ADD CONSTRAINT fk_takeover_events_tenant_project "
        "FOREIGN KEY (tenant_id,project_id) REFERENCES public.takeover_projects(tenant_id,id) NOT VALID",
        "ALTER TABLE public.takeover_cutover_events ADD CONSTRAINT fk_takeover_events_tenant_route "
        "FOREIGN KEY (tenant_id,project_id,route_version_id) "
        "REFERENCES public.takeover_route_versions(tenant_id,project_id,id) NOT VALID",
        "ALTER TABLE public.takeover_observations ADD CONSTRAINT fk_takeover_observations_tenant_project "
        "FOREIGN KEY (tenant_id,project_id) REFERENCES public.takeover_projects(tenant_id,id) NOT VALID",
        "ALTER TABLE public.takeover_observations ADD CONSTRAINT fk_takeover_observations_tenant_route "
        "FOREIGN KEY (tenant_id,project_id,route_version_id) "
        "REFERENCES public.takeover_route_versions(tenant_id,project_id,id) NOT VALID",
        "ALTER TABLE public.takeover_observations ADD CONSTRAINT fk_takeover_observations_tenant_event "
        "FOREIGN KEY (tenant_id,project_id,route_version_id,transition_event_id) "
        "REFERENCES public.takeover_cutover_events(tenant_id,project_id,route_version_id,id) NOT VALID",
        "ALTER TABLE public.takeover_route_versions ADD CONSTRAINT fk_takeover_routes_tenant_current_event "
        "FOREIGN KEY (tenant_id,project_id,id,current_event_id) "
        "REFERENCES public.takeover_cutover_events(tenant_id,project_id,route_version_id,id) NOT VALID",
        "ALTER TABLE public.takeover_projects ADD CONSTRAINT fk_takeover_projects_tenant_active_route "
        "FOREIGN KEY (tenant_id,id,active_route_version_id) "
        "REFERENCES public.takeover_route_versions(tenant_id,project_id,id) NOT VALID",
    )
    for statement in statements:
        op.execute(statement)

    op.execute(
        "ALTER TABLE public.takeover_import_jobs ADD CONSTRAINT ck_takeover_import_jobs_attempt_count "
        "CHECK (attempt_count >= 0 AND attempt_count <= 20) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.takeover_import_jobs ADD CONSTRAINT ck_takeover_import_jobs_claim_state "
        "CHECK ((claim_token IS NULL AND claimed_at IS NULL) OR "
        "(claim_token IS NOT NULL AND claimed_at IS NOT NULL AND status='processing')) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.takeover_observations ADD CONSTRAINT ck_takeover_observations_evidence_source "
        "CHECK (evidence_source IN ('legacy_client','server_probe')) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.takeover_observations ADD CONSTRAINT ck_takeover_observations_evidence_purpose "
        "CHECK (evidence_purpose IN ('legacy_client','pre_cutover','cutover','rollback')) NOT VALID"
    )
    digest_remainder = "evidence_digest"
    for character in "0123456789abcdef":
        digest_remainder = f"replace({digest_remainder}, '{character}', '')"
    op.execute(
        "ALTER TABLE public.takeover_observations ADD CONSTRAINT ck_takeover_observations_server_probe "
        "CHECK ((evidence_source='legacy_client' AND evidence_purpose='legacy_client') OR "
        "(transition_event_id IS NOT NULL "
        "AND observed_target_url IS NOT NULL AND length(evidence_digest)=64 "
        "AND evidence_purpose IN ('pre_cutover','cutover','rollback') "
        f"AND evidence_digest=lower(evidence_digest) AND {digest_remainder}='')) NOT VALID"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _protect_root_downgrade()
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    for table, name in (
        ("takeover_observations", "ck_takeover_observations_server_probe"),
        ("takeover_observations", "ck_takeover_observations_evidence_purpose"),
        ("takeover_observations", "ck_takeover_observations_evidence_source"),
        ("takeover_import_jobs", "ck_takeover_import_jobs_claim_state"),
        ("takeover_import_jobs", "ck_takeover_import_jobs_attempt_count"),
    ):
        op.drop_constraint(name, table, type_="check")
    foreign_key_tables = {
        "fk_takeover_import_jobs_tenant_project": "takeover_import_jobs",
        "fk_takeover_import_jobs_tenant_parent": "takeover_import_jobs",
        "fk_takeover_import_errors_tenant_job": "takeover_import_errors",
        "fk_takeover_aliases_tenant_project": "takeover_aliases",
        "fk_takeover_aliases_tenant_source_job": "takeover_aliases",
        "fk_takeover_aliases_tenant_code_item": "takeover_aliases",
        "fk_takeover_aliases_tenant_product": "takeover_aliases",
        "fk_takeover_aliases_tenant_production_batch": "takeover_aliases",
        "fk_takeover_domain_checks_tenant_project": "takeover_domain_checks",
        "fk_takeover_routes_tenant_project": "takeover_route_versions",
        "fk_takeover_events_tenant_project": "takeover_cutover_events",
        "fk_takeover_events_tenant_route": "takeover_cutover_events",
        "fk_takeover_observations_tenant_project": "takeover_observations",
        "fk_takeover_observations_tenant_route": "takeover_observations",
        "fk_takeover_observations_tenant_event": "takeover_observations",
        "fk_takeover_routes_tenant_current_event": "takeover_route_versions",
        "fk_takeover_projects_tenant_active_route": "takeover_projects",
    }
    for name in reversed(_FOREIGN_KEYS):
        op.drop_constraint(name, foreign_key_tables[name], type_="foreignkey")
    for table, name in reversed(_UNIQUE_CONSTRAINTS):
        op.drop_constraint(name, table, type_="unique")
    op.execute("ALTER TABLE public.takeover_projects ALTER COLUMN domain_verification_token DROP NOT NULL")
