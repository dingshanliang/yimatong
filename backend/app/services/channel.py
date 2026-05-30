"""渠道服务层：经销商/区域/门店 CRUD + 窜货检测"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import Distributor, DiversionClue, Region, Store
from app.models.code import CodeBatch, CodeItem


def _resolve_ip(ip: str) -> str | None:
    from app.services.geoip import resolve_ip_to_city

    return resolve_ip_to_city(ip)


async def create_distributor(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    code: str,
    contact_name: str | None = None,
    contact_phone: str | None = None,
) -> Distributor:
    phone_encrypted = None
    phone_hash = None
    if contact_phone:
        from app.utils.crypto import encrypt_phone, hash_phone

        phone_encrypted = encrypt_phone(contact_phone)
        phone_hash = hash_phone(contact_phone)

    dist = Distributor(
        tenant_id=tenant_id,
        name=name,
        code=code,
        contact_name=contact_name,
        contact_phone_encrypted=phone_encrypted,
        contact_phone_hash=phone_hash,
    )
    db.add(dist)
    await db.flush()
    await db.refresh(dist)
    return dist


async def list_distributors(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Distributor], int]:
    stmt = select(Distributor).where(Distributor.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(Distributor).where(Distributor.tenant_id == tenant_id)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(Distributor.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def create_region(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    code: str,
    province: str | None = None,
    city: str | None = None,
    distributor_id: uuid.UUID | None = None,
) -> Region:
    region = Region(
        tenant_id=tenant_id,
        name=name,
        code=code,
        province=province,
        city=city,
        distributor_id=distributor_id,
    )
    db.add(region)
    await db.flush()
    await db.refresh(region)
    return region


async def list_regions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Region], int]:
    stmt = select(Region).where(Region.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(Region).where(Region.tenant_id == tenant_id)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(Region.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def create_store(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    code: str,
    region_id: uuid.UUID | None = None,
    distributor_id: uuid.UUID | None = None,
    address: str | None = None,
) -> Store:
    store = Store(
        tenant_id=tenant_id,
        name=name,
        code=code,
        region_id=region_id,
        distributor_id=distributor_id,
        address=address,
    )
    db.add(store)
    await db.flush()
    await db.refresh(store)
    return store


async def assign_batch_to_channel(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    distributor_id: uuid.UUID | None = None,
    region_id: uuid.UUID | None = None,
) -> dict | None:
    """将码批次分配给经销商/区域"""
    result = await db.execute(select(CodeBatch).where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id))
    batch = result.scalar_one_or_none()
    if not batch:
        return None

    if distributor_id:
        batch.distributor_id = distributor_id
    if region_id:
        batch.region_id = region_id
    await db.flush()
    await db.refresh(batch)

    return {
        "id": str(batch.id),
        "distributor_id": str(batch.distributor_id) if batch.distributor_id else None,
        "region_id": str(batch.region_id) if batch.region_id else None,
    }


async def get_batch_expected_region(
    db: AsyncSession,
    batch_id: uuid.UUID,
) -> str | None:
    """获取批次分配的区域城市"""
    result = await db.execute(select(CodeBatch).where(CodeBatch.id == batch_id))
    batch = result.scalar_one_or_none()
    if not batch or not batch.region_id:
        return None

    region_result = await db.execute(select(Region).where(Region.id == batch.region_id))
    region = region_result.scalar_one_or_none()
    return region.city if region else None


async def check_diversion(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
    ip: str,
) -> DiversionClue | None:
    """检测窜货：扫码 IP 城市与批次分配区域不匹配"""
    detected_city = _resolve_ip(ip)
    if not detected_city:
        return None

    # 获取码项和批次
    item_result = await db.execute(select(CodeItem).where(CodeItem.public_id == public_id))
    item = item_result.scalar_one_or_none()
    if not item or not item.code_batch_id:
        return None

    expected_region = await get_batch_expected_region(db, item.code_batch_id)
    if not expected_region:
        return None

    # 城市匹配检查
    if detected_city == expected_region:
        return None

    # 生成窜货线索
    batch_result = await db.execute(select(CodeBatch).where(CodeBatch.id == item.code_batch_id))
    batch = batch_result.scalar_one_or_none()

    clue = DiversionClue(
        tenant_id=tenant_id,
        public_id=public_id,
        code_item_id=item.id,
        expected_region=expected_region,
        detected_city=detected_city,
        distributor_id=batch.distributor_id if batch else None,
    )
    db.add(clue)
    await db.flush()
    await db.refresh(clue)
    return clue


async def list_diversion_clues(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    resolved: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[DiversionClue], int]:
    stmt = select(DiversionClue).where(DiversionClue.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(DiversionClue).where(DiversionClue.tenant_id == tenant_id)

    if resolved is not None:
        stmt = stmt.where(DiversionClue.resolved == resolved)
        count_stmt = count_stmt.where(DiversionClue.resolved == resolved)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(DiversionClue.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total
