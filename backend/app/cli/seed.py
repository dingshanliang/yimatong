"""ymt seed CLI 工具 - 创建种子数据"""

import asyncio
import uuid

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.product import SKU, Brand, Product
from app.models.tenant import Tenant
from app.services.tenant import create_tenant

app = typer.Typer(help="Seed data for development")

engine = create_async_engine(str(settings.database_url))
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _get_tenant_by_slug(db: AsyncSession, slug: str) -> Tenant | None:
    result = await db.execute(select(Tenant).where(Tenant.slug == slug))
    return result.scalar_one_or_none()


async def _generate_codes(db: AsyncSession, tenant_id: uuid.UUID, batch_code: str, count: int) -> int:
    # Placeholder - actual code generation will be in Wave A5
    return count


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
    name: str = typer.Option("演示租户", help="租户名称"),
    slug: str = typer.Option("demo", help="租户标识"),
    admin_email: str = typer.Option("admin@demo.com", help="管理员邮箱"),
    admin_name: str = typer.Option("Admin", help="管理员姓名"),
    admin_password: str = typer.Option("Admin1234", help="管理员密码"),
    brand: str = typer.Option("演示品牌", help="品牌名称"),
    product_name: str = typer.Option("演示产品", help="产品名称"),
    sku: str = typer.Option("DEMO-001", help="SKU 编码"),
):
    """一键创建全部种子数据（租户 + 产品链）"""

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

            # 2. 创建品牌 + 产品 + SKU
            b = await create_brand_if_needed(db, t.id, brand)
            typer.echo(f"Brand: {b.name} (id={b.id})")
            p = await create_product_if_needed(db, t.id, b.id, product_name)
            typer.echo(f"Product: {p.name} (id={p.id})")
            s = await create_sku_if_needed(db, t.id, p.id, sku, f"{product_name}-{sku}")
            typer.echo(f"SKU: {s.code} (id={s.id})")

            await db.commit()

        typer.echo("\nSeed complete! Login with:")
        typer.echo(f"  Email:    {admin_email}")
        typer.echo(f"  Password: {admin_password}")

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


if __name__ == "__main__":
    app()
