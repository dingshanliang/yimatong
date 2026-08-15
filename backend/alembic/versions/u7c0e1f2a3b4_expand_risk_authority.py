"""expand risk decision authority

Revision ID: u7c0e1f2a3b4
Revises: u7b1d2e3f4a5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u7c0e1f2a3b4"
down_revision: str | Sequence[str] | None = "u7b1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = ("risk_action_receipts", "risk_campaign_pauses", "risk_action_outbox")


def _preflight() -> None:
    bind = op.get_bind()
    checks = {
        "campaign_risk_rules campaign": """
            SELECT count(*) FROM campaign_risk_rules link LEFT JOIN campaigns c
              ON c.tenant_id=link.tenant_id AND c.id=link.campaign_id WHERE c.id IS NULL
        """,
        "campaign_risk_rules rule": """
            SELECT count(*) FROM campaign_risk_rules link LEFT JOIN risk_rules r
              ON r.tenant_id=link.tenant_id AND r.id=link.risk_rule_id WHERE r.id IS NULL
        """,
        "interception_records rule": """
            SELECT count(*) FROM interception_records i LEFT JOIN risk_rules r
              ON r.tenant_id=i.tenant_id AND r.id=i.risk_rule_id WHERE r.id IS NULL
        """,
        "interception_records campaign": """
            SELECT count(*) FROM interception_records i LEFT JOIN campaigns c
              ON c.tenant_id=i.tenant_id AND c.id=i.campaign_id
            WHERE i.campaign_id IS NOT NULL AND c.id IS NULL
        """,
        "risk_alerts code item": """
            SELECT count(*) FROM risk_alerts a LEFT JOIN code_items i
              ON i.tenant_id=a.tenant_id AND i.id=a.code_item_id WHERE i.id IS NULL
        """,
        "risk_notifications rule": """
            SELECT count(*) FROM risk_notifications n LEFT JOIN risk_rules r
              ON r.tenant_id=n.tenant_id AND r.id=n.risk_rule_id
            WHERE n.risk_rule_id IS NOT NULL AND r.id IS NULL
        """,
        "risk_notifications campaign": """
            SELECT count(*) FROM risk_notifications n LEFT JOIN campaigns c
              ON c.tenant_id=n.tenant_id AND c.id=n.campaign_id
            WHERE n.campaign_id IS NOT NULL AND c.id IS NULL
        """,
        "risk_notifications code item": """
            SELECT count(*) FROM risk_notifications n LEFT JOIN code_items i
              ON i.tenant_id=n.tenant_id AND i.id=n.code_item_id
            WHERE n.code_item_id IS NOT NULL AND i.id IS NULL
        """,
    }
    failures = [name for name, sql in checks.items() if bind.execute(sa.text(sql)).scalar_one()]
    if failures:
        raise RuntimeError("u7c0 preflight failed: tenant authority mismatch in " + ", ".join(failures))


def _create_rls(table: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY tenant_isolation ON public.{table}
        USING (tenant_id=public.current_tenant_id())
        WITH CHECK (tenant_id=public.current_tenant_id())"""
    )


def upgrade() -> None:
    _preflight()
    op.add_column("risk_rules", sa.Column("version", sa.BigInteger(), server_default="1", nullable=False))
    op.create_check_constraint("ck_risk_rules_version_positive", "risk_rules", "version >= 1")
    op.create_unique_constraint("uq_risk_rules_tenant_id_id", "risk_rules", ["tenant_id", "id"])
    op.add_column("risk_alerts", sa.Column("risk_rule_id", sa.Uuid(), nullable=True))
    op.add_column("risk_alerts", sa.Column("risk_action_receipt_id", sa.Uuid(), nullable=True))
    op.add_column("risk_alerts", sa.Column("actor_id", sa.Uuid(), nullable=True))
    op.add_column("risk_alerts", sa.Column("source", sa.String(30), nullable=True))
    op.add_column("risk_alerts", sa.Column("reason_snapshot", sa.String(200), nullable=True))
    op.add_column("risk_alerts", sa.Column("prior_code_status", sa.String(20), nullable=True))
    op.add_column("risk_alerts", sa.Column("current_code_status", sa.String(20), nullable=True))
    op.create_index("ix_risk_alerts_risk_rule_id", "risk_alerts", ["risk_rule_id"])
    op.create_index("ix_risk_alerts_risk_action_receipt_id", "risk_alerts", ["risk_action_receipt_id"])
    op.create_index("ix_risk_alerts_actor_id", "risk_alerts", ["actor_id"])
    op.create_unique_constraint(
        "uq_risk_alerts_tenant_receipt", "risk_alerts", ["tenant_id", "risk_action_receipt_id"]
    )

    op.drop_index("ix_campaign_risk_rules_unique", table_name="campaign_risk_rules")
    op.create_unique_constraint(
        "uq_campaign_risk_rules_tenant_id_id", "campaign_risk_rules", ["tenant_id", "id"]
    )
    op.create_unique_constraint(
        "uq_campaign_risk_rules_tenant_campaign_rule",
        "campaign_risk_rules",
        ["tenant_id", "campaign_id", "risk_rule_id"],
    )
    op.create_unique_constraint(
        "uq_interception_records_tenant_id_id", "interception_records", ["tenant_id", "id"]
    )

    foreign_keys = (
        ("fk_campaign_risk_rules_tenant_campaign", "campaign_risk_rules", ["tenant_id", "campaign_id"], "campaigns", ["tenant_id", "id"], "CASCADE"),
        ("fk_campaign_risk_rules_tenant_rule", "campaign_risk_rules", ["tenant_id", "risk_rule_id"], "risk_rules", ["tenant_id", "id"], "CASCADE"),
        ("fk_interception_records_tenant_rule", "interception_records", ["tenant_id", "risk_rule_id"], "risk_rules", ["tenant_id", "id"], None),
        ("fk_interception_records_tenant_campaign", "interception_records", ["tenant_id", "campaign_id"], "campaigns", ["tenant_id", "id"], None),
        ("fk_risk_alerts_tenant_code_item", "risk_alerts", ["tenant_id", "code_item_id"], "code_items", ["tenant_id", "id"], None),
        ("fk_risk_alerts_tenant_rule", "risk_alerts", ["tenant_id", "risk_rule_id"], "risk_rules", ["tenant_id", "id"], None),
        ("fk_risk_alerts_tenant_actor", "risk_alerts", ["tenant_id", "actor_id"], "accounts", ["tenant_id", "id"], None),
        ("fk_risk_notifications_tenant_rule", "risk_notifications", ["tenant_id", "risk_rule_id"], "risk_rules", ["tenant_id", "id"], None),
        ("fk_risk_notifications_tenant_campaign", "risk_notifications", ["tenant_id", "campaign_id"], "campaigns", ["tenant_id", "id"], None),
        ("fk_risk_notifications_tenant_code_item", "risk_notifications", ["tenant_id", "code_item_id"], "code_items", ["tenant_id", "id"], None),
    )
    for name, source, local, target, remote, ondelete in foreign_keys:
        op.create_foreign_key(name, source, target, local, remote, ondelete=ondelete)

    op.create_table(
        "risk_action_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("scan_event_id", sa.Uuid(), nullable=True),
        sa.Column("risk_rule_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("result", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_risk_action_receipts_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "action", "idempotency_key", name="uq_risk_receipt_tenant_action_idem"),
        sa.UniqueConstraint("tenant_id", "scan_event_id", "risk_rule_id", name="uq_risk_receipt_tenant_scan_rule"),
        sa.ForeignKeyConstraint(["tenant_id", "risk_rule_id"], ["risk_rules.tenant_id", "risk_rules.id"], name="fk_risk_receipts_tenant_rule"),
    )
    op.create_index("ix_risk_action_receipts_tenant_id", "risk_action_receipts", ["tenant_id"])
    op.create_foreign_key(
        "fk_risk_alerts_tenant_receipt",
        "risk_alerts",
        "risk_action_receipts",
        ["tenant_id", "risk_action_receipt_id"],
        ["tenant_id", "id"],
    )
    op.create_table(
        "risk_campaign_pauses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("risk_rule_id", sa.Uuid(), nullable=False),
        sa.Column("prior_status", sa.String(20), nullable=False),
        sa.Column("authority_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resumed_by_account_id", sa.Uuid(), nullable=True),
        sa.Column("resume_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("tenant_id", "id", name="uq_risk_campaign_pauses_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "receipt_id", "campaign_id", name="uq_risk_pause_receipt_campaign"),
        sa.CheckConstraint("status IN ('active','resumed')", name="ck_risk_campaign_pauses_status"),
        sa.CheckConstraint("version >= 1", name="ck_risk_campaign_pauses_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id", "receipt_id"], ["risk_action_receipts.tenant_id", "risk_action_receipts.id"], name="fk_risk_pauses_tenant_receipt"),
        sa.ForeignKeyConstraint(["tenant_id", "campaign_id"], ["campaigns.tenant_id", "campaigns.id"], name="fk_risk_pauses_tenant_campaign"),
        sa.ForeignKeyConstraint(["tenant_id", "risk_rule_id"], ["risk_rules.tenant_id", "risk_rules.id"], name="fk_risk_pauses_tenant_rule"),
    )
    op.create_index("ix_risk_campaign_pauses_tenant_id", "risk_campaign_pauses", ["tenant_id"])
    op.create_index("ix_risk_campaign_pauses_campaign_id", "risk_campaign_pauses", ["campaign_id"])
    op.create_index("ix_risk_campaign_pauses_tenant_campaign_status", "risk_campaign_pauses", ["tenant_id", "campaign_id", "status"])
    op.create_table(
        "risk_action_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "receipt_id", "topic", name="uq_risk_outbox_receipt_topic"),
        sa.CheckConstraint("attempts >= 0", name="ck_risk_action_outbox_attempts_nonnegative"),
        sa.ForeignKeyConstraint(["tenant_id", "receipt_id"], ["risk_action_receipts.tenant_id", "risk_action_receipts.id"], name="fk_risk_outbox_tenant_receipt"),
    )
    op.create_index("ix_risk_action_outbox_tenant_id", "risk_action_outbox", ["tenant_id"])
    op.create_index("ix_risk_action_outbox_pending", "risk_action_outbox", ["published_at", "created_at"])
    for table in _TENANT_TABLES:
        _create_rls(table)


def downgrade() -> None:
    facts = op.get_bind().execute(sa.text("SELECT count(*) FROM risk_action_receipts")).scalar_one()
    if facts:
        raise RuntimeError("u7c0 downgrade blocked: risk action receipts are immutable facts")
    op.drop_constraint("fk_risk_alerts_tenant_receipt", "risk_alerts", type_="foreignkey")
    for table in reversed(_TENANT_TABLES):
        op.drop_table(table)
    for name, table in reversed((
        ("fk_campaign_risk_rules_tenant_campaign", "campaign_risk_rules"),
        ("fk_campaign_risk_rules_tenant_rule", "campaign_risk_rules"),
        ("fk_interception_records_tenant_rule", "interception_records"),
        ("fk_interception_records_tenant_campaign", "interception_records"),
        ("fk_risk_alerts_tenant_code_item", "risk_alerts"),
        ("fk_risk_alerts_tenant_rule", "risk_alerts"),
        ("fk_risk_alerts_tenant_actor", "risk_alerts"),
        ("fk_risk_notifications_tenant_rule", "risk_notifications"),
        ("fk_risk_notifications_tenant_campaign", "risk_notifications"),
        ("fk_risk_notifications_tenant_code_item", "risk_notifications"),
    )):
        op.drop_constraint(name, table, type_="foreignkey")
    op.drop_constraint("uq_interception_records_tenant_id_id", "interception_records", type_="unique")
    op.drop_constraint("uq_campaign_risk_rules_tenant_campaign_rule", "campaign_risk_rules", type_="unique")
    op.drop_constraint("uq_campaign_risk_rules_tenant_id_id", "campaign_risk_rules", type_="unique")
    op.create_index("ix_campaign_risk_rules_unique", "campaign_risk_rules", ["campaign_id", "risk_rule_id"], unique=True)
    op.drop_constraint("uq_risk_alerts_tenant_receipt", "risk_alerts", type_="unique")
    op.drop_index("ix_risk_alerts_actor_id", table_name="risk_alerts")
    op.drop_index("ix_risk_alerts_risk_action_receipt_id", table_name="risk_alerts")
    op.drop_index("ix_risk_alerts_risk_rule_id", table_name="risk_alerts")
    for column in (
        "current_code_status",
        "prior_code_status",
        "reason_snapshot",
        "source",
        "actor_id",
        "risk_action_receipt_id",
        "risk_rule_id",
    ):
        op.drop_column("risk_alerts", column)
    op.drop_constraint("uq_risk_rules_tenant_id_id", "risk_rules", type_="unique")
    op.drop_constraint("ck_risk_rules_version_positive", "risk_rules", type_="check")
    op.drop_column("risk_rules", "version")
