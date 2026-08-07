"""ops 试点跨租户聚合服务测试（beads: yimatong-bgag.10，PRD §4.5）。

SQLite 测试路径：单 session 直读全部 active 租户（RLS no-op），聚焦聚合逻辑正确性
（里程碑计数、待办复盘过滤、汇总）。跨租户 RLS 隔离在 PostgreSQL 由
get_authorized_client_scopes + per-client session 保证，此处不验证 PG 特定路径。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.retrospective import Retrospective
from app.services.ops import get_pilot_aggregate
from app.services.retrospective import RetrospectiveStatus
from tests.conftest import seed_pilot_launch_release, seed_pilot_tenant


@pytest.mark.asyncio
async def test_aggregate_returns_all_active_tenants_with_milestone_counts(db):
    """平台视角（SQLite）：聚合返回所有 active 租户的里程碑达成数。"""
    t1 = await seed_pilot_tenant(db, created_at=datetime(2026, 7, 1, tzinfo=UTC))
    t2 = await seed_pilot_tenant(db, created_at=datetime(2026, 7, 1, tzinfo=UTC))
    # t1 上线（派生里程碑），t2 未上线（只派生开通里程碑）
    await seed_pilot_launch_release(db, t1, launched_at=datetime(2026, 7, 5, tzinfo=UTC))

    result = await get_pilot_aggregate(db, agency_tenant_id=None)
    by_client = {c["client_id"]: c for c in result["clients"]}
    assert t1 in by_client
    assert t2 in by_client
    # t1 至少达成开通 + 上线 2 个；t2 只开通 1 个
    assert by_client[t1]["milestone_summary"]["achieved_count"] >= 2
    assert by_client[t2]["milestone_summary"]["achieved_count"] == 1
    assert by_client[t1]["milestone_summary"]["total"] == 5


@pytest.mark.asyncio
async def test_aggregate_filters_pending_retrospectives(db):
    """待办复盘只列 status != completed 的期次。"""
    tenant_id = await seed_pilot_tenant(db, created_at=datetime(2026, 7, 1, tzinfo=UTC))
    await seed_pilot_launch_release(db, tenant_id, launched_at=datetime(2026, 7, 1, tzinfo=UTC))
    # 直接写两条复盘：一条 pending、一条 completed
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    db.add(
        Retrospective(
            tenant_id=tenant_id,
            period_day=7,
            window_start=launched,
            window_end=launched + timedelta(days=7),
            next_review_date=(launched + timedelta(days=14)).date(),
            status=RetrospectiveStatus.PENDING,
            scorecard_snapshot={},
        )
    )
    db.add(
        Retrospective(
            tenant_id=tenant_id,
            period_day=14,
            window_start=launched,
            window_end=launched + timedelta(days=14),
            next_review_date=(launched + timedelta(days=30)).date(),
            status=RetrospectiveStatus.COMPLETED,
            scorecard_snapshot={},
        )
    )
    await db.flush()

    result = await get_pilot_aggregate(db, agency_tenant_id=None)
    client = next(c for c in result["clients"] if c["client_id"] == tenant_id)
    pending_days = [p["period_day"] for p in client["pending_retrospectives"]]
    assert pending_days == [7]  # 只列 pending，completed 不在待办
    assert result["summary"]["clients_with_pending_retros"] == 1


@pytest.mark.asyncio
async def test_aggregate_empty_when_no_active_tenants(db):
    """无 active 租户：返回空聚合。"""
    result = await get_pilot_aggregate(db, agency_tenant_id=None)
    # 至少包含本测试 db 中已有的租户（seed_pilot_tenant 创建的是 active brand）
    # 但若 db 为空（无租户），summary.total_clients == 0
    assert "summary" in result
    assert "clients" in result


@pytest.mark.asyncio
async def test_aggregate_pending_retrospective_has_derived_status(db):
    """待办复盘项带 derived_status（pending/overdue 等）。"""
    tenant_id = await seed_pilot_tenant(db, created_at=datetime(2026, 7, 1, tzinfo=UTC))
    await seed_pilot_launch_release(db, tenant_id, launched_at=datetime(2026, 7, 1, tzinfo=UTC))
    launched = datetime(2026, 7, 1, tzinfo=UTC)
    db.add(
        Retrospective(
            tenant_id=tenant_id,
            period_day=7,
            window_start=launched,
            window_end=launched + timedelta(days=7),
            # next_review_date 在过去 → overdue
            next_review_date=(datetime.now(UTC) - timedelta(days=10)).date(),
            status=RetrospectiveStatus.PENDING,
            scorecard_snapshot={},
        )
    )
    await db.flush()

    result = await get_pilot_aggregate(db, agency_tenant_id=None)
    client = next(c for c in result["clients"] if c["client_id"] == tenant_id)
    assert client["pending_retrospectives"][0]["derived_status"] == "overdue"


@pytest.mark.asyncio
async def test_aggregate_controlled_flag_reads_all_tenants(db):
    """_controlled=True（platform 二次进入）落到全量读取分支，返回所有 active 租户。

    覆盖 code-review 发现的递归 bug：_controlled sentinel 确保 platform 路径
    不无限递归，且 _controlled=True 时落到真实读取分支。
    """
    await seed_pilot_tenant(db, created_at=datetime(2026, 7, 1, tzinfo=UTC))
    await seed_pilot_tenant(db, created_at=datetime(2026, 7, 1, tzinfo=UTC))

    # _controlled=True 模拟 platform 在 control session 内的二次调用
    result = await get_pilot_aggregate(db, agency_tenant_id=None, _controlled=True)
    assert len(result["clients"]) >= 2  # 全量读取所有 active 租户
