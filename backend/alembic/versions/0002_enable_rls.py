"""enable rls for all tenant-scoped tables

Revision ID: 0002_enable_rls
Revises: 0001_initial_schema
Create Date: 2026-05-28

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_enable_rls"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 所有包含 tenant_id 的业务表（排除 tenants 本身和关联表）
RLS_TABLES = [
    # Auth
    "organizations",
    "accounts",
    "roles",
    "permissions",
    # Product
    "brands",
    "products",
    "skus",
    "production_batches",
    # Code
    "code_batches",
    "code_items",
    # Page
    "page_templates",
    "page_versions",
    # Campaign
    "campaigns",
    "benefits",
    "benefit_claims",
    # Scan
    "scan_events",
    # Analytics
    "daily_scan_stats",
    # Risk
    "risk_alerts",
    "risk_rules",
    "campaign_risk_rules",
    "interception_records",
    # Channel
    "distributors",
    "regions",
    "stores",
    "diversion_clues",
    # Member
    "consumer_profiles",
    "point_transactions",
    "point_rules",
    # Connector
    "connectors",
    "coupon_pools",
    # Integration
    "sync_records",
    # Webhook
    "webhook_endpoints",
    "api_keys",
    "webhook_deliveries",
    # Regional
    "regional_orgs",
    "regional_org_members",
    "regional_product_auths",
    # i18n
    "translations",
    # Red Packet
    "redpacket_rules",
    "kyc_records",
    "redpacket_claims",
    "withdrawals",
    # GMV
    "external_orders",
    "gmv_attributions",
]


def upgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id::text = current_setting('app.tenant_id', true)) "
            f"WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"
        )


def downgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
