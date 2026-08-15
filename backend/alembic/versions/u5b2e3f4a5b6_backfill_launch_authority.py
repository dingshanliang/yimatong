"""backfill launch authority ownership and legacy manifests

Revision ID: u5b2e3f4a5b6
Revises: u5b1d2e3f4a5
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u5b2e3f4a5b6"
down_revision: str | None = "u5b1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    missing = bind.execute(sa.text("""
        SELECT count(*) FROM public.launch_releases AS release
        LEFT JOIN public.accounts AS creator ON creator.id=release.created_by
        LEFT JOIN public.accounts AS confirmer ON confirmer.id=release.brand_confirmed_by
        LEFT JOIN public.accounts AS launcher ON launcher.id=release.launched_by
        LEFT JOIN public.accounts AS suspender ON suspender.id=release.suspended_by
        WHERE creator.id IS NULL
           OR (release.brand_confirmed_by IS NOT NULL AND confirmer.id IS NULL)
           OR (release.launched_by IS NOT NULL AND launcher.id IS NULL)
           OR (release.suspended_by IS NOT NULL AND suspender.id IS NULL)
    """)).scalar_one()
    if missing:
        raise RuntimeError("launch releases contain actors without authoritative accounts")
    bind.execute(sa.text("""
        UPDATE public.launch_releases AS release SET
          created_by_tenant_id=(SELECT tenant_id FROM public.accounts WHERE id=release.created_by),
          brand_confirmed_by_tenant_id=(SELECT tenant_id FROM public.accounts WHERE id=release.brand_confirmed_by),
          launched_by_tenant_id=(SELECT tenant_id FROM public.accounts WHERE id=release.launched_by),
          suspended_by_tenant_id=(SELECT tenant_id FROM public.accounts WHERE id=release.suspended_by),
          readiness_manifest=jsonb_build_object(
              'version',1,'legacy',true,'snapshot',release.readiness_snapshot
          )
        WHERE release.created_by_tenant_id IS NULL OR release.readiness_manifest IS NULL
               OR (release.brand_confirmed_by IS NOT NULL AND release.brand_confirmed_by_tenant_id IS NULL)
               OR (release.launched_by IS NOT NULL AND release.launched_by_tenant_id IS NULL)
               OR (release.suspended_by IS NOT NULL AND release.suspended_by_tenant_id IS NULL)
    """))
    remaining = bind.execute(sa.text("""
        SELECT count(*) FROM public.launch_releases
        WHERE created_by_tenant_id IS NULL OR readiness_manifest IS NULL
           OR (brand_confirmed_by IS NULL) <> (brand_confirmed_by_tenant_id IS NULL)
           OR (launched_by IS NULL) <> (launched_by_tenant_id IS NULL)
           OR (suspended_by IS NULL) <> (suspended_by_tenant_id IS NULL)
    """)).scalar_one()
    if remaining:
        raise RuntimeError("launch release authority backfill is incomplete")


def downgrade() -> None:
    op.execute("""
        UPDATE public.launch_releases SET
          created_by_tenant_id=NULL,brand_confirmed_by_tenant_id=NULL,
          launched_by_tenant_id=NULL,suspended_by_tenant_id=NULL,
          readiness_manifest=NULL,readiness_scan_event_id=NULL
    """)
