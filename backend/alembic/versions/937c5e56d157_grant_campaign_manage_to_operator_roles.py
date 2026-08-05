"""grant campaign manage to operator roles

Revision ID: 937c5e56d157
Revises: i507ae867b99
Create Date: 2026-08-03 20:13:09.324051

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "937c5e56d157"
down_revision: str | None = "i507ae867b99"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    op.create_table(
        "operator_campaign_manage_grants",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.Column("permission_created", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )
    op.create_index(
        "ix_operator_campaign_manage_grants_tenant_id",
        "operator_campaign_manage_grants",
        ["tenant_id"],
    )

    bind = op.get_bind()
    operators = list(bind.execute(sa.text("SELECT id, tenant_id FROM roles WHERE name = 'operator'")))
    for role_id, tenant_id in operators:
        permission_id = bind.execute(
            sa.text(
                "SELECT id FROM permissions "
                "WHERE tenant_id = :tenant_id AND code = 'campaign:manage' "
                "ORDER BY id LIMIT 1"
            ),
            {"tenant_id": tenant_id},
        ).scalar_one_or_none()
        permission_created = permission_id is None
        if permission_created:
            permission_id = uuid.uuid4()
            bind.execute(
                sa.text(
                    "INSERT INTO permissions (id, tenant_id, code, description) "
                    "VALUES (:id, :tenant_id, 'campaign:manage', '内置角色权限：campaign:manage')"
                ),
                {"id": permission_id, "tenant_id": tenant_id},
            )

        mapping_exists = bind.execute(
            sa.text(
                "SELECT 1 FROM role_permissions "
                "WHERE role_id = :role_id AND permission_id = :permission_id"
            ),
            {"role_id": role_id, "permission_id": permission_id},
        ).scalar_one_or_none()
        if mapping_exists is not None:
            continue

        bind.execute(
            sa.text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:role_id, :permission_id)"),
            {"role_id": role_id, "permission_id": permission_id},
        )
        bind.execute(
            sa.text(
                "INSERT INTO operator_campaign_manage_grants "
                "(tenant_id, role_id, permission_id, permission_created) "
                "VALUES (:tenant_id, :role_id, :permission_id, :permission_created)"
            ),
            {
                "tenant_id": tenant_id,
                "role_id": role_id,
                "permission_id": permission_id,
                "permission_created": permission_created,
            },
        )

    op.execute("ALTER TABLE public.operator_campaign_manage_grants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.operator_campaign_manage_grants FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON public.operator_campaign_manage_grants
        USING (
            tenant_id = public.current_tenant_id()
            OR (public.current_tenant_id() IS NULL AND current_setting('app.bypass_rls', true) = 'true')
        )
        WITH CHECK (
            tenant_id = public.current_tenant_id()
            OR (public.current_tenant_id() IS NULL AND current_setting('app.bypass_rls', true) = 'true')
        )
        """
    )


def downgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    bind = op.get_bind()
    grants = list(bind.execute(sa.text("SELECT role_id, permission_id FROM operator_campaign_manage_grants")))
    created_permission_ids = list(
        bind.execute(
            sa.text(
                "SELECT DISTINCT permission_id FROM operator_campaign_manage_grants WHERE permission_created = true"
            )
        ).scalars()
    )
    for role_id, permission_id in grants:
        bind.execute(
            sa.text(
                "DELETE FROM role_permissions "
                "WHERE role_id = :role_id AND permission_id = :permission_id"
            ),
            {"role_id": role_id, "permission_id": permission_id},
        )

    for permission_id in created_permission_ids:
        bind.execute(
            sa.text(
                "DELETE FROM permissions WHERE id = :permission_id "
                "AND NOT EXISTS (SELECT 1 FROM role_permissions WHERE permission_id = :permission_id)"
            ),
            {"permission_id": permission_id},
        )

    op.execute("DROP POLICY tenant_isolation ON public.operator_campaign_manage_grants")
    op.execute("ALTER TABLE public.operator_campaign_manage_grants NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.operator_campaign_manage_grants DISABLE ROW LEVEL SECURITY")
    op.drop_table("operator_campaign_manage_grants")
