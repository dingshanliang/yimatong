"""渠道服务层：经销商/区域/门店 CRUD + 码段分配 + 窜货检测"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import CodeAllocation, Distributor, DiversionClue, Region, Store
from app.models.code import CodeBatch, CodeItem
from app.utils import utcnow

# ── 经销商 ────────────────────────────────────────


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
    total = (await db.execute(
        select(func.count()).select_from(Distributor).where(Distributor.tenant_id == tenant_id)
    )).scalar() or 0

    rows = (await db.execute(
        select(Distributor).where(Distributor.tenant_id == tenant_id)
        .order_by(Distributor.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return list(rows), total


async def update_distributor(
    db: AsyncSession,
    distributor_id: uuid.UUID,
    name: str | None = None,
    status: str | None = None,
) -> Distributor | None:
    result = await db.execute(select(Distributor).where(Distributor.id == distributor_id))
    dist = result.scalar_one_or_none()
    if not dist:
        return None
    if name is not None:
        dist.name = name
    if status is not None:
        dist.status = status
    await db.flush()
    await db.refresh(dist)
    return dist


# ── 区域 ──────────────────────────────────────────


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
        tenant_id=tenant_id, name=name, code=code,
        province=province, city=city, distributor_id=distributor_id,
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
    total = (await db.execute(
        select(func.count()).select_from(Region).where(Region.tenant_id == tenant_id)
    )).scalar() or 0

    rows = (await db.execute(
        select(Region).where(Region.tenant_id == tenant_id)
        .order_by(Region.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return list(rows), total


async def update_region(
    db: AsyncSession,
    region_id: uuid.UUID,
    name: str | None = None,
    distributor_id: uuid.UUID | None = None,
) -> Region | None:
    result = await db.execute(select(Region).where(Region.id == region_id))
    region = result.scalar_one_or_none()
    if not region:
        return None
    if name is not None:
        region.name = name
    if distributor_id is not None:
        region.distributor_id = distributor_id
    await db.flush()
    await db.refresh(region)
    return region


# ── 门店 ──────────────────────────────────────────


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
        tenant_id=tenant_id, name=name, code=code,
        region_id=region_id, distributor_id=distributor_id, address=address,
    )
    db.add(store)
    await db.flush()
    await db.refresh(store)
    return store


async def list_stores(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    region_id: uuid.UUID | None = None,
    distributor_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Store], int]:
    conditions = [Store.tenant_id == tenant_id, Store.status == "active"]
    if region_id:
        conditions.append(Store.region_id == region_id)
    if distributor_id:
        conditions.append(Store.distributor_id == distributor_id)

    total = (await db.execute(
        select(func.count()).select_from(Store).where(*conditions)
    )).scalar() or 0

    rows = (await db.execute(
        select(Store).where(*conditions)
        .order_by(Store.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return list(rows), total


async def update_store(
    db: AsyncSession,
    store_id: uuid.UUID,
    name: str | None = None,
    region_id: uuid.UUID | None = None,
    distributor_id: uuid.UUID | None = None,
    address: str | None = None,
    status: str | None = None,
) -> Store | None:
    result = await db.execute(select(Store).where(Store.id == store_id))
    store = result.scalar_one_or_none()
    if not store:
        return None
    if name is not None:
        store.name = name
    if region_id is not None:
        store.region_id = region_id
    if distributor_id is not None:
        store.distributor_id = distributor_id
    if address is not None:
        store.address = address
    if status is not None:
        store.status = status
    await db.flush()
    await db.refresh(store)
    return store


async def delete_store(db: AsyncSession, store_id: uuid.UUID) -> bool:
    result = await db.execute(select(Store).where(Store.id == store_id))
    store = result.scalar_one_or_none()
    if not store:
        return False
    store.status = "inactive"
    await db.flush()
    return True


# ── 码段分配 ──────────────────────────────────────


async def assign_batch_to_channel(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    distributor_id: uuid.UUID | None = None,
    region_id: uuid.UUID | None = None,
) -> dict | None:
    """将码批次分配给经销商/区域（批次级）"""
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


async def allocate_codes_to_store(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    store_id: uuid.UUID,
    quantity: int,
) -> CodeAllocation | None:
    """将码批次的一部分分配给门店"""
    # 验证批次存在
    batch = (await db.execute(
        select(CodeBatch).where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if not batch:
        return None

    # 验证门店存在
    store = (await db.execute(
        select(Store).where(Store.id == store_id, Store.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if not store:
        return None

    alloc = CodeAllocation(
        tenant_id=tenant_id,
        batch_id=batch_id,
        store_id=store_id,
        distributor_id=store.distributor_id,
        quantity=quantity,
        allocated_at=utcnow().isoformat(),
    )
    db.add(alloc)
    await db.flush()
    await db.refresh(alloc)
    return alloc


async def list_allocations(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID | None = None,
    store_id: uuid.UUID | None = None,
) -> list[CodeAllocation]:
    """查询码段分配记录"""
    conditions = [CodeAllocation.tenant_id == tenant_id]
    if batch_id:
        conditions.append(CodeAllocation.batch_id == batch_id)
    if store_id:
        conditions.append(CodeAllocation.store_id == store_id)

    rows = (await db.execute(
        select(CodeAllocation).where(*conditions).order_by(CodeAllocation.id.desc())
    )).scalars().all()
    return list(rows)


async def resolve_store_for_code(
    db: AsyncSession,
    public_id: str,
) -> dict | None:
    """扫码时自动匹配门店归属"""
    # 找到码项
    item = (await db.execute(
        select(CodeItem).where(CodeItem.public_id == public_id)
    )).scalar_one_or_none()
    if not item:
        return None

    # 查找该批次的分配记录
    alloc = (await db.execute(
        select(CodeAllocation).where(CodeAllocation.batch_id == item.code_batch_id)
        .order_by(CodeAllocation.id.desc()).limit(1)
    )).scalar_one_or_none()

    if not alloc or not alloc.store_id:
        return None

    # 获取门店信息
    store = (await db.execute(
        select(Store).where(Store.id == alloc.store_id)
    )).scalar_one_or_none()

    if not store:
        return None

    result = {
        "store_id": str(store.id),
        "store_name": store.name,
        "store_code": store.code,
        "address": store.address,
    }

    # 获取区域和经销商
    if store.region_id:
        region = (await db.execute(
            select(Region).where(Region.id == store.region_id)
        )).scalar_one_or_none()
        if region:
            result["region_name"] = region.name
            result["city"] = region.city

    if store.distributor_id:
        dist = (await db.execute(
            select(Distributor).where(Distributor.id == store.distributor_id)
        )).scalar_one_or_none()
        if dist:
            result["distributor_name"] = dist.name

    return result


# ── 窜货检测 ──────────────────────────────────────


def _resolve_ip(ip: str) -> str | None:
    from app.services.geoip import resolve_ip_to_city
    return resolve_ip_to_city(ip)


async def get_code_expected_region(
    db: AsyncSession,
    public_id: str,
) -> dict | None:
    """获取码的归属区域信息（通过 CodeAllocation → Store → Region 链路，回退到 CodeBatch.region_id）"""
    item = (await db.execute(
        select(CodeItem).where(CodeItem.public_id == public_id)
    )).scalar_one_or_none()
    if not item or not item.code_batch_id:
        return None

    # 优先通过门店分配查区域
    alloc = (await db.execute(
        select(CodeAllocation).where(CodeAllocation.batch_id == item.code_batch_id)
        .order_by(CodeAllocation.id.desc()).limit(1)
    )).scalar_one_or_none()

    if alloc and alloc.store_id:
        store = (await db.execute(
            select(Store).where(Store.id == alloc.store_id)
        )).scalar_one_or_none()
        if store and store.region_id:
            region = (await db.execute(
                select(Region).where(Region.id == store.region_id)
            )).scalar_one_or_none()
            if region:
                return {
                    "city": region.city,
                    "region_name": region.name,
                    "store_id": str(store.id),
                    "store_name": store.name,
                    "distributor_id": str(store.distributor_id) if store.distributor_id else None,
                }

    # 回退：通过批次 region_id
    batch = (await db.execute(
        select(CodeBatch).where(CodeBatch.id == item.code_batch_id)
    )).scalar_one_or_none()
    if batch and batch.region_id:
        region = (await db.execute(
            select(Region).where(Region.id == batch.region_id)
        )).scalar_one_or_none()
        if region:
            return {
                "city": region.city,
                "region_name": region.name,
                "distributor_id": str(batch.distributor_id) if batch.distributor_id else None,
            }

    return None


async def check_diversion(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    public_id: str,
    ip: str,
) -> DiversionClue | None:
    """检测窜货：扫码 IP 城市与码归属区域不匹配（支持门店级和批次级两种链路）"""
    detected_city = _resolve_ip(ip)
    if not detected_city:
        return None

    expected = await get_code_expected_region(db, public_id)
    if not expected or not expected.get("city"):
        return None

    expected_region = expected["city"]
    if detected_city == expected_region:
        return None

    item = (await db.execute(
        select(CodeItem).where(CodeItem.public_id == public_id)
    )).scalar_one_or_none()

    dist_id = None
    if expected.get("distributor_id"):
        try:
            dist_id = uuid.UUID(expected["distributor_id"])
        except (ValueError, TypeError):
            pass

    clue = DiversionClue(
        tenant_id=tenant_id,
        public_id=public_id,
        code_item_id=item.id if item else uuid.Nil,
        expected_region=expected_region,
        detected_city=detected_city,
        distributor_id=dist_id,
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
    conditions = [DiversionClue.tenant_id == tenant_id]
    if resolved is not None:
        conditions.append(DiversionClue.resolved == resolved)

    total = (await db.execute(
        select(func.count()).select_from(DiversionClue).where(*conditions)
    )).scalar() or 0

    rows = (await db.execute(
        select(DiversionClue).where(*conditions)
        .order_by(DiversionClue.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return list(rows), total
