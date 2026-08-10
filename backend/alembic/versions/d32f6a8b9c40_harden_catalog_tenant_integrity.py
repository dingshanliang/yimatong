"""Harden catalog tenant integrity and trusted evidence.

Revision ID: d32f6a8b9c40
Revises: c21e5f7a9b31
Create Date: 2026-08-10

The existing globally unique parent identifiers and their legacy single-column
foreign keys remain in place for rolling compatibility.  This revision adds
the authoritative tenant-aware relationships beside them, rejects malformed
legacy rows before DDL, and narrows API-key catalog audit append to a live
credential already locked by the request transaction.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d32f6a8b9c40"
down_revision: str | None = "c21e5f7a9b31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_ROLE = "yimatong_app"
_API_KEY_CONTEXT_PARAMETER = "app.api_key_id"
_API_KEY_CONTEXT_SECRET_TABLE = "api_key_catalog_audit_context_secrets"
_LOCK_FUNCTION = "lock_active_api_key"
_AUTH_AUDIT_FUNCTION = "append_authenticated_audit_event"
_API_KEY_AUDIT_FUNCTION = "append_api_key_catalog_audit_event"
_ASSET_GUARD_FUNCTION = "guard_product_asset_trust_evidence"
_QUOTA_ROLLOUT_ADVISORY_LOCK_KEY = 6434892150882653249

_TENANT_FOREIGN_KEYS = (
    ("brands", "fk_brands_tenant", ("tenant_id",), "tenants", ("id",)),
    ("products", "fk_products_tenant", ("tenant_id",), "tenants", ("id",)),
    ("skus", "fk_skus_tenant", ("tenant_id",), "tenants", ("id",)),
    (
        "production_batches",
        "fk_production_batches_tenant",
        ("tenant_id",),
        "tenants",
        ("id",),
    ),
    ("product_assets", "fk_product_assets_tenant", ("tenant_id",), "tenants", ("id",)),
)
_RELATION_FOREIGN_KEYS = (
    (
        "products",
        "fk_products_tenant_brand",
        ("tenant_id", "brand_id"),
        "brands",
        ("tenant_id", "id"),
    ),
    (
        "skus",
        "fk_skus_tenant_product",
        ("tenant_id", "product_id"),
        "products",
        ("tenant_id", "id"),
    ),
    (
        "product_assets",
        "fk_product_assets_tenant_product",
        ("tenant_id", "product_id"),
        "products",
        ("tenant_id", "id"),
    ),
    (
        "production_batches",
        "fk_production_batches_tenant_product",
        ("tenant_id", "product_id"),
        "products",
        ("tenant_id", "id"),
    ),
    (
        "production_batches",
        "fk_production_batches_tenant_product_sku",
        ("tenant_id", "product_id", "sku_id"),
        "skus",
        ("tenant_id", "product_id", "id"),
    ),
)


def _runtime_role_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"),
            {"role": _RUNTIME_ROLE},
        )
        .scalar_one()
    )


def _preflight() -> None:
    op.execute(
        """
        DO $block$
        DECLARE invalid_rows text;
        BEGIN
            WITH violations AS (
                SELECT 'brands.tenant_id' AS contract, brand.id
                FROM public.brands AS brand
                LEFT JOIN public.tenants AS tenant ON tenant.id = brand.tenant_id
                WHERE tenant.id IS NULL
                UNION ALL
                SELECT 'products.tenant_id', product.id
                FROM public.products AS product
                LEFT JOIN public.tenants AS tenant ON tenant.id = product.tenant_id
                WHERE tenant.id IS NULL
                UNION ALL
                SELECT 'skus.tenant_id', sku.id
                FROM public.skus AS sku
                LEFT JOIN public.tenants AS tenant ON tenant.id = sku.tenant_id
                WHERE tenant.id IS NULL
                UNION ALL
                SELECT 'production_batches.tenant_id', batch.id
                FROM public.production_batches AS batch
                LEFT JOIN public.tenants AS tenant ON tenant.id = batch.tenant_id
                WHERE tenant.id IS NULL
                UNION ALL
                SELECT 'product_assets.tenant_id', asset.id
                FROM public.product_assets AS asset
                LEFT JOIN public.tenants AS tenant ON tenant.id = asset.tenant_id
                WHERE tenant.id IS NULL
                UNION ALL
                SELECT 'products.tenant_brand', product.id
                FROM public.products AS product
                LEFT JOIN public.brands AS brand
                  ON brand.tenant_id = product.tenant_id AND brand.id = product.brand_id
                WHERE brand.id IS NULL
                UNION ALL
                SELECT 'skus.tenant_product', sku.id
                FROM public.skus AS sku
                LEFT JOIN public.products AS product
                  ON product.tenant_id = sku.tenant_id AND product.id = sku.product_id
                WHERE product.id IS NULL
                UNION ALL
                SELECT 'product_assets.tenant_product', asset.id
                FROM public.product_assets AS asset
                LEFT JOIN public.products AS product
                  ON product.tenant_id = asset.tenant_id AND product.id = asset.product_id
                WHERE product.id IS NULL
                UNION ALL
                SELECT 'production_batches.tenant_product', batch.id
                FROM public.production_batches AS batch
                LEFT JOIN public.products AS product
                  ON product.tenant_id = batch.tenant_id AND product.id = batch.product_id
                WHERE product.id IS NULL
                UNION ALL
                SELECT 'production_batches.tenant_product_sku', batch.id
                FROM public.production_batches AS batch
                LEFT JOIN public.skus AS sku
                  ON sku.tenant_id = batch.tenant_id
                 AND sku.product_id = batch.product_id
                 AND sku.id = batch.sku_id
                WHERE sku.id IS NULL
                UNION ALL
                SELECT 'production_batches.expiry_date', batch.id
                FROM public.production_batches AS batch
                WHERE batch.expiry_date < batch.production_date
                UNION ALL
                SELECT 'product_assets.active_trust_evidence', asset.id
                FROM public.product_assets AS asset
                WHERE asset.status::text = 'active'
                  AND asset.asset_type::text IN ('test_report', 'certificate')
                  AND (
                      NULLIF(btrim(asset.issuer), '') IS NULL
                      OR NOT (
                          COALESCE(btrim(asset.file_url), '') ~ '^https://'
                          OR COALESCE(btrim(asset.file_url), '') ~ '^/api/v1/files/public/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@-]*(/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@-]*)*$'
                          OR COALESCE(btrim(asset.image_url), '') ~ '^https://'
                          OR COALESCE(btrim(asset.image_url), '') ~ '^/api/v1/files/public/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@-]*(/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@-]*)*$'
                      )
                      OR (
                          asset.valid_until IS NOT NULL
                          AND (
                              asset.valid_until < asset.created_at::date
                              OR asset.valid_until < CURRENT_DATE
                          )
                      )
                  )
            )
            SELECT string_agg(contract || '=' || id::text, ', ' ORDER BY contract, id::text)
            INTO invalid_rows
            FROM violations;
            IF invalid_rows IS NOT NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'Catalog tenant/evidence preflight failed: ' || invalid_rows;
            END IF;
        END
        $block$
        """
    )


def _add_foreign_key(
    table: str,
    name: str,
    local_columns: tuple[str, ...],
    parent: str,
    remote_columns: tuple[str, ...],
) -> None:
    local = ", ".join(local_columns)
    remote = ", ".join(remote_columns)
    op.execute(
        f"""
        ALTER TABLE public.{table}
        ADD CONSTRAINT {name}
        FOREIGN KEY ({local}) REFERENCES public.{parent} ({remote})
        MATCH SIMPLE ON UPDATE NO ACTION ON DELETE NO ACTION
        NOT DEFERRABLE INITIALLY IMMEDIATE NOT VALID
        """
    )
    op.execute(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")


def _install_schema_contract() -> None:
    op.alter_column(
        "platform_audit_log",
        "operator_id",
        existing_type=sa.String(length=36),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.create_table(
        _API_KEY_CONTEXT_SECRET_TABLE,
        sa.Column("singleton_id", sa.SmallInteger(), nullable=False),
        sa.Column("context_secret", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "singleton_id = 1",
            name="ck_api_key_catalog_audit_context_secrets_singleton",
        ),
        sa.CheckConstraint(
            "octet_length(context_secret) = 32",
            name="ck_api_key_catalog_audit_context_secrets_length",
        ),
        sa.PrimaryKeyConstraint("singleton_id"),
        schema="public",
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON TABLE public.{_API_KEY_CONTEXT_SECRET_TABLE} FROM PUBLIC"
    )
    if _runtime_role_exists():
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE public.{_API_KEY_CONTEXT_SECRET_TABLE} FROM {_RUNTIME_ROLE}"
        )
    op.execute(
        f"INSERT INTO public.{_API_KEY_CONTEXT_SECRET_TABLE} (singleton_id, context_secret) "
        "VALUES (1, gen_random_bytes(32))"
    )

    op.create_unique_constraint("uq_brands_tenant_id_id", "brands", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_products_tenant_id_id", "products", ["tenant_id", "id"]
    )
    op.create_unique_constraint("uq_skus_tenant_id_id", "skus", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_skus_tenant_product_id_id", "skus", ["tenant_id", "product_id", "id"]
    )

    op.create_index(
        "ix_products_tenant_brand", "products", ["tenant_id", "brand_id"], unique=False
    )
    op.create_index(
        "ix_product_assets_tenant_product",
        "product_assets",
        ["tenant_id", "product_id"],
        unique=False,
    )
    op.create_index(
        "ix_production_batches_tenant_product",
        "production_batches",
        ["tenant_id", "product_id"],
        unique=False,
    )
    op.create_index(
        "ix_production_batches_tenant_product_sku",
        "production_batches",
        ["tenant_id", "product_id", "sku_id"],
        unique=False,
    )

    for definition in (*_TENANT_FOREIGN_KEYS, *_RELATION_FOREIGN_KEYS):
        _add_foreign_key(*definition)

    op.execute(
        """
        ALTER TABLE public.production_batches
        ADD CONSTRAINT ck_production_batches_expiry_not_before_production
        CHECK (expiry_date >= production_date) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE public.production_batches "
        "VALIDATE CONSTRAINT ck_production_batches_expiry_not_before_production"
    )
    op.execute(
        """
        ALTER TABLE public.product_assets
        ADD CONSTRAINT ck_product_assets_active_trust_evidence
        CHECK (
            status::text <> 'active'
            OR asset_type::text NOT IN ('test_report', 'certificate')
            OR (
                NULLIF(btrim(issuer), '') IS NOT NULL
                AND (
                    COALESCE(btrim(file_url), '') ~ '^https://'
                    OR COALESCE(btrim(file_url), '') ~ '^/api/v1/files/public/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@-]*(/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@-]*)*$'
                    OR COALESCE(btrim(image_url), '') ~ '^https://'
                    OR COALESCE(btrim(image_url), '') ~ '^/api/v1/files/public/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@-]*(/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@-]*)*$'
                )
                AND (valid_until IS NULL OR valid_until >= created_at::date)
            )
        ) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE public.product_assets VALIDATE CONSTRAINT ck_product_assets_active_trust_evidence"
    )


def _install_asset_guard() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_ASSET_GUARD_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF NEW.status::text = 'active'
               AND NEW.asset_type::text IN ('test_report', 'certificate')
               AND NEW.valid_until IS NOT NULL
               AND NEW.valid_until < CURRENT_DATE THEN
                RAISE EXCEPTION USING
                    ERRCODE = '22023',
                    MESSAGE = 'active trust evidence is already expired';
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_ASSET_GUARD_FUNCTION}() FROM PUBLIC")
    op.execute(
        f"""
        CREATE TRIGGER trg_guard_product_asset_trust_evidence
        BEFORE INSERT OR UPDATE OF asset_type, status, issuer, valid_until, file_url, image_url
        ON public.product_assets
        FOR EACH ROW EXECUTE FUNCTION public.{_ASSET_GUARD_FUNCTION}()
        """
    )


def _install_lock_interface(*, bind_request_context: bool) -> None:
    context_success = (
        f"""
        SELECT context_secret INTO request_context_secret
        FROM public.{_API_KEY_CONTEXT_SECRET_TABLE}
        WHERE singleton_id = 1;
        IF request_context_secret IS NULL THEN
            RAISE EXCEPTION USING ERRCODE = 'XX001', MESSAGE = 'API-key request binding secret is missing';
        END IF;
        request_context_payload := requested_tenant_id::text || ':' || candidate.id::text || ':'
            || pg_current_xact_id()::text || ':' || pg_backend_pid()::text;
        request_context_token := encode(
            hmac(convert_to(request_context_payload, 'UTF8'), request_context_secret, 'sha256'),
            'hex'
        );
        PERFORM set_config(
            '{_API_KEY_CONTEXT_PARAMETER}',
            candidate.id::text || ':' || request_context_token,
            true
        );
        """
        if bind_request_context
        else "NULL;"
    )
    context_failure = (
        f"PERFORM set_config('{_API_KEY_CONTEXT_PARAMETER}', '', true);"
        if bind_request_context
        else "NULL;"
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.{_LOCK_FUNCTION}(requested_tenant_id uuid, requested_api_key_id uuid)
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE candidate public.api_keys%ROWTYPE;
        DECLARE request_context_payload text;
        DECLARE request_context_secret bytea;
        DECLARE request_context_token text;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_tenant_id THEN
                {context_failure}
                RETURN false;
            END IF;
            PERFORM pg_advisory_xact_lock_shared({_QUOTA_ROLLOUT_ADVISORY_LOCK_KEY});
            PERFORM tenant.id
            FROM public.tenants AS tenant
            WHERE tenant.id = requested_tenant_id
              AND tenant.status = 'active'
              AND tenant.tenant_type = 'brand'
            FOR UPDATE;
            IF NOT FOUND THEN
                {context_failure}
                RETURN false;
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended('api-key:' || requested_api_key_id::text, 0));
            SELECT * INTO candidate
            FROM public.api_keys
            WHERE id = requested_api_key_id AND tenant_id = requested_tenant_id
            FOR UPDATE;
            IF FOUND
               AND NOT candidate.revoked
               AND candidate.revoked_at IS NULL
               AND (candidate.expires_at IS NULL OR candidate.expires_at > CURRENT_TIMESTAMP) THEN
                {context_success}
                RETURN true;
            END IF;
            {context_failure}
            RETURN false;
        END
        $function$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{_LOCK_FUNCTION}(uuid, uuid) FROM PUBLIC"
    )
    if _runtime_role_exists():
        op.execute(
            f"GRANT EXECUTE ON FUNCTION public.{_LOCK_FUNCTION}(uuid, uuid) TO {_RUNTIME_ROLE}"
        )


def _install_authenticated_audit_interface(*, include_catalog_actions: bool) -> None:
    catalog_cases = ""
    if include_catalog_actions:
        catalog_cases = """
                        WHEN 'brand_created' THEN 'products'
                        WHEN 'brand_updated' THEN 'products'
                        WHEN 'brand_deleted' THEN 'products'
                        WHEN 'product_deleted' THEN 'products'
                        WHEN 'sku_created' THEN 'products'
                        WHEN 'sku_updated' THEN 'products'
                        WHEN 'sku_deleted' THEN 'products'
                        WHEN 'production_batch_created' THEN 'products'
                        WHEN 'production_batch_updated' THEN 'products'
                        WHEN 'production_batch_deleted' THEN 'products'
                        WHEN 'production_batch_imported' THEN 'products'
                        WHEN 'product_asset_created' THEN 'products'
                        WHEN 'product_asset_updated' THEN 'products'
                        WHEN 'product_asset_deleted' THEN 'products'
        """
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.{_AUTH_AUDIT_FUNCTION}(
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
                    {catalog_cases}
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
                  AND (required_scope IS NULL OR authz.scope::jsonb ? required_scope);
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
    signature = f"public.{_AUTH_AUDIT_FUNCTION}(uuid, uuid, text, text, text, jsonb)"
    op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {_RUNTIME_ROLE}")


def _install_api_key_audit_interface() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_API_KEY_AUDIT_FUNCTION}(
            requested_id uuid,
            requested_api_key_id uuid,
            requested_target_tenant uuid,
            requested_action text,
            requested_resource_id uuid,
            requested_details jsonb DEFAULT NULL
        ) RETURNS TABLE (audit_id uuid, resolved_operator_id text, recorded_at timestamptz)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE request_context_value text;
        DECLARE request_context_key uuid;
        DECLARE provided_token bytea;
        DECLARE expected_token bytea;
        DECLARE request_context_payload text;
        DECLARE request_context_secret bytea;
        DECLARE token_difference integer := 0;
        DECLARE token_offset integer;
        DECLARE required_permission text;
        DECLARE resource_prefix text;
        DECLARE principal_operator_id text;
        DECLARE now_at timestamptz := CURRENT_TIMESTAMP;
        BEGIN
            IF public.current_tenant_id() IS DISTINCT FROM requested_target_tenant THEN
                RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'API-key audit tenant context is not authorized';
            END IF;
            request_context_value := NULLIF(current_setting('{_API_KEY_CONTEXT_PARAMETER}', true), '');
            BEGIN
                request_context_key := split_part(request_context_value, ':', 1)::uuid;
                provided_token := decode(split_part(request_context_value, ':', 2), 'hex');
            EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value THEN
                RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'API-key audit request context is invalid';
            END;
            SELECT context_secret INTO request_context_secret
            FROM public.{_API_KEY_CONTEXT_SECRET_TABLE}
            WHERE singleton_id = 1;
            request_context_payload := requested_target_tenant::text || ':' || requested_api_key_id::text || ':'
                || pg_current_xact_id()::text || ':' || pg_backend_pid()::text;
            expected_token := hmac(
                convert_to(request_context_payload, 'UTF8'),
                request_context_secret,
                'sha256'
            );
            IF request_context_key IS DISTINCT FROM requested_api_key_id
               OR octet_length(provided_token) IS DISTINCT FROM octet_length(expected_token) THEN
                RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'API-key audit credential is not request-bound';
            END IF;
            FOR token_offset IN 0..octet_length(expected_token) - 1 LOOP
                token_difference := token_difference
                    | (get_byte(provided_token, token_offset) # get_byte(expected_token, token_offset));
            END LOOP;
            IF token_difference <> 0 THEN
                RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'API-key audit credential is not request-bound';
            END IF;

            required_permission := CASE requested_action
                WHEN 'product_created' THEN 'product:create'
                WHEN 'product_updated' THEN 'product:update'
                WHEN 'sku_created' THEN 'product:create'
                WHEN 'sku_updated' THEN 'product:update'
                WHEN 'production_batch_created' THEN 'product:create'
                WHEN 'production_batch_updated' THEN 'product:update'
                ELSE NULL
            END;
            resource_prefix := CASE
                WHEN requested_action LIKE 'product_%' THEN 'product:'
                WHEN requested_action LIKE 'sku_%' THEN 'sku:'
                WHEN requested_action LIKE 'production_batch_%' THEN 'production_batch:'
                ELSE NULL
            END;
            IF required_permission IS NULL OR resource_prefix IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'API-key catalog audit action is not allowed';
            END IF;

            PERFORM pg_advisory_xact_lock(hashtextextended('api-key:' || requested_api_key_id::text, 0));
            IF NOT EXISTS (
                SELECT 1
                FROM public.api_keys AS api_key
                JOIN public.tenants AS tenant ON tenant.id = api_key.tenant_id
                WHERE api_key.id = requested_api_key_id
                  AND api_key.tenant_id = requested_target_tenant
                  AND NOT api_key.revoked
                  AND api_key.revoked_at IS NULL
                  AND (api_key.expires_at IS NULL OR api_key.expires_at > now_at)
                  AND api_key.permissions::jsonb ? required_permission
                  AND tenant.status = 'active'
                  AND tenant.tenant_type = 'brand'
                FOR UPDATE OF api_key
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'API-key catalog audit credential is not live';
            END IF;

            IF requested_action LIKE 'product_%' AND NOT EXISTS (
                SELECT 1 FROM public.products
                WHERE tenant_id = requested_target_tenant AND id = requested_resource_id
            ) OR requested_action LIKE 'sku_%' AND NOT EXISTS (
                SELECT 1 FROM public.skus
                WHERE tenant_id = requested_target_tenant AND id = requested_resource_id
            ) OR requested_action LIKE 'production_batch_%' AND NOT EXISTS (
                SELECT 1 FROM public.production_batches
                WHERE tenant_id = requested_target_tenant AND id = requested_resource_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23503', MESSAGE = 'API-key catalog audit resource is not tenant-owned';
            END IF;

            principal_operator_id := 'api-key:' || requested_api_key_id::text;
            INSERT INTO public.platform_audit_log (
                id, operator_id, target_tenant_id, action, resource, details,
                timestamp, created_at, updated_at
            ) VALUES (
                requested_id,
                principal_operator_id,
                requested_target_tenant::text,
                requested_action,
                resource_prefix || requested_resource_id::text,
                requested_details,
                now_at,
                now_at,
                now_at
            );
            RETURN QUERY SELECT requested_id, principal_operator_id, now_at;
        END
        $function$
        """
    )
    signature = f"public.{_API_KEY_AUDIT_FUNCTION}(uuid, uuid, uuid, text, uuid, jsonb)"
    op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    if _runtime_role_exists():
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {_RUNTIME_ROLE}")


def _downgrade_preflight() -> None:
    op.execute(
        """
        DO $block$
        DECLARE oversized_ids text;
        BEGIN
            SELECT string_agg(id::text, ', ' ORDER BY id::text)
            INTO oversized_ids
            FROM public.platform_audit_log
            WHERE length(operator_id) > 36;
            IF oversized_ids IS NOT NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '22001',
                    MESSAGE = 'Cannot restore platform audit operator_id varchar(36); oversized rows: ' || oversized_ids;
            END IF;
        END
        $block$
        """
    )


def _drop_schema_contract() -> None:
    op.execute(
        "DROP TRIGGER trg_guard_product_asset_trust_evidence ON public.product_assets"
    )
    op.execute(f"DROP FUNCTION public.{_ASSET_GUARD_FUNCTION}()")

    op.drop_constraint(
        "ck_product_assets_active_trust_evidence",
        "product_assets",
        type_="check",
    )
    op.drop_constraint(
        "ck_production_batches_expiry_not_before_production",
        "production_batches",
        type_="check",
    )
    for table, name, *_rest in reversed(
        (*_TENANT_FOREIGN_KEYS, *_RELATION_FOREIGN_KEYS)
    ):
        op.drop_constraint(name, table, type_="foreignkey")

    op.drop_index(
        "ix_production_batches_tenant_product_sku",
        table_name="production_batches",
    )
    op.drop_index(
        "ix_production_batches_tenant_product",
        table_name="production_batches",
    )
    op.drop_index("ix_product_assets_tenant_product", table_name="product_assets")
    op.drop_index("ix_products_tenant_brand", table_name="products")

    op.drop_constraint("uq_skus_tenant_product_id_id", "skus", type_="unique")
    op.drop_constraint("uq_skus_tenant_id_id", "skus", type_="unique")
    op.drop_constraint("uq_products_tenant_id_id", "products", type_="unique")
    op.drop_constraint("uq_brands_tenant_id_id", "brands", type_="unique")

    op.drop_table(_API_KEY_CONTEXT_SECRET_TABLE, schema="public")

    op.alter_column(
        "platform_audit_log",
        "operator_id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=36),
        existing_nullable=False,
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")

    _preflight()
    _install_schema_contract()
    _install_asset_guard()
    op.execute(f'REVOKE SET ON PARAMETER "{_API_KEY_CONTEXT_PARAMETER}" FROM PUBLIC')
    op.execute(
        f"""
        DO $block$
        BEGIN
            EXECUTE format(
                'GRANT SET ON PARAMETER "{_API_KEY_CONTEXT_PARAMETER}" TO %I',
                current_user
            );
        END
        $block$
        """
    )
    _install_lock_interface(bind_request_context=True)
    _install_authenticated_audit_interface(include_catalog_actions=True)
    _install_api_key_audit_interface()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")

    _downgrade_preflight()
    signature = f"public.{_API_KEY_AUDIT_FUNCTION}(uuid, uuid, uuid, text, uuid, jsonb)"
    if _runtime_role_exists():
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM {_RUNTIME_ROLE}")
    op.execute(f"DROP FUNCTION {signature}")
    _install_authenticated_audit_interface(include_catalog_actions=False)
    _install_lock_interface(bind_request_context=False)
    op.execute(
        f"""
        DO $block$
        BEGIN
            EXECUTE format(
                'REVOKE SET ON PARAMETER "{_API_KEY_CONTEXT_PARAMETER}" FROM %I',
                current_user
            );
        END
        $block$
        """
    )
    op.execute(f'GRANT SET ON PARAMETER "{_API_KEY_CONTEXT_PARAMETER}" TO PUBLIC')
    _drop_schema_contract()
