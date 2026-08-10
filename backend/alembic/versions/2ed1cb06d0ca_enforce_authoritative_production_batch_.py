"""Enforce the authoritative production-batch and recall boundary.

Revision ID: 2ed1cb06d0ca
Revises: d32f6a8b9c40
Create Date: 2026-08-10

Legacy globally unique foreign keys remain for rolling compatibility.  The
tenant-aware keys added here reject mixed-tenant catalog chains at the
database boundary.  Recall is an absorbing, audited state: linked active code
items are frozen atomically while their traceability records remain present.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2ed1cb06d0ca"
down_revision: str | None = "d32f6a8b9c40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_ROLE = "yimatong_app"
_AUTH_AUDIT_FUNCTION = "append_authenticated_audit_event"
_QUOTA_ROLLOUT_ADVISORY_LOCK_KEY = 6434892150882653249
_PB_GUARD_FUNCTION = "guard_production_batch_recall"
_CODE_BATCH_GUARD_FUNCTION = "guard_code_batch_production_link"
_CODE_ITEM_GUARD_FUNCTION = "guard_code_item_production_batch_active"
_RECALL_FREEZE_FUNCTION = "freeze_recalled_production_batch_codes"
_CHINA_BUSINESS_DATE_SQL = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')::date"

_TENANT_FOREIGN_KEYS = (
    ("code_batches", "fk_code_batches_tenant", ("tenant_id",), "tenants", ("id",)),
    ("code_items", "fk_code_items_tenant", ("tenant_id",), "tenants", ("id",)),
)
_RELATION_FOREIGN_KEYS = (
    (
        "code_batches",
        "fk_code_batches_tenant_product",
        ("tenant_id", "product_id"),
        "products",
        ("tenant_id", "id"),
    ),
    (
        "code_batches",
        "fk_code_batches_tenant_product_sku",
        ("tenant_id", "product_id", "sku_id"),
        "skus",
        ("tenant_id", "product_id", "id"),
    ),
    (
        "code_batches",
        "fk_code_batches_tenant_product_sku_production_batch",
        ("tenant_id", "product_id", "sku_id", "production_batch_id"),
        "production_batches",
        ("tenant_id", "product_id", "sku_id", "id"),
    ),
    (
        "code_items",
        "fk_code_items_tenant_code_batch",
        ("tenant_id", "code_batch_id"),
        "code_batches",
        ("tenant_id", "id"),
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
                SELECT 'code_batches.tenant_id' AS contract, batch.id
                FROM public.code_batches AS batch
                LEFT JOIN public.tenants AS tenant ON tenant.id = batch.tenant_id
                WHERE tenant.id IS NULL
                UNION ALL
                SELECT 'code_batches.production_batch_id', batch.id
                FROM public.code_batches AS batch
                WHERE batch.production_batch_id IS NULL
                UNION ALL
                SELECT 'code_batches.tenant_product', batch.id
                FROM public.code_batches AS batch
                LEFT JOIN public.products AS product
                  ON product.tenant_id = batch.tenant_id AND product.id = batch.product_id
                WHERE product.id IS NULL
                UNION ALL
                SELECT 'code_batches.tenant_product_sku', batch.id
                FROM public.code_batches AS batch
                LEFT JOIN public.skus AS sku
                  ON sku.tenant_id = batch.tenant_id
                 AND sku.product_id = batch.product_id
                 AND sku.id = batch.sku_id
                WHERE sku.id IS NULL
                UNION ALL
                SELECT 'code_batches.tenant_product_sku_production_batch', batch.id
                FROM public.code_batches AS batch
                LEFT JOIN public.production_batches AS production_batch
                  ON production_batch.tenant_id = batch.tenant_id
                 AND production_batch.product_id = batch.product_id
                 AND production_batch.sku_id = batch.sku_id
                 AND production_batch.id = batch.production_batch_id
                WHERE production_batch.id IS NULL
                UNION ALL
                SELECT 'code_items.tenant_id', item.id
                FROM public.code_items AS item
                LEFT JOIN public.tenants AS tenant ON tenant.id = item.tenant_id
                WHERE tenant.id IS NULL
                UNION ALL
                SELECT 'code_items.tenant_code_batch', item.id
                FROM public.code_items AS item
                LEFT JOIN public.code_batches AS batch
                  ON batch.tenant_id = item.tenant_id AND batch.id = item.code_batch_id
                WHERE batch.id IS NULL
                UNION ALL
                SELECT 'production_batches.legacy_recalled_without_metadata', production_batch.id
                FROM public.production_batches AS production_batch
                WHERE production_batch.status::text = 'recalled'
            )
            SELECT string_agg(contract || '=' || id::text, ', ' ORDER BY contract, id::text)
            INTO invalid_rows
            FROM violations;
            IF invalid_rows IS NOT NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'Authoritative production-batch preflight failed: ' || invalid_rows;
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
    op.add_column(
        "production_batches",
        sa.Column("recall_reason", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "production_batches",
        sa.Column("recalled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "production_batches",
        sa.Column("recalled_by", sa.String(length=64), nullable=True),
    )

    op.create_unique_constraint(
        "uq_production_batches_tenant_product_sku_id",
        "production_batches",
        ["tenant_id", "product_id", "sku_id", "id"],
    )
    op.create_unique_constraint(
        "uq_code_batches_tenant_id_id",
        "code_batches",
        ["tenant_id", "id"],
    )
    op.create_index(
        "ix_code_batches_tenant_product",
        "code_batches",
        ["tenant_id", "product_id"],
        unique=False,
    )
    op.create_index(
        "ix_code_batches_tenant_product_sku",
        "code_batches",
        ["tenant_id", "product_id", "sku_id"],
        unique=False,
    )
    op.create_index(
        "ix_code_batches_tenant_product_sku_production_batch",
        "code_batches",
        ["tenant_id", "product_id", "sku_id", "production_batch_id"],
        unique=False,
    )

    for definition in (*_TENANT_FOREIGN_KEYS, *_RELATION_FOREIGN_KEYS):
        _add_foreign_key(*definition)

    op.execute(
        """
        ALTER TABLE public.code_batches
        ADD CONSTRAINT ck_code_batches_production_batch_id_not_null
        CHECK (production_batch_id IS NOT NULL) NOT VALID
        """
    )
    op.execute("ALTER TABLE public.code_batches VALIDATE CONSTRAINT ck_code_batches_production_batch_id_not_null")
    op.alter_column(
        "code_batches",
        "production_batch_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_constraint(
        "ck_code_batches_production_batch_id_not_null",
        "code_batches",
        type_="check",
    )

    op.execute(
        """
        ALTER TABLE public.production_batches
        ADD CONSTRAINT ck_production_batches_recall_metadata
        CHECK (
            (status::text = 'recalled'
             AND NULLIF(btrim(recall_reason), '') IS NOT NULL
             AND recalled_at IS NOT NULL
             AND recalled_by IS NOT NULL)
            OR
            (status::text <> 'recalled'
             AND recall_reason IS NULL
             AND recalled_at IS NULL
             AND recalled_by IS NULL)
        ) NOT VALID
        """
    )
    op.execute("ALTER TABLE public.production_batches VALIDATE CONSTRAINT ck_production_batches_recall_metadata")


def _install_recall_guards() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.{_PB_GUARD_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                RETURN NEW;
            END IF;
            IF OLD.status::text IN ('recalled', 'expired') THEN
                IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.product_id IS DISTINCT FROM OLD.product_id
                   OR NEW.sku_id IS DISTINCT FROM OLD.sku_id
                   OR NEW.batch_code IS DISTINCT FROM OLD.batch_code
                   OR NEW.production_date IS DISTINCT FROM OLD.production_date
                   OR NEW.expiry_date IS DISTINCT FROM OLD.expiry_date
                   OR NEW.origin IS DISTINCT FROM OLD.origin
                   OR NEW.external_id IS DISTINCT FROM OLD.external_id
                   OR NEW.source_system IS DISTINCT FROM OLD.source_system
                   OR NEW.status IS DISTINCT FROM OLD.status
                   OR NEW.recall_reason IS DISTINCT FROM OLD.recall_reason
                   OR NEW.recalled_at IS DISTINCT FROM OLD.recalled_at
                   OR NEW.recalled_by IS DISTINCT FROM OLD.recalled_by THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '22023',
                        MESSAGE = 'terminal production batch authoritative facts are immutable';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status THEN
                IF OLD.status::text <> 'active' OR NEW.status::text <> 'recalled' THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'production batch status may transition only from active to recalled';
                END IF;
            ELSIF NEW.recall_reason IS DISTINCT FROM OLD.recall_reason
               OR NEW.recalled_at IS DISTINCT FROM OLD.recalled_at
               OR NEW.recalled_by IS DISTINCT FROM OLD.recalled_by THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'recall metadata must be written with the active-to-recalled transition';
            END IF;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_PB_GUARD_FUNCTION}() FROM PUBLIC")
    op.execute(
        f"""
        CREATE TRIGGER trg_guard_production_batch_recall
        BEFORE INSERT OR UPDATE ON public.production_batches
        FOR EACH ROW EXECUTE FUNCTION public.{_PB_GUARD_FUNCTION}()
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_CODE_BATCH_GUARD_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND NEW.tenant_id IS NOT DISTINCT FROM OLD.tenant_id
               AND NEW.product_id IS NOT DISTINCT FROM OLD.product_id
               AND NEW.sku_id IS NOT DISTINCT FROM OLD.sku_id
               AND NEW.production_batch_id IS NOT DISTINCT FROM OLD.production_batch_id
               AND (
                   NEW.status IS NOT DISTINCT FROM OLD.status
                   OR NEW.status::text NOT IN (
                       'generating', 'completed', 'exported', 'printing', 'delivered', 'activated'
                   )
               ) THEN
                RETURN NEW;
            END IF;
            PERFORM production_batch.id
            FROM public.production_batches AS production_batch
            WHERE production_batch.tenant_id = NEW.tenant_id
              AND production_batch.product_id = NEW.product_id
              AND production_batch.sku_id = NEW.sku_id
              AND production_batch.id = NEW.production_batch_id
              AND production_batch.status::text = 'active'
              AND production_batch.expiry_date >= {_CHINA_BUSINESS_DATE_SQL}
            FOR SHARE OF production_batch NOWAIT;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'code batch requires an active, unexpired authoritative production batch';
            END IF;
            RETURN NEW;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING
                ERRCODE = '55P03',
                MESSAGE = 'authoritative production batch is concurrently changing';
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_CODE_BATCH_GUARD_FUNCTION}() FROM PUBLIC")
    op.execute(
        f"""
        CREATE TRIGGER trg_guard_code_batch_production_link
        BEFORE INSERT OR UPDATE OF tenant_id, product_id, sku_id, production_batch_id, status
        ON public.code_batches
        FOR EACH ROW EXECUTE FUNCTION public.{_CODE_BATCH_GUARD_FUNCTION}()
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_CODE_ITEM_GUARD_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        BEGIN
            IF NEW.status::text NOT IN ('activated', 'bound') THEN
                RETURN NEW;
            END IF;
            PERFORM production_batch.id
            FROM public.code_batches AS code_batch
            JOIN public.production_batches AS production_batch
              ON production_batch.tenant_id = code_batch.tenant_id
             AND production_batch.product_id = code_batch.product_id
             AND production_batch.sku_id = code_batch.sku_id
             AND production_batch.id = code_batch.production_batch_id
            WHERE code_batch.tenant_id = NEW.tenant_id
              AND code_batch.id = NEW.code_batch_id
              AND production_batch.status::text = 'active'
              AND production_batch.expiry_date >= {_CHINA_BUSINESS_DATE_SQL}
            FOR SHARE OF production_batch NOWAIT;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'active code item requires an active, unexpired authoritative production batch';
            END IF;
            RETURN NEW;
        EXCEPTION WHEN lock_not_available THEN
            RAISE EXCEPTION USING
                ERRCODE = '55P03',
                MESSAGE = 'authoritative production batch is concurrently changing';
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_CODE_ITEM_GUARD_FUNCTION}() FROM PUBLIC")
    op.execute(
        f"""
        CREATE TRIGGER trg_guard_code_item_production_batch_active
        BEFORE INSERT OR UPDATE OF tenant_id, code_batch_id, status
        ON public.code_items
        FOR EACH ROW EXECUTE FUNCTION public.{_CODE_ITEM_GUARD_FUNCTION}()
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION public.{_RECALL_FREEZE_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE code_batch_row record;
        DECLARE code_item_row record;
        BEGIN
            IF OLD.status::text <> 'active' OR NEW.status::text <> 'recalled' THEN
                RETURN NEW;
            END IF;
            FOR code_batch_row IN
                SELECT code_batch.id
                FROM public.code_batches AS code_batch
                WHERE code_batch.tenant_id = NEW.tenant_id
                  AND code_batch.product_id = NEW.product_id
                  AND code_batch.sku_id = NEW.sku_id
                  AND code_batch.production_batch_id = NEW.id
                ORDER BY code_batch.id::text
                FOR UPDATE
            LOOP
                FOR code_item_row IN
                    SELECT code_item.id
                    FROM public.code_items AS code_item
                    WHERE code_item.tenant_id = NEW.tenant_id
                      AND code_item.code_batch_id = code_batch_row.id
                      AND code_item.status::text IN ('activated', 'bound')
                    ORDER BY code_item.id::text
                    FOR UPDATE
                LOOP
                    UPDATE public.code_items
                    SET status = 'frozen', updated_at = CURRENT_TIMESTAMP
                    WHERE id = code_item_row.id;
                END LOOP;
            END LOOP;
            RETURN NEW;
        END
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_RECALL_FREEZE_FUNCTION}() FROM PUBLIC")
    op.execute(
        f"""
        CREATE TRIGGER trg_freeze_recalled_production_batch_codes
        AFTER UPDATE OF status ON public.production_batches
        FOR EACH ROW EXECUTE FUNCTION public.{_RECALL_FREEZE_FUNCTION}()
        """
    )


def _install_authenticated_audit_interface(*, include_authority_actions: bool) -> None:
    authority_cases = ""
    authority_declaration = ""
    authority_validation = ""
    if include_authority_actions:
        authority_cases = """
                    WHEN 'production_batch_recalled' THEN 'products'
                    WHEN 'code_batch_created' THEN 'codes'
                    WHEN 'code_batch_updated' THEN 'codes'
                    WHEN 'code_import_completed' THEN 'codes'
                    WHEN 'code_bind' THEN 'codes'
                    WHEN 'code_mark_printing' THEN 'codes'
                    WHEN 'code_mark_delivered' THEN 'codes'
                    WHEN 'catalog_import_completed' THEN 'products'
        """
        authority_declaration = """
        DECLARE requested_resource_id uuid;
        DECLARE requested_resource_public_id text;
        """
        authority_validation = """
            IF requested_action = 'production_batch_recalled' THEN
                BEGIN
                    requested_resource_id := split_part(requested_resource, ':', 2)::uuid;
                EXCEPTION WHEN invalid_text_representation THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '22023',
                        MESSAGE = 'production batch recall audit resource is invalid';
                END;
                IF requested_resource IS DISTINCT FROM 'production_batch:' || requested_resource_id::text
                   OR NOT EXISTS (
                       SELECT 1 FROM public.production_batches
                       WHERE tenant_id = target_uuid AND id = requested_resource_id
                   ) THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23503',
                        MESSAGE = 'production batch recall audit resource is not tenant-owned';
                END IF;
            ELSIF requested_action IN (
                'code_batch_created',
                'code_batch_updated',
                'code_import_completed',
                'code_mark_printing',
                'code_mark_delivered'
            ) THEN
                BEGIN
                    requested_resource_id := split_part(requested_resource, ':', 2)::uuid;
                EXCEPTION WHEN invalid_text_representation THEN
                    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'code batch audit resource is invalid';
                END;
                IF requested_resource_id IS NULL
                   OR requested_resource IS DISTINCT FROM 'code_batch:' || requested_resource_id::text THEN
                    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'code batch audit resource is invalid';
                END IF;
                IF NOT EXISTS (
                       SELECT 1 FROM public.code_batches
                       WHERE tenant_id = target_uuid AND id = requested_resource_id
                   ) THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23503',
                        MESSAGE = 'code batch audit resource is not tenant-owned';
                END IF;
            ELSIF requested_action = 'code_bind' THEN
                requested_resource_public_id := split_part(requested_resource, ':', 2);
                IF requested_resource_public_id IS NULL
                   OR requested_resource_public_id = ''
                   OR requested_resource IS DISTINCT FROM 'code_item:' || requested_resource_public_id THEN
                    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'code item audit resource is invalid';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM public.code_items
                    WHERE tenant_id = target_uuid AND public_id = requested_resource_public_id
                ) THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23503',
                        MESSAGE = 'code item audit resource is not tenant-owned';
                END IF;
            ELSIF requested_action = 'catalog_import_completed' THEN
                BEGIN
                    requested_resource_id := split_part(requested_resource, ':', 2)::uuid;
                EXCEPTION WHEN invalid_text_representation THEN
                    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'catalog import audit resource is invalid';
                END;
                IF requested_resource IS DISTINCT FROM 'catalog_import:' || requested_resource_id::text THEN
                    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'catalog import audit resource is invalid';
                END IF;
                IF jsonb_typeof(requested_details) IS DISTINCT FROM 'object'
                   OR NOT requested_details ?& ARRAY['created', 'updated', 'errors', 'import_type']
                   OR EXISTS (
                       SELECT 1 FROM jsonb_object_keys(requested_details) AS detail_key
                       WHERE detail_key <> ALL (ARRAY['created', 'updated', 'errors', 'import_type'])
                   )
                   OR jsonb_typeof(requested_details -> 'created') IS DISTINCT FROM 'number'
                   OR jsonb_typeof(requested_details -> 'updated') IS DISTINCT FROM 'number'
                   OR jsonb_typeof(requested_details -> 'errors') IS DISTINCT FROM 'number'
                   OR jsonb_typeof(requested_details -> 'import_type') IS DISTINCT FROM 'string' THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '22023',
                        MESSAGE = 'catalog import audit details must contain summary counts and import_type only';
                END IF;
            END IF;
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
        {authority_declaration}
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
                    {authority_cases}
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
            {authority_validation}
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


def _downgrade_preflight() -> None:
    op.execute(
        """
        DO $block$
        DECLARE recall_ids text;
        BEGIN
            SELECT string_agg(id::text, ', ' ORDER BY id::text)
            INTO recall_ids
            FROM public.production_batches
            WHERE recall_reason IS NOT NULL OR recalled_at IS NOT NULL OR recalled_by IS NOT NULL;
            IF recall_ids IS NOT NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'Cannot discard authoritative recall metadata; production batch ids: ' || recall_ids;
            END IF;
        END
        $block$
        """
    )


def _drop_recall_guards() -> None:
    op.execute("DROP TRIGGER trg_freeze_recalled_production_batch_codes ON public.production_batches")
    op.execute(f"DROP FUNCTION public.{_RECALL_FREEZE_FUNCTION}()")
    op.execute("DROP TRIGGER trg_guard_code_item_production_batch_active ON public.code_items")
    op.execute(f"DROP FUNCTION public.{_CODE_ITEM_GUARD_FUNCTION}()")
    op.execute("DROP TRIGGER trg_guard_code_batch_production_link ON public.code_batches")
    op.execute(f"DROP FUNCTION public.{_CODE_BATCH_GUARD_FUNCTION}()")
    op.execute("DROP TRIGGER trg_guard_production_batch_recall ON public.production_batches")
    op.execute(f"DROP FUNCTION public.{_PB_GUARD_FUNCTION}()")


def _drop_schema_contract() -> None:
    op.drop_constraint(
        "ck_production_batches_recall_metadata",
        "production_batches",
        type_="check",
    )
    op.alter_column(
        "code_batches",
        "production_batch_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    for table, name, *_rest in reversed((*_TENANT_FOREIGN_KEYS, *_RELATION_FOREIGN_KEYS)):
        op.drop_constraint(name, table, type_="foreignkey")
    op.drop_index(
        "ix_code_batches_tenant_product_sku_production_batch",
        table_name="code_batches",
    )
    op.drop_index("ix_code_batches_tenant_product_sku", table_name="code_batches")
    op.drop_index("ix_code_batches_tenant_product", table_name="code_batches")
    op.drop_constraint("uq_code_batches_tenant_id_id", "code_batches", type_="unique")
    op.drop_constraint(
        "uq_production_batches_tenant_product_sku_id",
        "production_batches",
        type_="unique",
    )
    op.drop_column("production_batches", "recalled_by")
    op.drop_column("production_batches", "recalled_at")
    op.drop_column("production_batches", "recall_reason")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")

    _preflight()
    _install_schema_contract()
    _install_recall_guards()
    _install_authenticated_audit_interface(include_authority_actions=True)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("SET LOCAL search_path = public, pg_catalog")

    _downgrade_preflight()
    _install_authenticated_audit_interface(include_authority_actions=False)
    _drop_recall_guards()
    _drop_schema_contract()
