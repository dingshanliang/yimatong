"""Attach the tenant identity key and unvalidated lifecycle binding.

Revision ID: 9e53a7c1d4f6
Revises: 8d42f6b0c3e5
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9e53a7c1d4f6"
down_revision: str | None = "8d42f6b0c3e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMPAT_FUNCTION = "populate_legacy_frozen_provenance"
_CANONICAL_IDENTITY_INDEX = "uq_code_items_tenant_id_id"
_DOWNGRADE_IDENTITY_INDEX = "uq_code_items_tenant_id_id_downgrade"


def _index_valid(name: str) -> bool | None:
    return op.get_bind().execute(
        sa.text(
            "SELECT idx.indisvalid FROM pg_index idx "
            "WHERE idx.indexrelid=to_regclass('public.' || :name)"
        ),
        {"name": name},
    ).scalar()


def _prepare_downgrade_identity_index() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute("SET statement_timeout = '10min'")
        if _index_valid(_DOWNGRADE_IDENTITY_INDEX) is False:
            op.execute(f"DROP INDEX CONCURRENTLY public.{_DOWNGRADE_IDENTITY_INDEX}")
        if _index_valid(_DOWNGRADE_IDENTITY_INDEX) is None:
            op.execute(
                f"CREATE UNIQUE INDEX CONCURRENTLY {_DOWNGRADE_IDENTITY_INDEX} "
                "ON public.code_items (tenant_id,id)"
            )
        if _index_valid(_DOWNGRADE_IDENTITY_INDEX) is not True:
            raise RuntimeError(f"concurrent index {_DOWNGRADE_IDENTITY_INDEX} is not valid")
        op.execute("RESET lock_timeout")
        op.execute("RESET statement_timeout")


def _install_compat_guard() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_COMPAT_FUNCTION}()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF NEW.status::text = 'frozen' THEN
                IF NEW.freeze_provenance_version IS NULL THEN
                    NEW.frozen_from_status := CASE
                        WHEN TG_OP = 'UPDATE' AND OLD.status::text = 'bound' THEN 'bound'
                        WHEN NEW.bound_at IS NOT NULL THEN 'bound'
                        ELSE 'activated'
                    END;
                    NEW.frozen_at := COALESCE(NEW.updated_at, NEW.created_at, CURRENT_TIMESTAMP);
                    NEW.freeze_provenance_version := 0;
                END IF;
            ELSIF TG_OP = 'UPDATE' AND OLD.status::text = 'frozen' THEN
                NEW.frozen_from_status := NULL;
                NEW.frozen_at := NULL;
                NEW.frozen_by := NULL;
                NEW.freeze_reason := NULL;
                NEW.freeze_provenance_version := NULL;
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_COMPAT_FUNCTION}() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_populate_legacy_frozen_provenance "
        "BEFORE INSERT OR UPDATE OF status ON public.code_items FOR EACH ROW "
        f"EXECUTE FUNCTION public.{_COMPAT_FUNCTION}()"
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute(
        "ALTER TABLE public.code_items ADD CONSTRAINT uq_code_items_tenant_id_id "
        "UNIQUE USING INDEX uq_code_items_tenant_id_id"
    )
    op.execute(
        "ALTER TABLE public.interception_records "
        "ADD CONSTRAINT fk_interception_records_tenant_code_item "
        "FOREIGN KEY (tenant_id,code_item_id) REFERENCES public.code_items(tenant_id,id) NOT VALID"
    )
    _install_compat_guard()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _prepare_downgrade_identity_index()
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("DROP TRIGGER trg_populate_legacy_frozen_provenance ON public.code_items")
    op.execute(f"DROP FUNCTION public.{_COMPAT_FUNCTION}()")
    op.drop_constraint("fk_interception_records_tenant_code_item", "interception_records", type_="foreignkey")
    op.drop_constraint("uq_code_items_tenant_id_id", "code_items", type_="unique")
    op.execute(
        f"ALTER INDEX public.{_DOWNGRADE_IDENTITY_INDEX} RENAME TO {_CANONICAL_IDENTITY_INDEX}"
    )
