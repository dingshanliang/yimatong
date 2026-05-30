"""码包导出服务"""

import csv
import io
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.code import CodeItem


async def generate_code_csv(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
) -> str:
    """从数据库查询码项并生成 CSV 内容"""
    stmt = (
        select(CodeItem)
        .where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id == batch_id,
        )
        .order_by(CodeItem.id.desc())
    )
    result = await db.execute(stmt)
    items = result.scalars().all()

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "public_id",
            "status",
            "code_type",
            "code_url",
        ],
    )
    writer.writeheader()
    for item in items:
        writer.writerow(
            {
                "public_id": item.public_id,
                "status": item.status,
                "code_type": item.code_type,
                "code_url": f"https://qr.yimatong.cn/c/{item.public_id}",
            }
        )
    return output.getvalue()
