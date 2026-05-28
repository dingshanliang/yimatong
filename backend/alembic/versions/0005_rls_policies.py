"""enable Row Level Security on all tenant-scoped tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-28

Creates a current_tenant_id() helper function and RLS policies
on every table that has a tenant_id column. Policies enforce:
- Tenanted sessions: only see rows matching their tenant_id
- Superuser/platform sessions: bypass RLS (no SET LOCAL issued)
"""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# All tables with tenant_id (excludes: tenants, platform_audit_log, account_roles, role_permissions)
RLS_TABLES = [
    "organizations",
    "accounts",
    "roles",
    "permissions",
    "brands",
    "products",
    "skus",
    "production_batches",
    "code_batches",
    "code_items",
    "page_templates",
    "page_versions",
    "campaigns",
    "benefits",
    "benefit_claims",
    "scan_events",
    "daily_scan_stats",
    "risk_alerts",
    "risk_rules",
    "campaign_risk_rules",
    "interception_records",
    "consumer_profiles",
    "point_transactions",
    "point_rules",
    "distributors",
    "regions",
    "stores",
    "diversion_clues",
    "external_orders",
    "gmv_attributions",
    "redpacket_rules",
    "kyc_records",
    "redpacket_claims",
    "withdrawals",
    "connectors",
    "coupon_pools",
    "coupon_codes",
    "webhook_endpoints",
    "api_keys",
    "webhook_deliveries",
    "sync_records",
    "translations",
    "regional_orgs",
    "regional_org_members",
    "regional_product_auths",
    "regional_code_rules",
    "whitelabel_configs",
]


def upgrade() -> None:
    # 1. Create helper function to read session variable
    op.execute("""
    CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS UUID AS $$
        BEGIN
            RETURN current_setting('app.tenant_id', true)::uuid;
        EXCEPTION
            WHEN others THEN
                RETURN NULL;
        END;
    $$ LANGUAGE plpgsql STABLE;
    """)

    # 2. Enable RLS and create policies on each table
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (
                tenant_id = current_tenant_id()
                OR current_tenant_id() IS NULL
            )
            WITH CHECK (
                tenant_id = current_tenant_id()
                OR current_tenant_id() IS NULL
            )
        """)


def downgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS current_tenant_id()")
