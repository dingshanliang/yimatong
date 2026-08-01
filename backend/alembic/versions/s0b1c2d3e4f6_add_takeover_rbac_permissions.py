"""grant legacy-code takeover permissions to existing tenant roles"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "s0b1c2d3e4f6"
down_revision: str | None = "s0b1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TAKEOVER_ROLE_PERMISSIONS = {
    "admin": (
        "takeover:prepare",
        "takeover:approve",
        "takeover:execute",
        "takeover:rollback",
        "takeover:audit",
    ),
    "operator": ("takeover:prepare", "takeover:audit"),
}
PERMISSION_DESCRIPTION_PREFIX = "既有码接管默认权限："


def _tables() -> tuple[sa.Table, sa.Table, sa.Table, sa.Table]:
    tenants = sa.table("tenants", sa.column("id", sa.Uuid()))
    roles = sa.table("roles", sa.column("id", sa.Uuid()), sa.column("tenant_id", sa.Uuid()), sa.column("name"))
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("code"),
        sa.column("description"),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
    )
    return tenants, roles, permissions, role_permissions


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SET LOCAL app.bypass_rls = 'true'"))
    tenants, roles, permissions, role_permissions = _tables()

    tenant_ids = connection.execute(sa.select(tenants.c.id)).scalars().all()
    for tenant_id in tenant_ids:
        permission_ids: dict[str, uuid.UUID] = {}
        for code in {code for codes in TAKEOVER_ROLE_PERMISSIONS.values() for code in codes}:
            permission_id = connection.execute(
                sa.select(permissions.c.id).where(
                    permissions.c.tenant_id == tenant_id,
                    permissions.c.code == code,
                )
            ).scalar_one_or_none()
            if permission_id is None:
                permission_id = uuid.uuid4()
                connection.execute(
                    sa.insert(permissions).values(
                        id=permission_id,
                        tenant_id=tenant_id,
                        code=code,
                        description=f"{PERMISSION_DESCRIPTION_PREFIX}{code}",
                    )
                )
            permission_ids[code] = permission_id

        for role_name, codes in TAKEOVER_ROLE_PERMISSIONS.items():
            role_ids = connection.execute(
                sa.select(roles.c.id).where(
                    roles.c.tenant_id == tenant_id,
                    roles.c.name == role_name,
                )
            ).scalars()
            for role_id in role_ids:
                for code in codes:
                    permission_id = permission_ids[code]
                    already_linked = connection.execute(
                        sa.select(role_permissions.c.role_id).where(
                            role_permissions.c.role_id == role_id,
                            role_permissions.c.permission_id == permission_id,
                        )
                    ).first()
                    if already_linked is None:
                        connection.execute(
                            sa.insert(role_permissions).values(role_id=role_id, permission_id=permission_id)
                        )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SET LOCAL app.bypass_rls = 'true'"))
    _, _, permissions, role_permissions = _tables()
    owned_permissions = sa.select(permissions.c.id).where(
        permissions.c.description.like(f"{PERMISSION_DESCRIPTION_PREFIX}%")
    )
    connection.execute(sa.delete(role_permissions).where(role_permissions.c.permission_id.in_(owned_permissions)))
    connection.execute(sa.delete(permissions).where(permissions.c.id.in_(owned_permissions)))
