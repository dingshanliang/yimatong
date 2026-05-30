"""CRM 消费者匹配与 SyncMapping 管理模块。

匹配优先级：
1. 通过 SyncMapping 的 external_id 直接匹配（最快）
2. 通过 phone_encrypted 解密后调 CRM API 查手机号匹配
3. 无匹配 → 视为新消费者
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.member import ConsumerProfile
from app.models.sync_mapping import SyncMapping
from app.utils.crypto import CryptoError, decrypt_phone


class MatchResult:
    __slots__ = ("consumer", "mapping", "is_new", "match_method")

    def __init__(
        self,
        consumer: ConsumerProfile | None,
        mapping: SyncMapping | None,
        is_new: bool,
        match_method: str,
    ):
        self.consumer = consumer
        self.mapping = mapping
        self.is_new = is_new
        self.match_method = match_method


async def match_by_external_id(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    source_system: str,
    external_id: str,
) -> MatchResult | None:
    """通过 external_id 在 SyncMapping 中查找已有映射。"""
    result = await db.execute(
        select(SyncMapping).where(
            SyncMapping.tenant_id == tenant_id,
            SyncMapping.source_system == source_system,
            SyncMapping.external_id == external_id,
            SyncMapping.local_entity_type == "consumer_profile",
        )
    )
    mapping = result.scalar_one_or_none()
    if not mapping:
        return None

    cresult = await db.execute(
        select(ConsumerProfile).where(
            ConsumerProfile.tenant_id == tenant_id,
            ConsumerProfile.id == mapping.local_entity_id,
        )
    )
    consumer = cresult.scalar_one_or_none()
    return MatchResult(
        consumer=consumer,
        mapping=mapping,
        is_new=False,
        match_method="external_id",
    )


async def match_by_phone(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    phone_plain: str,
) -> ConsumerProfile | None:
    """通过手机号在一码通 ConsumerProfile 中查找匹配。"""
    from app.utils.crypto import hash_phone

    phone_hash = hash_phone(phone_plain)
    result = await db.execute(
        select(ConsumerProfile).where(
            ConsumerProfile.tenant_id == tenant_id,
            ConsumerProfile.phone_hash == phone_hash,
        )
    )
    return result.scalar_one_or_none()


async def decrypt_and_match_phone(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer: ConsumerProfile,
) -> str | None:
    """解密消费者的 phone_encrypted 并返回明文手机号。"""
    if not consumer.phone_encrypted:
        return None
    try:
        return decrypt_phone(consumer.phone_encrypted)
    except CryptoError:
        return None


async def create_or_update_mapping(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    source_system: str,
    external_id: str,
    external_phone_hash: str | None = None,
    sync_direction: str = "bidirectional",
) -> SyncMapping:
    """创建或更新 SyncMapping 记录。"""
    result = await db.execute(
        select(SyncMapping).where(
            SyncMapping.tenant_id == tenant_id,
            SyncMapping.source_system == source_system,
            SyncMapping.external_id == external_id,
        )
    )
    mapping = result.scalar_one_or_none()

    if mapping:
        mapping.local_entity_id = consumer_id
        mapping.last_synced_at = datetime.now(UTC)
        if external_phone_hash:
            mapping.external_phone_hash = external_phone_hash
    else:
        mapping = SyncMapping(
            tenant_id=tenant_id,
            local_entity_type="consumer_profile",
            local_entity_id=consumer_id,
            source_system=source_system,
            external_id=external_id,
            external_phone_hash=external_phone_hash,
            sync_direction=sync_direction,
            last_synced_at=datetime.now(UTC),
        )
        db.add(mapping)

    await db.flush()
    return mapping


async def get_mappings_for_consumer(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
) -> list[SyncMapping]:
    """获取消费者的所有外部系统映射。"""
    result = await db.execute(
        select(SyncMapping).where(
            SyncMapping.tenant_id == tenant_id,
            SyncMapping.local_entity_type == "consumer_profile",
            SyncMapping.local_entity_id == consumer_id,
        )
    )
    return list(result.scalars().all())
