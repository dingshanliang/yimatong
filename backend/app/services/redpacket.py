"""现金红包服务"""

import random
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.redpacket import KYCRecord, RedPacketClaim, RedPacketRule, Withdrawal


async def create_rule(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    total_budget: int,
    min_amount: int,
    max_amount: int,
    daily_limit_per_user: int,
    single_limit_per_user: int,
    require_kyc: bool,
    start_time: datetime,
    end_time: datetime,
) -> RedPacketRule:
    rule = RedPacketRule(
        tenant_id=tenant_id,
        name=name,
        total_budget=total_budget,
        min_amount=min_amount,
        max_amount=max_amount,
        daily_limit_per_user=daily_limit_per_user,
        single_limit_per_user=single_limit_per_user,
        require_kyc=require_kyc,
        start_time=start_time,
        end_time=end_time,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


async def list_rules(db: AsyncSession, tenant_id: uuid.UUID) -> list[RedPacketRule]:
    result = await db.execute(
        select(RedPacketRule)
        .where(RedPacketRule.tenant_id == tenant_id)
        .order_by(RedPacketRule.created_at.desc())
    )
    return list(result.scalars().all())


async def submit_kyc(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    real_name: str,
    id_number: str,
    phone: str,
) -> KYCRecord:
    kyc = KYCRecord(
        tenant_id=tenant_id,
        account_id=account_id,
        real_name=real_name,
        id_number=id_number,
        phone=phone,
    )
    db.add(kyc)
    await db.commit()
    await db.refresh(kyc)
    return kyc


async def get_kyc_status(
    db: AsyncSession, tenant_id: uuid.UUID, account_id: uuid.UUID,
) -> KYCRecord | None:
    result = await db.execute(
        select(KYCRecord)
        .where(KYCRecord.tenant_id == tenant_id, KYCRecord.account_id == account_id)
        .order_by(KYCRecord.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def claim_redpacket(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    rule_id: uuid.UUID,
) -> RedPacketClaim:
    rule_result = await db.execute(
        select(RedPacketRule).where(
            RedPacketRule.tenant_id == tenant_id,
            RedPacketRule.id == rule_id,
        )
    )
    rule = rule_result.scalar_one()
    now = datetime.now(UTC)

    # 风控：检查日领取次数
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    daily_count_result = await db.execute(
        select(func.count()).select_from(RedPacketClaim).where(
            RedPacketClaim.tenant_id == tenant_id,
            RedPacketClaim.rule_id == rule_id,
            RedPacketClaim.account_id == account_id,
            RedPacketClaim.created_at >= today_start,
        )
    )
    daily_count = daily_count_result.scalar() or 0
    if daily_count >= rule.daily_limit_per_user:
        raise ValueError("已达到今日领取上限")

    # 风控：检查总领取次数
    total_count_result = await db.execute(
        select(func.count()).select_from(RedPacketClaim).where(
            RedPacketClaim.tenant_id == tenant_id,
            RedPacketClaim.rule_id == rule_id,
            RedPacketClaim.account_id == account_id,
        )
    )
    total_count = total_count_result.scalar() or 0
    if total_count >= rule.single_limit_per_user:
        raise ValueError("已达到总领取上限")

    # 风控：检查预算
    if rule.claimed_budget >= rule.total_budget:
        raise ValueError("红包预算已用尽")

    # 随机金额
    amount = random.randint(rule.min_amount, rule.max_amount)

    claim = RedPacketClaim(
        tenant_id=tenant_id,
        rule_id=rule_id,
        account_id=account_id,
        amount=amount,
    )
    db.add(claim)

    rule.claimed_budget += amount
    await db.commit()
    await db.refresh(claim)
    return claim


async def request_withdrawal(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    amount: int,
) -> Withdrawal:
    w = Withdrawal(
        tenant_id=tenant_id,
        account_id=account_id,
        amount=amount,
    )
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return w


async def list_withdrawals(
    db: AsyncSession, tenant_id: uuid.UUID,
) -> list[Withdrawal]:
    result = await db.execute(
        select(Withdrawal)
        .where(Withdrawal.tenant_id == tenant_id)
        .order_by(Withdrawal.created_at.desc())
    )
    return list(result.scalars().all())


def check_risk(rule_id: str, amount: int) -> dict:
    """简易风控检查"""
    # 单笔限额：20000 分 = 200 元
    single_limit = 20000
    if amount > single_limit:
        return {"passed": False, "reason": "超过单笔限额"}
    return {"passed": True, "reason": ""}
