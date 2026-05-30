"""strengthen RLS: require explicit bypass flag when tenant_id is NULL

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-30

Previously, any session without SET LOCAL app.tenant_id could see all rows.
Now requires BOTH conditions: NULL tenant_id AND app.bypass_rls = 'true'.
Platform admin and background workers must explicitly opt in.
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

RLS_TABLES = [
    "organizations", "accounts", "roles", "permissions",
    "brands", "products", "skus", "production_batches",
    "code_batches", "code_items",
    "page_templates", "page_versions",
    "campaigns", "benefits", "benefit_claims",
    "scan_events", "daily_scan_stats",
    "risk_alerts", "risk_rules", "campaign_risk_rules", "interception_records",
    "consumer_profiles", "point_transactions", "point_rules",
    "distributors", "regions", "stores", "diversion_clues",
    "external_orders", "gmv_attributions", "redpacket_rules",
    "kyc_records", "redpacket_claims", "withdrawals",
    "connectors", "coupon_pools", "coupon_codes",
    "webhook_endpoints", "api_keys", "webhook_deliveries", "sync_records",
    "translations", "regional_orgs", "regional_org_members",
    "regional_product_auths", "regional_code_rules", "whitelabel_configs",
]


def upgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = '{table}' AND column_name = 'tenant_id'
            ) THEN
                DROP POLICY IF EXISTS tenant_isolation ON {table};
                CREATE POLICY tenant_isolation ON {table}
                USING (
                    tenant_id = current_tenant_id()
                    OR (
                        current_tenant_id() IS NULL
                        AND current_setting('app.bypass_rls', true) = 'true'
                    )
                )
                WITH CHECK (
                    tenant_id = current_tenant_id()
                    OR (
                        current_tenant_id() IS NULL
                        AND current_setting('app.bypass_rls', true) = 'true'
                    )
                );
            END IF;
        END $$;
        """)


def downgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = '{table}' AND column_name = 'tenant_id'
            ) THEN
                DROP POLICY IF EXISTS tenant_isolation ON {table};
                CREATE POLICY tenant_isolation ON {table}
                USING (
                    tenant_id = current_tenant_id()
                    OR current_tenant_id() IS NULL
                )
                WITH CHECK (
                    tenant_id = current_tenant_id()
                    OR current_tenant_id() IS NULL
                );
            END IF;
        END $$;
        """)
