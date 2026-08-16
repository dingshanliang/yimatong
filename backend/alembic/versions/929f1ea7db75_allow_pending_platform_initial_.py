"""Allow a pending platform initial administrator.

Revision ID: 929f1ea7db75
Revises: k9f0a1b2c3d4
Create Date: 2026-08-16 21:22:00.873117

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "929f1ea7db75"
down_revision: str | Sequence[str] | None = "k9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FUNCTION_PREFIX = """
CREATE OR REPLACE FUNCTION public.assert_tenant_has_active_admin()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    governed_tenant uuid;
    requires_check boolean := false;
BEGIN
    IF TG_TABLE_NAME = 'account_roles' THEN
        SELECT account.tenant_id,
               EXISTS (SELECT 1 FROM public.roles WHERE id = OLD.role_id AND name = 'admin')
        INTO governed_tenant, requires_check
        FROM public.accounts AS account WHERE account.id = OLD.account_id;
    ELSIF TG_TABLE_NAME = 'accounts' THEN
        governed_tenant := CASE WHEN TG_OP = 'INSERT' THEN NEW.tenant_id ELSE OLD.tenant_id END;
        requires_check := TG_OP = 'INSERT' OR TG_OP = 'DELETE' OR (OLD.is_active AND NOT NEW.is_active);
    ELSIF TG_TABLE_NAME = 'roles' THEN
        governed_tenant := OLD.tenant_id;
        requires_check := OLD.name = 'admin' AND (TG_OP = 'DELETE' OR NEW.name <> 'admin');
    END IF;
    IF requires_check AND NOT EXISTS (
        SELECT 1
        FROM public.accounts AS account
        JOIN public.account_roles AS mapping ON mapping.account_id = account.id
        JOIN public.roles AS role ON role.id = mapping.role_id
        WHERE account.tenant_id = governed_tenant
          AND role.tenant_id = governed_tenant
          AND account.is_active IS TRUE
          AND role.name = 'admin'
    ) THEN
"""

_FUNCTION_SUFFIX = """
        RAISE EXCEPTION 'tenant % must retain at least one active administrator', governed_tenant
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$function$
"""


def upgrade() -> None:
    """Permit only the platform's recorded initial-admin activation window."""

    op.execute(
        _FUNCTION_PREFIX
        + """
        IF TG_TABLE_NAME = 'accounts' AND TG_OP = 'INSERT' THEN
            IF NEW.is_active IS FALSE AND EXISTS (
                SELECT 1
                FROM public.platform_tenant_openings AS opening
                JOIN public.account_roles AS mapping
                  ON mapping.account_id = opening.initial_admin_id
                JOIN public.roles AS role
                  ON role.id = mapping.role_id
                WHERE opening.tenant_id = governed_tenant
                  AND opening.initial_admin_id = NEW.id
                  AND opening.initial_admin_state = 'pending_activation'
                  AND mapping.tenant_id = governed_tenant
                  AND role.tenant_id = governed_tenant
                  AND role.name = 'admin'
            ) THEN
                RETURN NULL;
            END IF;
        END IF;
"""
        + _FUNCTION_SUFFIX
    )


def downgrade() -> None:
    """Restore the original unconditional active-administrator guard."""

    # Hold the same governance relations against concurrent writes from the
    # compatibility check through the function replacement and transaction
    # commit. Otherwise a platform opening could commit between the check and
    # restoration of the unconditional guard.
    op.execute(
        "LOCK TABLE public.accounts, public.account_roles, public.roles, "
        "public.platform_tenant_openings IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute(
        """
        DO $block$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.platform_tenant_openings AS opening
                JOIN public.accounts AS pending_admin
                  ON pending_admin.id = opening.initial_admin_id
                 AND pending_admin.tenant_id = opening.tenant_id
                WHERE opening.initial_admin_state = 'pending_activation'
                  AND pending_admin.is_active IS FALSE
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.accounts AS active_account
                      JOIN public.account_roles AS mapping
                        ON mapping.account_id = active_account.id
                      JOIN public.roles AS role
                        ON role.id = mapping.role_id
                      WHERE active_account.tenant_id = opening.tenant_id
                        AND role.tenant_id = opening.tenant_id
                        AND active_account.is_active IS TRUE
                        AND role.name = 'admin'
                  )
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 929f1ea7db75 while a platform initial administrator is pending activation'
                    USING ERRCODE = 'check_violation';
            END IF;
        END
        $block$
        """
    )
    op.execute(_FUNCTION_PREFIX + _FUNCTION_SUFFIX)
