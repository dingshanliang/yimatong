"""add wecom contact flow

Revision ID: d1e2f3a4b5c6
Revises: c8d9e0f1a2b3
Create Date: 2026-06-01 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wecom_contact_ways",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("benefit_id", sa.Uuid(), nullable=True),
        sa.Column("config_id", sa.String(length=120), nullable=True),
        sa.Column("qr_code", sa.String(length=1000), nullable=True),
        sa.Column("state", sa.String(length=128), nullable=False),
        sa.Column("user_ids", sa.JSON(), nullable=False),
        sa.Column("scan_token_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["benefit_id"], ["benefits.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.ForeignKeyConstraint(["connector_id"], ["connectors.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "state", name="uq_wecom_contact_ways_tenant_state"),
    )
    op.create_index(op.f("ix_wecom_contact_ways_tenant_id"), "wecom_contact_ways", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_wecom_contact_ways_connector_id"), "wecom_contact_ways", ["connector_id"], unique=False)
    op.create_index(op.f("ix_wecom_contact_ways_campaign_id"), "wecom_contact_ways", ["campaign_id"], unique=False)
    op.create_index(op.f("ix_wecom_contact_ways_benefit_id"), "wecom_contact_ways", ["benefit_id"], unique=False)
    op.create_index(
        op.f("ix_wecom_contact_ways_scan_token_hash"),
        "wecom_contact_ways",
        ["scan_token_hash"],
        unique=False,
    )
    op.create_index(
        "ix_wecom_contact_ways_lookup",
        "wecom_contact_ways",
        ["tenant_id", "campaign_id", "benefit_id", "status"],
        unique=False,
    )

    op.create_table(
        "wecom_external_contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("contact_way_id", sa.Uuid(), nullable=True),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("benefit_id", sa.Uuid(), nullable=True),
        sa.Column("external_userid", sa.String(length=120), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=128), nullable=True),
        sa.Column("consumer_id", sa.String(length=100), nullable=True),
        sa.Column("scan_token_hash", sa.String(length=64), nullable=True),
        sa.Column("unionid", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_event", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["benefit_id"], ["benefits.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.ForeignKeyConstraint(["connector_id"], ["connectors.id"]),
        sa.ForeignKeyConstraint(["contact_way_id"], ["wecom_contact_ways.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "connector_id",
            "external_userid",
            "state",
            name="uq_wecom_external_contacts_source",
        ),
    )
    op.create_index(
        op.f("ix_wecom_external_contacts_tenant_id"),
        "wecom_external_contacts",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wecom_external_contacts_connector_id"),
        "wecom_external_contacts",
        ["connector_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wecom_external_contacts_contact_way_id"),
        "wecom_external_contacts",
        ["contact_way_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wecom_external_contacts_campaign_id"),
        "wecom_external_contacts",
        ["campaign_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wecom_external_contacts_benefit_id"),
        "wecom_external_contacts",
        ["benefit_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wecom_external_contacts_state"),
        "wecom_external_contacts",
        ["state"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wecom_external_contacts_scan_token_hash"),
        "wecom_external_contacts",
        ["scan_token_hash"],
        unique=False,
    )
    op.create_index(
        "ix_wecom_external_contacts_lookup",
        "wecom_external_contacts",
        ["tenant_id", "benefit_id", "scan_token_hash", "status"],
        unique=False,
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ("wecom_contact_ways", "wecom_external_contacts"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(
                f"""
                CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id = current_tenant_id() OR current_tenant_id() IS NULL)
                WITH CHECK (tenant_id = current_tenant_id() OR current_tenant_id() IS NULL)
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ("wecom_external_contacts", "wecom_contact_ways"):
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_wecom_external_contacts_lookup", table_name="wecom_external_contacts")
    op.drop_index(op.f("ix_wecom_external_contacts_scan_token_hash"), table_name="wecom_external_contacts")
    op.drop_index(op.f("ix_wecom_external_contacts_state"), table_name="wecom_external_contacts")
    op.drop_index(op.f("ix_wecom_external_contacts_benefit_id"), table_name="wecom_external_contacts")
    op.drop_index(op.f("ix_wecom_external_contacts_campaign_id"), table_name="wecom_external_contacts")
    op.drop_index(op.f("ix_wecom_external_contacts_contact_way_id"), table_name="wecom_external_contacts")
    op.drop_index(op.f("ix_wecom_external_contacts_connector_id"), table_name="wecom_external_contacts")
    op.drop_index(op.f("ix_wecom_external_contacts_tenant_id"), table_name="wecom_external_contacts")
    op.drop_table("wecom_external_contacts")

    op.drop_index("ix_wecom_contact_ways_lookup", table_name="wecom_contact_ways")
    op.drop_index(op.f("ix_wecom_contact_ways_scan_token_hash"), table_name="wecom_contact_ways")
    op.drop_index(op.f("ix_wecom_contact_ways_benefit_id"), table_name="wecom_contact_ways")
    op.drop_index(op.f("ix_wecom_contact_ways_campaign_id"), table_name="wecom_contact_ways")
    op.drop_index(op.f("ix_wecom_contact_ways_connector_id"), table_name="wecom_contact_ways")
    op.drop_index(op.f("ix_wecom_contact_ways_tenant_id"), table_name="wecom_contact_ways")
    op.drop_table("wecom_contact_ways")
