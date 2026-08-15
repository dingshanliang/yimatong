"""Backfill canonical consumer-detail grants for built-in tenant roles.

Revision ID: u6i0a1b2c3d4
Revises: u6h1f2a3b4c5
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u6i0a1b2c3d4"
down_revision: str | None = "u6h1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEDGER = "consumer_detail_role_grant_backfills"
_PERMISSION_CODE = "consumer:detail"


def upgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    op.create_table(
        _LEDGER,
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.Column("permission_created", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_consumer_detail_role_grant_backfills_tenant",
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )
    op.create_index(
        "ix_consumer_detail_role_grant_backfills_tenant_id",
        _LEDGER,
        ["tenant_id"],
    )

    bind = op.get_bind()
    roles = list(
        bind.execute(
            sa.text(
                "SELECT role.id,role.tenant_id FROM public.roles AS role "
                "WHERE role.name IN ('admin','operator') ORDER BY role.tenant_id,role.name,role.id"
            )
        )
    )
    for role_id, tenant_id in roles:
        permission_id = bind.execute(
            sa.text(
                "SELECT permission.id FROM public.permissions AS permission "
                "WHERE permission.tenant_id=:tenant_id AND permission.code=:permission_code"
            ),
            {"tenant_id": tenant_id, "permission_code": _PERMISSION_CODE},
        ).scalar_one_or_none()
        permission_created = permission_id is None
        if permission_created:
            permission_id = uuid.uuid4()
            bind.execute(
                sa.text(
                    "INSERT INTO public.permissions(id,tenant_id,code,description,created_at,updated_at) "
                    "VALUES(:permission_id,:tenant_id,:permission_code,:description,now(),now())"
                ),
                {
                    "permission_id": permission_id,
                    "tenant_id": tenant_id,
                    "permission_code": _PERMISSION_CODE,
                    "description": "内置角色权限：consumer:detail",
                },
            )

        mapping_exists = bind.execute(
            sa.text(
                "SELECT 1 FROM public.role_permissions AS mapping "
                "WHERE mapping.tenant_id=:tenant_id AND mapping.role_id=:role_id "
                "AND mapping.permission_id=:permission_id"
            ),
            {"tenant_id": tenant_id, "role_id": role_id, "permission_id": permission_id},
        ).scalar_one_or_none()
        if mapping_exists is not None:
            continue

        bind.execute(
            sa.text(
                "INSERT INTO public.role_permissions(tenant_id,role_id,permission_id) "
                "VALUES(:tenant_id,:role_id,:permission_id)"
            ),
            {"tenant_id": tenant_id, "role_id": role_id, "permission_id": permission_id},
        )
        bind.execute(
            sa.text(
                f"INSERT INTO public.{_LEDGER}(tenant_id,role_id,permission_id,permission_created) "
                "VALUES(:tenant_id,:role_id,:permission_id,:permission_created)"
            ),
            {
                "tenant_id": tenant_id,
                "role_id": role_id,
                "permission_id": permission_id,
                "permission_created": permission_created,
            },
        )

    op.execute(f"ALTER TABLE public.{_LEDGER} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{_LEDGER} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON public.{_LEDGER}
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
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{_LEDGER} FROM PUBLIC")
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{_LEDGER} FROM yimatong_app")


def downgrade() -> None:
    op.execute("SET LOCAL search_path = public, pg_catalog")
    bind = op.get_bind()
    grants = list(
        bind.execute(
            sa.text(
                f"SELECT tenant_id,role_id,permission_id,permission_created FROM public.{_LEDGER} "
                "ORDER BY tenant_id,role_id,permission_id"
            )
        )
    )
    for tenant_id, role_id, permission_id, _permission_created in grants:
        bind.execute(
            sa.text(
                "DELETE FROM public.role_permissions WHERE tenant_id=:tenant_id "
                "AND role_id=:role_id AND permission_id=:permission_id"
            ),
            {"tenant_id": tenant_id, "role_id": role_id, "permission_id": permission_id},
        )

    created_permission_ids = {permission_id for _, _, permission_id, created in grants if created}
    for permission_id in created_permission_ids:
        bind.execute(
            sa.text(
                "DELETE FROM public.permissions WHERE id=:permission_id "
                "AND NOT EXISTS(SELECT 1 FROM public.role_permissions WHERE permission_id=:permission_id)"
            ),
            {"permission_id": permission_id},
        )

    op.execute(f"DROP POLICY tenant_isolation ON public.{_LEDGER}")
    op.execute(f"ALTER TABLE public.{_LEDGER} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{_LEDGER} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_consumer_detail_role_grant_backfills_tenant_id", table_name=_LEDGER)
    op.drop_table(_LEDGER)
