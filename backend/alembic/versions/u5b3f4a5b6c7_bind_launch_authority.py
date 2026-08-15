"""bind launch release tenant authority

Revision ID: u5b3f4a5b6c7
Revises: u5b2e3f4a5b6
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u5b3f4a5b6c7"
down_revision: str | None = "u5b2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FKS = (
    (
        "launch_release_actions",
        "fk_launch_release_actions_tenant",
        "FOREIGN KEY (tenant_id) REFERENCES public.tenants(id)",
    ),
    ("launch_releases", "fk_launch_releases_tenant_campaign", "FOREIGN KEY (tenant_id,campaign_id) REFERENCES public.campaigns(tenant_id,id)"),
    ("launch_releases", "fk_launch_releases_tenant_code_batch", "FOREIGN KEY (tenant_id,code_batch_id) REFERENCES public.code_batches(tenant_id,id)"),
    ("launch_releases", "fk_launch_releases_tenant_readiness_code_item", "FOREIGN KEY (tenant_id,readiness_code_item_id) REFERENCES public.code_items(tenant_id,id)"),
    ("launch_releases", "fk_launch_releases_creator_tenant_account", "FOREIGN KEY (created_by_tenant_id,created_by) REFERENCES public.accounts(tenant_id,id)"),
    ("launch_releases", "fk_launch_releases_confirmer_tenant_account", "FOREIGN KEY (brand_confirmed_by_tenant_id,brand_confirmed_by) REFERENCES public.accounts(tenant_id,id)"),
    ("launch_releases", "fk_launch_releases_launcher_tenant_account", "FOREIGN KEY (launched_by_tenant_id,launched_by) REFERENCES public.accounts(tenant_id,id)"),
    ("launch_releases", "fk_launch_releases_suspender_tenant_account", "FOREIGN KEY (suspended_by_tenant_id,suspended_by) REFERENCES public.accounts(tenant_id,id)"),
    ("launch_release_actions", "fk_launch_release_actions_tenant_release", "FOREIGN KEY (tenant_id,release_id) REFERENCES public.launch_releases(tenant_id,id)"),
    ("launch_release_actions", "fk_launch_release_actions_actor_tenant_account", "FOREIGN KEY (actor_tenant_id,actor_id) REFERENCES public.accounts(tenant_id,id)"),
)


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(sa.text("""
        SELECT count(*) FROM (
          SELECT tenant_id,code_batch_id FROM public.launch_releases
          WHERE status='live' GROUP BY tenant_id,code_batch_id HAVING count(*)>1
        ) AS duplicate
    """)).scalar_one()
    if duplicates:
        raise RuntimeError("multiple live launch releases exist for one tenant/code batch")
    op.execute("ALTER TABLE public.launch_releases ADD CONSTRAINT uq_launch_releases_tenant_id_id UNIQUE USING INDEX uq_launch_releases_tenant_id_id_idx")
    for table, name, definition in _FKS:
        op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} {definition} NOT VALID")
    op.execute("""
        ALTER TABLE public.launch_releases ADD CONSTRAINT ck_launch_releases_actor_tenant_pairs CHECK (
          (brand_confirmed_by IS NULL)=(brand_confirmed_by_tenant_id IS NULL)
          AND (launched_by IS NULL)=(launched_by_tenant_id IS NULL)
          AND (suspended_by IS NULL)=(suspended_by_tenant_id IS NULL)
        ) NOT VALID
    """)
    for table, name, _ in _FKS:
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")
    op.execute("ALTER TABLE public.launch_releases VALIDATE CONSTRAINT ck_launch_releases_actor_tenant_pairs")
    op.alter_column("launch_releases", "created_by_tenant_id", nullable=False)
    op.alter_column("launch_releases", "readiness_manifest", nullable=False)


def downgrade() -> None:
    op.alter_column("launch_releases", "readiness_manifest", nullable=True)
    op.alter_column("launch_releases", "created_by_tenant_id", nullable=True)
    op.drop_constraint("ck_launch_releases_actor_tenant_pairs", "launch_releases", type_="check")
    for table, name, _ in reversed(_FKS):
        op.drop_constraint(name, table, type_="foreignkey")
    op.drop_constraint("uq_launch_releases_tenant_id_id", "launch_releases", type_="unique")
