import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)

from app.models.base import Base  # noqa: E402
from app.models.analytics import DailyScanStats  # noqa: F401
from app.models.audit import PlatformAuditLog  # noqa: F401
from app.models.campaign import Benefit, BenefitClaim, Campaign  # noqa: F401
from app.models.channel import Distributor, Region, Store, DiversionClue  # noqa: F401
from app.models.code import CodeBatch, CodeItem  # noqa: F401
from app.models.consent import ConsentRecord  # noqa: F401
from app.models.connector import Connector, CouponPool, CouponCode, BenefitDelivery  # noqa: F401
from app.models.ai_generation import AiGeneration  # noqa: F401
from app.models.export_log import ExportLog  # noqa: F401
from app.models.gmv import ExternalOrder, GmvAttribution  # noqa: F401
from app.models.private_domain import PrivateDomainConfig  # noqa: F401
from app.models.i18n import Translation  # noqa: F401
from app.models.integration import SyncRecord  # noqa: F401
from app.models.member import ConsumerProfile, PointProduct, PointRedemption, PointRule, PointTransaction  # noqa: F401
from app.models.page import PageTemplate, PageVersion  # noqa: F401
from app.models.product import Brand, Product, ProductionBatch, SKU  # noqa: F401
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
from app.models.tenant import Account, Organization, Permission, Role, Tenant, account_roles, role_permissions  # noqa: F401
from app.models.webhook import ApiKey, WebhookDelivery, WebhookEndpoint  # noqa: F401
from app.models.wecom import WeComContactWay, WeComExternalContact  # noqa: F401

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
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
