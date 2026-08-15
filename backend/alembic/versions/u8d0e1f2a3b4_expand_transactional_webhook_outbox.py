"""expand transactional webhook outbox

Revision ID: u8d0e1f2a3b4
Revises: u8c4d5e6f7a8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "u8d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "u8c4d5e6f7a8"
branch_labels = None
depends_on = None

_CONTROL = "public.current_tenant_id() IS NULL AND current_setting('app.bypass_rls',true)='true' AND has_parameter_privilege(session_user,'app.bypass_rls','SET')"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.add_column("webhook_endpoints", sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=True))
    op.add_column("webhook_endpoints", sa.Column("secret_nonce", sa.LargeBinary(), nullable=True))
    op.add_column("webhook_endpoints", sa.Column("secret_key_id", sa.String(64), nullable=True))
    op.add_column("webhook_endpoints", sa.Column("config_version", sa.Integer(), nullable=True))

    op.create_table(
        "webhook_domain_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expanded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_domain_events"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_webhook_domain_events_tenant"),
    )
    op.execute("ALTER TABLE public.webhook_domain_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.webhook_domain_events FORCE ROW LEVEL SECURITY")
    scope = f"(tenant_id=public.current_tenant_id() OR ({_CONTROL}))"
    op.execute(f"CREATE POLICY webhook_domain_events_tenant_isolation ON public.webhook_domain_events USING ({scope}) WITH CHECK ({scope})")

    for column in (
        sa.Column("domain_event_id", sa.Uuid(), nullable=True),
        sa.Column("payload_digest", sa.String(64), nullable=True),
        sa.Column("endpoint_url", sa.String(500), nullable=True),
        sa.Column("endpoint_secret_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("endpoint_secret_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("endpoint_secret_key_id", sa.String(64), nullable=True),
        sa.Column("endpoint_config_version", sa.Integer(), nullable=True),
        sa.Column("legacy_payload_wrapped", sa.Boolean(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("webhook_deliveries", column)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    facts = op.get_bind().execute(sa.text("SELECT count(*) FROM webhook_domain_events")).scalar_one()
    if facts:
        raise RuntimeError("u8d0 downgrade blocked: durable webhook event facts exist")
    for name in (
        "lease_expires_at", "lease_token", "attempt_count", "legacy_payload_wrapped", "endpoint_config_version",
        "endpoint_secret_key_id", "endpoint_secret_nonce", "endpoint_secret_ciphertext",
        "endpoint_url", "payload_digest", "domain_event_id",
    ):
        op.drop_column("webhook_deliveries", name)
    op.drop_table("webhook_domain_events")
    for name in ("config_version", "secret_key_id", "secret_nonce", "secret_ciphertext"):
        op.drop_column("webhook_endpoints", name)
