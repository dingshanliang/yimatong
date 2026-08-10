import asyncio
import re
from logging.config import fileConfig
from typing import Any

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from alembic.operations import ops
from app.core.config import settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.migration_database_url or settings.database_url)

from app.models.base import Base  # noqa: E402
from app.models.analytics import DailyScanStats  # noqa: F401
from app.models.audit import PlatformAuditLog  # noqa: F401
from app.models.auth_security import AuthSession, ConsumedRefreshToken, PlatformAuthSession  # noqa: F401
from app.models.campaign import Benefit, BenefitClaim, Campaign  # noqa: F401
from app.models.channel import AccountChannelScope, Distributor, Region, Store, DiversionClue  # noqa: F401
from app.models.code import CodeBatch, CodeItem  # noqa: F401
from app.models.consent import ConsentRecord  # noqa: F401
from app.models.connector import Connector, CouponPool, CouponCode, BenefitDelivery  # noqa: F401
from app.models.diversion_evidence import DiversionEvidence  # noqa: F401
from app.models.diversion_history import DiversionInvestigationHistory  # noqa: F401
from app.models.ai_generation import AiGeneration  # noqa: F401
from app.models.export_log import ExportLog  # noqa: F401
from app.models.gmv import ExternalOrder, GmvAttribution  # noqa: F401
from app.models.intent_event import IntentEvent  # noqa: F401
from app.models.private_domain import PrivateDomainConfig  # noqa: F401
from app.models.i18n import Translation  # noqa: F401
from app.models.integration import SyncRecord  # noqa: F401
from app.models.invite_code import TenantInviteCode  # noqa: F401
from app.models.invite_registration import InviteRegistrationReceipt  # noqa: F401
from app.models.launch import LaunchRelease  # noqa: F401
from app.models.member import ConsumerProfile, PointProduct, PointRedemption, PointRule, PointTransaction  # noqa: F401
from app.models.page import PageTemplate, PageVersion  # noqa: F401
from app.models.plan import PlanDefinition  # noqa: F401
from app.models.pilot_milestone import PilotMilestone, PilotMilestoneCorrection  # noqa: F401
from app.models.platform_opening import PlatformTenantOpening  # noqa: F401
from app.models.role_template_backup import (  # noqa: F401
    OperatorCampaignManageGrant,
    OrganizationParentRepairBackup,
    RoleTemplateBackup,
    TenantPlatformRoleAssignmentBackup,
)
from app.models.product import Brand, Product, ProductionBatch, SKU  # noqa: F401
from app.models.retrospective import Retrospective  # noqa: F401
from app.models.regional import (  # noqa: F401
    RegionalCodeRule,
    RegionalOrg,
    RegionalOrgMember,
    RegionalProductAuth,
    RegionalTemplate,
    WhitelabelConfig,
)
from app.models.risk import CampaignRiskRule, InterceptionRecord, RiskAlert, RiskRule  # noqa: F401
from app.models.scan import ScanEvent  # noqa: F401
from app.models.sync_mapping import SyncMapping  # noqa: F401
from app.models.takeover import (  # noqa: F401
    TakeoverAlias,
    TakeoverCutoverEvent,
    TakeoverDomainCheck,
    TakeoverImportError,
    TakeoverImportJob,
    TakeoverObservation,
    TakeoverProject,
    TakeoverRouteVersion,
)
from app.models.tenant import Account, Organization, Permission, Role, Tenant, account_roles, role_permissions  # noqa: F401
from app.models.tenant_health import TenantHealthMetrics  # noqa: F401
from app.models.platform_config import PlatformConfig  # noqa: F401
from app.models.visitor import AnonymousVisitor  # noqa: F401
from app.models.webhook import ApiKey, WebhookDelivery, WebhookEndpoint  # noqa: F401
from app.models.wecom import WeComContactWay, WeComExternalContact  # noqa: F401

target_metadata = Base.metadata

SCAN_EVENT_PARTITION_NAME = re.compile(r"scan_events_\d{4}_\d{2}")

# Frozen baseline for timestamp nullability drift inherited from old migrations.
# New tables/columns must be migrated normally and must not be added here casually.
_LEGACY_BOTH_TIMESTAMP_TABLES = {
    "accounts",
    "api_keys",
    "campaign_risk_rules",
    "campaigns",
    "code_allocations",
    "code_batches",
    "code_items",
    "consumer_profiles",
    "coupon_codes",
    "coupon_pools",
    "daily_scan_stats",
    "diversion_clues",
    "external_orders",
    "gmv_attributions",
    "gmv_daily_stats",
    "interception_records",
    "organizations",
    "permissions",
    "platform_audit_log",
    "point_rules",
    "product_assets",
    "production_batches",
    "products",
    "regional_code_rules",
    "regional_org_members",
    "regional_orgs",
    "regional_product_auths",
    "regional_templates",
    "risk_alerts",
    "risk_notifications",
    "risk_rules",
    "roles",
    "scan_events",
    "skus",
    "sync_mappings",
    "sync_records",
    "tenant_domains",
    "translations",
    "webhook_deliveries",
    "webhook_endpoints",
    "whitelabel_configs",
}
LEGACY_TIMESTAMP_NULLABILITY = {
    *((table_name, "created_at") for table_name in _LEGACY_BOTH_TIMESTAMP_TABLES),
    *((table_name, "updated_at") for table_name in _LEGACY_BOTH_TIMESTAMP_TABLES),
    ("point_redemptions", "updated_at"),
    ("point_transactions", "updated_at"),
    ("tenants", "updated_at"),
}

MIGRATION_ONLY_TABLES = {
    "agency_authorization_integrity_backups",
    "alembic_version",
    "api_key_catalog_audit_context_secrets",
    "api_key_legacy_secret_backups",
    "code_delivery_contract_rollout_state",
    "rls_force_remediation_backups",
    "runtime_privilege_remediation_backup",
}


def include_object(object, name: str | None, type_: str, reflected: bool, compare_to) -> bool:
    """Exclude PostgreSQL child partitions that are managed by migrations, not ORM models."""
    if type_ == "table" and reflected and name:
        if name in MIGRATION_ONLY_TABLES:
            return False
        if name == "scan_events_default" or SCAN_EVENT_PARTITION_NAME.fullmatch(name):
            return False
    return True


def _is_legacy_timestamp_nullable_change(operation: ops.MigrateOperation) -> bool:
    return (
        isinstance(operation, ops.AlterColumnOp)
        and (operation.table_name, operation.column_name) in LEGACY_TIMESTAMP_NULLABILITY
        and operation.existing_nullable is True
        and operation.modify_nullable is False
        and operation.modify_type is None
        and operation.modify_server_default is False
        and operation.modify_comment is False
        and operation.modify_name is None
    )


def _filter_legacy_timestamp_nullable_changes(container: ops.OpContainer) -> None:
    retained: list[ops.MigrateOperation] = []
    for operation in container.ops:
        if isinstance(operation, ops.OpContainer):
            _filter_legacy_timestamp_nullable_changes(operation)
            if not operation.ops:
                continue
        if _is_legacy_timestamp_nullable_change(operation):
            continue
        retained.append(operation)
    container.ops = retained


def process_revision_directives(
    migration_context: Any,
    revision: Any,
    directives: list[Any],
) -> None:
    """Baseline intentional timestamp nullability only for the CI schema check."""
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("baseline_legacy_timestamp_nullability") != "true":
        return
    for directive in directives:
        _filter_legacy_timestamp_nullable_changes(directive.upgrade_ops)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        version_table_schema="public",
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.execute("SET search_path TO public, pg_catalog")
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # Session scope is required because historical migrations use Alembic
    # autocommit blocks; SET LOCAL would be cleared at the first such boundary.
    connection.exec_driver_sql("SET SESSION search_path TO public, pg_catalog")
    connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
        process_revision_directives=process_revision_directives,
        version_table_schema="public",
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
