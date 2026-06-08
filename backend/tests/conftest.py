import os
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 将 backend 目录加入 Python 路径
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

# 设置测试环境变量（在导入 app 之前）
os.environ.setdefault("database_url", "sqlite+aiosqlite://")
os.environ.setdefault("redis_url", "redis://localhost:6379/0")
os.environ.setdefault("secret_key", "test-secret-key")
os.environ.setdefault("AES_MASTER_KEY_V1", "00" * 32)
os.environ.setdefault("HMAC_PEPPER", "ff" * 32)

# 必须在设置环境变量后导入
from app.models.analytics import DailyScanStats  # noqa: E402, F401
from app.models.audit import PlatformAuditLog  # noqa: E402, F401
from app.models.base import Base  # noqa: E402
from app.models.campaign import Benefit, BenefitClaim, Campaign  # noqa: E402, F401
from app.models.channel import AccountChannelScope, Distributor, DiversionClue, Region, Store  # noqa: E402, F401
from app.models.code import CodeBatch, CodeItem  # noqa: E402, F401
from app.models.connector import BenefitDelivery, Connector, CouponCode, CouponPool  # noqa: E402, F401
from app.models.consent import ConsentRecord  # noqa: E402, F401
from app.models.export_log import ExportLog  # noqa: E402, F401
from app.models.gmv import ExternalOrder, GmvAttribution  # noqa: E402, F401
from app.models.invite_code import TenantInviteCode  # noqa: E402, F401
from app.models.i18n import Translation  # noqa: E402, F401
from app.models.integration import SyncRecord  # noqa: E402, F401
from app.models.member import (  # noqa: E402, F401
    ConsumerProfile,
    PointProduct,
    PointRedemption,
    PointRule,
    PointTransaction,
)
from app.models.page import PageTemplate, PageVersion  # noqa: E402, F401
from app.models.private_domain import PrivateDomainConfig  # noqa: E402, F401
from app.models.product import SKU, Brand, Product, ProductionBatch  # noqa: E402, F401

# redpacket models removed in EPIC-23 (red packet is now a benefit_type)
from app.models.regional import (  # noqa: E402, F401
    RegionalCodeRule,
    RegionalOrg,
    RegionalOrgMember,
    RegionalProductAuth,
    RegionalTemplate,
    WhitelabelConfig,
)
from app.models.risk import (  # noqa: E402, F401
    CampaignRiskRule,
    InterceptionRecord,
    RiskAlert,  # noqa: E402, F401
    RiskRule,
)
from app.models.scan import ScanEvent  # noqa: E402, F401
from app.models.sync_mapping import SyncMapping  # noqa: E402, F401
from app.models.tenant import (  # noqa: E402, F401
    Account,
    AgencyAuthorization,
    Organization,
    Permission,
    Role,
    Tenant,
    account_roles,
    role_permissions,
)
from app.models.webhook import ApiKey, WebhookDelivery, WebhookEndpoint  # noqa: E402, F401
from app.models.wecom import WeComContactWay, WeComExternalContact  # noqa: E402, F401
from app.utils.crypto import EnvKeyProvider, init_crypto  # noqa: E402

# 初始化加密模块（读取上面设置的环境变量）
init_crypto(EnvKeyProvider())

TEST_DATABASE_URL = "sqlite+aiosqlite://"
test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def setup_database():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db():

    async with TestSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
