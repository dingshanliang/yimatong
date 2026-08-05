"""secure ops tasks and backfill fixed roles

Revision ID: e163ca423d55
Revises: d052bb312c44
Create Date: 2026-08-03 16:35:00
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e163ca423d55"
down_revision: str | None = "d052bb312c44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_PERMISSIONS = {
    "admin": (
        "tenant:manage",
        "account:manage",
        "role:manage",
        "product:create",
        "product:update",
        "product:delete",
        "code:generate",
        "code:export",
        "code:manage",
        "page:create",
        "page:publish",
        "campaign:create",
        "campaign:manage",
        "analytics:view",
        "export:run",
        "takeover:prepare",
        "takeover:approve",
        "takeover:execute",
        "takeover:rollback",
        "takeover:audit",
    ),
    "operator": (
        "product:create",
        "product:update",
        "code:generate",
        "code:export",
        "page:create",
        "page:publish",
        "campaign:create",
        "analytics:view",
        "takeover:prepare",
        "takeover:audit",
    ),
    "viewer": (),
}


def upgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    op.create_table(
        "role_template_backups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("role_name", sa.String(length=50), nullable=False),
        sa.Column("role_created", sa.Boolean(), nullable=False),
        sa.Column("prior_description", sa.String(length=255), nullable=True),
        sa.Column("prior_permission_ids", sa.JSON(), nullable=False),
        sa.Column("created_permission_ids", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "role_name", name="uq_role_template_backup_tenant_role"),
    )
    op.create_index("ix_role_template_backups_tenant_id", "role_template_backups", ["tenant_id"])

    bind = op.get_bind()
    tenant_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM tenants"))]
    descriptions = {"admin": "品牌管理员", "operator": "运营人员", "viewer": "无业务操作权限成员"}
    for tenant_id in tenant_ids:
        duplicate_roles = list(
            bind.execute(
                sa.text(
                    "SELECT name, count(*) FROM roles WHERE tenant_id = :tenant_id "
                    "AND name IN ('admin', 'operator', 'viewer') GROUP BY name HAVING count(*) > 1"
                ),
                {"tenant_id": tenant_id},
            )
        )
        if duplicate_roles:
            raise RuntimeError(f"Duplicate built-in roles require explicit cleanup for tenant {tenant_id}")
        created_permission_ids: list[uuid.UUID] = []
        permissions = {
            row.code: row.id
            for row in bind.execute(
                sa.text("SELECT id, code FROM permissions WHERE tenant_id = :tenant_id"), {"tenant_id": tenant_id}
            )
        }
        for code in dict.fromkeys(code for codes in ROLE_PERMISSIONS.values() for code in codes):
            if code not in permissions:
                permission_id = uuid.uuid4()
                bind.execute(
                    sa.text(
                        "INSERT INTO permissions (id, tenant_id, code, description) "
                        "VALUES (:id, :tenant_id, :code, :description)"
                    ),
                    {"id": permission_id, "tenant_id": tenant_id, "code": code, "description": f"内置角色权限：{code}"},
                )
                permissions[code] = permission_id
                created_permission_ids.append(permission_id)

        roles = {
            row.name: (row.id, row.description)
            for row in bind.execute(
                sa.text("SELECT id, name, description FROM roles WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        }
        for role_name, codes in ROLE_PERMISSIONS.items():
            existing_role = roles.get(role_name)
            role_created = existing_role is None
            prior_description = existing_role[1] if existing_role is not None else None
            if existing_role is None:
                role_id = uuid.uuid4()
                bind.execute(
                    sa.text(
                        "INSERT INTO roles (id, tenant_id, name, description) "
                        "VALUES (:id, :tenant_id, :name, :description)"
                    ),
                    {
                        "id": role_id,
                        "tenant_id": tenant_id,
                        "name": role_name,
                        "description": descriptions[role_name],
                    },
                )
            else:
                role_id = existing_role[0]
                bind.execute(
                    sa.text("UPDATE roles SET description = :description WHERE id = :role_id"),
                    {"description": descriptions[role_name], "role_id": role_id},
                )
            prior_permission_ids = [
                row[0]
                for row in bind.execute(
                    sa.text("SELECT permission_id FROM role_permissions WHERE role_id = :role_id"),
                    {"role_id": role_id},
                )
            ]
            bind.execute(
                sa.text(
                    "INSERT INTO role_template_backups "
                    "(id, tenant_id, role_id, role_name, role_created, prior_description, "
                    "prior_permission_ids, created_permission_ids) "
                    "VALUES (:id, :tenant_id, :role_id, :role_name, :role_created, :prior_description, "
                    "CAST(:prior_permission_ids AS json), CAST(:created_permission_ids AS json))"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "role_id": role_id,
                    "role_name": role_name,
                    "role_created": role_created,
                    "prior_description": prior_description,
                    "prior_permission_ids": json.dumps([str(item) for item in prior_permission_ids]),
                    "created_permission_ids": json.dumps([str(item) for item in created_permission_ids]),
                },
            )
            # Built-in roles are an exact authorization contract. Normalize
            # historical customization before mutation endpoints become read-only.
            bind.execute(
                sa.text("DELETE FROM role_permissions WHERE role_id = :role_id"),
                {"role_id": role_id},
            )
            for code in codes:
                bind.execute(
                    sa.text(
                        "INSERT INTO role_permissions (role_id, permission_id) "
                        "SELECT :role_id, :permission_id WHERE NOT EXISTS ("
                        "SELECT 1 FROM role_permissions WHERE role_id = :role_id AND permission_id = :permission_id)"
                    ),
                    {"role_id": role_id, "permission_id": permissions[code]},
                )

    # Take the ops_tasks DDL lock only after the potentially long data backfill.
    op.execute("ALTER TABLE public.ops_tasks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.ops_tasks FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.ops_tasks")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON public.ops_tasks
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
    records = list(bind.execute(sa.text("SELECT * FROM role_template_backups ORDER BY tenant_id, role_name")))
    created_permission_ids: set[uuid.UUID] = set()
    for record in records:
        role_id = record.role_id
        bind.execute(sa.text("DELETE FROM role_permissions WHERE role_id = :role_id"), {"role_id": role_id})
        if record.role_created:
            assigned = bind.execute(
                sa.text("SELECT count(*) FROM account_roles WHERE role_id = :role_id"), {"role_id": role_id}
            ).scalar_one()
            if assigned:
                raise RuntimeError(
                    f"Cannot downgrade fixed-role backfill: role {record.role_name} is assigned to {assigned} accounts"
                )
            bind.execute(sa.text("DELETE FROM roles WHERE id = :role_id"), {"role_id": role_id})
        else:
            bind.execute(
                sa.text("UPDATE roles SET description = :description WHERE id = :role_id"),
                {"description": record.prior_description, "role_id": role_id},
            )
            for permission_id in record.prior_permission_ids:
                bind.execute(
                    sa.text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:role_id, :permission_id)"),
                    {"role_id": role_id, "permission_id": uuid.UUID(permission_id)},
                )
        created_permission_ids.update(uuid.UUID(item) for item in record.created_permission_ids)

    for permission_id in created_permission_ids:
        bind.execute(
            sa.text(
                "DELETE FROM permissions WHERE id = :permission_id "
                "AND NOT EXISTS (SELECT 1 FROM role_permissions WHERE permission_id = :permission_id)"
            ),
            {"permission_id": permission_id},
        )

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.ops_tasks")
    op.execute("ALTER TABLE public.ops_tasks NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.ops_tasks DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_role_template_backups_tenant_id", table_name="role_template_backups")
    op.drop_table("role_template_backups")
