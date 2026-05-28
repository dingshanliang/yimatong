"""代运营工作台与上线检查服务"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign, CampaignStatus
from app.models.code import CodeBatch
from app.models.page import PageVersion, PageVersionStatus
from app.models.product import Brand, Product


async def get_tenant_status(
    db: AsyncSession, tenant_id: uuid.UUID,
) -> dict:
    """获取租户开通状态和初始化进度"""
    # 产品数
    products_count = await db.execute(
        select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id)
    )
    products = products_count.scalar() or 0

    # 品牌数
    brands_count = await db.execute(
        select(func.count()).select_from(Brand).where(Brand.tenant_id == tenant_id)
    )
    brands = brands_count.scalar() or 0

    # 已发布页面数
    published_pages = await db.execute(
        select(func.count()).select_from(PageVersion).where(
            PageVersion.tenant_id == tenant_id,
            PageVersion.status == PageVersionStatus.published,
        )
    )
    pages = published_pages.scalar() or 0

    # 已激活码批次数
    from app.models.code import CodeBatchStatus
    activated_batches = await db.execute(
        select(func.count()).select_from(CodeBatch).where(
            CodeBatch.tenant_id == tenant_id,
            CodeBatch.status == CodeBatchStatus.completed,
        )
    )
    activated = activated_batches.scalar() or 0

    # 已上线活动数
    active_campaigns = await db.execute(
        select(func.count()).select_from(Campaign).where(
            Campaign.tenant_id == tenant_id,
            Campaign.status == CampaignStatus.ACTIVE,
        )
    )
    campaigns = active_campaigns.scalar() or 0

    return {
        "tenant_id": str(tenant_id),
        "products": products,
        "brands": brands,
        "published_pages": pages,
        "activated_batches": activated,
        "active_campaigns": campaigns,
    }


async def get_launch_checklist(
    db: AsyncSession, tenant_id: uuid.UUID,
) -> dict:
    """获取上线检查清单"""
    status = await get_tenant_status(db, tenant_id)

    checks = [
        {
            "name": "至少 1 个产品已创建",
            "passed": status["products"] >= 1,
            "detail": f"当前产品数: {status['products']}",
        },
        {
            "name": "至少 1 个页面已发布",
            "passed": status["published_pages"] >= 1,
            "detail": f"当前已发布页面: {status['published_pages']}",
        },
        {
            "name": "至少 1 个码批次已激活",
            "passed": status["activated_batches"] >= 1,
            "detail": f"当前已激活批次: {status['activated_batches']}",
        },
        {
            "name": "至少 1 个品牌已创建",
            "passed": status["brands"] >= 1,
            "detail": f"当前品牌数: {status['brands']}",
        },
    ]

    all_passed = all(c["passed"] for c in checks)
    return {
        "tenant_id": str(tenant_id),
        "ready": all_passed,
        "checks": checks,
        "passed_count": sum(1 for c in checks if c["passed"]),
        "total_count": len(checks),
    }
