"""试点复盘服务测试（beads: yimatong-bgag.2，PRD §4.2/§6.1/§7）。"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.constants.retrospective import (
    RETO_STATE_OVERDUE,
    RETO_STATE_OVERDUE_COMPLETED,
    RETRO_PERIOD_DAYS,
    RetrospectiveStatus,
)
from app.services.retrospective import (
    _derived_status,
    _due_periods,
    complete_retrospective,
    generate_for_tenant,
    list_retrospectives,
)
from tests.conftest import seed_pilot_launch_release, seed_pilot_tenant


@pytest.mark.asyncio
async def test_generate_skips_unlaunched_tenant(db):
    """从未上线租户不生成复盘（PRD §4.2 Failure）。"""
    tenant_id = await seed_pilot_tenant(db)
    count = await generate_for_tenant(db, tenant_id, now=datetime(2026, 8, 1, tzinfo=UTC))
    assert count == 0
    assert await list_retrospectives(db, tenant_id) == []


@pytest.mark.asyncio
async def test_generate_creates_due_periods(db):
    """上线后第 7/14/30 天到期即生成对应期次。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)

    # 上线后 10 天：只有第 7 天到期
    count = await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=10))
    assert count == 1
    retros = await list_retrospectives(db, tenant_id)
    assert [r.period_day for r in retros] == [7]

    # 上线后 20 天：第 14 天也到期（第 7 天已存在，幂等）
    count = await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=20))
    assert count == 1  # 只新增第 14 天
    retros = await list_retrospectives(db, tenant_id)
    assert [r.period_day for r in retros] == [7, 14]


@pytest.mark.asyncio
async def test_generate_is_idempotent(db):
    """同租户同期不重复生成（幂等）。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)
    now = launched + timedelta(days=40)  # 三期全到期

    first = await generate_for_tenant(db, tenant_id, now=now)
    second = await generate_for_tenant(db, tenant_id, now=now)

    assert first == 3
    assert second == 0  # 全部已存在
    retros = await list_retrospectives(db, tenant_id)
    assert len(retros) == 3
    assert [r.period_day for r in retros] == RETRO_PERIOD_DAYS


@pytest.mark.asyncio
async def test_generate_materializes_scorecard_snapshot(db):
    """生成时物化 scorecard 快照。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)

    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=10))
    retros = await list_retrospectives(db, tenant_id)
    assert len(retros) == 1
    assert retros[0].scorecard_snapshot != {}
    assert "window_start" in retros[0].scorecard_snapshot
    assert retros[0].status == RetrospectiveStatus.PENDING


@pytest.mark.asyncio
async def test_complete_retrospective_freezes_snapshot(db):
    """完成复盘：状态 pending→completed，快照冻结不可改。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)
    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=10))
    retro = (await list_retrospectives(db, tenant_id))[0]
    snapshot_before = dict(retro.scorecard_snapshot)

    actor = uuid.uuid4()
    updated = await complete_retrospective(
        db,
        tenant_id,
        retro.id,
        issues="转化率偏低",
        actions=[{"content": "优化权益入口", "status": "pending"}],
        next_review_date=date(2026, 8, 1),
        goal="提升扫码转化",
        actor_id=actor,
    )

    assert updated.status == RetrospectiveStatus.COMPLETED
    assert updated.completed_at is not None
    assert updated.completed_by == actor
    assert updated.issues == "转化率偏低"
    # 快照未变（冻结）
    assert updated.scorecard_snapshot == snapshot_before


@pytest.mark.asyncio
async def test_complete_only_allows_supplementary_after_completion(db):
    """已完成复盘：只允许追加 supplementary_notes（PRD §6.1）。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)
    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=10))
    retro = (await list_retrospectives(db, tenant_id))[0]

    # 先完成
    await complete_retrospective(db, tenant_id, retro.id, issues="一期问题", actor_id=uuid.uuid4())
    # 再追加补充说明
    updated = await complete_retrospective(db, tenant_id, retro.id, supplementary_notes="补充：权益已优化")

    assert updated.supplementary_notes == "补充：权益已优化"
    # issues 不应被覆盖（已完成，issues 不在追加路径）
    assert updated.issues == "一期问题"


@pytest.mark.asyncio
async def test_derived_status_overdue(db):
    """pending 且超过 next_review_date → 派生态 overdue。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)
    # now 远超 next_review_date
    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=10))
    retro = (await list_retrospectives(db, tenant_id))[0]

    now_overdue = launched + timedelta(days=100)
    assert _derived_status(retro, now_overdue) == RETO_STATE_OVERDUE


@pytest.mark.asyncio
async def test_derived_status_overdue_completed(db):
    """pending→逾期后补填完成 → 派生态 overdue_completed。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    tenant_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, tenant_id, launched_at=launched)
    await generate_for_tenant(db, tenant_id, now=launched + timedelta(days=10))
    retro = (await list_retrospectives(db, tenant_id))[0]

    # 在 next_review_date 之后才完成
    late_complete = datetime(
        retro.next_review_date.year, retro.next_review_date.month, retro.next_review_date.day, tzinfo=UTC
    ) + timedelta(days=5)
    updated = await complete_retrospective(db, tenant_id, retro.id, actor_id=uuid.uuid4())
    # 手动把 completed_at 设到逾期后，模拟逾期补填
    updated.completed_at = late_complete
    await db.flush()

    assert _derived_status(updated, late_complete + timedelta(days=1)) == RETO_STATE_OVERDUE_COMPLETED


@pytest.mark.asyncio
async def test_tenant_isolation(db):
    """A 租户生成不影响 B 租户。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    a_id = await seed_pilot_tenant(db)
    b_id = await seed_pilot_tenant(db)
    await seed_pilot_launch_release(db, a_id, launched_at=launched)
    # B 未上线

    await generate_for_tenant(db, a_id, now=launched + timedelta(days=10))

    a_retros = await list_retrospectives(db, a_id)
    b_retros = await list_retrospectives(db, b_id)
    assert len(a_retros) == 1
    assert b_retros == []


def test_due_periods_calculation():
    """_due_periods：窗口与下一期复盘日计算正确。"""
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    # 上线后 20 天：第 7/14 天到期
    due = _due_periods(launched, launched + timedelta(days=20))
    periods = [d[0] for d in due]
    assert periods == [7, 14]
    # 第 7 天的 next_review 应为第 14 天
    d7 = next(d for d in due if d[0] == 7)
    assert d7[3] == (launched + timedelta(days=14)).date()

    # 上线后 40 天：三期全到期
    due_all = _due_periods(launched, launched + timedelta(days=40))
    assert [d[0] for d in due_all] == [7, 14, 30]
    # 第 30 天的 next_review 为 +30 天（无下一期）
    d30 = next(d for d in due_all if d[0] == 30)
    assert d30[3] == (launched + timedelta(days=30) + timedelta(days=30)).date()
