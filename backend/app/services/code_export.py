"""码包导出服务"""

import csv
import io
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.code import CodeBatch, CodeItem

# 导出上限：超过此数量分批处理
_EXPORT_ROW_LIMIT = 100_000


async def generate_code_csv(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
) -> str:
    """从数据库查询码项并生成 CSV 内容（含产品和批次信息）"""
    # 查询码批次（含关联的产品/SKU/生产批次）
    batch_result = await db.execute(
        select(CodeBatch)
        .options(
            selectinload(CodeBatch.product),
            selectinload(CodeBatch.sku),
            selectinload(CodeBatch.production_batch),
        )
        .where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id)
    )
    batch = batch_result.scalar_one_or_none()
    if not batch:
        return ""

    # 预提取批次级信息（所有码项共享）
    product_name = batch.product.name if batch.product else ""
    sku_name = batch.sku.name if batch.sku else ""
    sku_code = batch.sku.code if batch.sku else ""
    batch_code = batch.batch_code
    production_date = (
        batch.production_batch.production_date.isoformat()
        if batch.production_batch and batch.production_batch.production_date
        else ""
    )
    expiry_date = (
        batch.production_batch.expiry_date.isoformat()
        if batch.production_batch and batch.production_batch.expiry_date
        else ""
    )
    origin = batch.production_batch.origin if batch.production_batch and batch.production_batch.origin else ""

    # 查询码项（限制上限）
    stmt = (
        select(CodeItem)
        .where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id == batch_id,
        )
        .order_by(CodeItem.id.desc())
        .limit(_EXPORT_ROW_LIMIT)
    )
    result = await db.execute(stmt)
    items = result.scalars().all()

    fieldnames = [
        "public_id",
        "status",
        "code_type",
        "code_url",
        "product_name",
        "sku_name",
        "sku_code",
        "batch_code",
        "production_date",
        "expiry_date",
        "origin",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for item in items:
        writer.writerow(
            {
                "public_id": item.public_id,
                "status": item.status,
                "code_type": item.code_type,
                "code_url": f"https://qr.yimatong.cn/c/{item.public_id}",
                "product_name": product_name,
                "sku_name": sku_name,
                "sku_code": sku_code,
                "batch_code": batch_code,
                "production_date": production_date,
                "expiry_date": expiry_date,
                "origin": origin,
            }
        )

    return output.getvalue()
