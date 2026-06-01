"""渠道服务层：经销商/区域/门店 CRUD + 码段分配 + 窜货检测"""

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import AccountChannelScope, CodeAllocation, Distributor, DiversionClue, Region, Store
from app.models.code import CodeBatch, CodeItem
from app.models.product import SKU, Product
from app.utils import utcnow


def _like(value: str) -> str:
    return f"%{value.strip()}%"


def _dt(value) -> str | None:
    return value.isoformat() if value else None


async def _sum_allocated(db: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID) -> int:
    total = (
        await db.execute(
            select(func.coalesce(func.sum(CodeAllocation.quantity), 0)).where(
                CodeAllocation.tenant_id == tenant_id,
                CodeAllocation.batch_id == batch_id,
            )
        )
    ).scalar()
    return int(total or 0)


async def get_channel_overview(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    distributor_count = (
        await db.execute(select(func.count()).select_from(Distributor).where(Distributor.tenant_id == tenant_id))
    ).scalar() or 0
    region_count = (
        await db.execute(select(func.count()).select_from(Region).where(Region.tenant_id == tenant_id))
    ).scalar() or 0
    store_count = (
        await db.execute(
            select(func.count()).select_from(Store).where(Store.tenant_id == tenant_id, Store.status == "active")
        )
    ).scalar() or 0
    allocated_quantity = (
        await db.execute(
            select(func.coalesce(func.sum(CodeAllocation.quantity), 0)).where(CodeAllocation.tenant_id == tenant_id)
        )
    ).scalar() or 0
    pending_diversion_count = (
        await db.execute(
            select(func.count())
            .select_from(DiversionClue)
            .where(
                DiversionClue.tenant_id == tenant_id,
                DiversionClue.resolved.is_(False),
            )
        )
    ).scalar() or 0
    return {
        "distributor_count": distributor_count,
        "region_count": region_count,
        "store_count": store_count,
        "allocated_quantity": int(allocated_quantity),
        "pending_diversion_count": pending_diversion_count,
    }


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
    q: str | None = None,
    status: str | None = None,
) -> tuple[list[Distributor], int]:
    conditions = [Distributor.tenant_id == tenant_id]
    if q:
        pattern = _like(q)
        conditions.append(or_(Distributor.name.ilike(pattern), Distributor.code.ilike(pattern)))
    if status:
        conditions.append(Distributor.status == status)

    total = (await db.execute(select(func.count()).select_from(Distributor).where(*conditions))).scalar() or 0
    rows = (
        (
            await db.execute(
                select(Distributor)
                .where(*conditions)
                .order_by(Distributor.updated_at.desc(), Distributor.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def get_distributor_stats(db: AsyncSession, tenant_id: uuid.UUID, distributor_ids: list[uuid.UUID]) -> dict:
    if not distributor_ids:
        return {}
    stats = {did: {"region_count": 0, "store_count": 0, "allocated_quantity": 0} for did in distributor_ids}

    region_rows = (
        await db.execute(
            select(Region.distributor_id, func.count().label("count"))
            .where(Region.tenant_id == tenant_id, Region.distributor_id.in_(distributor_ids))
            .group_by(Region.distributor_id)
        )
    ).all()
    for row in region_rows:
        stats[row.distributor_id]["region_count"] = row.count

    store_rows = (
        await db.execute(
            select(Store.distributor_id, func.count().label("count"))
            .where(Store.tenant_id == tenant_id, Store.distributor_id.in_(distributor_ids), Store.status == "active")
            .group_by(Store.distributor_id)
        )
    ).all()
    for row in store_rows:
        stats[row.distributor_id]["store_count"] = row.count

    alloc_rows = (
        await db.execute(
            select(CodeAllocation.distributor_id, func.coalesce(func.sum(CodeAllocation.quantity), 0).label("quantity"))
            .where(CodeAllocation.tenant_id == tenant_id, CodeAllocation.distributor_id.in_(distributor_ids))
            .group_by(CodeAllocation.distributor_id)
        )
    ).all()
    for row in alloc_rows:
        stats[row.distributor_id]["allocated_quantity"] = int(row.quantity or 0)
    return stats


async def update_distributor(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    distributor_id: uuid.UUID,
    name: str | None = None,
    status: str | None = None,
) -> Distributor | None:
    result = await db.execute(
        select(Distributor).where(Distributor.id == distributor_id, Distributor.tenant_id == tenant_id)
    )
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
    q: str | None = None,
    status: str | None = None,
    distributor_id: uuid.UUID | None = None,
) -> tuple[list[Region], int]:
    conditions = [Region.tenant_id == tenant_id]
    if q:
        pattern = _like(q)
        conditions.append(or_(Region.name.ilike(pattern), Region.code.ilike(pattern), Region.city.ilike(pattern)))
    if status:
        conditions.append(Region.status == status)
    if distributor_id:
        conditions.append(Region.distributor_id == distributor_id)

    total = (await db.execute(select(func.count()).select_from(Region).where(*conditions))).scalar() or 0
    rows = (
        (
            await db.execute(
                select(Region)
                .where(*conditions)
                .order_by(Region.updated_at.desc(), Region.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def get_region_stats(db: AsyncSession, tenant_id: uuid.UUID, regions: list[Region]) -> dict:
    if not regions:
        return {}
    region_ids = [r.id for r in regions]
    distributor_ids = [r.distributor_id for r in regions if r.distributor_id]
    stats = {rid: {"store_count": 0, "distributor_name": None} for rid in region_ids}

    if distributor_ids:
        dist_rows = (
            (
                await db.execute(
                    select(Distributor).where(Distributor.tenant_id == tenant_id, Distributor.id.in_(distributor_ids))
                )
            )
            .scalars()
            .all()
        )
        names = {d.id: d.name for d in dist_rows}
        for region in regions:
            if region.distributor_id:
                stats[region.id]["distributor_name"] = names.get(region.distributor_id)

    store_rows = (
        await db.execute(
            select(Store.region_id, func.count().label("count"))
            .where(Store.tenant_id == tenant_id, Store.region_id.in_(region_ids), Store.status == "active")
            .group_by(Store.region_id)
        )
    ).all()
    for row in store_rows:
        stats[row.region_id]["store_count"] = row.count
    return stats


async def update_region(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    region_id: uuid.UUID,
    name: str | None = None,
    distributor_id: uuid.UUID | None = None,
    status: str | None = None,
) -> Region | None:
    result = await db.execute(select(Region).where(Region.id == region_id, Region.tenant_id == tenant_id))
    region = result.scalar_one_or_none()
    if not region:
        return None
    if name is not None:
        region.name = name
    if distributor_id is not None:
        region.distributor_id = distributor_id
    if status is not None:
        region.status = status
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


async def list_stores(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    region_id: uuid.UUID | None = None,
    distributor_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    status: str | None = "active",
) -> tuple[list[Store], int]:
    conditions = [Store.tenant_id == tenant_id]
    if status:
        conditions.append(Store.status == status)
    if region_id:
        conditions.append(Store.region_id == region_id)
    if distributor_id:
        conditions.append(Store.distributor_id == distributor_id)
    if q:
        pattern = _like(q)
        conditions.append(or_(Store.name.ilike(pattern), Store.code.ilike(pattern), Store.address.ilike(pattern)))

    total = (await db.execute(select(func.count()).select_from(Store).where(*conditions))).scalar() or 0
    rows = (
        (
            await db.execute(
                select(Store)
                .where(*conditions)
                .order_by(Store.updated_at.desc(), Store.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def get_store_stats(db: AsyncSession, tenant_id: uuid.UUID, stores: list[Store]) -> dict:
    if not stores:
        return {}
    store_ids = [s.id for s in stores]
    region_ids = [s.region_id for s in stores if s.region_id]
    distributor_ids = [s.distributor_id for s in stores if s.distributor_id]
    stats = {sid: {"region_name": None, "distributor_name": None, "allocated_quantity": 0} for sid in store_ids}

    if region_ids:
        regions = (
            (await db.execute(select(Region).where(Region.tenant_id == tenant_id, Region.id.in_(region_ids))))
            .scalars()
            .all()
        )
        names = {r.id: r.name for r in regions}
        for store in stores:
            if store.region_id:
                stats[store.id]["region_name"] = names.get(store.region_id)

    if distributor_ids:
        dists = (
            (
                await db.execute(
                    select(Distributor).where(Distributor.tenant_id == tenant_id, Distributor.id.in_(distributor_ids))
                )
            )
            .scalars()
            .all()
        )
        names = {d.id: d.name for d in dists}
        for store in stores:
            if store.distributor_id:
                stats[store.id]["distributor_name"] = names.get(store.distributor_id)

    alloc_rows = (
        await db.execute(
            select(CodeAllocation.store_id, func.coalesce(func.sum(CodeAllocation.quantity), 0).label("quantity"))
            .where(CodeAllocation.tenant_id == tenant_id, CodeAllocation.store_id.in_(store_ids))
            .group_by(CodeAllocation.store_id)
        )
    ).all()
    for row in alloc_rows:
        stats[row.store_id]["allocated_quantity"] = int(row.quantity or 0)
    return stats


async def update_store(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    name: str | None = None,
    region_id: uuid.UUID | None = None,
    distributor_id: uuid.UUID | None = None,
    address: str | None = None,
    status: str | None = None,
) -> Store | None:
    result = await db.execute(select(Store).where(Store.id == store_id, Store.tenant_id == tenant_id))
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


async def delete_store(db: AsyncSession, tenant_id: uuid.UUID, store_id: uuid.UUID) -> bool:
    result = await db.execute(select(Store).where(Store.id == store_id, Store.tenant_id == tenant_id))
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
    batch = (
        await db.execute(select(CodeBatch).where(CodeBatch.id == batch_id, CodeBatch.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if not batch:
        return None

    store = (
        await db.execute(select(Store).where(Store.id == store_id, Store.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if not store:
        return None

    allocated = await _sum_allocated(db, tenant_id, batch_id)
    remaining = batch.quantity - allocated
    if quantity > remaining:
        raise ValueError(f"分配数量超过当前剩余码量，剩余 {remaining} 个")

    alloc = CodeAllocation(
        tenant_id=tenant_id,
        batch_id=batch_id,
        store_id=store_id,
        distributor_id=store.distributor_id,
        quantity=quantity,
        allocated_at=utcnow().replace(microsecond=0).isoformat(),
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
    distributor_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CodeAllocation], int]:
    """查询码段分配记录"""
    conditions = [CodeAllocation.tenant_id == tenant_id]
    if batch_id:
        conditions.append(CodeAllocation.batch_id == batch_id)
    if store_id:
        conditions.append(CodeAllocation.store_id == store_id)
    if distributor_id:
        conditions.append(CodeAllocation.distributor_id == distributor_id)

    total = (await db.execute(select(func.count()).select_from(CodeAllocation).where(*conditions))).scalar() or 0
    rows = (
        (
            await db.execute(
                select(CodeAllocation)
                .where(*conditions)
                .order_by(CodeAllocation.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def allocation_to_dict(db: AsyncSession, tenant_id: uuid.UUID, alloc: CodeAllocation) -> dict:
    batch = (
        await db.execute(select(CodeBatch).where(CodeBatch.id == alloc.batch_id, CodeBatch.tenant_id == tenant_id))
    ).scalar_one_or_none()
    store = None
    distributor = None
    region = None
    product = None
    sku = None

    if alloc.store_id:
        store = (
            await db.execute(select(Store).where(Store.id == alloc.store_id, Store.tenant_id == tenant_id))
        ).scalar_one_or_none()
    if alloc.distributor_id:
        distributor = (
            await db.execute(
                select(Distributor).where(Distributor.id == alloc.distributor_id, Distributor.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
    if store and store.region_id:
        region = (
            await db.execute(select(Region).where(Region.id == store.region_id, Region.tenant_id == tenant_id))
        ).scalar_one_or_none()
    if batch:
        product = (
            await db.execute(select(Product).where(Product.id == batch.product_id, Product.tenant_id == tenant_id))
        ).scalar_one_or_none()
        sku = (
            await db.execute(select(SKU).where(SKU.id == batch.sku_id, SKU.tenant_id == tenant_id))
        ).scalar_one_or_none()
    allocated = await _sum_allocated(db, tenant_id, alloc.batch_id)
    batch_quantity = batch.quantity if batch else 0
    return {
        "id": str(alloc.id),
        "batch_id": str(alloc.batch_id),
        "batch_code": batch.batch_code if batch else None,
        "batch_quantity": batch_quantity,
        "allocated_quantity": allocated,
        "remaining_quantity": max(batch_quantity - allocated, 0),
        "store_id": str(alloc.store_id) if alloc.store_id else None,
        "store_name": store.name if store else None,
        "distributor_id": str(alloc.distributor_id) if alloc.distributor_id else None,
        "distributor_name": distributor.name if distributor else None,
        "region_id": str(region.id) if region else None,
        "region_name": region.name if region else None,
        "product_name": product.name if product else None,
        "sku_name": sku.name if sku else None,
        "quantity": alloc.quantity,
        "allocated_at": alloc.allocated_at,
    }


async def resolve_store_for_code(
    db: AsyncSession,
    public_id: str,
) -> dict | None:
    """扫码时自动匹配门店归属"""
    item = (await db.execute(select(CodeItem).where(CodeItem.public_id == public_id))).scalar_one_or_none()
    if not item:
        return None

    alloc = (
        await db.execute(
            select(CodeAllocation)
            .where(CodeAllocation.batch_id == item.code_batch_id)
            .order_by(CodeAllocation.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not alloc or not alloc.store_id:
        return None

    store = (await db.execute(select(Store).where(Store.id == alloc.store_id))).scalar_one_or_none()
    if not store:
        return None

    result = {
        "store_id": str(store.id),
        "store_name": store.name,
        "store_code": store.code,
        "address": store.address,
    }

    if store.region_id:
        region = (await db.execute(select(Region).where(Region.id == store.region_id))).scalar_one_or_none()
        if region:
            result["region_name"] = region.name
            result["city"] = region.city

    if store.distributor_id:
        dist = (
            await db.execute(select(Distributor).where(Distributor.id == store.distributor_id))
        ).scalar_one_or_none()
        if dist:
            result["distributor_name"] = dist.name

    return result


# ── 账号范围和渠道入口 ─────────────────────────────


async def create_account_scope(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    scope_type: str,
    distributor_id: uuid.UUID | None = None,
    store_id: uuid.UUID | None = None,
) -> AccountChannelScope:
    if scope_type == "distributor" and not distributor_id:
        raise ValueError("经销商账号必须绑定经销商")
    if scope_type == "store" and not store_id:
        raise ValueError("门店账号必须绑定门店")

    existing = (
        await db.execute(
            select(AccountChannelScope).where(
                AccountChannelScope.tenant_id == tenant_id,
                AccountChannelScope.account_id == account_id,
                AccountChannelScope.scope_type == scope_type,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.distributor_id = distributor_id
        existing.store_id = store_id
        await db.flush()
        await db.refresh(existing)
        return existing

    scope = AccountChannelScope(
        tenant_id=tenant_id,
        account_id=account_id,
        scope_type=scope_type,
        distributor_id=distributor_id,
        store_id=store_id,
    )
    db.add(scope)
    await db.flush()
    await db.refresh(scope)
    return scope


async def list_account_scopes(db: AsyncSession, tenant_id: uuid.UUID) -> list[AccountChannelScope]:
    rows = (
        (
            await db.execute(
                select(AccountChannelScope)
                .where(AccountChannelScope.tenant_id == tenant_id)
                .order_by(AccountChannelScope.updated_at.desc(), AccountChannelScope.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def delete_account_scope(db: AsyncSession, tenant_id: uuid.UUID, scope_id: uuid.UUID) -> bool:
    scope = (
        await db.execute(
            select(AccountChannelScope).where(
                AccountChannelScope.id == scope_id,
                AccountChannelScope.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not scope:
        return False
    await db.delete(scope)
    await db.flush()
    return True


async def get_account_scope(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    scope_type: str,
) -> AccountChannelScope | None:
    return (
        await db.execute(
            select(AccountChannelScope).where(
                AccountChannelScope.tenant_id == tenant_id,
                AccountChannelScope.account_id == account_id,
                AccountChannelScope.scope_type == scope_type,
            )
        )
    ).scalar_one_or_none()


async def get_distributor_portal_summary(db: AsyncSession, tenant_id: uuid.UUID, account_id: uuid.UUID) -> dict | None:
    scope = await get_account_scope(db, tenant_id, account_id, "distributor")
    if not scope or not scope.distributor_id:
        return None
    distributor = (
        await db.execute(
            select(Distributor).where(Distributor.id == scope.distributor_id, Distributor.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not distributor:
        return None
    stats = (await get_distributor_stats(db, tenant_id, [distributor.id])).get(distributor.id, {})
    pending_clues = (
        await db.execute(
            select(func.count())
            .select_from(DiversionClue)
            .where(
                DiversionClue.tenant_id == tenant_id,
                DiversionClue.distributor_id == distributor.id,
                DiversionClue.resolved.is_(False),
            )
        )
    ).scalar() or 0
    allocations, total_allocations = await list_allocations(
        db,
        tenant_id,
        distributor_id=distributor.id,
        page=1,
        page_size=5,
    )
    return {
        "scope": {"type": "distributor", "id": str(distributor.id), "name": distributor.name},
        "region_count": stats.get("region_count", 0),
        "store_count": stats.get("store_count", 0),
        "allocated_quantity": stats.get("allocated_quantity", 0),
        "pending_diversion_count": pending_clues,
        "allocation_count": total_allocations,
        "recent_allocations": [await allocation_to_dict(db, tenant_id, alloc) for alloc in allocations],
    }


async def get_store_portal_summary(db: AsyncSession, tenant_id: uuid.UUID, account_id: uuid.UUID) -> dict | None:
    scope = await get_account_scope(db, tenant_id, account_id, "store")
    if not scope or not scope.store_id:
        return None
    store = (
        await db.execute(select(Store).where(Store.id == scope.store_id, Store.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if not store:
        return None
    stats = (await get_store_stats(db, tenant_id, [store])).get(store.id, {})
    allocations, total_allocations = await list_allocations(db, tenant_id, store_id=store.id, page=1, page_size=5)
    return {
        "scope": {"type": "store", "id": str(store.id), "name": store.name, "code": store.code},
        "address": store.address,
        "status": store.status,
        "region_name": stats.get("region_name"),
        "distributor_name": stats.get("distributor_name"),
        "allocated_quantity": stats.get("allocated_quantity", 0),
        "allocation_count": total_allocations,
        "recent_allocations": [await allocation_to_dict(db, tenant_id, alloc) for alloc in allocations],
    }


# ── 窜货检测 ──────────────────────────────────────


def _resolve_ip(ip: str) -> str | None:
    from app.services.geoip import resolve_ip_to_city

    return resolve_ip_to_city(ip)


async def get_code_expected_region(
    db: AsyncSession,
    public_id: str,
) -> dict | None:
    """获取码的归属区域信息（通过 CodeAllocation → Store → Region 链路，回退到 CodeBatch.region_id）"""
    item = (await db.execute(select(CodeItem).where(CodeItem.public_id == public_id))).scalar_one_or_none()
    if not item or not item.code_batch_id:
        return None

    alloc = (
        await db.execute(
            select(CodeAllocation)
            .where(CodeAllocation.batch_id == item.code_batch_id)
            .order_by(CodeAllocation.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if alloc and alloc.store_id:
        store = (await db.execute(select(Store).where(Store.id == alloc.store_id))).scalar_one_or_none()
        if store and store.region_id:
            region = (await db.execute(select(Region).where(Region.id == store.region_id))).scalar_one_or_none()
            if region:
                return {
                    "city": region.city,
                    "region_name": region.name,
                    "store_id": str(store.id),
                    "store_name": store.name,
                    "distributor_id": str(store.distributor_id) if store.distributor_id else None,
                }

    batch = (await db.execute(select(CodeBatch).where(CodeBatch.id == item.code_batch_id))).scalar_one_or_none()
    if batch and batch.region_id:
        region = (await db.execute(select(Region).where(Region.id == batch.region_id))).scalar_one_or_none()
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

    item = (await db.execute(select(CodeItem).where(CodeItem.public_id == public_id))).scalar_one_or_none()

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
    distributor_id: uuid.UUID | None = None,
    q: str | None = None,
) -> tuple[list[DiversionClue], int]:
    conditions = [DiversionClue.tenant_id == tenant_id]
    if resolved is not None:
        conditions.append(DiversionClue.resolved == resolved)
    if distributor_id:
        conditions.append(DiversionClue.distributor_id == distributor_id)
    if q:
        pattern = _like(q)
        conditions.append(or_(DiversionClue.public_id.ilike(pattern), DiversionClue.detected_city.ilike(pattern)))

    total = (await db.execute(select(func.count()).select_from(DiversionClue).where(*conditions))).scalar() or 0
    rows = (
        (
            await db.execute(
                select(DiversionClue)
                .where(*conditions)
                .order_by(DiversionClue.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total
