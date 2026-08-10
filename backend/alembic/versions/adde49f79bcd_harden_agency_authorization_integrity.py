"""Harden agency authorization integrity and cross-tenant audit append.

Revision ID: adde49f79bcd
Revises: 649cdfd94581
Create Date: 2026-08-10 10:20:43.564007

The authorization relation is deliberately dual-tenant: the client owns grant
changes while the agency may read the resulting capability.  This revision
keeps that shape, but moves renewal/revocation and cross-client audit append
behind narrow database functions.  The relation is expected to remain small;
all validation scans and metadata locks are bounded before rollout.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "adde49f79bcd"
down_revision: str | None = "649cdfd94581"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_ROLE = "yimatong_app"
_BACKUP_TABLE = "agency_authorization_integrity_backups"
_SCOPE_FUNCTION = "agency_authorization_scope_is_canonical"
_ROW_GUARD_FUNCTION = "guard_agency_authorization_row"
_RENEW_FUNCTION = "renew_agency_authorization"
_REVOKE_FUNCTION = "revoke_agency_authorization"
_AUDIT_FUNCTION = "append_authenticated_audit_event"
_QUOTA_ROLLOUT_ADVISORY_LOCK_KEY = 6434892150882653249


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": _RUNTIME_ROLE})
        .scalar_one()
    )


def _guarded_bypass() -> str:
    return """
    (
        public.current_tenant_id() IS NULL
        AND current_setting('app.bypass_rls', true) = 'true'
        AND has_parameter_privilege(session_user, 'app.bypass_rls', 'SET')
    )
    """


def _install_scope_contract() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_SCOPE_FUNCTION}(candidate json)
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $function$
            SELECT
                json_typeof(candidate) = 'array'
                AND json_array_length(candidate) > 0
                AND NOT EXISTS (
                    SELECT 1
                    FROM json_array_elements(candidate) AS item(value)
                    WHERE json_typeof(item.value) <> 'string'
                       OR trim(both '"' from item.value::text) NOT IN (
                            'products', 'pages', 'campaigns', 'codes', 'analytics', 'release:execute'
                       )
                )
                AND candidate::jsonb = (
                    SELECT jsonb_agg(value ORDER BY ordering)
                    FROM (
                        SELECT DISTINCT value,
                               CASE value
                                   WHEN 'products' THEN 1
                                   WHEN 'pages' THEN 2
                                   WHEN 'campaigns' THEN 3
                                   WHEN 'codes' THEN 4
                                   WHEN 'analytics' THEN 5
                                   WHEN 'release:execute' THEN 6
                               END AS ordering
                        FROM jsonb_array_elements_text(candidate::jsonb) AS scope(value)
                    ) AS canonical
                )
        $function$
        """
    )
    # PostgreSQL evaluates CHECK helper privileges as the writing principal.
    # This pure immutable predicate reads no relations, so keep it executable
    # while table ACLs and the three mutation/audit interfaces stay narrow.
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_SCOPE_FUNCTION}(json) TO PUBLIC")


def _preflight_and_canonicalize() -> None:
    op.create_table(
        _BACKUP_TABLE,
        sa.Column("authorization_id", sa.Uuid(), nullable=False),
        sa.Column("original_scope", sa.JSON(), nullable=False),
        sa.Column("updated_at_was_null", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("authorization_id"),
        schema="public",
    )
    if _runtime_role_exists():
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{_BACKUP_TABLE} FROM {_RUNTIME_ROLE}")

    op.execute(
        """
        DO $block$
        DECLARE invalid_ids text;
        BEGIN
            SELECT string_agg(id::text, ', ' ORDER BY id::text) INTO invalid_ids
            FROM public.agency_authorizations AS authz
            WHERE json_typeof(authz.scope) <> 'array'
               OR json_array_length(authz.scope) = 0
               OR EXISTS (
                    SELECT 1
                    FROM json_array_elements(authz.scope) AS item(value)
                    WHERE json_typeof(item.value) <> 'string'
                       OR trim(both '"' from item.value::text) NOT IN (
                            'products', 'pages', 'campaigns', 'codes', 'analytics', 'release:execute'
                       )
               );
            IF invalid_ids IS NOT NULL THEN
                RAISE EXCEPTION 'Invalid agency authorization scope rows: %', invalid_ids;
            END IF;

            SELECT string_agg(authz.id::text, ', ' ORDER BY authz.id::text) INTO invalid_ids
            FROM public.agency_authorizations AS authz
            JOIN public.tenants AS agency ON agency.id = authz.agency_tenant_id
            JOIN public.tenants AS client ON client.id = authz.client_tenant_id
            LEFT JOIN public.accounts AS grantor ON grantor.id = authz.granted_by
            WHERE authz.agency_tenant_id = authz.client_tenant_id
               OR agency.tenant_type <> 'agency'
               OR client.tenant_type <> 'brand'
               OR (authz.granted_by IS NOT NULL AND grantor.tenant_id IS DISTINCT FROM client.id)
               OR (authz.status = 'active' AND authz.revoked_at IS NOT NULL)
               OR (
                    authz.status = 'revoked'
                    AND (authz.revoked_at IS NULL OR authz.revoked_at < authz.granted_at)
               )
               OR (
                    authz.status = 'expired'
                    AND (
                        authz.revoked_at IS NOT NULL
                        OR authz.expires_at IS NULL
                        OR authz.expires_at < authz.granted_at
                    )
               )
               OR (
                    authz.expires_at IS NOT NULL
                    AND authz.expires_at < authz.granted_at
               );
            IF invalid_ids IS NOT NULL THEN
                RAISE EXCEPTION 'Invalid agency authorization ownership or lifecycle rows: %', invalid_ids;
            END IF;
        END
        $block$
        """
    )

    op.execute(
        f"""
        INSERT INTO public.{_BACKUP_TABLE} (authorization_id, original_scope, updated_at_was_null)
        SELECT id, scope, updated_at IS NULL
        FROM public.agency_authorizations
        WHERE updated_at IS NULL OR NOT public.{_SCOPE_FUNCTION}(scope)
        """
    )
    op.execute(
        """
        UPDATE public.agency_authorizations AS authz
        SET scope = (
            SELECT json_agg(value ORDER BY ordering) AS scope
            FROM (
                SELECT DISTINCT value,
                       CASE value
                           WHEN 'products' THEN 1
                           WHEN 'pages' THEN 2
                           WHEN 'campaigns' THEN 3
                           WHEN 'codes' THEN 4
                           WHEN 'analytics' THEN 5
                           WHEN 'release:execute' THEN 6
                       END AS ordering
                FROM json_array_elements_text(authz.scope) AS item(value)
            ) AS ordered_scope
        ),
            updated_at = COALESCE(authz.updated_at, authz.created_at, authz.granted_at, now())
        WHERE authz.updated_at IS NULL
           OR NOT public.agency_authorization_scope_is_canonical(authz.scope)
        """
    )


def _install_constraints() -> None:
    op.execute(
        """
        ALTER TABLE public.agency_authorizations
        ADD CONSTRAINT fk_agency_authorizations_client_grantor
        FOREIGN KEY (client_tenant_id, granted_by)
        REFERENCES public.accounts (tenant_id, id)
        NOT VALID
        """
    )
    op.execute(
        """
        ALTER TABLE public.agency_authorizations
        ADD CONSTRAINT ck_agency_authorizations_distinct_tenants
        CHECK (agency_tenant_id <> client_tenant_id) NOT VALID
        """
    )
    op.execute(
        f"""
        ALTER TABLE public.agency_authorizations
        ADD CONSTRAINT ck_agency_authorizations_scope_canonical
        CHECK (public.{_SCOPE_FUNCTION}(scope)) NOT VALID
        """
    )
    op.execute(
        """
        ALTER TABLE public.agency_authorizations
        ADD CONSTRAINT ck_agency_authorizations_lifecycle
        CHECK (
            (status = 'active' AND revoked_at IS NULL)
            OR (status = 'revoked' AND revoked_at IS NOT NULL AND revoked_at >= granted_at)
            OR (
                status = 'expired'
                AND revoked_at IS NULL
                AND expires_at IS NOT NULL
                AND expires_at >= granted_at
            )
        ) NOT VALID
        """
    )
    op.execute(
        """
        ALTER TABLE public.agency_authorizations
        ADD CONSTRAINT ck_agency_authorizations_expiry_after_grant
        CHECK (expires_at IS NULL OR expires_at >= granted_at) NOT VALID
        """
    )
    op.execute(
        """
        ALTER TABLE public.agency_authorizations
        ADD CONSTRAINT ck_agency_authorizations_updated_at_nn
        CHECK (updated_at IS NOT NULL) NOT VALID
        """
    )
    for constraint in (
        "fk_agency_authorizations_client_grantor",
        "ck_agency_authorizations_distinct_tenants",
        "ck_agency_authorizations_scope_canonical",
        "ck_agency_authorizations_lifecycle",
        "ck_agency_authorizations_expiry_after_grant",
        "ck_agency_authorizations_updated_at_nn",
    ):
        op.execute(f"ALTER TABLE public.agency_authorizations VALIDATE CONSTRAINT {constraint}")
    op.alter_column("agency_authorizations", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.drop_constraint("ck_agency_authorizations_updated_at_nn", "agency_authorizations", type_="check")


def _install_row_guards() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_ROW_GUARD_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE agency_type public.tenanttype;
        DECLARE client_type public.tenanttype;
        BEGIN
            SELECT tenant_type INTO agency_type FROM public.tenants WHERE id = NEW.agency_tenant_id;
            SELECT tenant_type INTO client_type FROM public.tenants WHERE id = NEW.client_tenant_id;
            IF agency_type IS DISTINCT FROM 'agency'::public.tenanttype
               OR client_type IS DISTINCT FROM 'brand'::public.tenanttype
               OR NEW.agency_tenant_id = NEW.client_tenant_id THEN
                RAISE EXCEPTION 'agency authorization endpoints must be one agency and one distinct brand client';
            END IF;

            IF TG_OP = 'UPDATE' THEN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.agency_tenant_id IS DISTINCT FROM OLD.agency_tenant_id
                   OR NEW.client_tenant_id IS DISTINCT FROM OLD.client_tenant_id
                   OR NEW.scope::jsonb IS DISTINCT FROM OLD.scope::jsonb
                   OR NEW.granted_by IS DISTINCT FROM OLD.granted_by
                   OR NEW.granted_at IS DISTINCT FROM OLD.granted_at
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'agency authorization grant identity and scope are immutable';
                END IF;
                IF OLD.status <> 'active'::public.agencyauthstatus
                   OR NEW.status NOT IN ('revoked'::public.agencyauthstatus, 'expired'::public.agencyauthstatus) THEN
                    RAISE EXCEPTION 'agency authorization status may only transition from active to revoked or expired';
                END IF;
            ELSIF NEW.status <> 'active'::public.agencyauthstatus THEN
                RAISE EXCEPTION 'new agency authorization rows must start active';
            END IF;

            IF NEW.status = 'active'::public.agencyauthstatus
               AND NEW.expires_at IS NOT NULL
               AND NEW.expires_at <= CURRENT_TIMESTAMP THEN
                RAISE EXCEPTION 'new active agency authorization must expire in the future';
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ROW_GUARD_FUNCTION}() FROM PUBLIC")
    op.execute(
        f"""
        CREATE TRIGGER trg_guard_agency_authorization_row
        BEFORE INSERT OR UPDATE ON public.agency_authorizations
        FOR EACH ROW EXECUTE FUNCTION public.{_ROW_GUARD_FUNCTION}()
        """
    )


def _install_authorization_interfaces() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_RENEW_FUNCTION}(
            requested_authorization_id uuid,
            requested_agency_id uuid,
            requested_client_id uuid,
            requested_scope jsonb,
            requested_actor_id uuid,
            requested_auth_session_id uuid,
            requested_expires_at timestamptz DEFAULT NULL
        ) RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE existing_authorization public.agency_authorizations%ROWTYPE;
        DECLARE created_id uuid;
        DECLARE canonical_scope json;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_client_id THEN
                RAISE EXCEPTION 'agency authorization client must match current tenant';
            END IF;
            IF requested_agency_id = requested_client_id THEN
                RAISE EXCEPTION 'agency and client tenants must differ';
            END IF;
            IF requested_expires_at IS NOT NULL AND requested_expires_at <= CURRENT_TIMESTAMP THEN
                RAISE EXCEPTION 'agency authorization expiry must be in the future';
            END IF;

            SELECT json_agg(value ORDER BY ordering) INTO canonical_scope
            FROM (
                SELECT DISTINCT value,
                       CASE value
                           WHEN 'products' THEN 1
                           WHEN 'pages' THEN 2
                           WHEN 'campaigns' THEN 3
                           WHEN 'codes' THEN 4
                           WHEN 'analytics' THEN 5
                           WHEN 'release:execute' THEN 6
                       END AS ordering
                FROM jsonb_array_elements_text(requested_scope) AS item(value)
                WHERE value IN ('products', 'pages', 'campaigns', 'codes', 'analytics', 'release:execute')
            ) AS ordered_scope;
            IF canonical_scope IS NULL
               OR jsonb_array_length(canonical_scope::jsonb) <> jsonb_array_length(requested_scope)
               OR NOT public.{_SCOPE_FUNCTION}(canonical_scope) THEN
                RAISE EXCEPTION 'agency authorization scope must be a nonempty unique whitelist array';
            END IF;

            PERFORM pg_advisory_xact_lock_shared({_QUOTA_ROLLOUT_ADVISORY_LOCK_KEY});
            PERFORM tenant.id
            FROM public.tenants AS tenant
            WHERE tenant.id IN (requested_agency_id, requested_client_id)
            ORDER BY tenant.id::text
            FOR UPDATE;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('auth-session:' || requested_auth_session_id::text, 0)
            );

            IF NOT EXISTS (
                SELECT 1 FROM public.tenants
                WHERE id = requested_agency_id AND tenant_type = 'agency' AND status = 'active'
            ) OR NOT EXISTS (
                SELECT 1 FROM public.tenants
                WHERE id = requested_client_id
                  AND tenant_type = 'brand'
                  AND status = 'active'
                  AND (plan_expires_at IS NULL OR plan_expires_at > CURRENT_TIMESTAMP)
            ) OR NOT EXISTS (
                SELECT 1
                FROM public.auth_sessions AS auth_session
                JOIN public.accounts AS account
                  ON account.id = auth_session.account_id
                 AND account.tenant_id = auth_session.tenant_id
                WHERE auth_session.id = requested_auth_session_id
                  AND auth_session.account_id = requested_actor_id
                  AND auth_session.tenant_id = requested_client_id
                  AND auth_session.revoked_at IS NULL
                  AND auth_session.expires_at > CURRENT_TIMESTAMP
                  AND auth_session.auth_version = account.auth_version
                  AND account.is_active
                  AND EXISTS (
                      SELECT 1
                      FROM public.account_roles AS account_role
                      JOIN public.role_permissions AS role_permission
                        ON role_permission.tenant_id = account_role.tenant_id
                       AND role_permission.role_id = account_role.role_id
                      JOIN public.permissions AS permission
                        ON permission.tenant_id = role_permission.tenant_id
                       AND permission.id = role_permission.permission_id
                      WHERE account_role.tenant_id = requested_client_id
                        AND account_role.account_id = requested_actor_id
                        AND permission.code = 'tenant:manage'
                  )
            ) THEN
                RAISE EXCEPTION 'agency authorization endpoints or grantor session are not live';
            END IF;

            PERFORM pg_advisory_xact_lock(
                hashtextextended(requested_agency_id::text || ':' || requested_client_id::text, 0)
            );
            SELECT * INTO existing_authorization
            FROM public.agency_authorizations
            WHERE agency_tenant_id = requested_agency_id
              AND client_tenant_id = requested_client_id
              AND status = 'active'
            ;

            IF FOUND THEN
                UPDATE public.agency_authorizations
                SET status = CASE
                        WHEN existing_authorization.expires_at IS NOT NULL
                             AND existing_authorization.expires_at <= CURRENT_TIMESTAMP
                        THEN 'expired'::public.agencyauthstatus
                        ELSE 'revoked'::public.agencyauthstatus
                    END,
                    revoked_at = CASE
                        WHEN existing_authorization.expires_at IS NOT NULL
                             AND existing_authorization.expires_at <= CURRENT_TIMESTAMP
                        THEN NULL
                        ELSE CURRENT_TIMESTAMP
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = existing_authorization.id;
            END IF;

            INSERT INTO public.agency_authorizations (
                id, agency_tenant_id, client_tenant_id, scope, status, granted_by,
                granted_at, revoked_at, expires_at, created_at, updated_at
            ) VALUES (
                requested_authorization_id, requested_agency_id, requested_client_id, canonical_scope,
                'active', requested_actor_id, CURRENT_TIMESTAMP, NULL,
                requested_expires_at, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            ) RETURNING id INTO created_id;
            RETURN created_id;
        EXCEPTION
            WHEN invalid_parameter_value OR data_exception THEN
                RAISE EXCEPTION 'agency authorization scope must be a nonempty unique whitelist array';
        END
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_RENEW_FUNCTION}(uuid, uuid, uuid, jsonb, uuid, uuid, timestamptz) FROM PUBLIC"
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_REVOKE_FUNCTION}(
            requested_authorization_id uuid,
            requested_client_id uuid,
            requested_actor_id uuid,
            requested_auth_session_id uuid
        )
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE candidate public.agency_authorizations%ROWTYPE;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_client_id THEN
                RAISE EXCEPTION 'agency authorization client must match current tenant';
            END IF;
            SELECT * INTO candidate
            FROM public.agency_authorizations
            WHERE id = requested_authorization_id;
            IF NOT FOUND OR candidate.client_tenant_id <> requested_client_id THEN
                RETURN NULL;
            END IF;

            PERFORM pg_advisory_xact_lock_shared({_QUOTA_ROLLOUT_ADVISORY_LOCK_KEY});
            PERFORM tenant.id
            FROM public.tenants AS tenant
            WHERE tenant.id IN (candidate.agency_tenant_id, candidate.client_tenant_id)
            ORDER BY tenant.id::text
            FOR UPDATE;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('auth-session:' || requested_auth_session_id::text, 0)
            );
            IF NOT EXISTS (
                SELECT 1
                FROM public.auth_sessions AS auth_session
                JOIN public.accounts AS account
                  ON account.id = auth_session.account_id
                 AND account.tenant_id = auth_session.tenant_id
                WHERE auth_session.id = requested_auth_session_id
                  AND auth_session.account_id = requested_actor_id
                  AND auth_session.tenant_id = requested_client_id
                  AND auth_session.revoked_at IS NULL
                  AND auth_session.expires_at > CURRENT_TIMESTAMP
                  AND auth_session.auth_version = account.auth_version
                  AND account.is_active
                  AND EXISTS (
                      SELECT 1
                      FROM public.account_roles AS account_role
                      JOIN public.role_permissions AS role_permission
                        ON role_permission.tenant_id = account_role.tenant_id
                       AND role_permission.role_id = account_role.role_id
                      JOIN public.permissions AS permission
                        ON permission.tenant_id = role_permission.tenant_id
                       AND permission.id = role_permission.permission_id
                      WHERE account_role.tenant_id = requested_client_id
                        AND account_role.account_id = requested_actor_id
                        AND permission.code = 'tenant:manage'
                  )
            ) THEN
                RAISE EXCEPTION 'agency authorization actor session is not live';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.tenants
                WHERE id = candidate.agency_tenant_id
                  AND tenant_type = 'agency'
                  AND status = 'active'
            ) OR NOT EXISTS (
                SELECT 1 FROM public.tenants
                WHERE id = candidate.client_tenant_id
                  AND tenant_type = 'brand'
                  AND status = 'active'
            ) THEN
                RAISE EXCEPTION 'agency authorization endpoints or client plan are not active';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended(candidate.agency_tenant_id::text || ':' || candidate.client_tenant_id::text, 0)
            );
            SELECT * INTO candidate
            FROM public.agency_authorizations
            WHERE id = requested_authorization_id
              AND client_tenant_id = requested_client_id
            ;
            IF NOT FOUND OR candidate.status <> 'active'::public.agencyauthstatus THEN
                RETURN NULL;
            END IF;
            IF candidate.expires_at IS NOT NULL AND candidate.expires_at <= CURRENT_TIMESTAMP THEN
                UPDATE public.agency_authorizations
                SET status = 'expired', revoked_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = candidate.id;
                RETURN candidate.id;
            END IF;
            UPDATE public.agency_authorizations
            SET status = 'revoked', revoked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = candidate.id;
            RETURN candidate.id;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_REVOKE_FUNCTION}(uuid, uuid, uuid, uuid) FROM PUBLIC")

    bypass = _guarded_bypass()
    op.execute("DROP POLICY IF EXISTS agency_authorizations_update ON public.agency_authorizations")
    op.execute("DROP POLICY IF EXISTS agency_authorizations_delete ON public.agency_authorizations")
    op.execute(
        f"""
        CREATE POLICY agency_authorizations_control_update ON public.agency_authorizations
        FOR UPDATE USING ({bypass}) WITH CHECK ({bypass})
        """
    )
    op.execute(
        f"""
        CREATE POLICY agency_authorizations_control_delete ON public.agency_authorizations
        FOR DELETE USING ({bypass})
        """
    )
    if _runtime_role_exists():
        op.execute(
            f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
            f"ON TABLE public.agency_authorizations FROM {_RUNTIME_ROLE}"
        )
        op.execute(f"GRANT SELECT ON TABLE public.agency_authorizations TO {_RUNTIME_ROLE}")
        op.execute(
            f"GRANT EXECUTE ON FUNCTION "
            f"public.{_RENEW_FUNCTION}(uuid, uuid, uuid, jsonb, uuid, uuid, timestamptz) TO {_RUNTIME_ROLE}"
        )
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{_REVOKE_FUNCTION}(uuid, uuid, uuid, uuid) TO {_RUNTIME_ROLE}")


def _install_audit_interface() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_AUDIT_FUNCTION}(
            requested_id uuid,
            requested_auth_session_id uuid,
            requested_target_tenant text,
            requested_action text,
            requested_resource text,
            requested_details jsonb DEFAULT NULL
        ) RETURNS TABLE (audit_id uuid, resolved_operator_id text, recorded_at timestamptz)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE principal_tenant_id uuid;
        DECLARE principal_operator_id text;
        DECLARE target_uuid uuid;
        DECLARE required_scope text;
        DECLARE matched_authorization_id uuid;
        DECLARE matched_authorization_scope jsonb;
        DECLARE now_at timestamptz := CURRENT_TIMESTAMP;
        BEGIN
            BEGIN
                target_uuid := requested_target_tenant::uuid;
            EXCEPTION WHEN invalid_text_representation THEN
                RAISE EXCEPTION 'tenant audit target must be a UUID';
            END;

            SELECT auth_session.tenant_id
            INTO principal_tenant_id
            FROM public.auth_sessions AS auth_session
            WHERE auth_session.id = requested_auth_session_id;
            IF principal_tenant_id IS NULL THEN
                RAISE EXCEPTION 'audit auth session is not live';
            END IF;

            PERFORM pg_advisory_xact_lock_shared({_QUOTA_ROLLOUT_ADVISORY_LOCK_KEY});
            PERFORM tenant.id
            FROM public.tenants AS tenant
            WHERE tenant.id IN (principal_tenant_id, target_uuid)
            ORDER BY tenant.id::text
            FOR UPDATE;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('auth-session:' || requested_auth_session_id::text, 0)
            );

            SELECT auth_session.tenant_id, auth_session.account_id::text
            INTO principal_tenant_id, principal_operator_id
            FROM public.auth_sessions AS auth_session
            JOIN public.accounts AS account
              ON account.tenant_id = auth_session.tenant_id AND account.id = auth_session.account_id
            JOIN public.tenants AS tenant ON tenant.id = auth_session.tenant_id
            WHERE auth_session.id = requested_auth_session_id
              AND auth_session.revoked_at IS NULL
              AND auth_session.expires_at > now_at
              AND account.is_active
              AND account.auth_version = auth_session.auth_version
              AND tenant.status = 'active';
            IF principal_operator_id IS NULL THEN
                RAISE EXCEPTION 'audit auth session is not live';
            END IF;
            IF requested_action = 'agency_context_entered' AND NOT EXISTS (
                SELECT 1
                FROM public.account_roles AS account_role
                JOIN public.roles AS role
                  ON role.tenant_id = account_role.tenant_id
                 AND role.id = account_role.role_id
                WHERE account_role.tenant_id = principal_tenant_id
                  AND account_role.account_id::text = principal_operator_id
                  AND role.name IN ('admin', 'operator')
            ) THEN
                RAISE EXCEPTION 'agency context actor role is no longer authorized';
            END IF;

            IF target_uuid = principal_tenant_id THEN
                IF public.current_tenant_id() IS DISTINCT FROM principal_tenant_id
                   AND requested_action <> 'agency_context_exited' THEN
                    RAISE EXCEPTION 'own-tenant audit context does not match auth session tenant';
                END IF;
            ELSE
                    PERFORM pg_advisory_xact_lock(
                        hashtextextended(principal_tenant_id::text || ':' || target_uuid::text, 0)
                    );
                    required_scope := CASE requested_action
                        WHEN 'agency_context_entered' THEN NULL
                        WHEN 'product_created' THEN 'products'
                        WHEN 'product_updated' THEN 'products'
                        WHEN 'page_published' THEN 'pages'
                        WHEN 'launch_release_created' THEN 'pages'
                        WHEN 'launch_release_confirmation_requested' THEN 'pages'
                        WHEN 'launch_release_invalidated' THEN 'pages'
                        WHEN 'launch_release_launched_by_agency' THEN 'release:execute'
                        WHEN 'code_activate' THEN 'codes'
                        WHEN 'code_revoke' THEN 'codes'
                        WHEN 'code_freeze' THEN 'codes'
                        WHEN 'code_unfreeze' THEN 'codes'
                        WHEN 'code_void' THEN 'codes'
                        ELSE '__unsupported__'
                    END;
                    IF required_scope = '__unsupported__' THEN
                        RAISE EXCEPTION 'audit action is not allowed for agency cross-tenant append';
                    END IF;
                    IF public.current_tenant_id() NOT IN (principal_tenant_id, target_uuid) THEN
                        RAISE EXCEPTION 'agency audit context does not match principal or client tenant';
                    END IF;
                    SELECT authz.id, authz.scope::jsonb
                    INTO matched_authorization_id, matched_authorization_scope
                        FROM public.tenants AS agency
                        JOIN public.agency_authorizations AS authz
                          ON authz.agency_tenant_id = agency.id
                        JOIN public.tenants AS client ON client.id = authz.client_tenant_id
                        WHERE agency.id = principal_tenant_id
                          AND agency.tenant_type = 'agency'
                          AND agency.status = 'active'
                          AND (
                              requested_action <> 'agency_context_entered'
                              OR agency.plan_expires_at IS NULL
                              OR agency.plan_expires_at > now_at
                          )
                          AND client.id = target_uuid
                          AND client.tenant_type = 'brand'
                          AND client.status = 'active'
                          AND authz.status = 'active'
                          AND (authz.expires_at IS NULL OR authz.expires_at > now_at)
                          AND (required_scope IS NULL OR authz.scope::jsonb ? required_scope)
                    ;
                    IF matched_authorization_id IS NULL THEN
                        RAISE EXCEPTION 'agency audit append lacks a live scoped authorization';
                    END IF;
                    IF requested_action = 'agency_context_entered' THEN
                        requested_resource := 'agency_authorization:' || matched_authorization_id::text;
                        requested_details := COALESCE(requested_details, '{{}}'::jsonb) || jsonb_build_object(
                            'agency_tenant_id', principal_tenant_id::text,
                            'acting_tenant_id', target_uuid::text,
                            'scope', matched_authorization_scope
                        );
                    END IF;
            END IF;

            PERFORM set_config('app.tenant_id', target_uuid::text, true);
            INSERT INTO public.platform_audit_log (
                id, operator_id, target_tenant_id, action, resource, details,
                timestamp, created_at, updated_at
            ) VALUES (
                requested_id, principal_operator_id, requested_target_tenant,
                requested_action, requested_resource, requested_details,
                now_at, now_at, now_at
            );
            RETURN QUERY SELECT requested_id, principal_operator_id, now_at;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_AUDIT_FUNCTION}(uuid, uuid, text, text, text, jsonb) FROM PUBLIC")

    bypass = _guarded_bypass()
    op.execute("DROP POLICY IF EXISTS audit_tenant_append ON public.platform_audit_log")
    op.execute(
        f"""
        CREATE POLICY audit_tenant_append ON public.platform_audit_log
        FOR INSERT WITH CHECK (
            target_tenant_id = public.current_tenant_id()::text OR {bypass}
        )
        """
    )
    if _runtime_role_exists():
        op.execute(
            f"GRANT EXECUTE ON FUNCTION public.{_AUDIT_FUNCTION}(uuid, uuid, text, text, text, jsonb) "
            f"TO {_RUNTIME_ROLE}"
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")

    _install_scope_contract()
    _preflight_and_canonicalize()
    _install_constraints()
    _install_row_guards()
    _install_authorization_interfaces()
    _install_audit_interface()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")

    if _runtime_role_exists():
        op.execute(
            f"REVOKE EXECUTE ON FUNCTION public.{_AUDIT_FUNCTION}(uuid, uuid, text, text, text, jsonb) "
            f"FROM {_RUNTIME_ROLE}"
        )
        op.execute(f"REVOKE EXECUTE ON FUNCTION public.{_REVOKE_FUNCTION}(uuid, uuid, uuid, uuid) FROM {_RUNTIME_ROLE}")
        op.execute(
            f"REVOKE EXECUTE ON FUNCTION "
            f"public.{_RENEW_FUNCTION}(uuid, uuid, uuid, jsonb, uuid, uuid, timestamptz) "
            f"FROM {_RUNTIME_ROLE}"
        )
    op.execute(f"DROP FUNCTION public.{_AUDIT_FUNCTION}(uuid, uuid, text, text, text, jsonb)")
    op.execute(f"DROP FUNCTION public.{_REVOKE_FUNCTION}(uuid, uuid, uuid, uuid)")
    op.execute(f"DROP FUNCTION public.{_RENEW_FUNCTION}(uuid, uuid, uuid, jsonb, uuid, uuid, timestamptz)")

    op.execute("DROP POLICY IF EXISTS agency_authorizations_control_delete ON public.agency_authorizations")
    op.execute("DROP POLICY IF EXISTS agency_authorizations_control_update ON public.agency_authorizations")
    bypass = _guarded_bypass()
    op.execute(
        f"""
        CREATE POLICY agency_authorizations_update ON public.agency_authorizations
        FOR UPDATE
        USING (client_tenant_id = public.current_tenant_id() OR {bypass})
        WITH CHECK (client_tenant_id = public.current_tenant_id() OR {bypass})
        """
    )
    op.execute(
        f"""
        CREATE POLICY agency_authorizations_delete ON public.agency_authorizations
        FOR DELETE USING (client_tenant_id = public.current_tenant_id() OR {bypass})
        """
    )
    if _runtime_role_exists():
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agency_authorizations TO {_RUNTIME_ROLE}")

    op.execute("DROP POLICY IF EXISTS audit_tenant_append ON public.platform_audit_log")
    op.execute(
        f"""
        CREATE POLICY audit_tenant_append ON public.platform_audit_log
        FOR INSERT WITH CHECK (
            target_tenant_id = public.current_tenant_id()::text
            OR EXISTS (
                SELECT 1 FROM public.agency_authorizations AS agency_auth
                WHERE agency_auth.agency_tenant_id = public.current_tenant_id()
                  AND agency_auth.client_tenant_id::text = platform_audit_log.target_tenant_id
                  AND agency_auth.status = 'active'
                  AND (agency_auth.expires_at IS NULL OR agency_auth.expires_at > CURRENT_TIMESTAMP)
            )
            OR {bypass}
        )
        """
    )

    op.execute("DROP TRIGGER trg_guard_agency_authorization_row ON public.agency_authorizations")
    op.execute(f"DROP FUNCTION public.{_ROW_GUARD_FUNCTION}()")

    op.alter_column("agency_authorizations", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.drop_constraint("ck_agency_authorizations_expiry_after_grant", "agency_authorizations", type_="check")
    op.drop_constraint("ck_agency_authorizations_lifecycle", "agency_authorizations", type_="check")
    op.drop_constraint("ck_agency_authorizations_scope_canonical", "agency_authorizations", type_="check")
    op.drop_constraint("ck_agency_authorizations_distinct_tenants", "agency_authorizations", type_="check")
    op.drop_constraint("fk_agency_authorizations_client_grantor", "agency_authorizations", type_="foreignkey")

    op.execute(
        f"""
        UPDATE public.agency_authorizations AS authz
        SET scope = backup.original_scope,
            updated_at = CASE WHEN backup.updated_at_was_null THEN NULL ELSE authz.updated_at END
        FROM public.{_BACKUP_TABLE} AS backup
        WHERE backup.authorization_id = authz.id
        """
    )
    op.drop_table(_BACKUP_TABLE, schema="public")
    op.execute(f"DROP FUNCTION public.{_SCOPE_FUNCTION}(json)")
