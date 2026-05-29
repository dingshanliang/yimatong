"""消费者同意记录服务"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.consent import ConsentRecord, ConsentStatus
from app.utils import utcnow


async def grant_consent(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consent_type: str,
    public_id: str | None = None,
    consumer_id: uuid.UUID | None = None,
    ip_hash: str | None = None,
) -> ConsentRecord:
    record = ConsentRecord(
        tenant_id=tenant_id,
        consumer_id=consumer_id,
        consent_type=consent_type,
        status=ConsentStatus.granted,
        public_id=public_id,
        ip_hash=ip_hash,
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)
    return record


async def withdraw_consent(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consent_id: uuid.UUID,
) -> ConsentRecord | None:
    result = await db.execute(
        select(ConsentRecord).where(
            ConsentRecord.id == consent_id,
            ConsentRecord.tenant_id == tenant_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        return None
    record.status = ConsentStatus.withdrawn
    record.withdrawn_at = utcnow()
    await db.flush()
    await db.refresh(record)
    return record
