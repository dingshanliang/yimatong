"""积分引擎核心逻辑测试 — 真实服务函数测试"""

import uuid

import pytest
from sqlalchemy import select

from app.models.member import (
    ConsumerProfile,
    MemberLevel,
    PointProduct,
    PointRedemption,
    PointTransactionType,
)
from app.services.member import _update_member_level, award_points, spend_points
from app.services.point_shop import exchange_product
from tests.conftest import TestSessionLocal


@pytest.mark.anyio
async def test_award_points_increments_balance():
    """发放积分应增加余额并创建 earning 交易"""
    async with TestSessionLocal() as db:
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid))
        await db.flush()

        txn = await award_points(db, tid, cid, 100, "test")
        assert txn.amount == 100
        assert txn.balance_after == 100
        assert txn.txn_type == PointTransactionType.earning

        consumer = (await db.execute(select(ConsumerProfile).where(ConsumerProfile.id == cid))).scalar_one()
        assert consumer.total_points == 100


@pytest.mark.anyio
async def test_spend_points_decrements_balance():
    """消费积分应减少余额并创建 spending 交易"""
    async with TestSessionLocal() as db:
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=200))
        await db.flush()

        txn = await spend_points(db, tid, cid, 50, "test")
        assert txn.amount == -50
        assert txn.balance_after == 150
        assert txn.txn_type == PointTransactionType.spending


@pytest.mark.anyio
async def test_spend_insufficient_raises():
    """余额不足时应抛出 ValueError"""
    async with TestSessionLocal() as db:
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=10))
        await db.flush()

        with pytest.raises(ValueError, match="Insufficient"):
            await spend_points(db, tid, cid, 50, "test")


@pytest.mark.anyio
async def test_award_negative_raises():
    """负数积分应被拒绝"""
    async with TestSessionLocal() as db:
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid))
        await db.flush()

        with pytest.raises(ValueError, match="positive"):
            await award_points(db, tid, cid, -10, "cheat")


@pytest.mark.anyio
async def test_spend_negative_raises():
    """负数消费应被拒绝"""
    async with TestSessionLocal() as db:
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=100))
        await db.flush()

        with pytest.raises(ValueError, match="positive"):
            await spend_points(db, tid, cid, -50, "cheat")


@pytest.mark.anyio
async def test_award_accumulates():
    """多次发放积分应累加"""
    async with TestSessionLocal() as db:
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid))
        await db.flush()

        await award_points(db, tid, cid, 50, "first")
        await award_points(db, tid, cid, 30, "second")

        consumer = (await db.execute(select(ConsumerProfile).where(ConsumerProfile.id == cid))).scalar_one()
        assert consumer.total_points == 80


@pytest.mark.anyio
async def test_member_level_upgrade():
    """积分达到阈值应升级会员等级"""
    async with TestSessionLocal() as db:
        consumer = ConsumerProfile(tenant_id=uuid.uuid4(), total_points=0)
        db.add(consumer)
        await db.flush()

        for points, expected_level in [
            (0, MemberLevel.normal),
            (999, MemberLevel.normal),
            (1000, MemberLevel.silver),
            (4999, MemberLevel.silver),
            (5000, MemberLevel.gold),
            (9999, MemberLevel.gold),
            (10000, MemberLevel.platinum),
        ]:
            consumer.total_points = points
            await _update_member_level(db, consumer)
            assert consumer.member_level == expected_level, f"Expected {expected_level} at {points} points"


@pytest.mark.anyio
async def test_exchange_product_deducts_stock_and_points():
    """兑换商品应扣减库存和积分，创建兑换记录"""
    async with TestSessionLocal() as db:
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=200))
        db.add(
            PointProduct(
                id=uuid.uuid4(),
                tenant_id=tid,
                name="测试商品",
                points_cost=50,
                stock=5,
                total_claimed=0,
                enabled=True,
                per_consumer_limit=10,
                sort_order=0,
            )
        )
        await db.flush()

        product = (await db.execute(select(PointProduct))).scalar_one()
        result = await exchange_product(db, tid, cid, product.id)

        assert result["points_spent"] == 50
        assert result["balance_after"] == 150

        await db.refresh(product)
        assert product.stock == 4
        assert product.total_claimed == 1

        redemptions = (
            (await db.execute(select(PointRedemption).where(PointRedemption.consumer_id == cid))).scalars().all()
        )
        assert len(redemptions) == 1


@pytest.mark.anyio
async def test_exchange_out_of_stock_blocked():
    """库存为 0 应拒绝兑换"""
    async with TestSessionLocal() as db:
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=500))
        db.add(
            PointProduct(
                id=uuid.uuid4(),
                tenant_id=tid,
                name="无库存",
                points_cost=10,
                stock=0,
                total_claimed=0,
                enabled=True,
                per_consumer_limit=10,
                sort_order=0,
            )
        )
        await db.flush()

        product = (await db.execute(select(PointProduct))).scalar_one()
        with pytest.raises(ValueError, match="库存不足"):
            await exchange_product(db, tid, cid, product.id)


@pytest.mark.anyio
async def test_exchange_insufficient_points_blocked():
    """积分不足应拒绝兑换"""
    async with TestSessionLocal() as db:
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=5))
        db.add(
            PointProduct(
                id=uuid.uuid4(),
                tenant_id=tid,
                name="贵商品",
                points_cost=100,
                stock=10,
                total_claimed=0,
                enabled=True,
                per_consumer_limit=10,
                sort_order=0,
            )
        )
        await db.flush()

        product = (await db.execute(select(PointProduct))).scalar_one()
        with pytest.raises(ValueError, match="积分不足"):
            await exchange_product(db, tid, cid, product.id)


@pytest.mark.anyio
async def test_exchange_per_consumer_limit():
    """达到每人限兑次数应拒绝"""
    async with TestSessionLocal() as db:
        tid = uuid.uuid4()
        cid = uuid.uuid4()
        db.add(ConsumerProfile(id=cid, tenant_id=tid, total_points=500))
        db.add(
            PointProduct(
                id=uuid.uuid4(),
                tenant_id=tid,
                name="限兑商品",
                points_cost=10,
                stock=10,
                total_claimed=0,
                enabled=True,
                per_consumer_limit=1,
                sort_order=0,
            )
        )
        await db.flush()

        product = (await db.execute(select(PointProduct))).scalar_one()
        # First exchange succeeds
        await exchange_product(db, tid, cid, product.id)
        # Second fails
        with pytest.raises(ValueError, match="限兑"):
            await exchange_product(db, tid, cid, product.id)


@pytest.mark.anyio
async def test_award_nonexistent_consumer_raises():
    """发放积分给不存在的消费者应抛出 ValueError"""
    async with TestSessionLocal() as db:
        with pytest.raises(ValueError, match="Consumer not found"):
            await award_points(db, uuid.uuid4(), uuid.uuid4(), 100, "test")
