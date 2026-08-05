"""Enforce connector tenant consistency.

Revision ID: a313fa710459
Revises: 937c5e56d157
Create Date: 2026-08-03 20:19:01.191043

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a313fa710459"
down_revision: str | Sequence[str] | None = "937c5e56d157"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "public"


def _prepare_unique_index(index_name: str, table_name: str, columns: tuple[str, ...]) -> None:
    """Create or safely reuse the exact unique index required by this migration."""
    bind = op.get_bind()
    table = (
        bind.execute(
            sa.text(
                """
                SELECT tbl.oid AS table_oid, ns.oid AS schema_oid, ns.nspname AS schema_name, tbl.relname AS table_name
                FROM pg_class AS tbl
                JOIN pg_namespace AS ns ON ns.oid = tbl.relnamespace
                WHERE tbl.oid = to_regclass(:table_name)
                  AND ns.nspname = :schema_name
                  AND tbl.relkind IN ('r', 'p')
                """
            ),
            {"table_name": f"{_SCHEMA}.{table_name}", "schema_name": _SCHEMA},
        )
        .mappings()
        .one_or_none()
    )
    if table is None:
        raise RuntimeError(f"Required table {table_name} is not visible on the migration search_path")

    row = (
        bind.execute(
            sa.text(
                """
                SELECT
                    tbl.relname AS table_name,
                    i.indrelid AS table_oid,
                    i.indisvalid,
                    i.indisready,
                    i.indisunique,
                    am.amname AS access_method,
                    i.indpred IS NULL AS has_no_predicate,
                    i.indexprs IS NULL AS has_no_expressions,
                    i.indnatts = i.indnkeyatts AS has_no_includes,
                    NOT EXISTS (
                        SELECT 1
                        FROM unnest(i.indclass::oid[]) WITH ORDINALITY AS cls(opclass_oid, ordinality)
                        JOIN pg_opclass AS opc ON opc.oid = cls.opclass_oid
                        WHERE cls.ordinality <= i.indnkeyatts
                          AND NOT opc.opcdefault
                    ) AS uses_default_opclasses,
                    ARRAY(
                        SELECT attr.attname
                        FROM unnest(i.indkey::smallint[]) WITH ORDINALITY AS key(attnum, ordinality)
                        JOIN pg_attribute AS attr
                          ON attr.attrelid = i.indrelid
                         AND attr.attnum = key.attnum
                        WHERE key.ordinality <= i.indnkeyatts
                        ORDER BY key.ordinality
                    ) AS key_columns,
                    ARRAY(
                        SELECT option
                        FROM unnest(i.indoption::smallint[]) WITH ORDINALITY AS opts(option, ordinality)
                        WHERE opts.ordinality <= i.indnkeyatts
                        ORDER BY opts.ordinality
                    ) AS key_options
                FROM pg_class AS idx
                JOIN pg_namespace AS ns ON ns.oid = idx.relnamespace
                JOIN pg_index AS i ON i.indexrelid = idx.oid
                JOIN pg_class AS tbl ON tbl.oid = i.indrelid
                JOIN pg_am AS am ON am.oid = idx.relam
                WHERE ns.oid = :schema_oid
                  AND idx.relname = :index_name
                """
            ),
            {"index_name": index_name, "schema_oid": table["schema_oid"]},
        )
        .mappings()
        .one_or_none()
    )
    preparer = bind.dialect.identifier_preparer
    quoted_schema = preparer.quote_identifier(table["schema_name"])
    quoted_table = preparer.quote_identifier(table["table_name"])
    quoted_index = preparer.quote_identifier(index_name)
    qualified_index = f"{quoted_schema}.{quoted_index}"
    if row is not None and not row["indisvalid"]:
        # A failed/cancelled CREATE INDEX CONCURRENTLY leaves an invalid shell.
        # It is safe to remove only because invalid indexes cannot enforce a
        # constraint. A valid unknown index is never deleted below.
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {qualified_index}")
        row = None

    if row is not None:
        exact_match = (
            row["table_oid"] == table["table_oid"]
            and row["table_name"] == table["table_name"]
            and row["indisready"]
            and row["indisunique"]
            and row["access_method"] == "btree"
            and row["has_no_predicate"]
            and row["has_no_expressions"]
            and row["has_no_includes"]
            and row["uses_default_opclasses"]
            and tuple(row["key_columns"]) == columns
            and all(option == 0 for option in row["key_options"])
        )
        if not exact_match:
            raise RuntimeError(
                f"Refusing to reuse valid index {index_name}: its table, uniqueness, or key definition differs"
            )
        return

    quoted_columns = ", ".join(preparer.quote_identifier(column) for column in columns)
    op.execute(
        # PostgreSQL creates an index in its table's schema and does not accept
        # a schema-qualified name in CREATE INDEX; the table remains qualified.
        f"CREATE UNIQUE INDEX CONCURRENTLY {quoted_index} "
        f"ON {quoted_schema}.{quoted_table} ({quoted_columns})"
    )


def upgrade() -> None:
    # Tenant mismatches cannot be repaired safely here: silently clearing a
    # connector would change benefit fulfillment semantics. Stop with a clear
    # integrity error so operators can investigate and correct the exact rows.
    op.execute(
        """
        DO $$
        DECLARE
            invalid_benefits bigint;
            invalid_delivery_connectors bigint;
            invalid_delivery_benefits bigint;
        BEGIN
            SELECT count(*) INTO invalid_benefits
            FROM public.benefits AS b
            JOIN public.connectors AS c ON c.id = b.connector_id
            WHERE b.connector_id IS NOT NULL
              AND b.tenant_id <> c.tenant_id;

            SELECT count(*) INTO invalid_delivery_connectors
            FROM public.benefit_deliveries AS d
            LEFT JOIN public.connectors AS c ON c.id = d.connector_id
            WHERE c.id IS NULL OR d.tenant_id <> c.tenant_id;

            SELECT count(*) INTO invalid_delivery_benefits
            FROM public.benefit_deliveries AS d
            LEFT JOIN public.benefits AS b ON b.id = d.benefit_id
            WHERE d.benefit_id IS NOT NULL
              AND (b.id IS NULL OR d.tenant_id <> b.tenant_id);

            IF invalid_benefits > 0
               OR invalid_delivery_connectors > 0
               OR invalid_delivery_benefits > 0 THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = format(
                        'connector tenant consistency preflight failed: benefits=%s, ' ||
                        'delivery_connectors=%s, delivery_benefits=%s',
                        invalid_benefits,
                        invalid_delivery_connectors,
                        invalid_delivery_benefits
                    ),
                    HINT = 'Correct or explicitly detach the listed cross-tenant ' ||
                           'references before retrying the migration.';
            END IF;
        END
        $$;
        """
    )

    # Rollout: build the supporting unique indexes outside the migration
    # transaction so writes remain available during the table scans. The
    # catalog guard repairs invalid concurrent-index shells, reuses only exact
    # valid definitions, and fails closed on unknown valid same-name indexes.
    with op.get_context().autocommit_block():
        _prepare_unique_index("uq_connectors_tenant_id_id", "connectors", ("tenant_id", "id"))
        _prepare_unique_index("uq_benefits_tenant_id_id", "benefits", ("tenant_id", "id"))

    # Attaching a prebuilt index avoids the long table scan of an inline
    # UNIQUE constraint. The short metadata lock still needs a quiet rollout
    # window and normal lock/statement timeout monitoring.
    op.execute(
        "ALTER TABLE public.connectors ADD CONSTRAINT uq_connectors_tenant_id_id "
        "UNIQUE USING INDEX uq_connectors_tenant_id_id"
    )
    op.execute(
        "ALTER TABLE public.benefits ADD CONSTRAINT uq_benefits_tenant_id_id "
        "UNIQUE USING INDEX uq_benefits_tenant_id_id"
    )

    # Keep the existing single-column FK with ON DELETE SET NULL. Combined
    # with the tenant-aware FK below, it gives cross-dialect metadata the same
    # safe result as PostgreSQL's column-list SET NULL extension: connector_id
    # is cleared while the non-null tenant_id is preserved.
    op.execute(
        "ALTER TABLE public.benefits ADD CONSTRAINT fk_benefits_tenant_connector "
        "FOREIGN KEY (tenant_id, connector_id) "
        "REFERENCES public.connectors (tenant_id, id) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.benefit_deliveries ADD CONSTRAINT fk_benefit_deliveries_tenant_connector "
        "FOREIGN KEY (tenant_id, connector_id) "
        "REFERENCES public.connectors (tenant_id, id) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.benefit_deliveries ADD CONSTRAINT fk_benefit_deliveries_tenant_benefit "
        "FOREIGN KEY (tenant_id, benefit_id) "
        "REFERENCES public.benefits (tenant_id, id) NOT VALID"
    )
    # VALIDATE takes a weaker lock than adding an immediately validated FK and
    # permits normal reads/writes while PostgreSQL checks historical rows.
    op.execute("ALTER TABLE public.benefits VALIDATE CONSTRAINT fk_benefits_tenant_connector")
    op.execute(
        "ALTER TABLE public.benefit_deliveries VALIDATE CONSTRAINT fk_benefit_deliveries_tenant_connector"
    )
    op.execute("ALTER TABLE public.benefit_deliveries VALIDATE CONSTRAINT fk_benefit_deliveries_tenant_benefit")

    op.execute("ALTER TABLE public.benefit_deliveries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.benefit_deliveries FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON public.benefit_deliveries
        USING (
            tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
            )
        )
        WITH CHECK (
            tenant_id = public.current_tenant_id()
            OR (
                public.current_tenant_id() IS NULL
                AND current_setting('app.bypass_rls', true) = 'true'
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON public.benefit_deliveries")
    op.execute("ALTER TABLE public.benefit_deliveries NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.benefit_deliveries DISABLE ROW LEVEL SECURITY")
    op.drop_constraint(
        "fk_benefit_deliveries_tenant_benefit", "benefit_deliveries", type_="foreignkey", schema=_SCHEMA
    )
    op.drop_constraint(
        "fk_benefit_deliveries_tenant_connector", "benefit_deliveries", type_="foreignkey", schema=_SCHEMA
    )
    op.drop_constraint("fk_benefits_tenant_connector", "benefits", type_="foreignkey", schema=_SCHEMA)
    op.drop_constraint("uq_benefits_tenant_id_id", "benefits", type_="unique", schema=_SCHEMA)
    op.drop_constraint("uq_connectors_tenant_id_id", "connectors", type_="unique", schema=_SCHEMA)
