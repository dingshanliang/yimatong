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
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'yimatong_callback') THEN
        CREATE ROLE yimatong_callback
            LOGIN PASSWORD 'yimatong_callback' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    ELSE
        ALTER ROLE yimatong_callback
            WITH LOGIN PASSWORD 'yimatong_callback' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO yimatong_app', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO yimatong_callback', current_database());
END
$$;
GRANT USAGE ON SCHEMA public TO yimatong_app;
GRANT USAGE ON SCHEMA public TO yimatong_callback;

-- Replays start from a closed privilege set.  This script intentionally has
-- no ALL TABLES/ALL SEQUENCES grant and no runtime default table privilege:
-- future relations remain inaccessible until an audited migration explicitly
-- hardens and authorizes them.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM yimatong_app;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM yimatong_app;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM yimatong_callback;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM yimatong_callback;
ALTER DEFAULT PRIVILEGES FOR ROLE yimatong IN SCHEMA public
    REVOKE ALL ON TABLES FROM yimatong_app;
ALTER DEFAULT PRIVILEGES FOR ROLE yimatong IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM yimatong_app;
ALTER DEFAULT PRIVILEGES FOR ROLE yimatong IN SCHEMA public
    REVOKE ALL ON TABLES FROM yimatong_callback;
ALTER DEFAULT PRIVILEGES FOR ROLE yimatong IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM yimatong_callback;

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
    'ai_generations', 'anonymous_visitors',
    'brands',
    'channel_action_receipts', 'code_allocations', 'code_batch_generation_receipts', 'code_batches', 'code_items',
    'connectors', 'consumer_profiles', 'coupon_codes',
    'coupon_pools', 'daily_scan_stats', 'distributors',
    'gmv_attribution_confirmations', 'gmv_attributions', 'gmv_daily_stats', 'intent_events',
    'launch_release_actions', 'launch_releases', 'ops_tasks', 'organizations',
    'permissions', 'point_products',
    'point_redemptions', 'point_rules', 'point_transactions',
    'private_domain_configs', 'product_assets', 'production_batches',
    'products', 'regional_code_rules', 'regional_org_members', 'regional_orgs',
    'regional_product_auths', 'regional_templates', 'regions',
    'role_permissions', 'roles',
    'scan_events', 'skus', 'stores', 'sync_mappings', 'sync_records',
    'takeover_aliases', 'takeover_cutover_events', 'takeover_domain_checks',
    'takeover_import_errors', 'takeover_import_jobs', 'takeover_observations',
    'takeover_projects', 'takeover_route_versions', 'tenant_domains',
    'tenant_health_metrics', 'tenant_quota_usage', 'tenants', 'translations',
    'webhook_deliveries', 'webhook_domain_events',
    'wecom_contact_ways',
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
       ('interception_records');
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
VALUES ('agency_authorizations'),
       ('commerce_connection_events'),
       ('commerce_connections'),
       ('commerce_identity_handoffs'),
       ('commerce_integration_messages'),
       ('commerce_member_references'),
       ('commerce_order_facts'),
       ('commerce_order_line_facts'),
       ('commerce_product_mappings'),
       ('commerce_refund_facts'),
       ('commerce_repurchase_attributions'),
       ('commerce_service_credentials'),
       ('brand_membership_events'),
       ('brand_membership_profile_links'),
       ('brand_memberships'),
       ('member_coupons'),
       ('member_channel_grants'),
       ('member_notification_deliveries'),
       ('member_notification_preferences'),
       ('member_notifications'),
       ('repurchase_coupon_events'),
       ('repurchase_coupon_rule_versions'),
       ('repurchase_work_item_events'),
       ('repurchase_work_items'),
       ('privacy_rights_requests'),
       ('privacy_rights_events'),
       ('member_pii_access_events'),
       ('sensitive_member_export_events'),
       ('benefit_deliveries'),
       ('benefit_claims'),
       ('benefits'),
       ('campaign_claim_outbox'),
       ('campaign_delivery_callback_attempts'),
       ('campaigns'),
       ('diversion_action_receipts'),
       ('diversion_clues'),
       ('campaign_risk_rules'),
       ('interception_records'),
       ('risk_action_outbox'),
       ('risk_action_receipts'),
       ('risk_alerts'),
       ('risk_campaign_pauses'),
       ('risk_notifications'),
       ('risk_rules'),
       ('external_orders'),
       ('external_order_value_events'),
       ('external_order_value_receipts'),
       ('gmv_attribution_confirmations'),
       ('gmv_attributions'),
       ('wecom_callback_receipts'),
       ('wecom_external_contacts'),
       ('diversion_evidence'),
       ('diversion_investigation_history'),
       ('diversion_observations'),
       ('page_templates'),
       ('page_versions'),
       ('export_logs'),
       ('webhook_endpoints'),
       ('pilot_milestone_corrections'),
       ('pilot_milestones'),
       ('retrospectives');
INSERT INTO runtime_restricted_mutation_relation_allowlist (table_name)
SELECT 'member_identity_credentials'::name
WHERE to_regclass('public.member_identity_credentials') IS NOT NULL;
INSERT INTO runtime_restricted_mutation_relation_allowlist (table_name)
SELECT 'consent_records'::name WHERE to_regclass('public.consent_records') IS NOT NULL;
INSERT INTO runtime_restricted_mutation_relation_allowlist (table_name)
SELECT relation_name::name FROM unnest(ARRAY[
    'consumer_consent_policies', 'consumer_consent_policy_current', 'consumer_consent_actions'
]::text[]) AS relation_name
WHERE to_regclass('public.'||relation_name) IS NOT NULL;
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
    IF to_regprocedure('public.mutate_commerce_connection_authority(uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.mutate_commerce_connection_authority(uuid,jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.mutate_commerce_connection_authority(uuid,jsonb) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.ensure_commerce_member_reference_authority(uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.ensure_commerce_member_reference_authority(uuid,jsonb) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.ensure_commerce_member_reference_authority(uuid,jsonb) FROM yimatong_app;
        REVOKE ALL ON FUNCTION public.ensure_commerce_member_reference_authority(uuid,jsonb) FROM yimatong_callback;
    END IF;
    IF to_regprocedure('public.mutate_commerce_handoff_authority(uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.mutate_commerce_handoff_authority(uuid,jsonb) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.mutate_commerce_handoff_authority(uuid,jsonb) FROM yimatong_app;
        GRANT EXECUTE ON FUNCTION public.mutate_commerce_handoff_authority(uuid,jsonb) TO yimatong_callback;
    END IF;
    IF to_regprocedure('public.accept_commerce_message_authority(uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.accept_commerce_message_authority(uuid,jsonb) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.accept_commerce_message_authority(uuid,jsonb) FROM yimatong_app;
        GRANT EXECUTE ON FUNCTION public.accept_commerce_message_authority(uuid,jsonb) TO yimatong_callback;
    END IF;
    IF to_regprocedure('public.create_commerce_product_mapping_authority(uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.create_commerce_product_mapping_authority(uuid,jsonb) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.create_commerce_product_mapping_authority(uuid,jsonb) FROM yimatong_callback;
        GRANT EXECUTE ON FUNCTION public.create_commerce_product_mapping_authority(uuid,jsonb) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.project_commerce_order_event_authority(uuid,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.project_commerce_order_event_authority(uuid,uuid) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.project_commerce_order_event_authority(uuid,uuid) FROM yimatong_app;
        GRANT EXECUTE ON FUNCTION public.project_commerce_order_event_authority(uuid,uuid) TO yimatong_callback;
    END IF;
    IF to_regprocedure('public.mutate_member_notification_preference_authority(uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.mutate_member_notification_preference_authority(uuid,jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.mutate_member_notification_preference_authority(uuid,jsonb) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.record_commerce_member_notification_authority(uuid,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.record_commerce_member_notification_authority(uuid,uuid) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.record_commerce_member_notification_authority(uuid,uuid) FROM yimatong_app;
        GRANT EXECUTE ON FUNCTION public.record_commerce_member_notification_authority(uuid,uuid) TO yimatong_callback;
    END IF;
    IF to_regprocedure('public.record_coupon_member_notification_authority(uuid,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.record_coupon_member_notification_authority(uuid,uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.record_coupon_member_notification_authority(uuid,uuid) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.mutate_member_notification_delivery_authority(uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.mutate_member_notification_delivery_authority(uuid,jsonb) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.mutate_member_notification_delivery_authority(uuid,jsonb) FROM yimatong_app;
        GRANT EXECUTE ON FUNCTION public.mutate_member_notification_delivery_authority(uuid,jsonb) TO yimatong_callback;
    END IF;
    IF to_regprocedure('public.lease_member_notification_delivery_authority(uuid,uuid,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.lease_member_notification_delivery_authority(uuid,uuid,uuid) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.lease_member_notification_delivery_authority(uuid,uuid,uuid) FROM yimatong_callback;
        GRANT EXECUTE ON FUNCTION public.lease_member_notification_delivery_authority(uuid,uuid,uuid) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.create_member_marketing_notification_authority(uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.create_member_marketing_notification_authority(uuid,jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.create_member_marketing_notification_authority(uuid,jsonb) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.mutate_repurchase_coupon_rule_authority(uuid,text,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.mutate_repurchase_coupon_rule_authority(uuid,text,jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.mutate_repurchase_coupon_rule_authority(uuid,text,jsonb) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.mutate_member_coupon_authority(uuid,text,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.mutate_member_coupon_authority(uuid,text,jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.mutate_member_coupon_authority(uuid,text,jsonb) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.mutate_repurchase_work_item_authority(uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.mutate_repurchase_work_item_authority(uuid,jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.mutate_repurchase_work_item_authority(uuid,jsonb) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.create_consumer_privacy_request_authority(uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.create_consumer_privacy_request_authority(uuid,jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.create_consumer_privacy_request_authority(uuid,jsonb) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.mutate_privacy_rights_authority(uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.mutate_privacy_rights_authority(uuid,jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.mutate_privacy_rights_authority(uuid,jsonb) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.record_member_pii_access_authority(uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.record_member_pii_access_authority(uuid,jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.record_member_pii_access_authority(uuid,jsonb) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.mutate_sensitive_member_export_authority(uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.mutate_sensitive_member_export_authority(uuid,jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.mutate_sensitive_member_export_authority(uuid,jsonb) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.purge_expired_sensitive_exports_authority(uuid,integer)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.purge_expired_sensitive_exports_authority(uuid,integer) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.purge_expired_sensitive_exports_authority(uuid,integer) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.reapply_completed_privacy_controls_authority(uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.reapply_completed_privacy_controls_authority(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.reapply_completed_privacy_controls_authority(uuid) TO yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.create_brand_membership_authority(uuid,uuid,uuid,timestamp with time zone,text,text,uuid,boolean,uuid,text,uuid,uuid,text,text)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.create_brand_membership_authority(
            uuid,uuid,uuid,timestamptz,text,text,uuid,boolean,uuid,text,uuid,uuid,text,text
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.create_brand_membership_authority(
            uuid,uuid,uuid,timestamptz,text,text,uuid,boolean,uuid,text,uuid,uuid,text,text
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.bind_brand_member_identity_authority(uuid,uuid,uuid,text,text,text,bytea,bytea,text,text,uuid,text,text)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.bind_brand_member_identity_authority(
            uuid,uuid,uuid,text,text,text,bytea,bytea,text,text,uuid,text,text
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.bind_brand_member_identity_authority(
            uuid,uuid,uuid,text,text,text,bytea,bytea,text,text,uuid,text,text
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.recover_brand_membership_authority(uuid,uuid,uuid,uuid,uuid,uuid,text,text)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.recover_brand_membership_authority(
            uuid,uuid,uuid,uuid,uuid,uuid,text,text
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.recover_brand_membership_authority(
            uuid,uuid,uuid,uuid,uuid,uuid,text,text
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.merge_brand_memberships_authority(uuid,uuid,uuid,uuid,uuid,uuid,text,text,uuid,uuid,text,jsonb)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.merge_brand_memberships_authority(
            uuid,uuid,uuid,uuid,uuid,uuid,text,text,uuid,uuid,text,jsonb
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.merge_brand_memberships_authority(
            uuid,uuid,uuid,uuid,uuid,uuid,text,text,uuid,uuid,text,jsonb
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.get_current_consumer_consent_policy(uuid,text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.get_current_consumer_consent_policy(uuid,text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.get_current_consumer_consent_policy(uuid,text) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.get_consumer_consent_receipt_status(uuid,uuid,uuid,timestamp with time zone,text,text,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.get_consumer_consent_receipt_status(uuid,uuid,uuid,timestamptz,text,text,uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.get_consumer_consent_receipt_status(uuid,uuid,uuid,timestamptz,text,text,uuid) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.grant_consumer_consent(uuid,uuid,uuid,text,text,text,uuid,timestamp with time zone,text,text,uuid,text,text,text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.grant_consumer_consent(uuid,uuid,uuid,text,text,text,uuid,timestamptz,text,text,uuid,text,text,text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.grant_consumer_consent(uuid,uuid,uuid,text,text,text,uuid,timestamptz,text,text,uuid,text,text,text) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.capture_consumer_lead(uuid,uuid,uuid,uuid,uuid,timestamp with time zone,text,text,uuid,text,text,text,jsonb,text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.capture_consumer_lead(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,text,text,jsonb,text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.capture_consumer_lead(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,text,text,jsonb,text) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.capture_consumer_lead(uuid,uuid,uuid,uuid,uuid,timestamp with time zone,text,text,uuid,text,bytea,bytea,text,text,jsonb,text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.capture_consumer_lead(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,bytea,bytea,text,text,jsonb,text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.capture_consumer_lead(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,bytea,bytea,text,text,jsonb,text) TO yimatong_app;
        IF to_regprocedure('public.capture_consumer_lead(uuid,uuid,uuid,uuid,uuid,timestamp with time zone,text,text,uuid,text,text,text,jsonb,text)') IS NOT NULL THEN
            REVOKE EXECUTE ON FUNCTION public.capture_consumer_lead(uuid,uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text,text,text,jsonb,text) FROM yimatong_app;
        END IF;
    END IF;
    IF to_regprocedure('public.withdraw_consumer_consent(uuid,uuid,uuid,uuid,timestamp with time zone,text,text,uuid,text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.withdraw_consumer_consent(uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.withdraw_consumer_consent(uuid,uuid,uuid,uuid,timestamptz,text,text,uuid,text) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.validate_consumer_consent_subject(uuid,uuid,timestamp with time zone,text,text,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.validate_consumer_consent_subject(uuid,uuid,timestamptz,text,text,uuid) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.validate_consumer_consent_subject(uuid,uuid,timestamptz,text,text,uuid) FROM yimatong_app;
    END IF;
    IF to_regprocedure('public.seed_consumer_consent_policies_for_tenant()') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.seed_consumer_consent_policies_for_tenant() FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.seed_consumer_consent_policies_for_tenant() FROM yimatong_app;
    END IF;
    IF to_regprocedure('public.revoke_current_tenant_account_sessions(uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.revoke_current_tenant_account_sessions(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.revoke_current_tenant_account_sessions(uuid) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.mutate_page_template(uuid,uuid,uuid,text,uuid,uuid,text,text,text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.mutate_page_template(
            uuid, uuid, uuid, text, uuid, uuid, text, text, text
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.mutate_page_template(
            uuid, uuid, uuid, text, uuid, uuid, text, text, text
        ) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.create_page_version(uuid,uuid,uuid,uuid,uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.create_page_version(uuid, uuid, uuid, uuid, uuid, jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.create_page_version(uuid, uuid, uuid, uuid, uuid, jsonb)
            TO yimatong_app;
    END IF;
    IF to_regprocedure('public.update_page_version(uuid,uuid,uuid,uuid,jsonb)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.update_page_version(uuid, uuid, uuid, uuid, jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.update_page_version(uuid, uuid, uuid, uuid, jsonb) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.publish_page_version(uuid,uuid,uuid,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.publish_page_version(uuid, uuid, uuid, uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.publish_page_version(uuid, uuid, uuid, uuid) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.archive_page_version(uuid,uuid,uuid,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.archive_page_version(uuid, uuid, uuid, uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.archive_page_version(uuid, uuid, uuid, uuid) TO yimatong_app;
    END IF;
    IF to_regprocedure('public.rollback_page_version(uuid,uuid,uuid,uuid,uuid,uuid)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.rollback_page_version(uuid, uuid, uuid, uuid, uuid, uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.rollback_page_version(uuid, uuid, uuid, uuid, uuid, uuid)
            TO yimatong_app;
    END IF;
    IF to_regprocedure('public.authorize_page_actor(uuid,uuid,text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.authorize_page_actor(uuid, uuid, text) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.authorize_page_actor(uuid, uuid, text) FROM yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.mutate_page_version_authority(uuid,uuid,uuid,text,uuid,uuid,uuid,jsonb)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.mutate_page_version_authority(
            uuid, uuid, uuid, text, uuid, uuid, uuid, jsonb
        ) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.mutate_page_version_authority(
            uuid, uuid, uuid, text, uuid, uuid, uuid, jsonb
        ) FROM yimatong_app;
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
        REVOKE EXECUTE ON FUNCTION public.mark_code_item_first_scanned(uuid, text) FROM yimatong_app;
    END IF;
    IF to_regprocedure('public.record_public_code_scan(uuid,text,uuid,text,text,text,text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.record_public_code_scan(
            uuid, text, uuid, text, text, text, text
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.record_public_code_scan(
            uuid, text, uuid, text, text, text, text
        ) TO yimatong_app;
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
    'role_template_backups', 'sensitive_member_exports', 'tenant_invite_codes',
    'tenant_platform_role_assignment_backups'
]::name[]);
INSERT INTO runtime_control_relation_allowlist (table_name)
SELECT 'consumer_phone_encryption_keys'::name
WHERE to_regclass('public.consumer_phone_encryption_keys') IS NOT NULL;
INSERT INTO runtime_control_relation_allowlist (table_name)
SELECT 'takeover_domain_claims'::name
WHERE to_regclass('public.takeover_domain_claims') IS NOT NULL;
INSERT INTO runtime_control_relation_allowlist (table_name)
SELECT 'pilot_authority_receipts'::name
WHERE to_regclass('public.pilot_authority_receipts') IS NOT NULL;

DO $$
DECLARE signature text;
BEGIN
    FOREACH signature IN ARRAY ARRAY[
        'public.materialize_pilot_milestones(uuid,uuid,jsonb)',
        'public.update_pending_retrospective_authority(uuid,uuid,uuid,uuid,bigint,text,text,jsonb,date,text,text)',
        'public.complete_retrospective_authority(uuid,uuid,uuid,uuid,bigint,text,text,jsonb,date,text,text,text)',
        'public.append_retrospective_note_authority(uuid,uuid,uuid,uuid,bigint,text,text,text)'
    ]
    LOOP
        IF to_regprocedure(signature) IS NOT NULL THEN
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', signature);
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO yimatong_app', signature);
        END IF;
    END LOOP;
    FOREACH signature IN ARRAY ARRAY[
        'public.assert_pilot_tenant_actor(uuid,uuid,text,text,uuid)',
        'public.append_pilot_milestone_correction(uuid,uuid,uuid,text,timestamp with time zone,text,text,text,text)',
        'public.materialize_due_retrospective(uuid,uuid,uuid,integer,timestamp with time zone,timestamp with time zone,date,jsonb,text,uuid)'
    ]
    LOOP
        IF to_regprocedure(signature) IS NOT NULL THEN
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', signature);
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM yimatong_app', signature);
        END IF;
    END LOOP;
END
$$;

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
       ('channel_permission_backfill'),
       ('connector_secret_migration_backups'),
       ('consumer_detail_role_grant_backfills'),
       ('legacy_pii_recovery_markers'),
       ('external_order_ledger_recovery_markers'),
       ('external_order_permission_backfill'),
       ('webhook_permission_backfill'),
       ('wecom_member_recovery_markers'),
       ('rls_force_remediation_backups'),
       ('risk_permission_backfill'),
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
    IF registry_count <> 145
        + (CASE WHEN to_regclass('public.takeover_domain_claims') IS NULL THEN 0 ELSE 1 END)
        + (CASE WHEN to_regclass('public.consumer_phone_encryption_keys') IS NULL THEN 0 ELSE 1 END)
        + (CASE WHEN to_regclass('public.campaign_delivery_callback_attempts') IS NULL THEN 0 ELSE 1 END)
        + (CASE WHEN to_regclass('public.wecom_callback_receipts') IS NULL THEN 0 ELSE 1 END) THEN
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

-- Public scan history is append-only and can only be created together with
-- first-scan authority through record_public_code_scan().
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON TABLE public.scan_events FROM yimatong_app;

-- Confirmed GMV attribution and its evidence are database-authority owned.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON TABLE public.gmv_attributions,
             public.gmv_attribution_confirmations
    FROM yimatong_app;
GRANT SELECT ON TABLE public.gmv_attributions,
                      public.gmv_attribution_confirmations
    TO yimatong_app;

-- Diversion observations, evidence, history, lifecycle and receipts are
-- immutable and actor-bound. Runtime reads them through tenant RLS and writes
-- only through the reviewed authority functions below.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON TABLE public.diversion_clues,
             public.diversion_observations,
             public.diversion_evidence,
             public.diversion_investigation_history,
             public.diversion_action_receipts
    FROM yimatong_app;

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
    id, tenant_id, account_id, auth_session_id, export_type, resource_id,
    file_name, content_type, row_count, status, reason, scope_snapshot,
    idempotency_key, payload_digest, authority_version, code_batch_id,
    manifest_version, checksum_sha256, artifact_size_bytes, created_at, updated_at
) ON TABLE public.export_logs TO yimatong_app;
GRANT EXECUTE ON FUNCTION public.get_code_export_artifact(uuid, uuid, uuid) TO yimatong_app;
GRANT EXECUTE ON FUNCTION public.record_prepared_export(
    uuid, uuid, uuid, text, text, jsonb, text, text, text, integer, text,
    bigint, uuid, uuid, integer, bytea, bytea, text, text
) TO yimatong_app;

-- Sensitive member exports expose only workflow metadata. Encrypted artifacts,
-- nonces, key ids, and one-time token digests remain function-only.
GRANT SELECT (
    id, tenant_id, requester_account_id, approver_account_id, status, reason,
    recipient_purpose, requested_fields, filters, includes_full_pii, row_count,
    artifact_size_bytes, checksum_sha256, expires_at, downloaded_at, deleted_at,
    created_at, updated_at
) ON TABLE public.sensitive_member_exports TO yimatong_app;
GRANT SELECT ON TABLE public.sensitive_member_export_summaries TO yimatong_app;

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
        'consumer_detail_role_grant_backfills',
        'external_order_ledger_recovery_markers',
        'external_order_permission_backfill',
        'rls_force_remediation_backups',
        'risk_permission_backfill',
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

-- Campaign mutation and claim delivery are function-only authorities. Keep a
-- replay from reopening direct DML after the final U06A cutover.
DO $$
DECLARE
    signature text;
    relation_name text;
BEGIN
    IF to_regclass('public.campaigns') IS NOT NULL THEN
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.campaigns FROM yimatong_app;
        GRANT SELECT ON public.campaigns TO yimatong_app;
    END IF;
    IF to_regclass('public.benefits') IS NOT NULL THEN
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.benefits FROM yimatong_app;
        GRANT SELECT ON public.benefits TO yimatong_app;
    END IF;
    IF to_regclass('public.benefit_claims') IS NOT NULL THEN
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.benefit_claims FROM yimatong_app;
        GRANT SELECT ON public.benefit_claims TO yimatong_app;
    END IF;
    IF to_regclass('public.benefit_deliveries') IS NOT NULL THEN
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.benefit_deliveries FROM yimatong_app;
        GRANT SELECT ON public.benefit_deliveries TO yimatong_app;
    END IF;
    IF to_regclass('public.campaign_claim_outbox') IS NOT NULL THEN
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.campaign_claim_outbox FROM yimatong_app;
        GRANT SELECT ON public.campaign_claim_outbox TO yimatong_app;
    END IF;
    IF to_regclass('public.launch_releases') IS NOT NULL THEN
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
            ON public.launch_releases FROM yimatong_app;
        GRANT SELECT ON public.launch_releases TO yimatong_app;
    END IF;
    IF to_regclass('public.launch_release_actions') IS NOT NULL THEN
        REVOKE ALL ON public.launch_release_actions FROM yimatong_app;
        GRANT SELECT ON public.launch_release_actions TO yimatong_app;
    END IF;
    FOREACH relation_name IN ARRAY ARRAY[
        'distributors', 'regions', 'stores', 'code_allocations',
        'account_channel_scopes', 'channel_action_receipts',
        'risk_rules', 'campaign_risk_rules', 'interception_records', 'risk_alerts',
        'risk_notifications', 'risk_action_receipts', 'risk_campaign_pauses', 'risk_action_outbox'
    ] LOOP
        IF to_regclass('public.' || relation_name) IS NOT NULL THEN
            EXECUTE format(
                'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.%I FROM yimatong_app',
                relation_name
            );
            EXECUTE format('GRANT SELECT ON public.%I TO yimatong_app', relation_name);
        END IF;
    END LOOP;
    IF to_regclass('public.production_batches') IS NOT NULL
       AND to_regprocedure('public.recall_production_batch(uuid,uuid,uuid,uuid,text)') IS NOT NULL THEN
        REVOKE UPDATE ON public.production_batches FROM yimatong_app;
        GRANT UPDATE(product_id,sku_id,batch_code,production_date,expiry_date,origin,
            updated_at,external_id,source_system) ON public.production_batches TO yimatong_app;
    END IF;
    IF to_regprocedure('public.allocate_coupon_code(uuid,uuid,text,uuid)') IS NOT NULL THEN
        REVOKE UPDATE, DELETE ON public.coupon_codes FROM yimatong_app;
        REVOKE UPDATE ON public.coupon_pools FROM yimatong_app;
        GRANT UPDATE(name,updated_at) ON public.coupon_pools TO yimatong_app;
    END IF;
    IF to_regclass('public.consumer_profiles') IS NOT NULL
       AND to_regprocedure('public.create_anonymous_consumer_profile(uuid,uuid,uuid,uuid)') IS NOT NULL THEN
        REVOKE INSERT ON TABLE public.consumer_profiles FROM yimatong_app;
    END IF;
    IF to_regprocedure('public.claim_campaign_benefit(uuid,uuid,uuid,uuid,uuid,text,text,text)') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.claim_campaign_benefit(uuid,uuid,uuid,uuid,uuid,text,text,text)
            FROM PUBLIC, yimatong_app;
    END IF;
    IF to_regprocedure(
        'public.record_external_order_value_event(uuid,text,text,text,numeric,text,text,text,text,uuid,timestamp with time zone,timestamp with time zone,text,text,text,text)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.record_external_order_value_event(
            uuid,text,text,text,numeric,text,text,text,text,uuid,timestamp with time zone,
            timestamp with time zone,text,text,text,text
        ) FROM PUBLIC;
    END IF;
    IF to_regprocedure(
        'public.confirm_gmv_attribution(uuid,uuid,uuid,uuid,uuid,uuid,uuid,timestamp with time zone,integer,text,text)'
    ) IS NOT NULL THEN
        REVOKE ALL ON FUNCTION public.confirm_gmv_attribution(
            uuid,uuid,uuid,uuid,uuid,uuid,uuid,timestamp with time zone,integer,text,text
        ) FROM PUBLIC;
    END IF;
    FOREACH signature IN ARRAY ARRAY[
        'recall_production_batch(uuid,uuid,uuid,uuid,text)',
        'create_production_batch(uuid,uuid,uuid,uuid,uuid,uuid,text,date,date,text)',
        'update_production_batch(uuid,uuid,uuid,uuid,text,date,date,text,boolean)',
        'delete_production_batch(uuid,uuid,uuid,uuid)',
        'allocate_coupon_code(uuid,uuid,text,uuid)',
        'create_anonymous_consumer_profile(uuid,uuid,uuid,uuid)',
        'create_launch_release(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text)',
        'confirm_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)',
        'launch_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)',
        'suspend_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)',
        'resume_launch_release(uuid,uuid,uuid,uuid,uuid,text,text)',
        'invalidate_launch_release(uuid,uuid,uuid,uuid,uuid,text,text,text)',
        'resolve_current_launch_release(uuid,text)',
        'record_launch_release_valid_scan(uuid,uuid,uuid,timestamp with time zone)',
        'create_campaign(uuid,uuid,uuid,uuid,uuid,text,text,timestamptz,timestamptz,jsonb,text)',
        'update_campaign(uuid,uuid,uuid,uuid,boolean,uuid,text,text,timestamptz,timestamptz,jsonb,text)',
        'transition_campaign(uuid,uuid,uuid,uuid,text)',
        'create_benefit(uuid,uuid,uuid,uuid,uuid,text,text,jsonb,integer,integer,uuid)',
        'update_benefit(uuid,uuid,uuid,uuid,text,text,jsonb,integer,integer,boolean,uuid,text)',
        'attach_benefit(uuid,uuid,uuid,uuid,uuid)',
        'detach_benefit(uuid,uuid,uuid,uuid,uuid)',
        'delete_campaign(uuid,uuid,uuid,uuid)',
        'delete_benefit(uuid,uuid,uuid,uuid)',
        'claim_campaign_benefit(uuid,uuid,uuid,uuid,uuid,text,text,text,uuid,uuid,text)',
        'lease_campaign_claim_outbox(uuid,text,integer,integer)',
        'record_campaign_claim_delivery_result(uuid,uuid,uuid,uuid,uuid,text,text,jsonb,integer)',
        'complete_campaign_claim_outbox(uuid,uuid,uuid)',
        'fail_campaign_claim_outbox(uuid,uuid,uuid,text,integer)',
        'redeem_campaign_benefit_claim(uuid,uuid,uuid,uuid)',
        'create_channel_distributor(uuid,uuid,uuid,uuid,text,text,text,text,text,text,text)',
        'update_channel_distributor(uuid,uuid,uuid,uuid,bigint,text,text,text,text,text,text)',
        'create_channel_region(uuid,uuid,uuid,uuid,text,text,text,text,text,text,jsonb,uuid,text)',
        'update_channel_region(uuid,uuid,uuid,uuid,bigint,text,text,text,text,text,jsonb,uuid,text)',
        'create_channel_store(uuid,uuid,uuid,uuid,text,text,text,uuid,uuid,text,text)',
        'update_channel_store(uuid,uuid,uuid,uuid,bigint,text,text,uuid,uuid,text,text)',
        'archive_channel_store(uuid,uuid,uuid,uuid,bigint,text)',
        'assign_code_batch_channel(uuid,uuid,uuid,uuid,text,uuid,uuid)',
        'allocate_code_batch_channel(uuid,uuid,uuid,uuid,text,uuid,text,uuid,bigint,text)',
        'reassign_code_batch_channel(uuid,uuid,uuid,uuid,text,uuid,bigint,text,uuid,bigint,text)',
        'archive_code_batch_allocation(uuid,uuid,uuid,uuid,text,uuid,bigint,text)',
        'set_account_channel_scope(uuid,uuid,uuid,uuid,text,uuid,text,uuid)',
        'delete_account_channel_scope(uuid,uuid,uuid,uuid,bigint,text)',
        'get_my_channel_scope(uuid,uuid,text)'
        ,'record_diversion_observation(uuid,uuid,uuid,timestamptz,text,text,uuid,text,text,text,text,text,boolean,uuid,uuid,text,text)'
        ,'add_diversion_evidence(uuid,uuid,uuid,uuid,uuid,bigint,text,text,text,text,text)'
        ,'transition_diversion_clue(uuid,uuid,uuid,uuid,bigint,text,text,text,text)'
        ,'mutate_risk_rule(uuid,uuid,uuid,text,uuid,bigint,text,text,text,text,jsonb,boolean)'
        ,'set_campaign_risk_rule(uuid,uuid,uuid,uuid,uuid,boolean,text)'
        ,'resume_risk_campaign_pause(uuid,uuid,uuid,uuid,bigint,text,text)'
        ,'evaluate_execute_scan_risk(uuid,uuid,uuid,uuid,text,jsonb)'
        ,'freeze_code_item_with_risk_alert(uuid,uuid,uuid,uuid,uuid,uuid,text,text)'
        ,'mark_risk_notification_read(uuid,uuid,uuid,uuid,text)'
        ,'mark_all_risk_notifications_read(uuid,uuid,uuid,text)'
        ,'record_external_order_value_event(uuid,text,text,text,numeric,text,text,text,text,uuid,timestamp with time zone,timestamp with time zone,text,text,text,text)'
        ,'confirm_gmv_attribution(uuid,uuid,uuid,uuid,uuid,uuid,uuid,timestamp with time zone,integer,text,text)'
    ] LOOP
        IF to_regprocedure('public.' || signature) IS NOT NULL THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION public.%s TO yimatong_app', signature);
        END IF;
    END LOOP;
END
$$;

DO $$
DECLARE callback_signature text;
BEGIN
    FOREACH callback_signature IN ARRAY ARRAY[
        'settle_campaign_claim_callback(uuid,uuid,uuid,uuid,uuid,text,text,jsonb)',
        'bind_wechat_oauth_consumer(uuid,uuid,uuid,timestamp with time zone,text,text,uuid,uuid,text,bytea,bytea,text,uuid,uuid)',
        'apply_verified_wecom_contact_event(uuid,uuid,uuid,text,text,text,text,text,timestamp with time zone,bigint,text)'
    ] LOOP
        IF to_regprocedure('public.' || callback_signature) IS NOT NULL THEN
            EXECUTE format('REVOKE ALL ON FUNCTION public.%s FROM PUBLIC', callback_signature);
            EXECUTE format('REVOKE ALL ON FUNCTION public.%s FROM yimatong_app', callback_signature);
            EXECUTE format('GRANT EXECUTE ON FUNCTION public.%s TO yimatong_callback', callback_signature);
        END IF;
    END LOOP;
END
$$;

-- Commerce credential ciphertext and handoff token digests are callback/control
-- secrets. Runtime receives only the non-secret columns needed for status and
-- reconciliation; SECURITY DEFINER functions retain full-row authority.
REVOKE SELECT ON public.commerce_service_credentials FROM yimatong_app;
GRANT SELECT(id,tenant_id,connection_id,direction,version,key_prefix,valid_from,valid_until,
    overlap_until,revoked_at,created_at) ON public.commerce_service_credentials TO yimatong_app;
REVOKE SELECT ON public.commerce_identity_handoffs FROM yimatong_app;
GRANT SELECT(id,tenant_id,connection_id,member_reference_id,expires_at,redeemed_at,created_at)
    ON public.commerce_identity_handoffs TO yimatong_app;

COMMIT;
