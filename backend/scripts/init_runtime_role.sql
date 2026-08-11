-- Development and CI only.
-- Production must provision a restricted runtime role with managed secrets;
-- never execute this file against a production database.
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'yimatong_app') THEN
        CREATE ROLE yimatong_app
            LOGIN
            PASSWORD 'yimatong_app'
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOBYPASSRLS;
    ELSE
        ALTER ROLE yimatong_app
            WITH LOGIN
            PASSWORD 'yimatong_app'
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOBYPASSRLS;
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO yimatong_app', current_database());
END
$$;
GRANT USAGE ON SCHEMA public TO yimatong_app;

-- Replays start from a closed privilege set.  This script intentionally has
-- no ALL TABLES/ALL SEQUENCES grant and no runtime default table privilege:
-- future relations remain inaccessible until an audited migration explicitly
-- hardens and authorizes them.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM yimatong_app;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM yimatong_app;
ALTER DEFAULT PRIVILEGES FOR ROLE yimatong IN SCHEMA public
    REVOKE ALL ON TABLES FROM yimatong_app;
ALTER DEFAULT PRIVILEGES FOR ROLE yimatong IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM yimatong_app;

-- This is the reviewed business-relation registry.  A listed relation is not
-- sufficient by itself: it receives DML only when the live catalog proves
-- ENABLE+FORCE RLS and every policy that mentions bypass also verifies the
-- session principal's parameter privilege.  New tables must be added here by
-- the same migration that establishes those invariants.
CREATE TEMP TABLE runtime_business_relation_allowlist (
    table_name name PRIMARY KEY
) ON COMMIT DROP;

INSERT INTO runtime_business_relation_allowlist (table_name)
SELECT unnest(ARRAY[
    'account_channel_scopes', 'account_roles', 'accounts',
    'ai_generations', 'anonymous_visitors', 'benefit_claims',
    'benefit_deliveries', 'benefits', 'brands', 'campaign_risk_rules',
    'campaigns', 'code_allocations', 'code_batch_generation_receipts', 'code_batches', 'code_items',
    'connectors', 'consent_records', 'consumer_profiles', 'coupon_codes',
    'coupon_pools', 'daily_scan_stats', 'distributors', 'diversion_clues',
    'diversion_evidence', 'diversion_investigation_history', 'export_logs',
    'external_orders', 'gmv_attributions', 'gmv_daily_stats', 'intent_events',
    'interception_records', 'launch_releases', 'ops_tasks', 'organizations',
    'page_templates', 'page_versions', 'permissions', 'point_products',
    'pilot_milestone_corrections', 'pilot_milestones',
    'point_redemptions', 'point_rules', 'point_transactions',
    'private_domain_configs', 'product_assets', 'production_batches',
    'products', 'regional_code_rules', 'regional_org_members', 'regional_orgs',
    'regional_product_auths', 'regional_templates', 'regions', 'risk_alerts',
    'risk_notifications', 'risk_rules', 'role_permissions', 'roles',
    'scan_events', 'skus', 'stores', 'sync_mappings', 'sync_records',
    'takeover_aliases', 'takeover_cutover_events', 'takeover_domain_checks',
    'takeover_import_errors', 'takeover_import_jobs', 'takeover_observations',
    'takeover_projects', 'takeover_route_versions', 'tenant_domains',
    'tenant_health_metrics', 'tenant_quota_usage', 'tenants', 'translations', 'retrospectives', 'webhook_deliveries',
    'webhook_endpoints', 'wecom_contact_ways', 'wecom_external_contacts',
    'whitelabel_configs'
]::name[]);

-- Physical code identities, generation receipts, and export manifests are
-- durable evidence. Runtime may create and advance them through guarded
-- transitions, but it must never erase them.
CREATE TEMP TABLE runtime_no_delete_relation_allowlist (
    table_name name PRIMARY KEY
) ON COMMIT DROP;
INSERT INTO runtime_no_delete_relation_allowlist (table_name)
VALUES ('code_batches'),
       ('code_items'),
       ('code_batch_generation_receipts'),
       ('interception_records'),
       ('export_logs');
-- During the explicit expand/deploy phase the nullable legacy raw column still
-- exists so drained old processes require the previous tenant-scoped CRUD ACL.
-- The finalize revision drops that column and moves api_keys to the restricted
-- mutation registry below.
INSERT INTO runtime_business_relation_allowlist (table_name)
SELECT 'api_keys'::name
WHERE EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'api_keys' AND column_name = 'key'
);

-- Sensitive tenant-owned state is visible to runtime but all transitions are
-- available only through reviewed SECURITY DEFINER functions.
CREATE TEMP TABLE runtime_restricted_mutation_relation_allowlist (
    table_name name PRIMARY KEY
) ON COMMIT DROP;
INSERT INTO runtime_restricted_mutation_relation_allowlist (table_name)
VALUES ('agency_authorizations');
INSERT INTO runtime_restricted_mutation_relation_allowlist (table_name)
SELECT 'api_keys'::name
WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'api_keys' AND column_name = 'key'
);

CREATE TEMP TABLE runtime_append_only_relation_allowlist (
    table_name name PRIMARY KEY
) ON COMMIT DROP;
INSERT INTO runtime_append_only_relation_allowlist (table_name)
VALUES ('platform_audit_log');

-- Scoped capability for tenant-owned account maintenance. The SECURITY
-- DEFINER function validates the runtime principal and current tenant before
-- touching the otherwise control-only auth_sessions relation.
DO $$
BEGIN
    IF to_regprocedure('public.revoke_current_tenant_account_sessions(uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.revoke_current_tenant_account_sessions(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.revoke_current_tenant_account_sessions(uuid) TO yimatong_app;
    END IF;
END
$$;

DO $$
BEGIN
    IF to_regprocedure(
        'public.renew_agency_authorization(uuid,uuid,uuid,jsonb,uuid,uuid,timestamp with time zone)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.renew_agency_authorization(
            uuid, uuid, uuid, jsonb, uuid, uuid, timestamptz
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.renew_agency_authorization(
            uuid, uuid, uuid, jsonb, uuid, uuid, timestamptz
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.revoke_agency_authorization(uuid,uuid,uuid,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.revoke_agency_authorization(uuid, uuid, uuid, uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.revoke_agency_authorization(uuid, uuid, uuid, uuid) TO yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.append_authenticated_audit_event(uuid,uuid,text,text,text,jsonb)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.append_authenticated_audit_event(
            uuid, uuid, text, text, text, jsonb
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.append_authenticated_audit_event(
            uuid, uuid, text, text, text, jsonb
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.mark_code_item_first_scanned(uuid,text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.mark_code_item_first_scanned(uuid, text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.mark_code_item_first_scanned(uuid, text) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.authorize_code_lifecycle_actor(uuid,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.authorize_code_lifecycle_actor(uuid, uuid) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.authorize_code_lifecycle_actor(uuid, uuid) FROM yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.transition_code_item_lifecycle(uuid,uuid,uuid,uuid,text,text)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.transition_code_item_lifecycle(
            uuid, uuid, uuid, uuid, text, text
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.transition_code_item_lifecycle(
            uuid, uuid, uuid, uuid, text, text
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.transition_code_batch_lifecycle(uuid,uuid,uuid,uuid,text,text)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.transition_code_batch_lifecycle(
            uuid, uuid, uuid, uuid, text, text
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.transition_code_batch_lifecycle(
            uuid, uuid, uuid, uuid, text, text
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.freeze_code_item_for_risk(uuid,uuid,uuid,uuid,uuid)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.freeze_code_item_for_risk(
            uuid, uuid, uuid, uuid, uuid
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.freeze_code_item_for_risk(
            uuid, uuid, uuid, uuid, uuid
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.enqueue_takeover_import_job(uuid,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.enqueue_takeover_import_job(uuid,uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.enqueue_takeover_import_job(uuid,uuid) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.claim_takeover_import_job(uuid,uuid,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.claim_takeover_import_job(uuid,uuid,uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.claim_takeover_import_job(uuid,uuid,uuid) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.fail_takeover_import_job(uuid,uuid,uuid,text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.fail_takeover_import_job(uuid,uuid,uuid,text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.fail_takeover_import_job(uuid,uuid,uuid,text) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.complete_takeover_import_job(uuid,uuid,uuid,text,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.complete_takeover_import_job(uuid,uuid,uuid,text,jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.complete_takeover_import_job(uuid,uuid,uuid,text,jsonb) TO yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.record_takeover_server_probe(uuid,uuid,uuid,uuid,uuid,uuid,text,text,text,double precision,jsonb,text)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.record_takeover_server_probe(
            uuid,uuid,uuid,uuid,uuid,uuid,text,text,text,double precision,jsonb,text
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.record_takeover_server_probe(
            uuid,uuid,uuid,uuid,uuid,uuid,text,text,text,double precision,jsonb,text
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.record_takeover_domain_check(uuid,uuid,uuid,uuid,text,jsonb,jsonb,jsonb,integer,text,timestamp with time zone,text,text,jsonb)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.record_takeover_domain_check(
            uuid,uuid,uuid,uuid,text,jsonb,jsonb,jsonb,integer,text,timestamp with time zone,text,text,jsonb
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.record_takeover_domain_check(
            uuid,uuid,uuid,uuid,text,jsonb,jsonb,jsonb,integer,text,timestamp with time zone,text,text,jsonb
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.transition_takeover_route(uuid,uuid,uuid,uuid,uuid,text,text,text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.transition_takeover_route(
            uuid,uuid,uuid,uuid,uuid,text,text,text
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.transition_takeover_route(
            uuid,uuid,uuid,uuid,uuid,text,text,text
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.resolve_takeover_public_route(text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.resolve_takeover_public_route(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.resolve_takeover_public_route(text) TO yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.append_api_key_catalog_audit_event(uuid,uuid,uuid,text,uuid,jsonb)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.append_api_key_catalog_audit_event(
            uuid, uuid, uuid, text, uuid, jsonb
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.append_api_key_catalog_audit_event(
            uuid, uuid, uuid, text, uuid, jsonb
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.resolve_active_api_key(text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.resolve_active_api_key(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.resolve_active_api_key(text) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.lock_active_api_key(uuid,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.lock_active_api_key(uuid, uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.lock_active_api_key(uuid, uuid) TO yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.issue_api_key(uuid,uuid,uuid,uuid,text,text,text,text,timestamp with time zone,text,text,bytea,text,uuid)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.issue_api_key(
            uuid, uuid, uuid, uuid, text, text, text, text, timestamptz,
            text, text, bytea, text, uuid
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.issue_api_key(
            uuid, uuid, uuid, uuid, text, text, text, text, timestamptz,
            text, text, bytea, text, uuid
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.rotate_api_key(uuid,uuid,uuid,uuid,uuid,text,text,text,text,timestamp with time zone,text,text,bytea,uuid)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.rotate_api_key(
            uuid, uuid, uuid, uuid, uuid, text, text, text, text, timestamptz,
            text, text, bytea, uuid
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.rotate_api_key(
            uuid, uuid, uuid, uuid, uuid, text, text, text, text, timestamptz,
            text, text, bytea, uuid
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.revoke_api_key(uuid,uuid,uuid,uuid,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.revoke_api_key(uuid, uuid, uuid, uuid, uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.revoke_api_key(uuid, uuid, uuid, uuid, uuid) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.api_key_permissions_for_role(text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.api_key_permissions_for_role(text) FROM PUBLIC;
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'api_keys' AND column_name = 'key'
        ) THEN
            GRANT EXECUTE ON FUNCTION public.api_key_permissions_for_role(text) TO yimatong_app;
        ELSE
            REVOKE EXECUTE ON FUNCTION public.api_key_permissions_for_role(text) FROM yimatong_app;
        END IF;
    END IF;
END
$$;

CREATE TEMP TABLE runtime_control_relation_allowlist (
    table_name name PRIMARY KEY
) ON COMMIT DROP;
INSERT INTO runtime_control_relation_allowlist (table_name)
SELECT unnest(ARRAY[
    'auth_sessions', 'consumed_refresh_tokens', 'invite_registration_receipts',
    'operator_campaign_manage_grants', 'organization_parent_repair_backups',
    'platform_auth_sessions', 'platform_configs', 'platform_tenant_openings',
    'role_template_backups', 'tenant_invite_codes',
    'tenant_platform_role_assignment_backups'
]::name[]);
INSERT INTO runtime_control_relation_allowlist (table_name)
SELECT 'takeover_domain_claims'::name
WHERE to_regclass('public.takeover_domain_claims') IS NOT NULL;

CREATE TEMP TABLE runtime_public_relation_allowlist (
    table_name name PRIMARY KEY
) ON COMMIT DROP;
INSERT INTO runtime_public_relation_allowlist (table_name) VALUES ('plan_definitions');

-- Global deployment state needed by ordinary quota writes. The runtime role
-- may lock/read the singleton but never mutate rollout control state.
CREATE TEMP TABLE runtime_read_only_global_relation_allowlist (
    table_name name PRIMARY KEY
) ON COMMIT DROP;
INSERT INTO runtime_read_only_global_relation_allowlist (table_name)
VALUES ('quota_rollout_state');

CREATE TEMP TABLE runtime_migration_relation_allowlist (
    table_name name PRIMARY KEY
) ON COMMIT DROP;
INSERT INTO runtime_migration_relation_allowlist (table_name)
VALUES ('alembic_version'),
       ('agency_authorization_integrity_backups'),
       ('api_key_catalog_audit_context_secrets'),
       ('api_key_legacy_secret_backups'),
       ('code_delivery_contract_rollout_state'),
       ('rls_force_remediation_backups'),
       ('runtime_privilege_remediation_backup');

-- The live public root catalog and the reviewed ORM registry must be a
-- one-to-one classification.  Missing required business relations are an
-- outage and fail provisioning; unexpected future relations remain closed and
-- force the same review instead of being silently skipped.
DO $$
DECLARE
    registry_count integer;
    unclassified text;
    missing text;
BEGIN
    SELECT count(*) INTO registry_count
    FROM (
        SELECT table_name FROM runtime_business_relation_allowlist
        UNION ALL SELECT table_name FROM runtime_restricted_mutation_relation_allowlist
        UNION ALL SELECT table_name FROM runtime_append_only_relation_allowlist
        UNION ALL SELECT table_name FROM runtime_control_relation_allowlist
        UNION ALL SELECT table_name FROM runtime_public_relation_allowlist
        UNION ALL SELECT table_name FROM runtime_read_only_global_relation_allowlist
    ) AS orm_registry;
    IF registry_count <> 98 + (CASE
        WHEN to_regclass('public.takeover_domain_claims') IS NULL THEN 0 ELSE 1 END) THEN
        RAISE EXCEPTION 'Runtime ORM registry count is inconsistent, got %', registry_count;
    END IF;

    SELECT string_agg(table_name::text, ', ' ORDER BY table_name) INTO missing
    FROM (
        SELECT table_name FROM runtime_business_relation_allowlist
        UNION ALL SELECT table_name FROM runtime_restricted_mutation_relation_allowlist
        UNION ALL SELECT table_name FROM runtime_append_only_relation_allowlist
        UNION ALL SELECT table_name FROM runtime_control_relation_allowlist
        UNION ALL SELECT table_name FROM runtime_public_relation_allowlist
        UNION ALL SELECT table_name FROM runtime_read_only_global_relation_allowlist
        UNION ALL SELECT table_name FROM runtime_migration_relation_allowlist
    ) AS registry
    WHERE to_regclass(format('public.%I', table_name)) IS NULL;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Required classified relations are missing: %', missing;
    END IF;

    SELECT string_agg(cls.relname, ', ' ORDER BY cls.relname) INTO unclassified
    FROM pg_class AS cls
    JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
    WHERE ns.nspname = 'public'
      AND cls.relkind IN ('r', 'p')
      AND NOT EXISTS (SELECT 1 FROM pg_inherits AS inh WHERE inh.inhrelid = cls.oid)
      AND NOT EXISTS (
          SELECT 1
          FROM (
              SELECT table_name FROM runtime_business_relation_allowlist
              UNION ALL SELECT table_name FROM runtime_restricted_mutation_relation_allowlist
              UNION ALL SELECT table_name FROM runtime_append_only_relation_allowlist
              UNION ALL SELECT table_name FROM runtime_control_relation_allowlist
              UNION ALL SELECT table_name FROM runtime_public_relation_allowlist
              UNION ALL SELECT table_name FROM runtime_read_only_global_relation_allowlist
              UNION ALL SELECT table_name FROM runtime_migration_relation_allowlist
          ) AS registry
          WHERE registry.table_name = cls.relname
      );
    IF unclassified IS NOT NULL THEN
        RAISE EXCEPTION 'Unclassified public relations: %', unclassified;
    END IF;
END
$$;

-- Restricted mutation relations are SELECT-only for runtime. Every transition
-- is performed by reviewed functions after revalidating the durable login
-- session or the presented credential as appropriate.
DO $$
DECLARE
    relation_row record;
    has_select boolean;
BEGIN
    FOR relation_row IN
        SELECT cls.oid, ns.nspname AS schema_name, cls.relname AS table_name,
               cls.relrowsecurity, cls.relforcerowsecurity
        FROM runtime_restricted_mutation_relation_allowlist AS allowlist
        JOIN pg_class AS cls ON cls.relname = allowlist.table_name
        JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
        WHERE ns.nspname = 'public' AND cls.relkind = 'r'
    LOOP
        IF NOT relation_row.relrowsecurity OR NOT relation_row.relforcerowsecurity THEN
            RAISE EXCEPTION 'Restricted mutation relation %.% lacks ENABLE+FORCE RLS',
                relation_row.schema_name, relation_row.table_name;
        END IF;
        SELECT bool_or(polcmd IN ('*', 'r') AND polqual IS NOT NULL)
        INTO has_select
        FROM pg_policy
        WHERE polrelid = relation_row.oid;
        IF NOT COALESCE(has_select, false) THEN
            RAISE EXCEPTION 'Restricted mutation relation lacks a tenant read policy';
        END IF;
        EXECUTE format(
            'GRANT SELECT ON TABLE %I.%I TO yimatong_app',
            relation_row.schema_name,
            relation_row.table_name
        );
        EXECUTE format(
            'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
            'ON TABLE %I.%I FROM yimatong_app',
            relation_row.schema_name,
            relation_row.table_name
        );
    END LOOP;
END
$$;

-- The audit ledger is tenant-readable and append-only for the runtime role.
-- It deliberately has a separate registry class so replay can never grant
-- UPDATE or DELETE merely because it is tenant-visible.
DO $$
DECLARE
    relation_row record;
    unsafe_policy text;
    has_select boolean;
    has_insert boolean;
    has_update boolean;
    has_delete boolean;
BEGIN
    FOR relation_row IN
        SELECT cls.oid, ns.nspname AS schema_name, cls.relname AS table_name,
               cls.relrowsecurity, cls.relforcerowsecurity
        FROM runtime_append_only_relation_allowlist AS allowlist
        JOIN pg_class AS cls ON cls.relname = allowlist.table_name
        JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
        WHERE ns.nspname = 'public' AND cls.relkind = 'r'
    LOOP
        IF NOT relation_row.relrowsecurity OR NOT relation_row.relforcerowsecurity THEN
            RAISE EXCEPTION 'Append-only relation %.% lacks ENABLE+FORCE RLS',
                relation_row.schema_name, relation_row.table_name;
        END IF;

        SELECT string_agg(policy.polname, ', ' ORDER BY policy.polname)
        INTO unsafe_policy
        FROM pg_policy AS policy
        WHERE policy.polrelid = relation_row.oid
          AND (
              (policy.polqual IS NOT NULL
               AND pg_get_expr(policy.polqual, policy.polrelid) LIKE '%app.bypass_rls%'
               AND pg_get_expr(policy.polqual, policy.polrelid) NOT LIKE '%has_parameter_privilege%')
              OR
              (policy.polwithcheck IS NOT NULL
               AND pg_get_expr(policy.polwithcheck, policy.polrelid) LIKE '%app.bypass_rls%'
               AND pg_get_expr(policy.polwithcheck, policy.polrelid) NOT LIKE '%has_parameter_privilege%')
          );
        IF unsafe_policy IS NOT NULL THEN
            RAISE EXCEPTION 'Append-only relation % has unguarded bypass policies: %',
                relation_row.table_name, unsafe_policy;
        END IF;

        SELECT
            bool_or(polcmd IN ('*', 'r') AND polqual IS NOT NULL),
            bool_or(polcmd IN ('*', 'a') AND COALESCE(polwithcheck, polqual) IS NOT NULL),
            bool_or(polcmd IN ('*', 'w')),
            bool_or(polcmd IN ('*', 'd'))
        INTO has_select, has_insert, has_update, has_delete
        FROM pg_policy
        WHERE polrelid = relation_row.oid;
        IF NOT COALESCE(has_select, false)
           OR NOT COALESCE(has_insert, false)
           OR COALESCE(has_update, false)
           OR COALESCE(has_delete, false) THEN
            RAISE EXCEPTION
                'Append-only relation % policy coverage invalid (select %, insert %, update %, delete %)',
                relation_row.table_name, has_select, has_insert, has_update, has_delete;
        END IF;

        EXECUTE format(
            'GRANT SELECT, INSERT ON TABLE %I.%I TO yimatong_app',
            relation_row.schema_name,
            relation_row.table_name
        );
        EXECUTE format(
            'REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE %I.%I FROM yimatong_app',
            relation_row.schema_name,
            relation_row.table_name
        );
    END LOOP;
END
$$;

DO $$
DECLARE
    relation_row record;
    unsafe_policy text;
    has_select boolean;
    has_insert boolean;
    has_update boolean;
    has_delete boolean;
BEGIN
    FOR relation_row IN
        SELECT cls.oid, ns.nspname AS schema_name, cls.relname AS table_name,
               cls.relrowsecurity, cls.relforcerowsecurity
        FROM runtime_business_relation_allowlist AS allowlist
        JOIN pg_class AS cls ON cls.relname = allowlist.table_name
        JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
        WHERE ns.nspname = 'public'
          AND cls.relkind IN ('r', 'p')
    LOOP
        IF NOT relation_row.relrowsecurity OR NOT relation_row.relforcerowsecurity THEN
            RAISE EXCEPTION 'Business relation %.% lacks ENABLE+FORCE RLS',
                relation_row.schema_name, relation_row.table_name;
        END IF;

        -- USING and WITH CHECK are independent command expressions.  A
        -- missing USING on INSERT is normal and must not invalidate the valid
        -- WITH CHECK guard.  Only an expression which actually mentions the
        -- bypass parameter is required to prove parameter privilege.
        SELECT string_agg(policy.polname, ', ' ORDER BY policy.polname)
        INTO unsafe_policy
        FROM pg_policy AS policy
        WHERE policy.polrelid = relation_row.oid
          AND (
              (policy.polqual IS NOT NULL
               AND pg_get_expr(policy.polqual, policy.polrelid) LIKE '%app.bypass_rls%'
               AND pg_get_expr(policy.polqual, policy.polrelid) NOT LIKE '%has_parameter_privilege%')
              OR
              (policy.polwithcheck IS NOT NULL
               AND pg_get_expr(policy.polwithcheck, policy.polrelid) LIKE '%app.bypass_rls%'
               AND pg_get_expr(policy.polwithcheck, policy.polrelid) NOT LIKE '%has_parameter_privilege%')
          );
        IF unsafe_policy IS NOT NULL THEN
            RAISE EXCEPTION 'Business relation % has unguarded bypass policies: %',
                relation_row.table_name, unsafe_policy;
        END IF;

        SELECT
            bool_or(polcmd IN ('*', 'r') AND polqual IS NOT NULL),
            bool_or(polcmd IN ('*', 'a') AND COALESCE(polwithcheck, polqual) IS NOT NULL),
            bool_or(polcmd IN ('*', 'w') AND polqual IS NOT NULL),
            bool_or(polcmd IN ('*', 'd') AND polqual IS NOT NULL)
        INTO has_select, has_insert, has_update, has_delete
        FROM pg_policy
        WHERE polrelid = relation_row.oid;
        IF NOT COALESCE(has_select, false)
           OR NOT COALESCE(has_insert, false)
           OR NOT COALESCE(has_update, false)
           OR NOT COALESCE(has_delete, false) THEN
            RAISE EXCEPTION
                'Business relation % lacks complete CRUD policy coverage (select %, insert %, update %, delete %)',
                relation_row.table_name, has_select, has_insert, has_update, has_delete;
        END IF;

        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %I.%I TO yimatong_app',
            relation_row.schema_name,
            relation_row.table_name
        );
    END LOOP;
END
$$;

DO $$
DECLARE
    relation_row record;
BEGIN
    FOR relation_row IN
        SELECT cls.oid, ns.nspname AS schema_name, cls.relname AS table_name
        FROM runtime_no_delete_relation_allowlist AS allowlist
        JOIN pg_class AS cls ON cls.relname = allowlist.table_name
        JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
        WHERE ns.nspname = 'public' AND cls.relkind = 'r'
    LOOP
        EXECUTE format(
            'REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE %I.%I FROM yimatong_app',
            relation_row.schema_name,
            relation_row.table_name
        );
    END LOOP;
END
$$;

-- Code items remain runtime-insertable for authoritative generation and
-- tenant-readable for scan resolution, but lifecycle mutation is function-only.
REVOKE UPDATE ON TABLE public.code_items FROM yimatong_app;

-- Takeover delivery evidence and state transitions are function-controlled.
-- Runtime may insert immutable route candidates, staged aliases, and import
-- evidence; domain checks and lifecycle evidence use actor-bound functions. It cannot retarget live aliases, forge lifecycle
-- events, or forge authoritative server probes.
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON TABLE public.takeover_route_versions FROM yimatong_app;
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON TABLE public.takeover_aliases FROM yimatong_app;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON TABLE public.takeover_observations FROM yimatong_app;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON TABLE public.takeover_domain_checks FROM yimatong_app;
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON TABLE public.takeover_import_errors,
             public.takeover_import_jobs
    FROM yimatong_app;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON TABLE public.takeover_cutover_events FROM yimatong_app;

-- CSV artifacts contain the complete code list. The runtime role may read
-- ordinary export-log metadata, but encrypted envelope columns are available
-- only through the tenant-bound SECURITY DEFINER getter installed by Alembic.
REVOKE SELECT ON TABLE public.export_logs FROM yimatong_app;
GRANT SELECT (
    id, tenant_id, account_id, export_type, resource_id, file_name,
    row_count, status, code_batch_id, manifest_version, checksum_sha256,
    artifact_size_bytes, created_at, updated_at
) ON TABLE public.export_logs TO yimatong_app;
GRANT EXECUTE ON FUNCTION public.get_code_export_artifact(uuid, uuid, uuid) TO yimatong_app;

-- Deliberately public catalog data used by tenant requests is read-only.
GRANT SELECT ON TABLE public.plan_definitions TO yimatong_app;
GRANT SELECT ON TABLE public.quota_rollout_state TO yimatong_app;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON TABLE public.quota_rollout_state FROM yimatong_app;

-- Cross-tenant control state and migration evidence never belong to the
-- ordinary application role. Keep new tables deny-by-default; migrations must
-- explicitly grant runtime DML for tenant business tables.
REVOKE SET ON PARAMETER "app.bypass_rls" FROM PUBLIC;
REVOKE SET ON PARAMETER "app.api_key_id" FROM PUBLIC;
DO $$
DECLARE
    protected_table text;
BEGIN
    FOREACH protected_table IN ARRAY ARRAY[
        'platform_tenant_openings',
        'consumed_refresh_tokens',
        'invite_registration_receipts',
        'role_template_backups',
        'organization_parent_repair_backups',
        'tenant_platform_role_assignment_backups',
        'platform_auth_sessions',
        'platform_configs',
        'tenant_invite_codes',
        'operator_campaign_manage_grants',
        'takeover_domain_claims',
        'agency_authorization_integrity_backups',
        'api_key_catalog_audit_context_secrets',
        'api_key_legacy_secret_backups',
        'rls_force_remediation_backups',
        'runtime_privilege_remediation_backup',
        'alembic_version'
    ]
    LOOP
        IF to_regclass(format('public.%I', protected_table)) IS NOT NULL THEN
            EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.%I FROM yimatong_app', protected_table);
        END IF;
    END LOOP;
END
$$;

-- A replay must never expose a newly attached scan partition before the
-- partition lifecycle has installed FORCE RLS and the guarded tenant policy.
DO $$
DECLARE
    partition_row record;
    is_guarded boolean;
BEGIN
    FOR partition_row IN
        SELECT child_ns.nspname AS schema_name, child_cls.relname AS table_name,
               child_cls.relrowsecurity, child_cls.relforcerowsecurity, child_cls.oid
        FROM pg_inherits AS inh
        JOIN pg_class AS parent ON parent.oid = inh.inhparent
        JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
        JOIN pg_class AS child_cls ON child_cls.oid = inh.inhrelid
        JOIN pg_namespace AS child_ns ON child_ns.oid = child_cls.relnamespace
        WHERE parent_ns.nspname = 'public'
          AND parent.relname = 'scan_events'
          AND child_ns.nspname = 'public'
    LOOP
        SELECT EXISTS (
            SELECT 1
            FROM pg_policy AS policy
            WHERE policy.polrelid = partition_row.oid
              AND COALESCE(pg_get_expr(policy.polqual, policy.polrelid), '') LIKE '%current_tenant_id%'
              AND COALESCE(pg_get_expr(policy.polqual, policy.polrelid), '') LIKE '%has_parameter_privilege%'
        ) INTO is_guarded;
        -- Child ACL is intentionally always empty. Runtime traffic is
        -- authorized on the partitioned parent; direct child access fails
        -- closed before and after every replay.
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM yimatong_app',
            partition_row.schema_name,
            partition_row.table_name
        );
        IF NOT partition_row.relrowsecurity OR NOT partition_row.relforcerowsecurity OR NOT is_guarded THEN
            RAISE WARNING 'scan_events partition %.% is not lifecycle-hardened; direct runtime access remains revoked',
                partition_row.schema_name, partition_row.table_name;
        END IF;
    END LOOP;
END
$$;

COMMIT;
