"""Add authoritative refresh-token family state.

Revision ID: a8d1c4e7f2b6
Revises: fec8dda0b399
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a8d1c4e7f2b6"
down_revision: str | None = "fec8dda0b399"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
        op.execute("SET LOCAL search_path = public, pg_catalog")

    # Account ids are already globally unique, so this cannot reject historic
    # data; the composite key lets PostgreSQL enforce workspace-bound sessions.
    op.create_unique_constraint("uq_accounts_tenant_id_id", "accounts", ["tenant_id", "id"])
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("current_refresh_jti", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["accounts.tenant_id", "accounts.id"],
            name="fk_auth_sessions_tenant_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("current_refresh_jti", name="uq_auth_sessions_current_refresh_jti"),
    )
    op.create_index("ix_auth_sessions_account_id", "auth_sessions", ["account_id"])
    op.create_index("ix_auth_sessions_tenant_id", "auth_sessions", ["tenant_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    if bind.dialect.name == "postgresql":
        op.execute("REVOKE ALL PRIVILEGES ON TABLE public.auth_sessions FROM PUBLIC")
        role_exists = bind.execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='yimatong_app')")
        ).scalar_one()
        if role_exists:
            op.execute('REVOKE ALL PRIVILEGES ON TABLE public.auth_sessions FROM "yimatong_app"')


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
        op.execute("SET LOCAL search_path = public, pg_catalog")

    # The parent revision has no durable refresh-family revocation state. Bump
    # every represented account before dropping the table so a rollback cannot
    # revive an ancestor/current refresh token or any family access token. This
    # intentionally signs those accounts out on downgrade; the bounded lock
    # timeout above makes operational contention fail instead of waiting forever.
    if bind.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.auth_sessions IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        UPDATE accounts AS account
        SET auth_version = account.auth_version + 1
        WHERE EXISTS (
            SELECT 1
            FROM auth_sessions AS session
            WHERE session.account_id = account.id
              AND session.tenant_id = account.tenant_id
        )
        """
    )
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_tenant_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_account_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_constraint("uq_accounts_tenant_id_id", "accounts", type_="unique")
