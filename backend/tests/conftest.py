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
os.environ.setdefault("aes_master_key_v1", "00" * 32)
os.environ.setdefault("hmac_pepper", "ff" * 32)

# 必须在设置环境变量后导入
from app.models.analytics import DailyScanStats  # noqa: E402, F401
from app.models.audit import PlatformAuditLog  # noqa: E402, F401
from app.models.base import Base  # noqa: E402
from app.models.campaign import Benefit, BenefitClaim, Campaign  # noqa: E402, F401
from app.models.code import CodeBatch, CodeItem  # noqa: E402, F401
from app.models.page import PageTemplate, PageVersion  # noqa: E402, F401
from app.models.product import SKU, Brand, Product, ProductionBatch  # noqa: E402, F401
from app.models.scan import ScanEvent  # noqa: E402, F401
from app.models.risk import RiskAlert  # noqa: E402, F401
from app.models.risk import RiskRule, CampaignRiskRule, InterceptionRecord  # noqa: E402, F401
from app.models.regional import RegionalOrg, RegionalOrgMember, RegionalTemplate, RegionalProductAuth, RegionalCodeRule, WhitelabelConfig  # noqa: E402, F401
from app.models.gmv import ExternalOrder, GmvAttribution  # noqa: E402, F401
from app.models.connector import Connector, CouponPool, CouponCode  # noqa: E402, F401
from app.models.webhook import WebhookEndpoint, ApiKey, WebhookDelivery  # noqa: E402, F401
from app.models.integration import SyncRecord  # noqa: E402, F401
from app.models.i18n import Translation  # noqa: E402, F401
from app.models.redpacket import RedPacketRule, KYCRecord, RedPacketClaim, Withdrawal  # noqa: E402, F401
from app.models.channel import Distributor, Region, Store, DiversionClue  # noqa: E402, F401
from app.models.member import ConsumerProfile, PointTransaction, PointRule  # noqa: E402, F401
from app.models.tenant import (  # noqa: E402, F401
    Account,
    Organization,
    Permission,
    Role,
    Tenant,
    account_roles,
    role_permissions,
)

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
