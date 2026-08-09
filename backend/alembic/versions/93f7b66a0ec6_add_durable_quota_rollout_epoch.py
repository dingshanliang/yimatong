"""Add the durable global quota rollout epoch.

Revision ID: 93f7b66a0ec6
Revises: a38ad952599f
Create Date: 2026-08-09 17:19:23.708948

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "93f7b66a0ec6"
down_revision: str | None = "a38ad952599f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "quota_rollout_state"
_RUNTIME_ROLE = "yimatong_app"
_SOURCE_REVISION = revision


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": _RUNTIME_ROLE})
        .scalar_one()
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
        op.execute("SET LOCAL search_path = public, pg_catalog")

    op.create_table(
        _TABLE,
        sa.Column("id", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("source_revision", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=16), server_default="bridge", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("drained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("drained_by", sa.String(length=128), nullable=True),
        sa.Column("activated_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_quota_rollout_state_singleton"),
        sa.CheckConstraint(
            "phase IN ('bridge', 'drained', 'active')",
            name="quota_rollout_phase",
        ),
        sa.CheckConstraint(
            "length(trim(source_revision)) > 0",
            name="ck_quota_rollout_state_source_revision",
        ),
        sa.CheckConstraint(
            "(phase = 'bridge' AND drained_at IS NULL AND activated_at IS NULL "
            "AND drained_by IS NULL AND activated_by IS NULL) OR "
            "(phase = 'drained' AND drained_at IS NOT NULL AND length(trim(drained_by)) > 0 "
            "AND activated_at IS NULL AND activated_by IS NULL) OR "
            "(phase = 'active' AND drained_at IS NOT NULL AND length(trim(drained_by)) > 0 "
            "AND activated_at IS NOT NULL AND length(trim(activated_by)) > 0)",
            name="ck_quota_rollout_state_phase_provenance",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(
        sa.table(
            _TABLE,
            sa.column("id", sa.SmallInteger()),
            sa.column("source_revision", sa.String(length=64)),
            sa.column("phase", sa.String(length=16)),
        ),
        [{"id": 1, "source_revision": _SOURCE_REVISION, "phase": "bridge"}],
    )

    if bind.dialect.name != "postgresql":
        return
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{_TABLE} FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(f'REVOKE ALL PRIVILEGES ON TABLE public.{_TABLE} FROM "{_RUNTIME_ROLE}"')
        op.execute(f'GRANT SELECT ON TABLE public.{_TABLE} TO "{_RUNTIME_ROLE}"')


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
        op.execute("SET LOCAL search_path = public, pg_catalog")
        op.execute(f"LOCK TABLE public.{_TABLE} IN ACCESS EXCLUSIVE MODE")
        if _runtime_role_exists():
            op.execute(f'REVOKE ALL PRIVILEGES ON TABLE public.{_TABLE} FROM "{_RUNTIME_ROLE}"')
    # The per-tenant counters and markers remain derived, non-enforcing state;
    # downgrade removes only the global epoch switch.
    op.drop_table(_TABLE)
