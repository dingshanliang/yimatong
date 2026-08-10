"""码解析服务"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import bootstrap_tenant_row
from app.models.code import CodeBatch, CodeItem
from app.models.page import PageTemplate
from app.models.product import ProductionBatch
from app.services.product import effective_production_batch_status


async def resolve_public_code(
    db: AsyncSession,
    public_id: str,
) -> dict | None:
    """解析公开码，返回码信息+关联数据（使用 selectinload 预加载 CodeBatch）"""
    bootstrap_item = await bootstrap_tenant_row(
        db,
        select(CodeItem).where(CodeItem.public_id == public_id),
    )
    if bootstrap_item is None:
        return None

    result = await db.execute(
        select(CodeItem, CodeBatch, ProductionBatch)
        .join(
            CodeBatch,
            (CodeBatch.id == CodeItem.code_batch_id) & (CodeBatch.tenant_id == CodeItem.tenant_id),
        )
        .join(
            ProductionBatch,
            (ProductionBatch.id == CodeBatch.production_batch_id)
            & (ProductionBatch.tenant_id == CodeBatch.tenant_id)
            & (ProductionBatch.product_id == CodeBatch.product_id)
            & (ProductionBatch.sku_id == CodeBatch.sku_id),
        )
        .where(
            CodeItem.public_id == public_id,
            CodeBatch.tenant_id == bootstrap_item.tenant_id,
            ProductionBatch.tenant_id == bootstrap_item.tenant_id,
        )
    )
    row = result.one_or_none()
    if not row:
        return None
    item, batch, production_batch = row

    data = {
        "public_id": item.public_id,
        "status": item.status,
        "tenant_id": str(item.tenant_id),
        "code_batch_id": str(item.code_batch_id),
        "code_type": item.code_type,
        "pair_id": str(item.pair_id) if item.pair_id else None,
    }

    data["product_id"] = str(batch.product_id)
    data["sku_id"] = str(batch.sku_id)
    data["production_batch_id"] = str(production_batch.id)
    data["production_batch_status"] = effective_production_batch_status(production_batch)
    data["production_batch_recall_reason"] = production_batch.recall_reason
    data["production_batch_recalled_at"] = (
        production_batch.recalled_at.isoformat() if production_batch.recalled_at else None
    )

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
