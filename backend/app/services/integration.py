"""CRM/ERP/商城集成服务"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import SyncRecord
from app.models.product import Product


async def batch_import_products(
    db: AsyncSession, tenant_id: uuid.UUID, items: list[dict],
) -> int:
    count = 0
    for item in items:
        product = Product(
            tenant_id=tenant_id,
            brand_id=uuid.UUID(item["brand_id"]),
            name=item["name"],
            category=item.get("category", ""),
        )
        db.add(product)
        count += 1
    await db.flush()
    return count


async def sync_inventory(
    db: AsyncSession, tenant_id: uuid.UUID, records: list[dict],
) -> int:
    count = 0
    for rec in records:
        sync = SyncRecord(
            tenant_id=tenant_id,
            sync_type="inventory",
            data=rec,
        )
        db.add(sync)
        count += 1
    await db.flush()
    return count


async def sync_customers(
    db: AsyncSession, tenant_id: uuid.UUID, customers: list[dict],
) -> int:
    count = 0
    for cust in customers:
        sync = SyncRecord(
            tenant_id=tenant_id,
            sync_type="customer",
            external_id=cust.get("external_id"),
            data=cust,
        )
        db.add(sync)
        count += 1
    await db.flush()
    return count
