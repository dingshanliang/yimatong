import os
import sys
import uuid
from datetime import UTC, datetime
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
from app.models.campaign import Benefit, BenefitClaim, Campaign, CampaignStatus  # noqa: E402, F401
from app.models.channel import AccountChannelScope, Distributor, DiversionClue, Region, Store  # noqa: E402, F401
from app.models.code import CodeBatch, CodeBatchStatus, CodeItem, CodeItemStatus  # noqa: E402, F401
from app.models.connector import BenefitDelivery, Connector, CouponCode, CouponPool  # noqa: E402, F401
from app.models.consent import ConsentRecord  # noqa: E402, F401
from app.models.diversion_evidence import DiversionEvidence  # noqa: E402, F401
from app.models.diversion_history import DiversionInvestigationHistory  # noqa: E402, F401
from app.models.export_log import ExportLog  # noqa: E402, F401
from app.models.gmv import ExternalOrder, GmvAttribution  # noqa: E402, F401
from app.models.i18n import Translation  # noqa: E402, F401
from app.models.integration import SyncRecord  # noqa: E402, F401
from app.models.intent_event import IntentEvent  # noqa: E402, F401
from app.models.invite_code import TenantInviteCode  # noqa: E402, F401
from app.models.launch import LaunchRelease  # noqa: E402, F401
from app.models.member import (  # noqa: E402, F401
    ConsumerProfile,
    PointProduct,
    PointRedemption,
    PointRule,
    PointTransaction,
)
from app.models.page import PageTemplate, PageVersion, PageVersionStatus  # noqa: E402, F401
from app.models.plan import PlanDefinition  # noqa: E402
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
from app.models.visitor import AnonymousVisitor  # noqa: E402, F401
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
    async with TestSessionLocal() as session:
        session.add_all(
            [
                PlanDefinition(
                    id="test-plan-free",
                    name="free",
                    display_name="免费版",
                    quota_defaults={"max_codes": 10000, "max_campaigns": 50, "max_accounts": 10},
                    feature_flags={},
                ),
                PlanDefinition(
                    id="test-plan-starter",
                    name="starter",
                    display_name="入门版",
                    quota_defaults={"max_codes": 10000, "max_campaigns": 10, "max_accounts": 5},
                    feature_flags={"ai_assistant": True},
                ),
                PlanDefinition(
                    id="test-plan-pro",
                    name="pro",
                    display_name="专业版",
                    quota_defaults={"max_codes": 100000, "max_campaigns": 50, "max_accounts": 20},
                    feature_flags={"ai_assistant": True, "risk_module": True},
                ),
                PlanDefinition(
                    id="test-plan-enterprise",
                    name="enterprise",
                    display_name="企业版",
                    quota_defaults={"max_codes": -1, "max_campaigns": -1, "max_accounts": -1},
                    feature_flags={"ai_assistant": True, "risk_module": True, "white_label": True},
                ),
            ]
        )
        await session.commit()
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


@pytest.fixture
async def launch_facts(db):
    """创建一组已满足上线门禁的真实数据库事实。"""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    product_id = uuid.uuid4()
    template = PageTemplate(
        tenant_id=tenant_id,
        product_id=product_id,
        name="上线门禁页",
        template_type="product_info",
    )
    db.add(template)
    await db.flush()
    version = PageVersion(
        tenant_id=tenant_id,
        page_template_id=template.id,
        version=1,
        config_json={"dsl_version": "1.0", "title": "正式页"},
        status=PageVersionStatus.published,
        created_by=account_id,
    )
    campaign = Campaign(
        tenant_id=tenant_id,
        product_id=product_id,
        name="首发活动",
        campaign_type="scan",
        status=CampaignStatus.ACTIVE,
        start_at="2026-07-01T00:00:00Z",
        end_at="2026-12-31T00:00:00Z",
        rules_json={},
    )
    batch = CodeBatch(
        tenant_id=tenant_id,
        product_id=product_id,
        sku_id=uuid.uuid4(),
        batch_code="LAUNCH-001",
        quantity=1,
        status=CodeBatchStatus.activated,
        created_by=account_id,
    )
    db.add_all([version, campaign, batch])
    await db.flush()
    item = CodeItem(
        tenant_id=tenant_id,
        code_batch_id=batch.id,
        public_id="LAUNCHCODE001",
        status=CodeItemStatus.activated,
    )
    db.add(item)
    await db.flush()
    db.add(
        ScanEvent(
            tenant_id=tenant_id,
            public_id=item.public_id,
            scan_time=datetime.now(UTC),
            is_valid_visit=True,
            environment="test",
        )
    )
    await db.flush()
    return tenant_id, account_id, version, campaign, batch
