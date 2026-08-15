"""会员与积分服务层"""

import uuid
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.database import _session_uses_postgresql, get_request_security_credential
from app.models.auth_security import AuthSession
from app.models.campaign import Benefit
from app.models.member import (
    ConsumerProfile,
    MemberLevel,
    PointProduct,
    PointRedemption,
    PointRule,
    PointTransaction,
    PointTransactionType,
)
from app.models.tenant import Account, Permission, Role, Tenant, account_roles, role_permissions
from app.utils import utcnow
from app.utils.crypto import CryptoError, decrypt_consumer_phone, hash_phone, mask_phone
from app.utils.model_helpers import apply_allowed_updates


def _member_auth_session_id() -> uuid.UUID:
    credential = get_request_security_credential()
    if credential is None or credential[0] != "auth_session":
        raise HTTPException(status_code=401, detail="Live login session required for member changes")
    try:
        return uuid.UUID(credential[1])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid login session") from exc


async def create_anonymous_consumer_profile_authority(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """Create one PII-free points profile through actor-bound database authority."""

    consumer_id = uuid7()
    auth_session_id = _member_auth_session_id()
    if not _session_uses_postgresql(db):
        authorized_session_id = await db.scalar(
            select(AuthSession.id)
            .join(
                Account,
                (Account.tenant_id == AuthSession.tenant_id) & (Account.id == AuthSession.account_id),
            )
            .join(Tenant, Tenant.id == AuthSession.tenant_id)
            .join(
                account_roles,
                (account_roles.c.tenant_id == Account.tenant_id) & (account_roles.c.account_id == Account.id),
            )
            .join(
                Role,
                (Role.tenant_id == account_roles.c.tenant_id) & (Role.id == account_roles.c.role_id),
            )
            .join(
                role_permissions,
                (role_permissions.c.tenant_id == Role.tenant_id) & (role_permissions.c.role_id == Role.id),
            )
            .join(
                Permission,
                (Permission.tenant_id == role_permissions.c.tenant_id)
                & (Permission.id == role_permissions.c.permission_id),
            )
            .where(
                AuthSession.id == auth_session_id,
                AuthSession.tenant_id == tenant_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > utcnow(),
                AuthSession.auth_version == Account.auth_version,
                Account.is_active.is_(True),
                Tenant.status == "active",
                Role.name.in_({"admin", "operator"}),
                Permission.code == "consumer:detail",
            )
            .limit(1)
        )
        if authorized_session_id is None:
            raise HTTPException(status_code=403, detail="Member authority denied")
        profile = ConsumerProfile(id=consumer_id, tenant_id=tenant_id)
        db.add(profile)
        await db.flush()
        return {"consumer_id": profile.id, "created_at": profile.created_at}
    try:
        row = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM public.create_anonymous_consumer_profile("
                        ":tenant_id,:auth_session_id,:audit_id,:consumer_id)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "auth_session_id": auth_session_id,
                        "audit_id": uuid7(),
                        "consumer_id": consumer_id,
                    },
                )
            )
            .mappings()
            .one()
        )
    except DBAPIError as exc:
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if sqlstate == "42501":
            raise HTTPException(status_code=403, detail="Member authority denied") from exc
        if sqlstate in {"22023", "23514", "23505"}:
            raise HTTPException(status_code=409, detail="Member authority conflict") from exc
        if sqlstate == "55P03":
            raise HTTPException(
                status_code=409, detail="Member authority is busy", headers={"Retry-After": "1"}
            ) from exc
        raise
    return dict(row)


async def _get_consumer_for_update(db: AsyncSession, tenant_id: uuid.UUID, consumer_id: uuid.UUID) -> ConsumerProfile:
    """获取消费者档案并加行锁（FOR UPDATE）。"""
    result = await db.execute(
        select(ConsumerProfile)
        .where(
            ConsumerProfile.id == consumer_id,
            ConsumerProfile.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    consumer = result.scalar_one_or_none()
    if not consumer:
        raise ValueError("Consumer not found")
    return consumer


async def award_points(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    points: int,
    reason: str,
    reference_id: str | None = None,
) -> PointTransaction:
    """发放积分"""
    if points <= 0:
        raise ValueError("Points must be positive")
    consumer = await _get_consumer_for_update(db, tenant_id, consumer_id)

    new_balance = consumer.total_points + points
    consumer.total_points = new_balance

    txn = PointTransaction(
        tenant_id=tenant_id,
        consumer_id=consumer_id,
        amount=points,
        balance_after=new_balance,
        txn_type=PointTransactionType.earning,
        reason=reason,
        reference_id=reference_id,
    )
    db.add(txn)
    await db.flush()
    await db.refresh(txn)

    # 更新会员等级
    await _update_member_level(db, consumer)
    return txn


async def spend_points(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    points: int,
    reason: str,
    reference_id: str | None = None,
) -> PointTransaction:
    """消费积分"""
    if points <= 0:
        raise ValueError("Points must be positive")
    consumer = await _get_consumer_for_update(db, tenant_id, consumer_id)

    if consumer.total_points < points:
        raise ValueError("Insufficient points")

    new_balance = consumer.total_points - points
    consumer.total_points = new_balance

    txn = PointTransaction(
        tenant_id=tenant_id,
        consumer_id=consumer_id,
        amount=-points,
        balance_after=new_balance,
        txn_type=PointTransactionType.spending,
        reason=reason,
        reference_id=reference_id,
    )
    db.add(txn)
    await db.flush()
    await db.refresh(txn)
    return txn


async def _update_member_level(db: AsyncSession, consumer: ConsumerProfile):
    """根据积分更新会员等级"""
    old_level = consumer.member_level
    if consumer.total_points >= 10000:
        consumer.member_level = MemberLevel.platinum
    elif consumer.total_points >= 5000:
        consumer.member_level = MemberLevel.gold
    elif consumer.total_points >= 1000:
        consumer.member_level = MemberLevel.silver
    else:
        consumer.member_level = MemberLevel.normal

    if consumer.member_level != old_level:
        await db.flush()


async def get_point_rules(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    enabled_only: bool = False,
) -> list[PointRule]:
    """获取租户积分规则"""
    stmt = select(PointRule).where(PointRule.tenant_id == tenant_id)
    if enabled_only:
        stmt = stmt.where(PointRule.enabled.is_(True))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_member_overview(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """会员积分运营概览。"""
    since = utcnow() - timedelta(days=7)
    enabled_rules = (
        await db.execute(
            select(func.count())
            .select_from(PointRule)
            .where(
                PointRule.tenant_id == tenant_id,
                PointRule.enabled.is_(True),
            )
        )
    ).scalar() or 0
    active_products = (
        await db.execute(
            select(func.count())
            .select_from(PointProduct)
            .where(
                PointProduct.tenant_id == tenant_id,
                PointProduct.enabled.is_(True),
            )
        )
    ).scalar() or 0
    points_awarded = (
        await db.execute(
            select(func.coalesce(func.sum(PointTransaction.amount), 0)).where(
                PointTransaction.tenant_id == tenant_id,
                PointTransaction.txn_type == PointTransactionType.earning,
                PointTransaction.created_at >= since,
            )
        )
    ).scalar() or 0
    points_spent_raw = (
        await db.execute(
            select(func.coalesce(func.sum(PointTransaction.amount), 0)).where(
                PointTransaction.tenant_id == tenant_id,
                PointTransaction.txn_type == PointTransactionType.spending,
                PointTransaction.created_at >= since,
            )
        )
    ).scalar() or 0
    redemptions_7d = (
        await db.execute(
            select(func.count())
            .select_from(PointRedemption)
            .where(
                PointRedemption.tenant_id == tenant_id,
                PointRedemption.created_at >= since,
            )
        )
    ).scalar() or 0
    low_stock_products = (
        await db.execute(
            select(func.count())
            .select_from(PointProduct)
            .where(
                PointProduct.tenant_id == tenant_id,
                PointProduct.enabled.is_(True),
                PointProduct.stock <= 5,
            )
        )
    ).scalar() or 0
    return {
        "enabled_rules": int(enabled_rules),
        "active_products": int(active_products),
        "points_awarded_7d": int(points_awarded),
        "points_spent_7d": abs(int(points_spent_raw)),
        "redemptions_7d": int(redemptions_7d),
        "low_stock_products": int(low_stock_products),
    }


def _masked_phone(consumer: ConsumerProfile) -> str | None:
    if (
        consumer.lead_contact_suppressed
        or consumer.phone_ciphertext is None
        or consumer.phone_nonce is None
        or consumer.phone_key_id is None
    ):
        return None
    try:
        return mask_phone(
            decrypt_consumer_phone(
                consumer.tenant_id,
                consumer.id,
                consumer.phone_ciphertext,
                consumer.phone_nonce,
                consumer.phone_key_id,
            )
        )
    except CryptoError:
        return None


def serialize_consumer_profile(consumer: ConsumerProfile) -> dict:
    return {
        "id": str(consumer.id),
        "nickname": None if consumer.lead_contact_suppressed else consumer.nickname,
        "phone": _masked_phone(consumer),
        "member_level": consumer.member_level,
        "total_points": consumer.total_points,
    }


async def search_consumers(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    keyword: str,
    lookup_type: str = "auto",
    limit: int = 20,
) -> list[dict]:
    """按消费者 ID、手机号或昵称搜索消费者。"""
    value = keyword.strip()
    if not value:
        return []

    normalized_type = lookup_type
    if lookup_type == "auto":
        try:
            uuid.UUID(value)
            normalized_type = "id"
        except ValueError:
            normalized_type = "phone" if value.isdigit() else "nickname"

    conditions = [ConsumerProfile.tenant_id == tenant_id]
    if normalized_type == "id":
        try:
            conditions.append(ConsumerProfile.id == uuid.UUID(value))
        except ValueError:
            return []
    elif normalized_type == "phone":
        conditions.append(ConsumerProfile.lead_contact_suppressed.is_(False))
        conditions.append(ConsumerProfile.phone_hash == hash_phone(value))
    else:
        escaped = value.replace("%", r"\%").replace("_", r"\_")
        if normalized_type == "nickname":
            conditions.append(ConsumerProfile.lead_contact_suppressed.is_(False))
            conditions.append(ConsumerProfile.nickname.ilike(f"%{escaped}%", escape="\\"))
        else:
            try:
                maybe_id = uuid.UUID(value)
            except ValueError:
                maybe_id = None
            phone_clause = ConsumerProfile.phone_hash == hash_phone(value) if value.isdigit() else None
            clauses = [
                and_(
                    ConsumerProfile.lead_contact_suppressed.is_(False),
                    ConsumerProfile.nickname.ilike(f"%{escaped}%", escape="\\"),
                )
            ]
            if maybe_id:
                clauses.append(ConsumerProfile.id == maybe_id)
            if phone_clause is not None:
                clauses.append(and_(ConsumerProfile.lead_contact_suppressed.is_(False), phone_clause))
            conditions.append(or_(*clauses))

    result = await db.execute(
        select(ConsumerProfile).where(*conditions).order_by(ConsumerProfile.id.desc()).limit(limit)
    )
    return [serialize_consumer_profile(c) for c in result.scalars().all()]


async def create_point_rule(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    rule_type: str,
    points: int,
    *,
    daily_limit: int = 0,
    description: str | None = None,
    config: dict | None = None,
) -> PointRule:
    """创建积分规则"""
    rule = PointRule(
        tenant_id=tenant_id,
        rule_type=rule_type,
        points=points,
        daily_limit=daily_limit,
        description=description,
        config=config,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return rule


async def update_point_rule(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    rule_id: uuid.UUID,
    **kwargs,
) -> PointRule | None:
    """更新积分规则"""
    result = await db.execute(select(PointRule).where(PointRule.id == rule_id, PointRule.tenant_id == tenant_id))
    rule = result.scalar_one_or_none()
    if not rule:
        return None
    apply_allowed_updates(rule, kwargs, {"points", "daily_limit", "description", "enabled", "config"})
    await db.flush()
    await db.refresh(rule)
    return rule


async def delete_point_rule(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    rule_id: uuid.UUID,
) -> bool:
    """删除积分规则"""
    result = await db.execute(select(PointRule).where(PointRule.id == rule_id, PointRule.tenant_id == tenant_id))
    rule = result.scalar_one_or_none()
    if not rule:
        return False
    await db.delete(rule)
    await db.flush()
    return True


async def get_consumer_profile(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
) -> dict | None:
    """获取消费者积分概览"""
    result = await db.execute(
        select(ConsumerProfile).where(
            ConsumerProfile.id == consumer_id,
            ConsumerProfile.tenant_id == tenant_id,
        )
    )
    consumer = result.scalar_one_or_none()
    if not consumer:
        return None

    # 获取最近交易
    txn_result = await db.execute(
        select(PointTransaction)
        .where(
            PointTransaction.consumer_id == consumer_id,
            PointTransaction.tenant_id == tenant_id,
        )
        .order_by(PointTransaction.id.desc())
        .limit(10)
    )
    recent_txns = list(txn_result.scalars().all())

    return {
        "id": str(consumer.id),
        "nickname": None if consumer.lead_contact_suppressed else consumer.nickname,
        "phone": _masked_phone(consumer),
        "member_level": consumer.member_level,
        "total_points": consumer.total_points,
        "recent_transactions": [
            {
                "id": str(t.id),
                "amount": t.amount,
                "balance_after": t.balance_after,
                "txn_type": t.txn_type,
                "reason": t.reason,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in recent_txns
        ],
    }


async def list_point_transactions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[PointTransaction], int]:
    """查询积分流水"""
    stmt = select(PointTransaction).where(
        PointTransaction.tenant_id == tenant_id,
        PointTransaction.consumer_id == consumer_id,
    )
    count_stmt = (
        select(func.count())
        .select_from(PointTransaction)
        .where(
            PointTransaction.tenant_id == tenant_id,
            PointTransaction.consumer_id == consumer_id,
        )
    )

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = (
        stmt.order_by(PointTransaction.created_at.desc(), PointTransaction.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def list_point_redemptions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    page: int = 1,
    page_size: int = 20,
    consumer_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    status: str | None = None,
) -> tuple[list[dict], int]:
    """后台查询积分兑换记录。"""
    filters = [PointRedemption.tenant_id == tenant_id]
    if consumer_id:
        filters.append(PointRedemption.consumer_id == consumer_id)
    if product_id:
        filters.append(PointRedemption.product_id == product_id)
    if status:
        filters.append(PointRedemption.status == status)

    count_stmt = select(func.count()).select_from(PointRedemption).where(*filters)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = (
        select(PointRedemption, PointProduct, ConsumerProfile, Benefit)
        .join(PointProduct, PointProduct.id == PointRedemption.product_id)
        .join(ConsumerProfile, ConsumerProfile.id == PointRedemption.consumer_id)
        .outerjoin(Benefit, Benefit.id == PointRedemption.benefit_id)
        .where(*filters)
        .order_by(PointRedemption.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    items = []
    for redemption, product, consumer, benefit in result.all():
        items.append(
            {
                "id": str(redemption.id),
                "consumer_id": str(redemption.consumer_id),
                "consumer_phone": _masked_phone(consumer),
                "consumer_nickname": None if consumer.lead_contact_suppressed else consumer.nickname,
                "product_id": str(redemption.product_id),
                "product_name": product.name,
                "points_cost": redemption.points_cost,
                "point_transaction_id": str(redemption.point_transaction_id),
                "benefit_id": str(redemption.benefit_id) if redemption.benefit_id else None,
                "benefit_name": benefit.name if benefit else None,
                "benefit_claim_id": str(redemption.benefit_claim_id) if redemption.benefit_claim_id else None,
                "status": redemption.status,
                "created_at": redemption.created_at.isoformat() if redemption.created_at else None,
            }
        )
    return items, total


async def get_consumer_phone(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
) -> str | None:
    """获取消费者脱敏手机号"""
    result = await db.execute(
        select(ConsumerProfile).where(
            ConsumerProfile.id == consumer_id,
            ConsumerProfile.tenant_id == tenant_id,
        )
    )
    consumer = result.scalar_one_or_none()
    if (
        not consumer
        or consumer.lead_contact_suppressed
        or consumer.phone_ciphertext is None
        or consumer.phone_nonce is None
        or consumer.phone_key_id is None
    ):
        return None
    try:
        return mask_phone(
            decrypt_consumer_phone(
                consumer.tenant_id,
                consumer.id,
                consumer.phone_ciphertext,
                consumer.phone_nonce,
                consumer.phone_key_id,
            )
        )
    except CryptoError:
        return None
