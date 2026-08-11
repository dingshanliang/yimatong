"""Build protected consumer identity lookup index online.

Revision ID: u6b1d2e3f4a5
Revises: u6b0c1d2e3f4
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u6b1d2e3f4a5"
down_revision: str | None = "u6b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "uq_consumer_profiles_tenant_wechat_openid_hash"
_EXPECTED = (
    "CREATE UNIQUE INDEX uq_consumer_profiles_tenant_wechat_openid_hash "
    "ON public.consumer_profiles USING btree (tenant_id, wechat_openid_hash) "
    "WHERE (wechat_openid_hash IS NOT NULL)"
)


def _facts() -> tuple[bool, bool, str | None]:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT idx.indisvalid,idx.indisunique,pg_get_indexdef(idx.indexrelid) "
                "FROM pg_index idx JOIN pg_class cls ON cls.oid=idx.indexrelid "
                "JOIN pg_namespace ns ON ns.oid=cls.relnamespace "
                "WHERE ns.nspname='public' AND cls.relname=:name"
            ),
            {"name": _INDEX},
        )
        .one_or_none()
    )
    return (False, False, None) if row is None else (bool(row[0]), bool(row[1]), str(row[2]))


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    valid, unique, definition = _facts()
    if valid and unique and definition == _EXPECTED:
        return
    if definition is not None and definition != _EXPECTED:
        raise RuntimeError(f"refusing unexpected index public.{_INDEX}")
    with op.get_context().autocommit_block():
        if definition is not None:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_INDEX}")
        try:
            op.execute(
                f"CREATE UNIQUE INDEX CONCURRENTLY {_INDEX} ON public.consumer_profiles "
                "(tenant_id,wechat_openid_hash) WHERE wechat_openid_hash IS NOT NULL"
            )
        except BaseException:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_INDEX}")
            raise
    valid, unique, definition = _facts()
    if not valid or not unique or definition != _EXPECTED:
        raise RuntimeError(f"protected consumer identity index public.{_INDEX} is not exact and valid")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{_INDEX}")
