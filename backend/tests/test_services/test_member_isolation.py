"""会员模块租户隔离测试"""

import uuid

import pytest
from sqlalchemy import select

from app.models.member import (
    ConsumerProfile,
    PointRule,
    PointTransaction,
)
from app.services.member import (
    award_points,
    get_consumer_profile,
    get_point_rules,
    search_consumers,
)
from tests.conftest import TestSessionLocal


@pytest.mark.anyio
async def test_get_point_rules_isolated_by_tenant():
    """租户 A 不能看到租户 B 的积分规则"""
    tid_a = uuid.uuid4()
    tid_b = uuid.uuid4()
    async with TestSessionLocal() as db:
        db.add(PointRule(tenant_id=tid_a, rule_type="scan", points=10, enabled=True, daily_limit=0))
        db.add(PointRule(tenant_id=tid_b, rule_type="scan", points=20, enabled=True, daily_limit=0))
        await db.commit()

    async with TestSessionLocal() as db:
        rules = await get_point_rules(db, tid_a)
        assert len(rules) == 1
        assert rules[0].points == 10


@pytest.mark.anyio
async def test_search_consumers_isolated():
    """搜索消费者应只返回当前租户的结果"""
    tid_a = uuid.uuid4()
    tid_b = uuid.uuid4()
    async with TestSessionLocal() as db:
        db.add(ConsumerProfile(tenant_id=tid_a, nickname="租户A用户"))
        db.add(ConsumerProfile(tenant_id=tid_b, nickname="租户B用户"))
        await db.commit()

    async with TestSessionLocal() as db:
        results = await search_consumers(db, tid_a, "用户")
        assert len(results) == 1
        assert results[0]["nickname"] == "租户A用户"


@pytest.mark.anyio
async def test_withdrawn_lead_contact_is_not_searchable_by_nickname():
    tenant_id = uuid.uuid4()
    async with TestSessionLocal() as db:
        db.add(
            ConsumerProfile(
                tenant_id=tenant_id,
                nickname="已撤回用户",
                lead_contact_suppressed=True,
            )
        )
        await db.commit()

    async with TestSessionLocal() as db:
        assert await search_consumers(db, tenant_id, "已撤回用户", lookup_type="nickname") == []


@pytest.mark.anyio
async def test_get_consumer_profile_cross_tenant_returns_none():
    """用租户 B 的 tenant_id 查询租户 A 的消费者应返回 None"""
    tid_a = uuid.uuid4()
    tid_b = uuid.uuid4()
    consumer = ConsumerProfile(tenant_id=tid_a, total_points=500)
    async with TestSessionLocal() as db:
        db.add(consumer)
        await db.commit()

    async with TestSessionLocal() as db:
        profile = await get_consumer_profile(db, tid_b, consumer.id)
        assert profile is None


@pytest.mark.anyio
async def test_award_points_wrong_tenant_raises():
    """给不同租户的消费者发放积分应失败"""
    tid_a = uuid.uuid4()
    tid_b = uuid.uuid4()
    consumer = ConsumerProfile(tenant_id=tid_a, total_points=0)
    async with TestSessionLocal() as db:
        db.add(consumer)
        await db.commit()

    async with TestSessionLocal() as db:
        with pytest.raises(ValueError, match="Consumer not found"):
            await award_points(db, tid_b, consumer.id, 100, "cheat")


@pytest.mark.anyio
async def test_point_transactions_isolated():
    """积分交易应只属于创建它的租户"""
    tid_a = uuid.uuid4()
    tid_b = uuid.uuid4()
    cid = uuid.uuid4()
    async with TestSessionLocal() as db:
        db.add(ConsumerProfile(id=cid, tenant_id=tid_a, total_points=100))
        await db.commit()

    async with TestSessionLocal() as db:
        await award_points(db, tid_a, cid, 50, "test")
        await db.commit()

    async with TestSessionLocal() as db:
        # Verify transaction belongs to tid_a
        result = await db.execute(select(PointTransaction).where(PointTransaction.tenant_id == tid_a))
        assert len(list(result.scalars().all())) == 1

        # Verify no transactions for tid_b
        result = await db.execute(select(PointTransaction).where(PointTransaction.tenant_id == tid_b))
        assert len(list(result.scalars().all())) == 0
