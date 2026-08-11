"""Expand page creator authority without coupling transactional DDL to index builds.

Rollout order: deploy the backward-compatible request transaction plumbing that
sets ``app.auth_session_id`` after durable-principal revalidation before this
revision. Legacy tokens without a durable session deliberately fail closed.

Revision ID: u5a0b1c2d3e4
Revises: u4c3a4b5c6d7
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u5a0b1c2d3e4"
down_revision: str | None = "u4c3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    op.add_column("page_versions", sa.Column("created_by_tenant_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        CREATE FUNCTION public.populate_page_version_creator_tenant() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
        AS $function$
        DECLARE requested_auth_session_id uuid;
        DECLARE target_tenant_id uuid;
        DECLARE resolved_tenant_id uuid;
        DECLARE matched_authorization_id uuid;
        DECLARE now_at timestamptz:=CURRENT_TIMESTAMP;
        BEGIN
            target_tenant_id:=public.current_tenant_id();
            IF target_tenant_id IS NULL OR target_tenant_id IS DISTINCT FROM NEW.tenant_id THEN
                RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page creator tenant context mismatch';
            END IF;
            BEGIN
                requested_auth_session_id:=NULLIF(current_setting('app.auth_session_id',true),'')::uuid;
            EXCEPTION WHEN invalid_text_representation THEN
                RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page creator session context is invalid';
            END;
            IF requested_auth_session_id IS NULL OR NEW.created_by IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='live page creator session is required';
            END IF;
            SELECT session.tenant_id INTO resolved_tenant_id
            FROM public.auth_sessions AS session
            JOIN public.accounts AS account
              ON account.tenant_id=session.tenant_id AND account.id=session.account_id
            JOIN public.tenants AS principal_tenant ON principal_tenant.id=session.tenant_id
            WHERE session.id=requested_auth_session_id
              AND session.account_id=NEW.created_by
              AND session.revoked_at IS NULL AND session.expires_at>now_at
              AND session.auth_version=account.auth_version AND account.is_active
              AND principal_tenant.status='active';
            IF resolved_tenant_id IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page creator session is not live';
            END IF;
            PERFORM target.id FROM public.tenants AS target
            WHERE target.id=target_tenant_id AND target.status='active' AND target.tenant_type='brand';
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page target tenant is unavailable';
            END IF;
            IF resolved_tenant_id IS DISTINCT FROM target_tenant_id THEN
                SELECT agency_auth.id INTO matched_authorization_id
                FROM public.agency_authorizations AS agency_auth
                JOIN public.tenants AS agency ON agency.id=agency_auth.agency_tenant_id
                WHERE agency_auth.agency_tenant_id=resolved_tenant_id
                  AND agency_auth.client_tenant_id=target_tenant_id
                  AND agency_auth.status='active'
                  AND (agency_auth.expires_at IS NULL OR agency_auth.expires_at>now_at)
                  AND agency_auth.scope::jsonb ? 'pages'
                  AND agency.status='active' AND agency.tenant_type='agency';
                IF matched_authorization_id IS NULL THEN
                    RAISE EXCEPTION USING ERRCODE='42501',
                        MESSAGE='page creator lacks a live pages authorization';
                END IF;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.account_roles AS account_role
                JOIN public.role_permissions AS role_permission
                  ON role_permission.tenant_id=account_role.tenant_id
                 AND role_permission.role_id=account_role.role_id
                JOIN public.permissions AS permission
                  ON permission.tenant_id=role_permission.tenant_id
                 AND permission.id=role_permission.permission_id
                WHERE account_role.tenant_id=resolved_tenant_id
                  AND account_role.account_id=NEW.created_by
                  AND permission.code='page:create'
            ) THEN
                RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='page creator lacks page:create permission';
            END IF;
            NEW.created_by_tenant_id:=resolved_tenant_id;
            RETURN NEW;
        END;
        $function$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_populate_page_version_creator_tenant "
        "BEFORE INSERT OR UPDATE OF created_by ON public.page_versions "
        "FOR EACH ROW EXECUTE FUNCTION public.populate_page_version_creator_tenant()"
    )
    op.execute("REVOKE ALL ON FUNCTION public.populate_page_version_creator_tenant() FROM PUBLIC")
    op.execute(
        """
        DO $block$
        BEGIN
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='yimatong_app') THEN
                REVOKE ALL ON FUNCTION public.populate_page_version_creator_tenant() FROM yimatong_app;
            END IF;
        END
        $block$
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("DROP TRIGGER trg_populate_page_version_creator_tenant ON public.page_versions")
    op.execute("DROP FUNCTION public.populate_page_version_creator_tenant()")
    op.drop_column("page_versions", "created_by_tenant_id")
