"""add tenant-scoped legacy-code takeover control plane

Revision ID: s0b1c2d3e4f5
Revises: r9a0b1c2d3e4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "s0b1c2d3e4f5"
down_revision: str | None = "r9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = (
    "takeover_projects",
    "takeover_import_jobs",
    "takeover_import_errors",
    "takeover_aliases",
    "takeover_domain_checks",
    "takeover_route_versions",
    "takeover_cutover_events",
    "takeover_observations",
)


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {table}
        USING (
            tenant_id = current_tenant_id()
            OR (
                current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
            )
        )
        WITH CHECK (
            tenant_id = current_tenant_id()
            OR (
                current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
            )
        )
        """
    )


def upgrade() -> None:
    op.create_table(
        "takeover_projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source_system", sa.String(length=100), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("source_domain", sa.String(length=253), nullable=True),
        sa.Column("consumer_domain", sa.String(length=253), nullable=True),
        sa.Column("expected_cname", sa.String(length=253), nullable=False),
        sa.Column("sample_url", sa.Text(), nullable=False),
        sa.Column("url_rule", sa.JSON(), nullable=False),
        sa.Column("code_scope", sa.JSON(), nullable=False),
        sa.Column("control_facts", sa.JSON(), nullable=False),
        sa.Column("responsible_person", sa.String(length=100), nullable=False),
        sa.Column("technical_owner", sa.String(length=100), nullable=True),
        sa.Column("rollback_contact", sa.String(length=100), nullable=False),
        sa.Column("fallback_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("assessment", sa.JSON(), nullable=False),
        sa.Column("readiness_snapshot", sa.JSON(), nullable=False),
        sa.Column("readiness_digest", sa.String(length=64), nullable=True),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("brand_confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("brand_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("brand_confirmation_digest", sa.String(length=64), nullable=True),
        sa.Column("active_route_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["brand_confirmed_by"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_takeover_projects_tenant_id", "takeover_projects", ["tenant_id"], unique=False)
    op.create_index("ix_takeover_projects_tenant_status", "takeover_projects", ["tenant_id", "status"], unique=False)

    op.create_table(
        "takeover_import_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("source_rows", sa.JSON(), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("parent_job_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["takeover_projects.id"]),
        sa.ForeignKeyConstraint(["parent_job_id"], ["takeover_import_jobs.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "file_sha256", name="uq_takeover_import_project_file"),
    )
    op.create_index("ix_takeover_import_jobs_tenant_id", "takeover_import_jobs", ["tenant_id"], unique=False)
    op.create_index("ix_takeover_import_jobs_project_id", "takeover_import_jobs", ["project_id"], unique=False)
    op.create_index(
        "ix_takeover_import_jobs_tenant_status", "takeover_import_jobs", ["tenant_id", "status"], unique=False
    )

    op.create_table(
        "takeover_import_errors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("legacy_code", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("raw_row", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["takeover_import_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_takeover_import_errors_tenant_id", "takeover_import_errors", ["tenant_id"], unique=False)
    op.create_index("ix_takeover_import_errors_job_id", "takeover_import_errors", ["job_id"], unique=False)

    op.create_table(
        "takeover_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_job_id", sa.Uuid(), nullable=True),
        sa.Column("legacy_code", sa.String(length=255), nullable=False),
        sa.Column("normalized_code", sa.String(length=255), nullable=False),
        sa.Column("alias_type", sa.String(length=20), nullable=False),
        sa.Column("internal_code_id", sa.Uuid(), nullable=True),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("production_batch_id", sa.Uuid(), nullable=True),
        sa.Column("channel_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("disabled_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["takeover_projects.id"]),
        sa.ForeignKeyConstraint(["source_job_id"], ["takeover_import_jobs.id"]),
        sa.ForeignKeyConstraint(["internal_code_id"], ["code_items.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["production_batch_id"], ["production_batches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "normalized_code", name="uq_takeover_alias_project_code"),
    )
    op.create_index("ix_takeover_aliases_tenant_id", "takeover_aliases", ["tenant_id"], unique=False)
    op.create_index("ix_takeover_aliases_project_id", "takeover_aliases", ["project_id"], unique=False)
    op.create_index("ix_takeover_aliases_tenant_code", "takeover_aliases", ["tenant_id", "normalized_code"], unique=False)

    op.create_table(
        "takeover_domain_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("expected_cname", sa.String(length=253), nullable=False),
        sa.Column("observed_cnames", sa.JSON(), nullable=False),
        sa.Column("observed_ips", sa.JSON(), nullable=False),
        sa.Column("ttl", sa.Integer(), nullable=True),
        sa.Column("tls_status", sa.String(length=30), nullable=False),
        sa.Column("certificate_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("raw_observation", sa.JSON(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["takeover_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_takeover_domain_checks_tenant_id", "takeover_domain_checks", ["tenant_id"], unique=False)
    op.create_index("ix_takeover_domain_checks_project_id", "takeover_domain_checks", ["project_id"], unique=False)

    op.create_table(
        "takeover_route_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("extraction_rule", sa.JSON(), nullable=False),
        sa.Column("sample_codes", sa.JSON(), nullable=False),
        sa.Column("code_prefix", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("readiness_snapshot", sa.JSON(), nullable=False),
        sa.Column("brand_confirmation_digest", sa.String(length=64), nullable=True),
        sa.Column("executed_by", sa.Uuid(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rollback_reason", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["takeover_projects.id"]),
        sa.ForeignKeyConstraint(["executed_by"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "version", name="uq_takeover_route_project_version"),
    )
    op.create_index("ix_takeover_route_versions_tenant_id", "takeover_route_versions", ["tenant_id"], unique=False)
    op.create_index("ix_takeover_route_versions_project_id", "takeover_route_versions", ["project_id"], unique=False)
    op.create_index(
        "ix_takeover_route_tenant_status", "takeover_route_versions", ["tenant_id", "status"], unique=False
    )

    op.create_table(
        "takeover_cutover_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("route_version_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["takeover_projects.id"]),
        sa.ForeignKeyConstraint(["route_version_id"], ["takeover_route_versions.id"]),
        sa.ForeignKeyConstraint(["actor_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_takeover_event_idempotency"),
    )
    op.create_index("ix_takeover_cutover_events_tenant_id", "takeover_cutover_events", ["tenant_id"], unique=False)
    op.create_index("ix_takeover_cutover_events_project_id", "takeover_cutover_events", ["project_id"], unique=False)
    op.create_index(
        "ix_takeover_events_project_created", "takeover_cutover_events", ["project_id", "created_at"], unique=False
    )

    op.create_table(
        "takeover_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("route_version_id", sa.Uuid(), nullable=True),
        sa.Column("checked_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("success_rate", sa.Float(), nullable=False),
        sa.Column("error_rate", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("h5_reach_rate", sa.Float(), nullable=False),
        sa.Column("target_match", sa.Boolean(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("recommendation", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["takeover_projects.id"]),
        sa.ForeignKeyConstraint(["route_version_id"], ["takeover_route_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_takeover_observations_tenant_id", "takeover_observations", ["tenant_id"], unique=False)
    op.create_index("ix_takeover_observations_project_id", "takeover_observations", ["project_id"], unique=False)

    for table in TABLES:
        _tenant_rls(table)


def downgrade() -> None:
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
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table)
