"""Build page authority keys without blocking ordinary writers.

Revision ID: u5a1b2c3d4e5
Revises: u5a0b1c2d3e4
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u5a1b2c3d4e5"
down_revision: str | None = "u5a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = {
    "uq_page_templates_tenant_id_id": (
        "page_templates",
        True,
        "CREATE UNIQUE INDEX uq_page_templates_tenant_id_id ON public.page_templates "
        "USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_page_templates_tenant_id_id "
        "ON public.page_templates (tenant_id, id)",
    ),
    "uq_page_versions_tenant_template_id": (
        "page_versions",
        True,
        "CREATE UNIQUE INDEX uq_page_versions_tenant_template_id ON public.page_versions "
        "USING btree (tenant_id, page_template_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_page_versions_tenant_template_id "
        "ON public.page_versions (tenant_id, page_template_id, id)",
    ),
    "uq_page_templates_one_active_product": (
        "page_templates",
        True,
        "CREATE UNIQUE INDEX uq_page_templates_one_active_product ON public.page_templates "
        "USING btree (tenant_id, product_id) WHERE (((status)::text = 'active'::text) "
        "AND (product_id IS NOT NULL))",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_page_templates_one_active_product "
        "ON public.page_templates (tenant_id, product_id) "
        "WHERE status='active' AND product_id IS NOT NULL",
    ),
    "uq_page_versions_one_published": (
        "page_versions",
        True,
        "CREATE UNIQUE INDEX uq_page_versions_one_published ON public.page_versions "
        "USING btree (tenant_id, page_template_id) WHERE ((status)::text = 'published'::text)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_page_versions_one_published "
        "ON public.page_versions (tenant_id, page_template_id) WHERE status='published'",
    ),
    "ix_page_templates_tenant_product": (
        "page_templates",
        False,
        "CREATE INDEX ix_page_templates_tenant_product ON public.page_templates "
        "USING btree (tenant_id, product_id)",
        "CREATE INDEX CONCURRENTLY ix_page_templates_tenant_product "
        "ON public.page_templates (tenant_id, product_id)",
    ),
    "ix_page_versions_tenant_creator": (
        "page_versions",
        False,
        "CREATE INDEX ix_page_versions_tenant_creator ON public.page_versions "
        "USING btree (created_by_tenant_id, created_by)",
        "CREATE INDEX CONCURRENTLY ix_page_versions_tenant_creator "
        "ON public.page_versions (created_by_tenant_id, created_by)",
    ),
    "ix_launch_releases_tenant_page_version": (
        "launch_releases",
        False,
        "CREATE INDEX ix_launch_releases_tenant_page_version ON public.launch_releases "
        "USING btree (tenant_id, page_template_id, page_version_id)",
        "CREATE INDEX CONCURRENTLY ix_launch_releases_tenant_page_version "
        "ON public.launch_releases (tenant_id, page_template_id, page_version_id)",
    ),
}


def _index_facts(name: str) -> tuple[bool, bool, str | None]:
    row = op.get_bind().execute(
        sa.text(
            """
            SELECT index.indisvalid, index.indisunique, pg_get_indexdef(index.indexrelid)
            FROM pg_index AS index
            JOIN pg_class AS relation ON relation.oid=index.indexrelid
            JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace
            WHERE namespace.nspname='public' AND relation.relname=:name
            """
        ),
        {"name": name},
    ).one_or_none()
    return (False, False, None) if row is None else (bool(row[0]), bool(row[1]), str(row[2]))


def _assert_preflight() -> None:
    checks = {
        "page templates contain duplicate active product bindings": """
            SELECT 1 FROM public.page_templates
            WHERE status='active' AND product_id IS NOT NULL
            GROUP BY tenant_id,product_id HAVING count(*)>1 LIMIT 1
        """,
        "page versions contain duplicate published bindings": """
            SELECT 1 FROM public.page_versions WHERE status='published'
            GROUP BY tenant_id,page_template_id HAVING count(*)>1 LIMIT 1
        """,
    }
    for message, query in checks.items():
        if op.get_bind().execute(sa.text(query)).first() is not None:
            raise RuntimeError(message)


def _prepare_index(name: str, expected_unique: bool, expected: str, create_sql: str) -> None:
    valid, unique, definition = _index_facts(name)
    if valid and unique is expected_unique and definition == expected:
        return
    if definition is not None and (unique is not expected_unique or definition != expected):
        raise RuntimeError(f"refusing to replace unexpected page authority index public.{name}")
    with op.get_context().autocommit_block():
        if definition is not None:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
        try:
            op.execute(create_sql)
        except BaseException:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
            raise
    valid, unique, definition = _index_facts(name)
    if not valid or unique is not expected_unique or definition != expected:
        raise RuntimeError(f"page authority index public.{name} is not exact and valid")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _assert_preflight()
    for name, (_table, unique, expected, create_sql) in _INDEXES.items():
        _prepare_index(name, unique, expected, create_sql)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        for name in reversed(tuple(_INDEXES)):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
