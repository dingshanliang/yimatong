"""Add durable Platform control-plane sessions.

Revision ID: b9e2c3d4f5a6
Revises: a8d1c4e7f2b6
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9e2c3d4f5a6"
down_revision: str | None = "a8d1c4e7f2b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
        op.execute("SET LOCAL search_path = public, pg_catalog")

    op.create_table(
        "platform_auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_platform_auth_sessions_expires_at", "platform_auth_sessions", ["expires_at"])

    if bind.dialect.name == "postgresql":
        op.execute("REVOKE ALL PRIVILEGES ON TABLE public.platform_auth_sessions FROM PUBLIC")
        role_exists = bind.execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')")
        ).scalar_one()
        if role_exists:
            op.execute('REVOKE ALL PRIVILEGES ON TABLE public.platform_auth_sessions FROM "yimatong_app"')


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
        op.execute("SET LOCAL search_path = public, pg_catalog")
        op.execute("LOCK TABLE public.platform_auth_sessions IN ACCESS EXCLUSIVE MODE")

    # python-jose evaluates NumericDate at whole-second precision and accepts
    # exp == now. Keep a small operational margin so the parent revision can
    # never revive a token during the expiry second after this table is gone.
    unexpired_sessions = bind.execute(
        sa.text(
            "SELECT count(*) FROM platform_auth_sessions "
            "WHERE expires_at + interval '5 seconds' > CURRENT_TIMESTAMP"
        )
    ).scalar_one()
    if unexpired_sessions:
        raise RuntimeError(
            "Cannot downgrade while Platform tokens may still be valid; wait for every session expiry safety margin"
        )

    op.drop_index("ix_platform_auth_sessions_expires_at", table_name="platform_auth_sessions")
    op.drop_table("platform_auth_sessions")
