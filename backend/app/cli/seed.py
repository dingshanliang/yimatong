"""ymt seed CLI 工具 - 创建种子数据"""

import asyncio
import uuid
from contextlib import AsyncExitStack
from datetime import UTC, date, datetime, timedelta

import typer
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.cli.lifecycle_auth import cli_lifecycle_auth_context
from app.constants.campaign import BenefitType
from app.core.config import settings
from app.core.database import control_session_factory, set_session_tenant_context
from app.models.campaign import Benefit, Campaign, CampaignStatus
from app.models.channel import AccountChannelScope, CodeAllocation, Distributor, Region, Store
from app.models.code import CodeItem, CodeItemStatus
from app.models.connector import Connector  # noqa: F401 - register connector tables for Benefit FK sorting
from app.models.page import PageTemplate, PageVersion, PageVersionStatus, TemplateType
from app.models.product import SKU, Brand, Product, ProductionBatch
from app.models.scan import ScanEvent
from app.models.tenant import Account, Organization, Permission, Role, Tenant, account_roles, role_permissions
from app.services import channel_authority, diversion_authority
from app.services.analytics import aggregate_daily_stats
from app.services.audit import write_audit_log
from app.services.auth import revoke_current_tenant_account_sessions
from app.services.campaign import (
    change_campaign_status,
    create_benefit,
    create_campaign,
)
from app.services.code import activate_batch, create_code_batch, mark_delivered, mark_printing, revoke_code_item
from app.services.code_export import generate_code_csv
from app.services.page import create_page_template, create_page_version, publish_page_version
from app.services.quota import lock_quota_rollout_state, refresh_quota_usage_from_authoritative_rows
from app.services.risk import freeze_code_item
from app.services.tenant import create_tenant
from app.utils import utcnow
from app.utils.auth_rbac import WEB_ROLE_PERMISSIONS
from app.utils.crypto import EnvKeyProvider, init_crypto
from app.utils.email import normalize_email
from app.utils.security import hash_password, verify_password

app = typer.Typer(help="Seed data for development")

engine = create_async_engine(str(settings.database_url))
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
control_session = control_session_factory
if settings.migration_database_url is None:
    seed_owner_session_factory: async_sessionmaker[AsyncSession] | None = None
else:
    seed_owner_engine = create_async_engine(str(settings.migration_database_url))
    seed_owner_session_factory = async_sessionmaker(seed_owner_engine, class_=AsyncSession, expire_on_commit=False)

DEMO_ACCOUNTS = [
    {
        "email": "admin@demo.com",
        "password": "Admin1234",
        "name": "品牌管理员",
        "role": "admin",
        "title": "品牌方管理者",
    },
    {
        "email": "ops@demo.com",
        "password": "Ops123456",
        "name": "活动运营",
        "role": "operator",
        "title": "日常运营人员",
    },
    {
        "email": "agency@demo.com",
        "password": "Agency1234",
        "name": "代运营顾问",
        "role": "operator",
        "title": "代运营服务人员",
    },
    {
        "email": "dist@demo.com",
        "password": "Dist123456",
        "name": "华东经销商账号",
        "role": "distributor",
        "title": "经销商入口",
    },
    {
        "email": "store@demo.com",
        "password": "Store123456",
        "name": "南京东路店账号",
        "role": "store_guide",
        "title": "门店入口",
    },
]

DEMO_ENABLED_FEATURES = {"channel_portal": True, "risk_module": True}
DEFAULT_TENANT_ADMIN_EMAIL = "admin@example.com"
DEFAULT_TENANT_ADMIN_PASSWORD = "Admin1234"


def _guard_seed_mutation(
    *,
    command: str,
    target: str,
    allow_production: bool,
    demo_only: bool = False,
    allow_non_demo_target: bool = False,
    unsafe_default_identity: bool = False,
    forbid_production: bool = False,
) -> None:
    """Authorize one exact seed target before any database work starts."""

    if not target.strip():
        typer.echo(f"拒绝执行 {command}：必须提供明确的目标租户", err=True)
        raise typer.Exit(code=2)
    if settings.environment == "production" and forbid_production:
        typer.echo(f"production 环境禁止执行 {command}：该命令会写入仓库内置演示账号或密码", err=True)
        raise typer.Exit(code=2)
    if settings.environment == "production" and not allow_production:
        typer.echo(
            f"拒绝在 production 环境执行 {command}；如确有需要请显式传 --allow-production",
            err=True,
        )
        raise typer.Exit(code=2)
    if settings.environment == "production" and unsafe_default_identity:
        typer.echo("拒绝在 production 环境使用默认管理员邮箱或密码创建租户", err=True)
        raise typer.Exit(code=2)
    if demo_only and target != "demo" and not allow_non_demo_target:
        typer.echo("拒绝向非 demo 租户写入演示数据；如确有需要请显式传 --allow-non-demo-target", err=True)
        raise typer.Exit(code=2)


async def _get_tenant_by_slug(db: AsyncSession, slug: str) -> Tenant | None:
    result = await db.execute(select(Tenant).where(Tenant.slug == slug))
    return result.scalar_one_or_none()


async def _prepare_control_session(db: AsyncSession) -> None:
    """Enable the explicit control-plane bootstrap scope for tenant discovery/creation."""
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        await db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))


async def _find_tenant_id(slug: str) -> uuid.UUID | None:
    """Resolve one tenant key through the bounded control-plane index."""
    async with control_session() as db:
        await _prepare_control_session(db)
        tenant = await _get_tenant_by_slug(db, slug)
        return tenant.id if tenant else None


async def _ensure_tenant(
    *,
    name: str,
    slug: str,
    admin_email: str,
    admin_name: str,
    admin_password: str,
) -> tuple[uuid.UUID, bool]:
    """Create the tenant atomically through control DB, without seeding its business rows there."""
    async with control_session() as db:
        await _prepare_control_session(db)
        existing = await _get_tenant_by_slug(db, slug)
        if existing:
            return existing.id, False
        tenant = await create_tenant(
            db,
            name=name,
            slug=slug,
            admin_email=admin_email,
            admin_name=admin_name,
            admin_password=admin_password,
            plan="free",
        )
        tenant_id = tenant.id
        await db.commit()
        return tenant_id, True


async def _open_tenant_scope(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    """Switch a fresh runtime session to exactly one tenant before business writes."""
    await set_session_tenant_context(db, tenant_id)
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:  # pragma: no cover - control lookup/create is the precondition
        raise RuntimeError("Tenant disappeared before scoped seed")
    return tenant


def _enable_demo_features(tenant: Tenant) -> None:
    tenant.enabled_features = {**(tenant.enabled_features or {}), **DEMO_ENABLED_FEATURES}


async def _generate_codes(db: AsyncSession, tenant_id: uuid.UUID, batch_code: str, count: int) -> int:
    # Placeholder - actual code generation will be in Wave A5
    return count


async def _get_default_org(db: AsyncSession, tenant_id: uuid.UUID) -> Organization:
    result = await db.execute(
        select(Organization).where(
            Organization.tenant_id == tenant_id,
            Organization.name == "演示默认组织",
        )
    )
    org = result.scalar_one_or_none()
    if org:
        return org
    org = Organization(tenant_id=tenant_id, name="演示默认组织")
    db.add(org)
    await db.flush()
    await db.refresh(org)
    return org


async def _ensure_role(db: AsyncSession, tenant_id: uuid.UUID, name: str, description: str) -> Role:
    result = await db.execute(select(Role).where(Role.tenant_id == tenant_id, Role.name == name))
    role = result.scalar_one_or_none()
    if role:
        role.description = description
    else:
        role = Role(tenant_id=tenant_id, name=name, description=description)
        db.add(role)
        await db.flush()
        await db.refresh(role)

    for code in WEB_ROLE_PERMISSIONS.get(name, []):
        permission = await db.scalar(
            select(Permission).where(Permission.tenant_id == tenant_id, Permission.code == code)
        )
        if permission is None:
            permission = Permission(
                tenant_id=tenant_id,
                code=code,
                description=f"默认权限：{code}",
            )
            db.add(permission)
            await db.flush()
        linked = await db.scalar(
            select(role_permissions.c.role_id).where(
                role_permissions.c.role_id == role.id,
                role_permissions.c.permission_id == permission.id,
            )
        )
        if linked is None:
            await db.execute(
                role_permissions.insert().values(
                    tenant_id=tenant_id,
                    role_id=role.id,
                    permission_id=permission.id,
                )
            )
    return role


def _demo_accounts_for_request(*, admin_email: str, admin_name: str, admin_password: str) -> list[dict[str, str]]:
    return [
        {
            **DEMO_ACCOUNTS[0],
            "email": normalize_email(admin_email),
            "password": admin_password,
            "name": admin_name,
        },
        *DEMO_ACCOUNTS[1:],
    ]


async def _ensure_demo_accounts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    org_id: uuid.UUID,
    *,
    admin_email: str = DEMO_ACCOUNTS[0]["email"],
    admin_name: str = DEMO_ACCOUNTS[0]["name"],
    admin_password: str = DEMO_ACCOUNTS[0]["password"],
    include_supporting_accounts: bool = True,
) -> list[Account]:
    roles = {
        "admin": await _ensure_role(
            db,
            tenant_id,
            "admin",
            "品牌管理员：可管理租户、账号、产品、码、页面、活动和数据。",
        ),
        "operator": await _ensure_role(
            db,
            tenant_id,
            "operator",
            "运营人员：可维护产品、码、页面、活动并查看数据。",
        ),
        "distributor": await _ensure_role(
            db,
            tenant_id,
            "distributor",
            "经销商：查看自己负责的收货流向、扫码数据和异常线索。",
        ),
        "store_guide": await _ensure_role(
            db,
            tenant_id,
            "store_guide",
            "门店：查看本店收货批次、扫码数据和线索统计。",
        ),
    }
    accounts: list[Account] = []
    account_specs = _demo_accounts_for_request(
        admin_email=admin_email,
        admin_name=admin_name,
        admin_password=admin_password,
    )
    if not include_supporting_accounts:
        account_specs = account_specs[:1]
    for item in account_specs:
        result = await db.execute(
            select(Account)
            .options(selectinload(Account.roles))
            .where(Account.tenant_id == tenant_id, Account.email == item["email"])
        )
        account = result.scalar_one_or_none()
        if account:
            desired_role = roles[item["role"]]
            current_role_ids = {role.id for role in account.roles}
            identity_changes: list[str] = []
            security_changes: list[str] = []
            before = {
                "name": account.name,
                "organization_id": str(account.organization_id),
                "roles": sorted(role.name for role in account.roles),
            }
            if account.name != item["name"]:
                account.name = item["name"]
                identity_changes.append("name")
            if account.organization_id != org_id:
                account.organization_id = org_id
                security_changes.append("organization")
            if not verify_password(item["password"], account.hashed_password):
                account.hashed_password = hash_password(item["password"])
                security_changes.append("password")
            if current_role_ids != {desired_role.id}:
                await db.execute(
                    account_roles.delete().where(
                        account_roles.c.tenant_id == tenant_id,
                        account_roles.c.account_id == account.id,
                    )
                )
                await db.execute(
                    account_roles.insert().values(
                        tenant_id=tenant_id,
                        account_id=account.id,
                        role_id=desired_role.id,
                    )
                )
                security_changes.append("roles")
            identity_changes.extend(security_changes)
            if security_changes:
                account.auth_version += 1
                await revoke_current_tenant_account_sessions(db, account.id)
            if identity_changes:
                await write_audit_log(
                    db,
                    operator_id="seed-cli",
                    target_tenant_id=str(tenant_id),
                    action="account_identity_reconciled",
                    resource=f"account:{account.id}",
                    details={
                        "resource_name": account.name,
                        "changed_fields": sorted(identity_changes),
                        "before": before,
                        "after": {
                            "name": account.name,
                            "organization_id": str(account.organization_id),
                            "roles": [desired_role.name],
                        },
                        "result": "success",
                    },
                )
        else:
            account = Account(
                tenant_id=tenant_id,
                organization_id=org_id,
                email=item["email"],
                hashed_password=hash_password(item["password"]),
                name=item["name"],
            )
            db.add(account)
            await db.flush()
            await db.execute(
                account_roles.insert().values(
                    tenant_id=tenant_id,
                    account_id=account.id,
                    role_id=roles[item["role"]].id,
                )
            )
        accounts.append(account)
    await db.flush()
    return accounts


async def _ensure_committed_demo_admin(
    tenant_id: uuid.UUID,
    *,
    admin_email: str,
    admin_name: str,
    admin_password: str,
) -> uuid.UUID:
    """Commit demo identity repair before the control plane creates a CLI credential."""

    async with async_session() as db:
        await lock_quota_rollout_state(db)
        tenant = await _open_tenant_scope(db, tenant_id)
        _enable_demo_features(tenant)
        organization = await _get_default_org(db, tenant.id)
        accounts = await _ensure_demo_accounts(
            db,
            tenant.id,
            organization.id,
            admin_email=admin_email,
            admin_name=admin_name,
            admin_password=admin_password,
            include_supporting_accounts=False,
        )
        admin = next((account for account in accounts if account.email == admin_email), None)
        if admin is None:
            raise RuntimeError("Durable demo admin is unavailable")
        admin_id = admin.id
        await refresh_quota_usage_from_authoritative_rows(db, tenant.id)
        await db.commit()
        return admin_id


def _demo_diversion_uuid(tenant_id: uuid.UUID, resource: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"yimatong:official-seed:diversion:v1:{tenant_id}:{resource}")


async def _ensure_demo_scan_history_and_quota(
    tenant_id: uuid.UUID,
    activated_public_ids: list[str],
    diversion_public_id: str | None = None,
) -> None:
    async with control_session() as scan_db:
        await set_session_tenant_context(scan_db, tenant_id)
        await _ensure_scan_events(scan_db, tenant_id, activated_public_ids)
        if diversion_public_id:
            scan_event_id = _demo_diversion_uuid(tenant_id, "scan")
            if await scan_db.get(ScanEvent, scan_event_id) is None:
                scan_db.add(
                    ScanEvent(
                        id=scan_event_id,
                        tenant_id=tenant_id,
                        public_id=diversion_public_id,
                        scan_time=datetime(2026, 1, 1, 8, tzinfo=UTC),
                        ip_hash=f"demo-diversion-{tenant_id.hex[:32]}",
                        user_agent="Mozilla/5.0 Demo Diversion Observation",
                        is_first_scan=False,
                        environment="wechat",
                        is_valid_visit=True,
                        location_source="ip_inference",
                        location_accuracy="medium",
                        location_authorized=None,
                    )
                )
                await scan_db.flush()
        await refresh_quota_usage_from_authoritative_rows(scan_db, tenant_id)
        await scan_db.commit()


async def _ensure_production_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    sku_id: uuid.UUID,
) -> ProductionBatch:
    result = await db.execute(
        select(ProductionBatch).where(
            ProductionBatch.tenant_id == tenant_id,
            ProductionBatch.batch_code == "DEMO-RICE-202605",
        )
    )
    batch = result.scalar_one_or_none()
    if batch:
        return batch
    today = date.today()
    batch = ProductionBatch(
        tenant_id=tenant_id,
        product_id=product_id,
        sku_id=sku_id,
        batch_code="DEMO-RICE-202605",
        production_date=today - timedelta(days=12),
        expiry_date=today + timedelta(days=365),
        external_id="ERP-DEMO-RICE-202605",
        source_system="demo",
    )
    db.add(batch)
    await db.flush()
    await db.refresh(batch)
    return batch


async def _ensure_page(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    created_by: uuid.UUID,
) -> PageTemplate:
    result = await db.execute(
        select(PageTemplate).where(
            PageTemplate.tenant_id == tenant_id,
            PageTemplate.product_id == product_id,
            PageTemplate.name == "五常稻花香扫码信任页",
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        created_template = await create_page_template(
            db,
            tenant_id,
            "五常稻花香扫码信任页",
            TemplateType.traceability,
            "客户演示用 H5：溯源、检测报告、首扫福利、私域承接。",
            product_id,
            created_by,
        )
        template = await db.scalar(
            select(PageTemplate).where(
                PageTemplate.tenant_id == tenant_id,
                PageTemplate.id == uuid.UUID(created_template["id"]),
            )
        )
        if template is None:  # pragma: no cover - authoritative function returned an impossible reference
            raise RuntimeError("Page template creation did not persist its authority row")

    result = await db.execute(
        select(PageVersion).where(
            PageVersion.tenant_id == tenant_id,
            PageVersion.page_template_id == template.id,
            PageVersion.status == PageVersionStatus.published,
        )
    )
    version = result.scalar_one_or_none()
    config = {
        "brand_name": "青岭良仓",
        "product_name": "五常稻花香大米 5kg",
        "story_content": "来自黑龙江五常核心产区，批次检测合格，扫码领取首购福利并进入复购服务群。",
        "traceability": {
            "origin": "黑龙江省哈尔滨市五常市",
            "batch_code": "DEMO-RICE-202605",
            "production_date": str(date.today() - timedelta(days=12)),
            "expiry_date": str(date.today() + timedelta(days=365)),
        },
        "benefit": "首扫领取 20 元复购券",
        "private_domain": "企业微信客服 / 小程序商城",
    }
    if version is None or version.config_json != config:
        created_version = await create_page_version(db, tenant_id, template.id, config, created_by)
        if created_version is None:  # pragma: no cover - template is locked by the authority function
            raise RuntimeError("Page version creation lost its template authority")
        await publish_page_version(db, tenant_id, uuid.UUID(created_version["id"]), created_by)
    await db.refresh(template)
    return template


async def _ensure_campaign(db: AsyncSession, tenant_id: uuid.UUID, product_id: uuid.UUID) -> Campaign:
    now = utcnow()
    result = await db.execute(
        select(Campaign).where(Campaign.tenant_id == tenant_id, Campaign.name == "首扫领券加企微复购活动")
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        created = await create_campaign(
            db,
            tenant_id,
            "首扫领券加企微复购活动",
            "coupon",
            now - timedelta(days=3),
            now + timedelta(days=30),
            {
                "participation_conditions": "首次扫码",
                "participation_condition_type": "first_scan",
                "claim_limits": "每人限领1次",
                "claim_limit_count": 1,
                "validity_period": "活动期内有效",
                "disclaimer": "最终解释权归品牌方所有",
                "minor_notice": "未成年人请在监护人陪同下参与",
                "customer_service_contact": "400-123-4567",
                "channels": ["wechat", "h5"],
            },
            "客户演示活动：首扫领券，引导加企微和商城复购。",
            product_id,
        )
        campaign = await db.get(Campaign, uuid.UUID(created["id"]), populate_existing=True)
        if campaign is None:  # pragma: no cover - authority returned the created row
            raise RuntimeError("Campaign creation lost its authority row")
    result = await db.execute(select(Benefit).where(Benefit.tenant_id == tenant_id, Benefit.campaign_id == campaign.id))
    benefit = result.scalar_one_or_none()
    if benefit is None:
        await create_benefit(
            db,
            tenant_id,
            campaign.id,
            "20 元复购券",
            BenefitType.EXTERNAL_LINK,
            {
                "url": "https://shop.example.com/demo-rice",
                "amount": 20,
                "threshold": 99,
                "validity_type": "campaign_period",
            },
            1000,
            1,
        )
    if campaign.status == CampaignStatus.DRAFT:
        await change_campaign_status(db, tenant_id, campaign.id, CampaignStatus.ACTIVE)
    await db.refresh(campaign)
    return campaign


def _seed_code_generation_idempotency_key(tenant_id: uuid.UUID, production_batch_id: uuid.UUID) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"yimatong:seed-code:v1:{tenant_id}:{production_batch_id}"))


async def _create_and_deliver_seed_code_batch(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    sku_id: uuid.UUID,
    production_batch_id: uuid.UUID,
    batch_code: str,
    quantity: int,
    created_by: uuid.UUID,
) -> uuid.UUID:
    init_crypto(EnvKeyProvider())
    data = await create_code_batch(
        db,
        tenant_id,
        product_id,
        sku_id,
        production_batch_id,
        quantity,
        created_by,
        batch_code=batch_code,
        idempotency_key=_seed_code_generation_idempotency_key(tenant_id, production_batch_id),
    )
    batch_id = uuid.UUID(data["id"])
    if seed_owner_session_factory is None:
        raise RuntimeError("migration_database_url is required for trusted seed export authority")
    await generate_code_csv(
        db,
        tenant_id,
        batch_id,
        created_by,
        seed_owner_session_factory=seed_owner_session_factory,
    )
    await mark_printing(db, tenant_id, batch_id, actor_id=str(created_by))
    await mark_delivered(
        db,
        tenant_id,
        batch_id,
        actor_id=str(created_by),
        reason="Seed data delivery",
        recipient="Seed data operations",
        confirm="deliver",
    )
    await activate_batch(db, tenant_id, batch_id, actor_id=str(created_by))
    return batch_id


async def _ensure_demo_codes(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    sku_id: uuid.UUID,
    production_batch_id: uuid.UUID,
    created_by: uuid.UUID,
) -> list[CodeItem]:
    from app.models.code import CodeBatch

    result = await db.execute(
        select(CodeBatch).where(CodeBatch.tenant_id == tenant_id, CodeBatch.batch_code == "DEMO-CODE-202605")
    )
    batch = result.scalar_one_or_none()
    if not batch:
        batch_id = await _create_and_deliver_seed_code_batch(
            db,
            tenant_id=tenant_id,
            product_id=product_id,
            sku_id=sku_id,
            production_batch_id=production_batch_id,
            batch_code="DEMO-CODE-202605",
            quantity=12,
            created_by=created_by,
        )
        result = await db.execute(select(CodeBatch).where(CodeBatch.id == batch_id))
        batch = result.scalar_one()

    result = await db.execute(
        select(CodeItem)
        .where(CodeItem.tenant_id == tenant_id, CodeItem.code_batch_id == batch.id)
        .order_by(CodeItem.public_id)
    )
    items = list(result.scalars().all())
    if not items:
        raise RuntimeError("Demo code batch exists without authoritative code items")
    if any(item.status == CodeItemStatus.created for item in items):
        raise RuntimeError("Demo code batch contains unactivated authoritative code items")
    if len(items) >= 3:
        # 生命周期契约（d297eb0518da）：void 拒绝 revoked/expired 终态，freeze 仅接受
        # activated/bound；重跑时旧码可能已处于这些状态，需按状态过滤。
        if items[1].status not in (CodeItemStatus.revoked, CodeItemStatus.expired):
            await revoke_code_item(
                db,
                tenant_id,
                items[1].id,
                actor_id=str(created_by),
                reason="source=official_seed; purpose=permanent_void_sample",
            )
        if items[2].status in (CodeItemStatus.activated, CodeItemStatus.bound):
            await freeze_code_item(
                db,
                tenant_id,
                items[2].id,
                actor_id=str(created_by),
                reason="source=official_seed; purpose=risk_freeze_sample",
                idempotency_key=f"official-seed-freeze:{tenant_id}:{items[2].id}",
            )
    return list(
        await db.scalars(
            select(CodeItem)
            .where(CodeItem.tenant_id == tenant_id, CodeItem.code_batch_id == batch.id)
            .order_by(CodeItem.public_id)
            .execution_options(populate_existing=True)
        )
    )


async def _ensure_scan_events(db: AsyncSession, tenant_id: uuid.UUID, activated_public_ids: list[str]) -> None:
    result = await db.execute(select(func.count()).select_from(ScanEvent).where(ScanEvent.tenant_id == tenant_id))
    now = utcnow()
    if result.scalar_one() == 0:
        events = []
        for day in range(7):
            for index, public_id in enumerate(activated_public_ids[:4]):
                events.append(
                    ScanEvent(
                        tenant_id=tenant_id,
                        public_id=public_id,
                        scan_time=now - timedelta(days=6 - day, hours=index),
                        ip_hash=f"demo-ip-{day}-{index}",
                        user_agent="Mozilla/5.0 Demo WeChat",
                        is_first_scan=index == 0,
                        environment="wechat",
                    )
                )
        db.add_all(events)
        await db.flush()
    for offset in range(7):
        await aggregate_daily_stats(db, tenant_id, (now - timedelta(days=offset)).date())


def _channel_seed_idem(tenant_id: uuid.UUID, action: str, natural_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"yimatong:official-seed:{tenant_id}:{action}:{natural_key}"))


async def _ensure_demo_channels(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    accounts: list[Account],
    code_items: list[CodeItem],
) -> tuple[Distributor, Region]:
    from app.models.code import CodeBatch

    distributor = (
        await db.execute(
            select(Distributor).where(Distributor.tenant_id == tenant_id, Distributor.code == "DEMO-DIST-EAST")
        )
    ).scalar_one_or_none()
    if not distributor:
        distributor = await channel_authority.create_distributor(
            db,
            tenant_id,
            idempotency_key=_channel_seed_idem(tenant_id, "create-distributor", "DEMO-DIST-EAST"),
            name="华东经销商",
            code="DEMO-DIST-EAST",
            contact_name="陈经理",
            contact_phone=None,
            status="active",
        )
    elif distributor.name != "华东经销商" or distributor.contact_name != "陈经理" or distributor.status != "active":
        distributor = await channel_authority.update_distributor(
            db,
            tenant_id,
            distributor.id,
            expected_version=distributor.version,
            idempotency_key=_channel_seed_idem(
                tenant_id, "update-distributor", f"{distributor.id}:v{distributor.version}"
            ),
            changes={"name": "华东经销商", "contact_name": "陈经理", "status": "active"},
        )

    region = (
        await db.execute(select(Region).where(Region.tenant_id == tenant_id, Region.code == "DEMO-REG-SH"))
    ).scalar_one_or_none()
    if not region:
        region = await channel_authority.create_region(
            db,
            tenant_id,
            idempotency_key=_channel_seed_idem(tenant_id, "create-region", "DEMO-REG-SH"),
            payload={
                "name": "上海区域",
                "code": "DEMO-REG-SH",
                "province": "上海",
                "city": "上海",
                "coverage_type": "city",
                "coverage_areas": [{"province": "上海", "city": "上海"}],
                "distributor_id": distributor.id,
                "status": "active",
            },
        )
    elif (
        region.coverage_type != "city"
        or region.coverage_areas != [{"province": "上海", "city": "上海"}]
        or region.distributor_id != distributor.id
        or region.status != "active"
    ):
        region = await channel_authority.update_region(
            db,
            tenant_id,
            region.id,
            expected_version=region.version,
            idempotency_key=_channel_seed_idem(tenant_id, "update-region", f"{region.id}:v{region.version}"),
            changes={
                "coverage_type": "city",
                "coverage_areas": [{"province": "上海", "city": "上海"}],
                "distributor_id": distributor.id,
                "status": "active",
            },
        )

    south_region = (
        await db.execute(select(Region).where(Region.tenant_id == tenant_id, Region.code == "DEMO-REG-SU"))
    ).scalar_one_or_none()
    if not south_region:
        south_region = await channel_authority.create_region(
            db,
            tenant_id,
            idempotency_key=_channel_seed_idem(tenant_id, "create-region", "DEMO-REG-SU"),
            payload={
                "name": "苏南区域",
                "code": "DEMO-REG-SU",
                "province": "江苏",
                "city": None,
                "coverage_type": "province",
                "coverage_areas": [{"province": "江苏", "city": None}],
                "distributor_id": distributor.id,
                "status": "active",
            },
        )
    elif (
        south_region.city is not None
        or south_region.coverage_type != "province"
        or south_region.coverage_areas != [{"province": "江苏", "city": None}]
        or south_region.distributor_id != distributor.id
        or south_region.status != "active"
    ):
        south_region = await channel_authority.update_region(
            db,
            tenant_id,
            south_region.id,
            expected_version=south_region.version,
            idempotency_key=_channel_seed_idem(
                tenant_id, "update-region", f"{south_region.id}:v{south_region.version}"
            ),
            changes={
                "city": None,
                "coverage_type": "province",
                "coverage_areas": [{"province": "江苏", "city": None}],
                "distributor_id": distributor.id,
                "status": "active",
            },
        )

    store = (
        await db.execute(select(Store).where(Store.tenant_id == tenant_id, Store.code == "DEMO-STORE-NJDL"))
    ).scalar_one_or_none()
    if not store:
        store = await channel_authority.create_store(
            db,
            tenant_id,
            idempotency_key=_channel_seed_idem(tenant_id, "create-store", "DEMO-STORE-NJDL"),
            payload={
                "name": "南京东路店",
                "code": "DEMO-STORE-NJDL",
                "region_id": region.id,
                "distributor_id": distributor.id,
                "address": "上海市黄浦区南京东路",
                "status": "active",
            },
        )
    elif store.region_id != region.id or store.distributor_id != distributor.id or store.status != "active":
        store = await channel_authority.update_store(
            db,
            tenant_id,
            store.id,
            expected_version=store.version,
            idempotency_key=_channel_seed_idem(tenant_id, "update-store", f"{store.id}:v{store.version}"),
            changes={"region_id": region.id, "distributor_id": distributor.id, "status": "active"},
        )

    batch_id = code_items[0].code_batch_id if code_items else None
    if batch_id:
        code_batch = (await db.execute(select(CodeBatch).where(CodeBatch.id == batch_id))).scalar_one_or_none()
        if code_batch:
            if code_batch.distributor_id != distributor.id or code_batch.region_id != region.id:
                await channel_authority.assign_batch(
                    db,
                    tenant_id,
                    code_batch.id,
                    idempotency_key=_channel_seed_idem(
                        tenant_id, "assign-batch", f"{code_batch.id}:{distributor.id}:{region.id}"
                    ),
                    distributor_id=distributor.id,
                    region_id=region.id,
                )

        existing_alloc = (
            await db.execute(
                select(CodeAllocation).where(
                    CodeAllocation.tenant_id == tenant_id,
                    CodeAllocation.batch_id == batch_id,
                    CodeAllocation.store_id == store.id,
                    CodeAllocation.effective_to.is_(None),
                    CodeAllocation.status == "active",
                )
            )
        ).scalar_one_or_none()
        if existing_alloc:
            if existing_alloc.quantity != 8:
                await channel_authority.reassign_allocation(
                    db,
                    tenant_id,
                    existing_alloc.id,
                    expected_version=existing_alloc.version,
                    idempotency_key=_channel_seed_idem(
                        tenant_id, "reassign-allocation", f"{existing_alloc.allocation_root_id}:8"
                    ),
                    target_type="store",
                    target_id=store.id,
                    quantity=8,
                    reason="official seed restores the store allocation",
                )
        else:
            await channel_authority.allocate(
                db,
                tenant_id,
                idempotency_key=_channel_seed_idem(tenant_id, "allocate-store", f"{batch_id}:{store.id}"),
                batch_id=batch_id,
                target_type="store",
                target_id=store.id,
                quantity=8,
                reason="official seed creates the store allocation",
            )
        existing_region_alloc = (
            await db.execute(
                select(CodeAllocation).where(
                    CodeAllocation.tenant_id == tenant_id,
                    CodeAllocation.batch_id == batch_id,
                    CodeAllocation.region_id == south_region.id,
                    CodeAllocation.store_id.is_(None),
                    CodeAllocation.effective_to.is_(None),
                    CodeAllocation.status == "active",
                )
            )
        ).scalar_one_or_none()
        if existing_region_alloc:
            if existing_region_alloc.quantity != 2:
                await channel_authority.reassign_allocation(
                    db,
                    tenant_id,
                    existing_region_alloc.id,
                    expected_version=existing_region_alloc.version,
                    idempotency_key=_channel_seed_idem(
                        tenant_id, "reassign-allocation", f"{existing_region_alloc.allocation_root_id}:2"
                    ),
                    target_type="region",
                    target_id=south_region.id,
                    quantity=2,
                    reason="official seed restores the regional allocation",
                )
        else:
            await channel_authority.allocate(
                db,
                tenant_id,
                idempotency_key=_channel_seed_idem(tenant_id, "allocate-region", f"{batch_id}:{south_region.id}"),
                batch_id=batch_id,
                target_type="region",
                target_id=south_region.id,
                quantity=2,
                reason="official seed creates the regional allocation",
            )

    dist_account = next((account for account in accounts if account.email == "dist@demo.com"), None)
    store_account = next((account for account in accounts if account.email == "store@demo.com"), None)
    if dist_account:
        existing_scope = await db.scalar(
            select(AccountChannelScope).where(
                AccountChannelScope.tenant_id == tenant_id,
                AccountChannelScope.account_id == dist_account.id,
                AccountChannelScope.scope_type == "distributor",
            )
        )
        if existing_scope is None or existing_scope.target_id != distributor.id:
            await channel_authority.set_scope(
                db,
                tenant_id,
                idempotency_key=_channel_seed_idem(
                    tenant_id, "scope-distributor", f"{dist_account.id}:{distributor.id}"
                ),
                account_id=dist_account.id,
                scope_type="distributor",
                target_id=distributor.id,
            )
    if store_account:
        existing_scope = await db.scalar(
            select(AccountChannelScope).where(
                AccountChannelScope.tenant_id == tenant_id,
                AccountChannelScope.account_id == store_account.id,
                AccountChannelScope.scope_type == "store",
            )
        )
        if existing_scope is None or existing_scope.target_id != store.id:
            await channel_authority.set_scope(
                db,
                tenant_id,
                idempotency_key=_channel_seed_idem(tenant_id, "scope-store", f"{store_account.id}:{store.id}"),
                account_id=store_account.id,
                scope_type="store",
                target_id=store.id,
            )

    return distributor, region


@app.command()
def tenant(
    name: str = typer.Option(..., help="租户名称"),
    slug: str = typer.Option(..., help="租户标识"),
    admin_email: str = typer.Option(DEFAULT_TENANT_ADMIN_EMAIL, help="管理员邮箱"),
    admin_name: str = typer.Option("Admin", help="管理员姓名"),
    admin_password: str = typer.Option(DEFAULT_TENANT_ADMIN_PASSWORD, help="管理员密码"),
    allow_production: bool = typer.Option(False, "--allow-production", help="明确允许在 production 环境运行"),
):
    """创建租户及默认组织和 admin 账号"""

    canonical_admin_email = normalize_email(admin_email)
    _guard_seed_mutation(
        command="tenant seed",
        target=slug,
        allow_production=allow_production,
        unsafe_default_identity=(
            canonical_admin_email == normalize_email(DEFAULT_TENANT_ADMIN_EMAIL)
            or admin_password == DEFAULT_TENANT_ADMIN_PASSWORD
        ),
    )

    async def _run():
        tenant_id, created = await _ensure_tenant(
            name=name,
            slug=slug,
            admin_email=canonical_admin_email,
            admin_name=admin_name,
            admin_password=admin_password,
        )
        if not created:
            typer.echo(f"Tenant '{slug}' already exists (id={tenant_id})")
            return
        typer.echo(f"Created tenant: {name} (id={tenant_id}, slug={slug})")

    asyncio.run(_run())


@app.command()
def product(
    tenant: str = typer.Option(..., help="租户 slug"),
    brand: str = typer.Option(..., help="品牌名称"),
    product_name: str = typer.Option(..., help="产品名称"),
    sku: str = typer.Option(..., help="SKU 编码"),
    allow_production: bool = typer.Option(False, "--allow-production", help="明确允许在 production 环境运行"),
):
    """创建完整产品链（品牌 → 产品 → SKU）"""

    _guard_seed_mutation(command="product seed", target=tenant, allow_production=allow_production)

    async def _run():
        tenant_id = await _find_tenant_id(tenant)
        if tenant_id is None:
            typer.echo(f"Tenant '{tenant}' not found", err=True)
            raise typer.Exit(code=1)
        async with async_session() as db:
            await _open_tenant_scope(db, tenant_id)
            b = await create_brand_if_needed(db, tenant_id, brand)
            p = await create_product_if_needed(db, tenant_id, b.id, product_name)
            s = await create_sku_if_needed(db, tenant_id, p.id, sku, f"{product_name}-{sku}")
            await db.commit()

            typer.echo(f"Brand: {b.name} (id={b.id})")
            typer.echo(f"Product: {p.name} (id={p.id})")
            typer.echo(f"SKU: {s.code} (id={s.id})")

    asyncio.run(_run())


@app.command()
def code(
    tenant: str = typer.Option(..., help="租户 slug"),
    batch_code: str = typer.Option(..., help="批次编码"),
    count: int = typer.Option(100, help="生成数量"),
    allow_production: bool = typer.Option(False, "--allow-production", help="明确允许在 production 环境运行"),
):
    """生成码批次"""

    _guard_seed_mutation(command="code seed", target=tenant, allow_production=allow_production)

    async def _run():
        tenant_id = await _find_tenant_id(tenant)
        if tenant_id is None:
            typer.echo(f"Tenant '{tenant}' not found", err=True)
            raise typer.Exit(code=1)
        async with async_session() as db:
            await _open_tenant_scope(db, tenant_id)
            generated = await _generate_codes(db, tenant_id, batch_code, count)
            await db.commit()
            typer.echo(f"Generated {generated} codes for batch '{batch_code}'")

    asyncio.run(_run())


@app.command()
def all(
    name: str = typer.Option("青岭良仓演示租户", help="租户名称"),
    slug: str = typer.Option("demo", help="租户标识"),
    admin_email: str = typer.Option("admin@demo.com", help="管理员邮箱"),
    admin_name: str = typer.Option("品牌管理员", help="管理员姓名"),
    admin_password: str = typer.Option("Admin1234", help="管理员密码"),
    brand: str = typer.Option("青岭良仓", help="品牌名称"),
    product_name: str = typer.Option("五常稻花香大米 5kg", help="产品名称"),
    sku: str = typer.Option("RICE-5KG-001", help="SKU 编码"),
    allow_production: bool = typer.Option(False, "--allow-production", help="明确允许向 production 环境写入演示数据"),
    allow_non_demo_target: bool = typer.Option(
        False,
        "--allow-non-demo-target",
        help="明确允许演示 seed 写入非 demo slug",
    ),
):
    """一键创建全部演示数据（租户 + 角色账号 + 产品 + 码 + 页面 + 活动 + 扫码数据）"""

    canonical_admin_email = normalize_email(admin_email)
    _guard_seed_mutation(
        command="all seed",
        target=slug,
        allow_production=allow_production,
        demo_only=True,
        allow_non_demo_target=allow_non_demo_target,
        forbid_production=True,
    )

    async def _run():
        tenant_id, created = await _ensure_tenant(
            name=name,
            slug=slug,
            admin_email=canonical_admin_email,
            admin_name=admin_name,
            admin_password=admin_password,
        )
        if created:
            typer.echo(f"Created tenant: {name} (id={tenant_id})")
        else:
            typer.echo(f"Tenant '{slug}' already exists (id={tenant_id})")

        admin_id = await _ensure_committed_demo_admin(
            tenant_id,
            admin_email=canonical_admin_email,
            admin_name=admin_name,
            admin_password=admin_password,
        )
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(
                cli_lifecycle_auth_context(
                    control_session,
                    tenant_id=tenant_id,
                    account_id=admin_id,
                )
            )
            db = await stack.enter_async_context(async_session())
            # Tenant creation/discovery above is the only control-plane stage.
            # All demo business rows are written through a restricted runtime
            # transaction scoped to exactly that tenant.
            await lock_quota_rollout_state(db)
            t = await _open_tenant_scope(db, tenant_id)
            _enable_demo_features(t)
            await _ensure_demo_accounts(
                db,
                t.id,
                (await _get_default_org(db, t.id)).id,
                admin_email=canonical_admin_email,
                admin_name=admin_name,
                admin_password=admin_password,
            )

            # 2. 创建品牌 + 产品 + SKU
            b = await create_brand_if_needed(db, t.id, brand)
            b.description = "客户演示品牌：主打产地可信、扫码领券和复购私域承接。"
            typer.echo(f"Brand: {b.name} (id={b.id})")
            p = await create_product_if_needed(db, t.id, b.id, product_name)
            p.category = "大米"
            p.description = "五常核心产区稻花香大米，适合演示溯源、检测报告、首扫福利和复购归因。"
            typer.echo(f"Product: {p.name} (id={p.id})")
            s = await create_sku_if_needed(db, t.id, p.id, sku, f"{product_name}-{sku}")
            s.specifications = {"净含量": "5kg", "产地": "黑龙江五常", "包装": "礼盒装"}
            typer.echo(f"SKU: {s.code} (id={s.id})")

            production_batch = await _ensure_production_batch(db, t.id, p.id, s.id)
            await _ensure_page(db, t.id, p.id, admin_id)
            await _ensure_campaign(db, t.id, p.id)
            code_items = await _ensure_demo_codes(
                db,
                t.id,
                p.id,
                s.id,
                production_batch.id,
                admin_id,
            )
            activated_public_ids = [item.public_id for item in code_items if item.status == CodeItemStatus.activated]
            accounts = list((await db.scalars(select(Account).where(Account.tenant_id == t.id))).all())
            distributor, region = await _ensure_demo_channels(db, t.id, accounts, code_items)
            await refresh_quota_usage_from_authoritative_rows(db, t.id)
            await db.commit()

            # Historical demo scans are synthetic control-plane fixtures, not
            # resolver-authoritative runtime writes. Keep runtime table DML
            # revoked and bind the control session to the exact tenant.
            diversion_item = next(
                (item for item in code_items if item.status == CodeItemStatus.activated),
                None,
            )
            await _ensure_demo_scan_history_and_quota(
                t.id,
                activated_public_ids,
                diversion_item.public_id if diversion_item else None,
            )
            if diversion_item:
                await set_session_tenant_context(db, t.id)
                scan_event = await db.scalar(
                    select(ScanEvent).where(
                        ScanEvent.tenant_id == t.id,
                        ScanEvent.id == _demo_diversion_uuid(t.id, "scan"),
                        ScanEvent.public_id == diversion_item.public_id,
                    )
                )
                if scan_event is None:
                    raise RuntimeError("Committed official diversion scan fact is unavailable")
                await diversion_authority.record_observation(
                    db,
                    t.id,
                    observation_id=_demo_diversion_uuid(t.id, "observation"),
                    scan_event_id=scan_event.id,
                    scan_time=scan_event.scan_time,
                    idempotency_key=f"official-seed-diversion-v1-{t.id.hex}",
                    public_id=diversion_item.public_id,
                    code_item_id=diversion_item.id,
                    ip_hash=scan_event.ip_hash,
                    detected_city="北京",
                    expected_region="上海",
                    location_source="ip_inference",
                    location_accuracy="medium",
                    location_authorized=None,
                    distributor_id=distributor.id,
                    region_id=region.id,
                    rule_name="cross_region_ip",
                    confidence="high",
                )
                await db.commit()

        typer.echo("\nDemo seed complete. Quick login accounts:")
        for account in _demo_accounts_for_request(
            admin_email=canonical_admin_email,
            admin_name=admin_name,
            admin_password=admin_password,
        ):
            typer.echo(f"  {account['title']}: {account['email']} / {account['password']} ({account['role']})")
        active_code = next((item.public_id for item in code_items if item.status == CodeItemStatus.activated), None)
        revoked_code = next((item.public_id for item in code_items if item.status == CodeItemStatus.revoked), None)
        frozen_code = next((item.public_id for item in code_items if item.status == CodeItemStatus.frozen), None)
        if active_code:
            typer.echo(f"\nDemo scan URL: /c/{active_code}")
        if revoked_code:
            typer.echo(f"Revoked-code URL: /c/{revoked_code}")
        if frozen_code:
            typer.echo(f"Frozen-code URL: /c/{frozen_code}")

    asyncio.run(_run())


async def create_brand_if_needed(db: AsyncSession, tenant_id: uuid.UUID, name: str) -> Brand:
    result = await db.execute(select(Brand).where(Brand.tenant_id == tenant_id, Brand.name == name))
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    from app.services.product import create_brand as _create_brand

    return await _create_brand(db, tenant_id, name)


async def create_product_if_needed(db: AsyncSession, tenant_id: uuid.UUID, brand_id: uuid.UUID, name: str) -> Product:
    result = await db.execute(select(Product).where(Product.tenant_id == tenant_id, Product.name == name))
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    from app.services.product import create_product as _create_product

    return await _create_product(db, tenant_id, brand_id, name)


async def create_sku_if_needed(
    db: AsyncSession, tenant_id: uuid.UUID, product_id: uuid.UUID, code: str, name: str
) -> SKU:
    result = await db.execute(select(SKU).where(SKU.product_id == product_id, SKU.code == code))
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    from app.services.product import create_sku as _create_sku

    return await _create_sku(db, tenant_id, product_id, code, name)


@app.command()
def demo(
    clean: bool = typer.Option(False, "--clean", help="清理现有演示数据后重新生成"),
    allow_production: bool = typer.Option(False, "--allow-production", help="明确允许在 production 环境运行"),
):
    """生成丰富演示数据（委托 scripts/seed_demo.py）"""
    import subprocess
    import sys
    from pathlib import Path

    _guard_seed_mutation(
        command="demo seed",
        target="demo",
        allow_production=allow_production,
        forbid_production=True,
    )

    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "seed_demo.py"
    if not script.exists():
        typer.echo(f"Demo script not found: {script}", err=True)
        raise typer.Exit(code=1)

    cmd = [sys.executable, str(script), "reset" if clean else "generate", "--target", "demo"]
    result = subprocess.run(cmd, cwd=str(script.parent.parent))
    raise typer.Exit(code=result.returncode)


if __name__ == "__main__":
    app()
