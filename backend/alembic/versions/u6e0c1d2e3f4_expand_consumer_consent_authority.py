"""expand consumer consent authority

Revision ID: u6e0c1d2e3f4
Revises: u5b5b6c7d8e9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u6e0c1d2e3f4"
down_revision: str | None = "u5b5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_policy(table: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON public.{table} "
        "USING (tenant_id=public.current_tenant_id()) "
        "WITH CHECK (tenant_id=public.current_tenant_id())"
    )


def upgrade() -> None:
    op.create_table(
        "consumer_consent_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(100), nullable=False),
        sa.Column("consent_type", sa.String(30), nullable=False),
        sa.Column("policy_version", sa.String(50), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("policy_title", sa.String(200), nullable=False),
        sa.Column("policy_content", sa.Text(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(policy_digest)=64", name="ck_consumer_consent_policies_digest_length"),
        sa.CheckConstraint(
            "length(trim(policy_title)) BETWEEN 1 AND 200", name="ck_consumer_consent_policies_title_length"
        ),
        sa.CheckConstraint(
            "length(policy_content) BETWEEN 1 AND 16384", name="ck_consumer_consent_policies_content_length"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_consumer_consent_policies_tenant"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_consumer_consent_policies_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "purpose", "policy_version", name="uq_consumer_consent_policies_tenant_purpose_version"
        ),
    )
    op.create_index(
        "ix_consumer_consent_policies_tenant_purpose",
        "consumer_consent_policies",
        ["tenant_id", "purpose"],
    )
    op.create_table(
        "consumer_consent_policy_current",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(100), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_consumer_consent_policy_current_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            ["consumer_consent_policies.tenant_id", "consumer_consent_policies.id"],
            name="fk_consumer_consent_policy_current_policy",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "purpose"),
    )
    op.create_index(
        "ix_consumer_consent_policy_current_policy",
        "consumer_consent_policy_current",
        ["tenant_id", "policy_id"],
    )
    op.create_table(
        "consumer_consent_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("consent_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("consumer_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("visitor_subject_hash", sa.String(64), nullable=False),
        sa.Column("result_status", sa.String(30), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("action IN ('grant','lead_capture','withdraw')", name="ck_consumer_consent_actions_action"),
        sa.CheckConstraint("length(payload_hash)=64", name="ck_consumer_consent_actions_payload_hash"),
        sa.CheckConstraint("length(visitor_subject_hash)=64", name="ck_consumer_consent_actions_subject_hash"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_consumer_consent_actions_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            ["consumer_consent_policies.tenant_id", "consumer_consent_policies.id"],
            name="fk_consumer_consent_actions_tenant_policy",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_consumer_consent_actions_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "action", "idempotency_key", name="uq_consumer_consent_actions_tenant_action_idempotency"
        ),
    )
    op.create_index(
        "ix_consumer_consent_actions_tenant_consent",
        "consumer_consent_actions",
        ["tenant_id", "consent_id", "occurred_at"],
    )
    for table in ("consumer_consent_policies", "consumer_consent_policy_current", "consumer_consent_actions"):
        _tenant_policy(table)

    op.add_column("consent_records", sa.Column("policy_id", sa.Uuid(), nullable=True))
    op.add_column("consent_records", sa.Column("purpose", sa.String(100), nullable=True))
    op.add_column("consent_records", sa.Column("policy_digest", sa.String(64), nullable=True))
    op.add_column("consent_records", sa.Column("visitor_subject_hash", sa.String(64), nullable=True))
    op.add_column("consent_records", sa.Column("scan_event_id", sa.Uuid(), nullable=True))
    op.add_column("consent_records", sa.Column("scan_event_time", sa.DateTime(timezone=True), nullable=True))
    op.add_column("consent_records", sa.Column("idempotency_key", sa.String(100), nullable=True))
    op.add_column("consent_records", sa.Column("authority_version", sa.Integer(), server_default="0", nullable=False))
    op.add_column(
        "consumer_profiles",
        sa.Column("lead_contact_suppressed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("consumer_profiles", sa.Column("lead_consent_id", sa.Uuid(), nullable=True))
    op.add_column("consumer_profiles", sa.Column("lead_scan_event_id", sa.Uuid(), nullable=True))
    op.add_column("consumer_profiles", sa.Column("lead_scan_event_time", sa.DateTime(timezone=True), nullable=True))
    op.add_column("consumer_profiles", sa.Column("lead_captured_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        populated = bind.execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM consumer_consent_actions) OR "
                "EXISTS(SELECT 1 FROM consent_records WHERE authority_version=1) OR "
                "EXISTS(SELECT 1 FROM consumer_profiles WHERE lead_contact_suppressed IS TRUE "
                "OR lead_consent_id IS NOT NULL OR lead_scan_event_id IS NOT NULL OR lead_scan_event_time IS NOT NULL "
                "OR lead_captured_at IS NOT NULL)"
            )
        ).scalar_one()
        if populated:
            raise RuntimeError("u6e0 downgrade blocked: authoritative consent facts exist")
    for column in (
        "lead_captured_at",
        "lead_scan_event_time",
        "lead_scan_event_id",
        "lead_consent_id",
        "lead_contact_suppressed",
    ):
        op.drop_column("consumer_profiles", column)
    for column in (
        "authority_version",
        "idempotency_key",
        "scan_event_time",
        "scan_event_id",
        "visitor_subject_hash",
        "policy_digest",
        "purpose",
        "policy_id",
    ):
        op.drop_column("consent_records", column)
    op.drop_table("consumer_consent_actions")
    op.drop_table("consumer_consent_policy_current")
    op.drop_table("consumer_consent_policies")
