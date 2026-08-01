"""ymt seed CLI 工具 - 创建种子数据"""

import asyncio
import uuid
from datetime import date, timedelta

import typer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.constants.campaign import BenefitType
from app.core.config import settings
from app.models.campaign import Benefit, Campaign, CampaignStatus
from app.models.channel import CodeAllocation, Distributor, DiversionClue, Region, Store
from app.models.code import CodeItem, CodeItemStatus
from app.models.connector import Connector  # noqa: F401 - register connector tables for Benefit FK sorting
from app.models.page import PageTemplate, PageTemplateStatus, PageVersion, PageVersionStatus, TemplateType
from app.models.product import SKU, Brand, Product, ProductionBatch
from app.models.scan import ScanEvent
from app.models.tenant import Account, Organization, Permission, Role, Tenant, account_roles, role_permissions
from app.services.analytics import aggregate_daily_stats
from app.services.channel import create_account_scope
from app.services.code import activate_batch, create_code_batch
from app.services.public_id import generate_public_id
from app.services.tenant import create_tenant
from app.utils import utcnow
from app.utils.auth_rbac import WEB_ROLE_PERMISSIONS
from app.utils.security import hash_password

app = typer.Typer(help="Seed data for development")

engine = create_async_engine(str(settings.database_url))
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

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

DEMO_ENABLED_FEATURES = {"channel_store": True}


async def _get_tenant_by_slug(db: AsyncSession, slug: str) -> Tenant | None:
    result = await db.execute(select(Tenant).where(Tenant.slug == slug))
    return result.scalar_one_or_none()


def _enable_demo_features(tenant: Tenant) -> None:
    tenant.enabled_features = {**(tenant.enabled_features or {}), **DEMO_ENABLED_FEATURES}


async def _generate_codes(db: AsyncSession, tenant_id: uuid.UUID, batch_code: str, count: int) -> int:
    # Placeholder - actual code generation will be in Wave A5
    return count


async def _get_default_org(db: AsyncSession, tenant_id: uuid.UUID) -> Organization:
    result = await db.execute(select(Organization).where(Organization.tenant_id == tenant_id).limit(1))
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
            await db.execute(role_permissions.insert().values(role_id=role.id, permission_id=permission.id))
    return role


async def _ensure_demo_accounts(db: AsyncSession, tenant_id: uuid.UUID, org_id: uuid.UUID) -> list[Account]:
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
    for item in DEMO_ACCOUNTS:
        result = await db.execute(select(Account).where(Account.tenant_id == tenant_id, Account.email == item["email"]))
        account = result.scalar_one_or_none()
        if account:
            account.name = item["name"]
            account.organization_id = org_id
            account.hashed_password = hash_password(item["password"])
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
        await db.execute(account_roles.delete().where(account_roles.c.account_id == account.id))
        await db.execute(account_roles.insert().values(account_id=account.id, role_id=roles[item["role"]].id))
        accounts.append(account)
    await db.flush()
    return accounts


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
        template = PageTemplate(
            tenant_id=tenant_id,
            product_id=product_id,
            name="五常稻花香扫码信任页",
            template_type=TemplateType.traceability,
            status=PageTemplateStatus.active,
            description="客户演示用 H5：溯源、检测报告、首扫福利、私域承接。",
        )
        db.add(template)
        await db.flush()

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
    if version:
        version.config_json = config
    else:
        db.add(
            PageVersion(
                tenant_id=tenant_id,
                page_template_id=template.id,
                version=1,
                config_json=config,
                status=PageVersionStatus.published,
                created_by=created_by,
            )
        )
    await db.flush()
    await db.refresh(template)
    return template


async def _ensure_campaign(db: AsyncSession, tenant_id: uuid.UUID) -> Campaign:
    now = utcnow()
    result = await db.execute(
        select(Campaign).where(Campaign.tenant_id == tenant_id, Campaign.name == "首扫领券加企微复购活动")
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        campaign = Campaign(
            tenant_id=tenant_id,
            name="首扫领券加企微复购活动",
            campaign_type="coupon",
            status=CampaignStatus.ACTIVE,
            start_at=(now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            end_at=(now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            rules_json={"first_scan_only": True, "per_person_limit": 1, "channels": ["wechat", "h5"]},
            description="客户演示活动：首扫领券，引导加企微和商城复购。",
        )
        db.add(campaign)
        await db.flush()
    result = await db.execute(select(Benefit).where(Benefit.tenant_id == tenant_id, Benefit.campaign_id == campaign.id))
    benefit = result.scalar_one_or_none()
    if benefit:
        benefit.stock_total = 1000
        benefit.stock_used = max(benefit.stock_used, 36)
    else:
        db.add(
            Benefit(
                tenant_id=tenant_id,
                campaign_id=campaign.id,
                name="20 元复购券",
                benefit_type=BenefitType.EXTERNAL_LINK,
                config_json={"url": "https://shop.example.com/demo-rice", "amount": 20, "threshold": 99},
                stock_total=1000,
                stock_used=36,
                per_person_limit=1,
                status="active",
            )
        )
    await db.flush()
    await db.refresh(campaign)
    return campaign


async def _ensure_demo_codes(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    sku_id: uuid.UUID,
    created_by: uuid.UUID,
) -> list[CodeItem]:
    from app.models.code import CodeBatch

    result = await db.execute(
        select(CodeBatch).where(CodeBatch.tenant_id == tenant_id, CodeBatch.batch_code == "DEMO-CODE-202605")
    )
    batch = result.scalar_one_or_none()
    if not batch:
        data = await create_code_batch(
            db,
            tenant_id,
            product_id,
            sku_id,
            "DEMO-CODE-202605",
            12,
            created_by,
        )
        await activate_batch(db, tenant_id, uuid.UUID(data["id"]))
        result = await db.execute(select(CodeBatch).where(CodeBatch.id == uuid.UUID(data["id"])))
        batch = result.scalar_one()

    result = await db.execute(
        select(CodeItem).where(CodeItem.tenant_id == tenant_id, CodeItem.code_batch_id == batch.id)
    )
    items = list(result.scalars().all())
    if not items:
        items = [
            CodeItem(
                tenant_id=tenant_id,
                code_batch_id=batch.id,
                public_id=generate_public_id(),
                status=CodeItemStatus.activated,
                activated_at=utcnow(),
            )
            for _ in range(12)
        ]
        db.add_all(items)
        await db.flush()
    for item in items:
        if item.status == CodeItemStatus.created:
            item.status = CodeItemStatus.activated
            item.activated_at = utcnow()
    if len(items) >= 3:
        items[1].status = CodeItemStatus.revoked
        items[1].revoked_at = utcnow()
        items[2].status = CodeItemStatus.frozen
    await db.flush()
    return items


async def _ensure_scan_events(db: AsyncSession, tenant_id: uuid.UUID, items: list[CodeItem]) -> None:
    result = await db.execute(select(func.count()).select_from(ScanEvent).where(ScanEvent.tenant_id == tenant_id))
    now = utcnow()
    if result.scalar_one() == 0:
        active_ids = [item.public_id for item in items if item.status == CodeItemStatus.activated][:4]
        events = []
        for day in range(7):
            for index, public_id in enumerate(active_ids):
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


async def _ensure_demo_channels(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    accounts: list[Account],
    code_items: list[CodeItem],
) -> None:
    from app.models.code import CodeBatch

    distributor = (
        await db.execute(
            select(Distributor).where(Distributor.tenant_id == tenant_id, Distributor.code == "DEMO-DIST-EAST")
        )
    ).scalar_one_or_none()
    if not distributor:
        distributor = Distributor(
            tenant_id=tenant_id,
            name="华东经销商",
            code="DEMO-DIST-EAST",
            contact_name="陈经理",
            status="active",
        )
        db.add(distributor)
        await db.flush()

    region = (
        await db.execute(select(Region).where(Region.tenant_id == tenant_id, Region.code == "DEMO-REG-SH"))
    ).scalar_one_or_none()
    if not region:
        region = Region(
            tenant_id=tenant_id,
            name="上海区域",
            code="DEMO-REG-SH",
            province="上海",
            city="上海",
            coverage_type="city",
            coverage_areas=[{"province": "上海", "city": "上海"}],
            distributor_id=distributor.id,
            status="active",
        )
        db.add(region)
        await db.flush()
    else:
        region.coverage_type = "city"
        region.coverage_areas = [{"province": "上海", "city": "上海"}]
        region.distributor_id = distributor.id
        region.status = "active"

    south_region = (
        await db.execute(select(Region).where(Region.tenant_id == tenant_id, Region.code == "DEMO-REG-SU"))
    ).scalar_one_or_none()
    if not south_region:
        south_region = Region(
            tenant_id=tenant_id,
            name="苏南区域",
            code="DEMO-REG-SU",
            province="江苏",
            city=None,
            coverage_type="province",
            coverage_areas=[{"province": "江苏", "city": None}],
            distributor_id=distributor.id,
            status="active",
        )
        db.add(south_region)
        await db.flush()
    else:
        south_region.city = None
        south_region.coverage_type = "province"
        south_region.coverage_areas = [{"province": "江苏", "city": None}]
        south_region.distributor_id = distributor.id
        south_region.status = "active"

    store = (
        await db.execute(select(Store).where(Store.tenant_id == tenant_id, Store.code == "DEMO-STORE-NJDL"))
    ).scalar_one_or_none()
    if not store:
        store = Store(
            tenant_id=tenant_id,
            name="南京东路店",
            code="DEMO-STORE-NJDL",
            region_id=region.id,
            distributor_id=distributor.id,
            address="上海市黄浦区南京东路",
            status="active",
        )
        db.add(store)
        await db.flush()
    else:
        store.region_id = region.id
        store.distributor_id = distributor.id
        store.status = "active"

    batch_id = code_items[0].code_batch_id if code_items else None
    if batch_id:
        code_batch = (await db.execute(select(CodeBatch).where(CodeBatch.id == batch_id))).scalar_one_or_none()
        if code_batch:
            code_batch.distributor_id = distributor.id
            code_batch.region_id = region.id

        existing_alloc = (
            await db.execute(
                select(CodeAllocation).where(
                    CodeAllocation.tenant_id == tenant_id,
                    CodeAllocation.batch_id == batch_id,
                    CodeAllocation.store_id == store.id,
                )
            )
        ).scalar_one_or_none()
        if existing_alloc:
            existing_alloc.quantity = 8
            existing_alloc.distributor_id = distributor.id
            existing_alloc.region_id = region.id
        else:
            db.add(
                CodeAllocation(
                    tenant_id=tenant_id,
                    batch_id=batch_id,
                    store_id=store.id,
                    region_id=region.id,
                    distributor_id=distributor.id,
                    quantity=8,
                    allocated_at=utcnow().replace(microsecond=0).isoformat(),
                )
            )
        existing_region_alloc = (
            await db.execute(
                select(CodeAllocation).where(
                    CodeAllocation.tenant_id == tenant_id,
                    CodeAllocation.batch_id == batch_id,
                    CodeAllocation.region_id == south_region.id,
                    CodeAllocation.store_id.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing_region_alloc:
            existing_region_alloc.quantity = 2
            existing_region_alloc.distributor_id = distributor.id
        else:
            db.add(
                CodeAllocation(
                    tenant_id=tenant_id,
                    batch_id=batch_id,
                    region_id=south_region.id,
                    distributor_id=distributor.id,
                    quantity=2,
                    allocated_at=utcnow().replace(microsecond=0).isoformat(),
                )
            )

    pending_clue = (
        await db.execute(
            select(DiversionClue).where(
                DiversionClue.tenant_id == tenant_id,
                DiversionClue.public_id == "DEMO-DIVERSION",
            )
        )
    ).scalar_one_or_none()
    if not pending_clue and code_items:
        db.add(
            DiversionClue(
                tenant_id=tenant_id,
                public_id="DEMO-DIVERSION",
                code_item_id=code_items[0].id,
                expected_region="上海",
                detected_city="北京",
                distributor_id=distributor.id,
                region_id=region.id,
                ip_hash="demo-diversion-ip",
                resolved=False,
            )
        )

    dist_account = next((account for account in accounts if account.email == "dist@demo.com"), None)
    store_account = next((account for account in accounts if account.email == "store@demo.com"), None)
    if dist_account:
        await create_account_scope(db, tenant_id, dist_account.id, "distributor", distributor_id=distributor.id)
    if store_account:
        await create_account_scope(db, tenant_id, store_account.id, "store", store_id=store.id)


@app.command()
def tenant(
    name: str = typer.Option(..., help="租户名称"),
    slug: str = typer.Option(..., help="租户标识"),
    admin_email: str = typer.Option("admin@example.com", help="管理员邮箱"),
    admin_name: str = typer.Option("Admin", help="管理员姓名"),
    admin_password: str = typer.Option("Admin1234", help="管理员密码"),
):
    """创建租户及默认组织和 admin 账号"""

    async def _run():
        async with async_session() as db:
            existing = await _get_tenant_by_slug(db, slug)
            if existing:
                typer.echo(f"Tenant '{slug}' already exists (id={existing.id})")
                return

            t = await create_tenant(
                db,
                name=name,
                slug=slug,
                admin_email=admin_email,
                admin_name=admin_name,
                admin_password=admin_password,
                plan="free",
            )
            await db.commit()
            typer.echo(f"Created tenant: {t.name} (id={t.id}, slug={slug})")

    asyncio.run(_run())


@app.command()
def product(
    tenant: str = typer.Option(..., help="租户 slug"),
    brand: str = typer.Option(..., help="品牌名称"),
    product_name: str = typer.Option(..., help="产品名称"),
    sku: str = typer.Option(..., help="SKU 编码"),
):
    """创建完整产品链（品牌 → 产品 → SKU）"""

    async def _run():
        async with async_session() as db:
            t = await _get_tenant_by_slug(db, tenant)
            if not t:
                typer.echo(f"Tenant '{tenant}' not found", err=True)
                raise typer.Exit(code=1)

            b = await create_brand_if_needed(db, t.id, brand)
            p = await create_product_if_needed(db, t.id, b.id, product_name)
            s = await create_sku_if_needed(db, t.id, p.id, sku, f"{product_name}-{sku}")
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
):
    """生成码批次"""

    async def _run():
        async with async_session() as db:
            t = await _get_tenant_by_slug(db, tenant)
            if not t:
                typer.echo(f"Tenant '{tenant}' not found", err=True)
                raise typer.Exit(code=1)

            generated = await _generate_codes(db, t.id, batch_code, count)
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
):
    """一键创建全部演示数据（租户 + 角色账号 + 产品 + 码 + 页面 + 活动 + 扫码数据）"""

    async def _run():
        async with async_session() as db:
            # 1. 创建租户
            existing = await _get_tenant_by_slug(db, slug)
            if existing:
                typer.echo(f"Tenant '{slug}' already exists (id={existing.id})")
                t = existing
            else:
                t = await create_tenant(
                    db,
                    name=name,
                    slug=slug,
                    admin_email=admin_email,
                    admin_name=admin_name,
                    admin_password=admin_password,
                    plan="free",
                )
                typer.echo(f"Created tenant: {t.name} (id={t.id})")
            _enable_demo_features(t)

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

            org = await _get_default_org(db, t.id)
            accounts = await _ensure_demo_accounts(db, t.id, org.id)
            admin_account = next((account for account in accounts if account.email == admin_email), accounts[0])
            await _ensure_production_batch(db, t.id, p.id, s.id)
            await _ensure_page(db, t.id, p.id, admin_account.id)
            await _ensure_campaign(db, t.id)
            code_items = await _ensure_demo_codes(db, t.id, p.id, s.id, admin_account.id)
            await _ensure_scan_events(db, t.id, code_items)
            await _ensure_demo_channels(db, t.id, accounts, code_items)
            await db.commit()

        typer.echo("\nDemo seed complete. Quick login accounts:")
        for account in DEMO_ACCOUNTS:
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
):
    """生成丰富演示数据（委托 scripts/seed_demo.py）"""
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "seed_demo.py"
    if not script.exists():
        typer.echo(f"Demo script not found: {script}", err=True)
        raise typer.Exit(code=1)

    cmd = [sys.executable, str(script), "reset" if clean else "generate"]
    result = subprocess.run(cmd, cwd=str(script.parent.parent))
    raise typer.Exit(code=result.returncode)


if __name__ == "__main__":
    app()
