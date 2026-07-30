"""码解析服务"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.code import CodeItem
from app.models.page import PageTemplate


async def resolve_public_code(
    db: AsyncSession,
    public_id: str,
) -> dict | None:
    """解析公开码，返回码信息+关联数据（使用 selectinload 预加载 CodeBatch）"""
    result = await db.execute(
        select(CodeItem).options(selectinload(CodeItem.code_batch)).where(CodeItem.public_id == public_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        return None

    data = {
        "public_id": item.public_id,
        "status": item.status,
        "tenant_id": str(item.tenant_id),
        "code_batch_id": str(item.code_batch_id),
        "code_type": item.code_type,
        "pair_id": str(item.pair_id) if item.pair_id else None,
    }

    # 批次已预加载
    batch = item.code_batch
    if batch:
        data["product_id"] = str(batch.product_id)
        data["sku_id"] = str(batch.sku_id)
        data["production_batch_id"] = str(batch.production_batch_id) if batch.production_batch_id else None

        # 查找产品关联的页面模板
        tmpl_result = await db.execute(
            select(PageTemplate)
            .where(
                PageTemplate.tenant_id == item.tenant_id,
                PageTemplate.product_id == batch.product_id,
                PageTemplate.status == "active",
            )
            .limit(1)
        )
        template = tmpl_result.scalar_one_or_none()
        if template:
            data["template_id"] = str(template.id)

    return data
