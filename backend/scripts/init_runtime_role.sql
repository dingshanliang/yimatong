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
    'account_channel_scopes', 'account_roles', 'accounts', 'agency_authorizations',
    'ai_generations', 'anonymous_visitors', 'api_keys', 'benefit_claims',
    'benefit_deliveries', 'benefits', 'brands', 'campaign_risk_rules',
    'campaigns', 'code_allocations', 'code_batches', 'code_items',
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
    'tenant_health_metrics', 'tenants', 'translations', 'retrospectives', 'webhook_deliveries',
    'webhook_endpoints', 'wecom_contact_ways', 'wecom_external_contacts',
    'whitelabel_configs'
]::name[]);

CREATE TEMP TABLE runtime_control_relation_allowlist (
    table_name name PRIMARY KEY
) ON COMMIT DROP;
INSERT INTO runtime_control_relation_allowlist (table_name)
SELECT unnest(ARRAY[
    'auth_sessions', 'consumed_refresh_tokens', 'invite_registration_receipts',
    'operator_campaign_manage_grants', 'organization_parent_repair_backups',
    'platform_audit_log', 'platform_configs', 'platform_tenant_openings',
    'role_template_backups', 'tenant_invite_codes',
    'tenant_platform_role_assignment_backups'
]::name[]);

CREATE TEMP TABLE runtime_public_relation_allowlist (
    table_name name PRIMARY KEY
) ON COMMIT DROP;
INSERT INTO runtime_public_relation_allowlist (table_name) VALUES ('plan_definitions');

CREATE TEMP TABLE runtime_migration_relation_allowlist (
    table_name name PRIMARY KEY
) ON COMMIT DROP;
INSERT INTO runtime_migration_relation_allowlist (table_name)
VALUES ('alembic_version'),
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
        UNION ALL SELECT table_name FROM runtime_control_relation_allowlist
        UNION ALL SELECT table_name FROM runtime_public_relation_allowlist
    ) AS orm_registry;
    IF registry_count <> 94 THEN
        RAISE EXCEPTION 'Runtime ORM registry must classify exactly 94 relations, got %', registry_count;
    END IF;

    SELECT string_agg(table_name::text, ', ' ORDER BY table_name) INTO missing
    FROM (
        SELECT table_name FROM runtime_business_relation_allowlist
        UNION ALL SELECT table_name FROM runtime_control_relation_allowlist
        UNION ALL SELECT table_name FROM runtime_public_relation_allowlist
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
              UNION ALL SELECT table_name FROM runtime_control_relation_allowlist
              UNION ALL SELECT table_name FROM runtime_public_relation_allowlist
              UNION ALL SELECT table_name FROM runtime_migration_relation_allowlist
          ) AS registry
          WHERE registry.table_name = cls.relname
      );
    IF unclassified IS NOT NULL THEN
        RAISE EXCEPTION 'Unclassified public relations: %', unclassified;
    END IF;
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

-- Deliberately public catalog data used by tenant requests is read-only.
GRANT SELECT ON TABLE public.plan_definitions TO yimatong_app;

-- Cross-tenant control state and migration evidence never belong to the
-- ordinary application role. Keep new tables deny-by-default; migrations must
-- explicitly grant runtime DML for tenant business tables.
REVOKE SET ON PARAMETER "app.bypass_rls" FROM PUBLIC;
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
        'platform_audit_log',
        'platform_configs',
        'tenant_invite_codes',
        'operator_campaign_manage_grants',
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
