"""Align repurchase workbench authority with campaign:manage permission code.

Revision ID: a7f2c9d41e58
Revises: 5e3f9a4bac21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a7f2c9d41e58"
down_revision: str | Sequence[str] | None = "5e3f9a4bac21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CHECK = "permission.code='campaign:write'"
_NEW_CHECK = "permission.code='campaign:manage'"
_FUNCTION = "public.mutate_repurchase_work_item_authority(uuid,jsonb)"


def _replace_check(old: str, new: str) -> None:
    escaped_old = old.replace("'", "''")
    escaped_new = new.replace("'", "''")
    op.execute(
        f"""
        DO $migration$
        DECLARE current_definition text; patched_definition text;
        BEGIN
          SELECT pg_get_functiondef('{_FUNCTION}'::regprocedure) INTO current_definition;
          patched_definition := replace(current_definition, '{escaped_old}', '{escaped_new}');
          IF patched_definition=current_definition THEN
            RAISE EXCEPTION 'repurchase workbench authority campaign:write check not found';
          END IF;
          EXECUTE patched_definition;
        END
        $migration$;
        """
    )


def upgrade() -> None:
    _replace_check(_OLD_CHECK, _NEW_CHECK)


def downgrade() -> None:
    _replace_check(_NEW_CHECK, _OLD_CHECK)
