"""统计日切切换后的存量聚合重算（Asia/Shanghai 口径，2026-09 切换）。

2026-09-06 之前 DailyScanStats / gmv_daily_stats 按 UTC 日切写入；
切到 Asia/Shanghai 后每个统计日的窗口整体平移 8 小时，存量行与新
口径不一致，需要对本租户的完整扫码历史按新口径重算：

    uv run python -m app.cli stats recompute             # 预览（默认 dry-run）
    uv run python -m app.cli stats recompute --apply     # 实际重写

- DailyScanStats：逐日 UPSERT 覆盖，无删除。
- gmv_daily_stats：窗口内旧行先删后算（UTC 窗口的行在新口径下不存在
  对应键，UPSERT 无法覆盖），因此 --apply 会先清理再重建。
"""

import asyncio
import uuid
from datetime import date, timedelta
from typing import Literal

import typer
from sqlalchemy import func, select, text

from app.models.scan import ScanEvent
from app.models.tenant import Tenant
from app.utils.stats_clock import STATS_TIMEZONE, stats_day_bounds, stats_today

app = typer.Typer(help="统计聚合重算（日切口径切换维护）")


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


@app.command()
def recompute(
    start: str | None = typer.Option(None, help="重算起始统计日（YYYY-MM-DD），默认取最早扫码事件所在日"),
    end: str | None = typer.Option(None, help="重算结束统计日（含），默认今天（统计时区）"),
    tenant_id: uuid.UUID | None = typer.Option(None, help="仅重算指定租户"),
    apply: bool = typer.Option(False, "--apply", help="实际写库；缺省为 dry-run"),
) -> None:
    """按 Asia/Shanghai 日切重算存量日聚合。"""
    asyncio.run(_recompute(start, end, tenant_id, "apply" if apply else "dry-run"))


async def _recompute(
    start_raw: str | None,
    end_raw: str | None,
    tenant_id: uuid.UUID | None,
    mode: Literal["dry-run", "apply"],
) -> None:
    from app.core.database import _session_uses_postgresql, control_session_factory, set_session_tenant_context
    from app.services import gmv as gmv_service
    from app.services.analytics import aggregate_daily_stats

    end_date = _parse_date(end_raw) if end_raw else stats_today()

    async with control_session_factory() as scope_db:
        if _session_uses_postgresql(scope_db):
            await scope_db.execute(text("SELECT set_config('app.tenant_id', '', true)"))
            await scope_db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))

        tenant_stmt = select(Tenant.id).where(Tenant.status != "terminated").order_by(Tenant.created_at)
        if tenant_id is not None:
            tenant_stmt = tenant_stmt.where(Tenant.id == tenant_id)
        tenant_ids = list((await scope_db.execute(tenant_stmt)).scalars().all())

    if not tenant_ids:
        typer.echo("没有待处理的租户")
        return

    total_days = 0
    for tid in tenant_ids:
        async with control_session_factory() as db:
            if _session_uses_postgresql(db):
                await set_session_tenant_context(db, tid)

            min_scan = (
                await db.execute(select(func.min(ScanEvent.scan_time)).where(ScanEvent.tenant_id == tid))
            ).scalar()
            first_day = min_scan.astimezone(STATS_TIMEZONE).date() if min_scan is not None else end_date
            start_date = _parse_date(start_raw) if start_raw else first_day
            if start_date > end_date:
                typer.echo(f"tenant {tid}: 无历史扫码数据，跳过")
                continue

            days = (end_date - start_date).days + 1
            typer.echo(
                f"tenant {tid}: 重算 {start_date} ~ {end_date} 共 {days} 天"
                + ("（dry-run）" if mode == "dry-run" else "")
            )
            if mode != "apply":
                total_days += days
                continue

            for offset in range(days):
                day = start_date + timedelta(days=offset)
                await aggregate_daily_stats(db, tid, day)
            # GMV 日统计：窗口内旧行（UTC 口径键）先删再按新口径重建
            delete_start, _ = stats_day_bounds(start_date)
            _, delete_end = stats_day_bounds(end_date)
            await db.execute(
                text("DELETE FROM gmv_daily_stats WHERE tenant_id = :tid AND stat_date >= :s AND stat_date < :e"),
                {"tid": str(tid), "s": delete_start, "e": delete_end},
            )
            for offset in range(days):
                day = start_date + timedelta(days=offset)
                await gmv_service.aggregate_daily_stats(db, tid, target_date=day)
            await db.commit()
            total_days += days

    typer.echo(f"完成：{'已重写' if mode == 'apply' else '预览'} {total_days} 个租户日")


if __name__ == "__main__":
    app()
