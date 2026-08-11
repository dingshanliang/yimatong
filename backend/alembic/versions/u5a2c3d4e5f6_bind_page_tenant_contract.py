"""Bind page rows to tenant-owned parents and validate state invariants.

Revision ID: u5a2c3d4e5f6
Revises: u5ab2c3d4e5f
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u5a2c3d4e5f6"
down_revision: str | None = "u5ab2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEMPLATE_KEY = "uq_page_templates_tenant_id_id"
_VERSION_KEY = "uq_page_versions_tenant_template_id"
_TEMPLATE_PRODUCT_FK = "fk_page_templates_tenant_product"
_VERSION_TEMPLATE_FK = "fk_page_versions_tenant_template"
_VERSION_CREATOR_FK = "fk_page_versions_tenant_creator"
_LAUNCH_TEMPLATE_FK = "fk_launch_releases_tenant_page_template"
_LAUNCH_VERSION_FK = "fk_launch_releases_tenant_page_version"
_CREATOR_NOT_NULL = "ck_page_versions_created_by_tenant_not_null"
_DOWNGRADE_INDEXES = {
    "uq_page_versions_tenant_template_id_downgrade": (
        "CREATE UNIQUE INDEX uq_page_versions_tenant_template_id_downgrade ON public.page_versions "
        "USING btree (tenant_id, page_template_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_page_versions_tenant_template_id_downgrade "
        "ON public.page_versions (tenant_id,page_template_id,id)",
    ),
    "uq_page_templates_tenant_id_id_downgrade": (
        "CREATE UNIQUE INDEX uq_page_templates_tenant_id_id_downgrade ON public.page_templates "
        "USING btree (tenant_id, id)",
        "CREATE UNIQUE INDEX CONCURRENTLY uq_page_templates_tenant_id_id_downgrade "
        "ON public.page_templates (tenant_id,id)",
    ),
}
_CHECKS = (
    ("page_templates", "ck_page_templates_status", "status IN ('active','archived')"),
    (
        "page_templates",
        "ck_page_templates_type",
        "template_type IN ('product_info','traceability','brand_story')",
    ),
    ("page_versions", "ck_page_versions_version_positive", "version > 0"),
    (
        "page_versions",
        "ck_page_versions_status",
        "status IN ('draft','published','archived')",
    ),
    (
        "page_versions",
        "ck_page_versions_publish_timestamp",
        "(status='published' AND published_at IS NOT NULL) OR "
        "(status='draft' AND published_at IS NULL) OR status='archived'",
    ),
)


def _assert_no_row(query: str, message: str) -> None:
    if op.get_bind().execute(sa.text(query)).first() is not None:
        raise RuntimeError(message)


def _index_facts(name: str) -> tuple[bool, str | None]:
    row = op.get_bind().execute(
        sa.text(
            "SELECT index.indisvalid,pg_get_indexdef(index.indexrelid) "
            "FROM pg_index AS index WHERE index.indexrelid=to_regclass('public.' || :name)"
        ),
        {"name": name},
    ).one_or_none()
    return (False, None) if row is None else (bool(row[0]), str(row[1]))


def _prepare_downgrade_index(name: str, expected: str, create_sql: str) -> bool:
    valid, definition = _index_facts(name)
    if valid and definition == expected:
        return False
    if definition is not None and definition != expected:
        raise RuntimeError(f"refusing to replace unexpected downgrade index public.{name}")
    with op.get_context().autocommit_block():
        if definition is not None:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
        try:
            op.execute(create_sql)
        except BaseException:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
            raise
    valid, definition = _index_facts(name)
    if not valid or definition != expected:
        raise RuntimeError(f"downgrade index public.{name} is not exact and valid")
    return True


def _preflight() -> None:
    _assert_no_row(
        "SELECT 1 FROM public.page_templates template "
        "LEFT JOIN public.products product ON product.tenant_id=template.tenant_id AND product.id=template.product_id "
        "WHERE template.product_id IS NOT NULL AND product.id IS NULL LIMIT 1",
        "page template product ownership preflight failed",
    )
    _assert_no_row(
        "SELECT 1 FROM public.page_versions version LEFT JOIN public.page_templates template "
        "ON template.tenant_id=version.tenant_id AND template.id=version.page_template_id "
        "WHERE template.id IS NULL LIMIT 1",
        "page version template ownership preflight failed",
    )
    _assert_no_row(
        "SELECT 1 FROM public.page_versions version LEFT JOIN public.accounts account "
        "ON account.tenant_id=version.created_by_tenant_id AND account.id=version.created_by "
        "WHERE account.id IS NULL LIMIT 1",
        "page version creator ownership preflight failed",
    )
    _assert_no_row(
        "SELECT 1 FROM public.launch_releases release LEFT JOIN public.page_versions version "
        "ON version.tenant_id=release.tenant_id AND version.page_template_id=release.page_template_id "
        "AND version.id=release.page_version_id WHERE version.id IS NULL LIMIT 1",
        "launch release page ownership preflight failed",
    )
    _assert_no_row(
        "SELECT 1 FROM public.page_templates WHERE status NOT IN ('active','archived') "
        "OR template_type NOT IN ('product_info','traceability','brand_story') LIMIT 1",
        "page template state preflight failed",
    )
    _assert_no_row(
        "SELECT 1 FROM public.page_versions WHERE version<=0 "
        "OR status NOT IN ('draft','published','archived') "
        "OR (status='published' AND published_at IS NULL) "
        "OR (status='draft' AND published_at IS NOT NULL) LIMIT 1",
        "page version state preflight failed",
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _preflight()
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute(
        f"ALTER TABLE public.page_versions ADD CONSTRAINT {_CREATOR_NOT_NULL} "
        "CHECK (created_by_tenant_id IS NOT NULL) NOT VALID"
    )
    op.execute(f"ALTER TABLE public.page_versions VALIDATE CONSTRAINT {_CREATOR_NOT_NULL}")
    op.execute("ALTER TABLE public.page_versions ALTER COLUMN created_by_tenant_id SET NOT NULL")
    op.execute(
        f"ALTER TABLE public.page_templates ADD CONSTRAINT {_TEMPLATE_KEY} UNIQUE USING INDEX {_TEMPLATE_KEY}"
    )
    op.execute(
        f"ALTER TABLE public.page_versions ADD CONSTRAINT {_VERSION_KEY} UNIQUE USING INDEX {_VERSION_KEY}"
    )
    for table, name, expression in _CHECKS:
        op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} CHECK ({expression}) NOT VALID")
    op.execute(
        f"ALTER TABLE public.page_templates ADD CONSTRAINT {_TEMPLATE_PRODUCT_FK} "
        "FOREIGN KEY (tenant_id,product_id) REFERENCES public.products(tenant_id,id) NOT VALID"
    )
    op.execute(
        f"ALTER TABLE public.page_versions ADD CONSTRAINT {_VERSION_TEMPLATE_FK} "
        "FOREIGN KEY (tenant_id,page_template_id) "
        "REFERENCES public.page_templates(tenant_id,id) NOT VALID"
    )
    op.execute(
        f"ALTER TABLE public.page_versions ADD CONSTRAINT {_VERSION_CREATOR_FK} "
        "FOREIGN KEY (created_by_tenant_id,created_by) REFERENCES public.accounts(tenant_id,id) NOT VALID"
    )
    op.execute(
        f"ALTER TABLE public.launch_releases ADD CONSTRAINT {_LAUNCH_TEMPLATE_FK} "
        "FOREIGN KEY (tenant_id,page_template_id) "
        "REFERENCES public.page_templates(tenant_id,id) NOT VALID"
    )
    op.execute(
        f"ALTER TABLE public.launch_releases ADD CONSTRAINT {_LAUNCH_VERSION_FK} "
        "FOREIGN KEY (tenant_id,page_template_id,page_version_id) "
        "REFERENCES public.page_versions(tenant_id,page_template_id,id) NOT VALID"
    )
    for table, name, _expression in _CHECKS:
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")
    for table, name in (
        ("page_templates", _TEMPLATE_PRODUCT_FK),
        ("page_versions", _VERSION_TEMPLATE_FK),
        ("page_versions", _VERSION_CREATOR_FK),
        ("launch_releases", _LAUNCH_TEMPLATE_FK),
        ("launch_releases", _LAUNCH_VERSION_FK),
    ):
        op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")
    op.execute(
        "ALTER TABLE public.page_versions DROP CONSTRAINT fk_page_versions_page_template_id"
    )
    op.execute(
        "ALTER TABLE public.launch_releases DROP CONSTRAINT launch_releases_page_template_id_fkey"
    )
    op.execute(
        "ALTER TABLE public.launch_releases DROP CONSTRAINT launch_releases_page_version_id_fkey"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _assert_no_row(
        "SELECT 1 FROM public.page_versions "
        "WHERE created_by_tenant_id IS DISTINCT FROM tenant_id LIMIT 1",
        "page authority downgrade blocked: acting-agency creator facts exist",
    )
    created_indexes: list[str] = []
    try:
        for name, (expected, create_sql) in _DOWNGRADE_INDEXES.items():
            if _prepare_downgrade_index(name, expected, create_sql):
                created_indexes.append(name)
    except BaseException:
        # The revision did not advance and no short DDL ran. Remove only the
        # replacement indexes created by this attempt so the catalog returns
        # to its exact entry state; preserve valid indexes from an earlier retry.
        with op.get_context().autocommit_block():
            for name in reversed(created_indexes):
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
        raise

    # Everything below is one short transaction. A lock/DDL failure leaves the
    # u5a2 constraints intact; the already-valid replacement indexes are safe
    # to reuse on retry.
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute(
        "ALTER TABLE public.page_versions ADD CONSTRAINT fk_page_versions_page_template_id "
        "FOREIGN KEY (page_template_id) REFERENCES public.page_templates(id)"
    )
    op.execute(
        "ALTER TABLE public.launch_releases ADD CONSTRAINT launch_releases_page_template_id_fkey "
        "FOREIGN KEY (page_template_id) REFERENCES public.page_templates(id)"
    )
    op.execute(
        "ALTER TABLE public.launch_releases ADD CONSTRAINT launch_releases_page_version_id_fkey "
        "FOREIGN KEY (page_version_id) REFERENCES public.page_versions(id)"
    )
    for table, name in reversed(
        (
            ("page_templates", _TEMPLATE_PRODUCT_FK),
            ("page_versions", _VERSION_TEMPLATE_FK),
            ("page_versions", _VERSION_CREATOR_FK),
            ("launch_releases", _LAUNCH_TEMPLATE_FK),
            ("launch_releases", _LAUNCH_VERSION_FK),
        )
    ):
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {name}")
    for table, name, _expression in reversed(_CHECKS):
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT {name}")
    op.execute("ALTER TABLE public.page_versions ALTER COLUMN created_by_tenant_id DROP NOT NULL")
    op.execute(f"ALTER TABLE public.page_versions DROP CONSTRAINT {_CREATOR_NOT_NULL}")
    op.execute(f"ALTER TABLE public.page_versions DROP CONSTRAINT {_VERSION_KEY}")
    op.execute(f"ALTER TABLE public.page_templates DROP CONSTRAINT {_TEMPLATE_KEY}")
    for canonical, replacement in {
        _TEMPLATE_KEY: "uq_page_templates_tenant_id_id_downgrade",
        _VERSION_KEY: "uq_page_versions_tenant_template_id_downgrade",
    }.items():
        op.execute(f"ALTER INDEX public.{replacement} RENAME TO {canonical}")
