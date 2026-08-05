"""enforce tenant-scoped organization parent links

Revision ID: f274db534e66
Revises: e163ca423d55
Create Date: 2026-08-03 17:05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f274db534e66"
down_revision: str | None = "e163ca423d55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    op.create_table(
        "organization_parent_repair_backups",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("prior_parent_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("organization_id"),
    )
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO organization_parent_repair_backups (organization_id, prior_parent_id) "
            "SELECT child.id, child.parent_id FROM organizations child "
            "JOIN organizations parent ON parent.id = child.parent_id "
            "WHERE child.tenant_id <> parent.tenant_id"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE organizations SET parent_id = NULL "
            "WHERE id IN (SELECT organization_id FROM organization_parent_repair_backups)"
        )
    )
    op.create_unique_constraint(
        "uq_organizations_tenant_id_id",
        "organizations",
        ["tenant_id", "id"],
    )
    op.drop_constraint("organizations_parent_id_fkey", "organizations", type_="foreignkey")
    op.create_foreign_key(
        "fk_organizations_tenant_parent",
        "organizations",
        "organizations",
        ["tenant_id", "parent_id"],
        ["tenant_id", "id"],
    )


def downgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    op.drop_constraint("fk_organizations_tenant_parent", "organizations", type_="foreignkey")
    op.create_foreign_key(
        "organizations_parent_id_fkey",
        "organizations",
        "organizations",
        ["parent_id"],
        ["id"],
    )
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE organizations child SET parent_id = backup.prior_parent_id "
            "FROM organization_parent_repair_backups backup "
            "WHERE child.id = backup.organization_id"
        )
    )
    op.drop_table("organization_parent_repair_backups")
    op.drop_constraint("uq_organizations_tenant_id_id", "organizations", type_="unique")
