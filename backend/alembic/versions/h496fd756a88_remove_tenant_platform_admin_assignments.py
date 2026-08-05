"""remove tenant platform admin assignments

Revision ID: h496fd756a88
Revises: g385ec645f77
Create Date: 2026-08-03 17:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "h496fd756a88"
down_revision: str | None = "g385ec645f77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    op.create_table(
        "tenant_platform_role_assignment_backups",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("auth_version_before", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "role_id"),
    )
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO tenant_platform_role_assignment_backups "
            "(account_id, role_id, auth_version_before) "
            "SELECT ar.account_id, ar.role_id, a.auth_version "
            "FROM account_roles ar "
            "JOIN accounts a ON a.id = ar.account_id "
            "JOIN roles r ON r.id = ar.role_id "
            "WHERE r.name = 'platform_admin'"
        )
    )
    bind.execute(
        sa.text("DELETE FROM account_roles ar USING roles r WHERE ar.role_id = r.id AND r.name = 'platform_admin'")
    )
    bind.execute(
        sa.text(
            "UPDATE accounts SET auth_version = auth_version + 1 "
            "WHERE id IN (SELECT account_id FROM tenant_platform_role_assignment_backups)"
        )
    )


def downgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO account_roles (account_id, role_id) "
            "SELECT account_id, role_id FROM tenant_platform_role_assignment_backups "
            "ON CONFLICT DO NOTHING"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE accounts a SET auth_version = GREATEST(a.auth_version - 1, 0) "
            "FROM tenant_platform_role_assignment_backups b WHERE a.id = b.account_id"
        )
    )
    op.drop_table("tenant_platform_role_assignment_backups")
