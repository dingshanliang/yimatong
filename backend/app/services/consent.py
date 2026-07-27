"""消费者同意记录服务"""

import uuid

from sqlalchemy import desc, select
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
    scenario: str | None = None,
    policy_version: str | None = None,
    user_agent: str | None = None,
) -> ConsentRecord:
    """授予同意。

    yimatong-zgb1.5：加合规证据链字段（scenario / policy_version / user_agent）。
    AGENTS.md §7 要求"授权场景、版本、时间、IP/UA、撤回时间"全部记录。
    """
    record = ConsentRecord(
        tenant_id=tenant_id,
        consumer_id=consumer_id,
        consent_type=consent_type,
        status=ConsentStatus.granted,
        public_id=public_id,
        ip_hash=ip_hash,
        scenario=scenario,
        policy_version=policy_version,
        user_agent=user_agent[:500] if user_agent else None,
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


async def has_active_consent(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consent_type: str,
    public_id: str | None = None,
    consumer_id: uuid.UUID | None = None,
) -> bool:
    """检查是否存在**当前有效**（granted 且未撤回）的指定类型同意。

    yimatong-zgb1.5 AC3：未同意前不采集手机号；撤回后停止使用。
    判定逻辑：查最近一条该（tenant, type, public_id/consumer_id）的 consent 记录，
    若状态为 granted → 有效；若最新一条为 withdrawn 或不存在 → 无效。

    优先级：consumer_id 命中 > public_id 命中（消费者级授权覆盖码级）。
    若都未提供，返回 False（保守：无法证明已授权）。
    """
    if not public_id and not consumer_id:
        return False

    filters = [
        ConsentRecord.tenant_id == tenant_id,
        ConsentRecord.consent_type == consent_type,
    ]
    if consumer_id is not None:
        filters.append(ConsentRecord.consumer_id == consumer_id)
    elif public_id is not None:
        filters.append(ConsentRecord.public_id == public_id)

    result = await db.execute(
        select(ConsentRecord.status).where(*filters).order_by(desc(ConsentRecord.granted_at)).limit(1)
    )
    latest_status = result.scalar_one_or_none()
    return latest_status == ConsentStatus.granted
