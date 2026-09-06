"""统计日切时钟（权威口径）。

业务在中国：运营看到的「昨日」「今日」按 Asia/Shanghai 自然日理解。
2026-09 起统计日切由 UTC 统一切换为 Asia/Shanghai；本模块是唯一
口径来源，任何按天聚合/默认日期都必须经由此处取边界，禁止散落
datetime.now(UTC).date() 或服务器本地 date.today()。

切换后存量按 UTC 日切写入的聚合行需要重算：
`uv run python -m app.cli stats recompute --apply`（见 app/cli/stats_recompute.py）。
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

STATS_TIMEZONE = ZoneInfo("Asia/Shanghai")
"""统计日切时区。"""

STATS_TZ_NAME = "Asia/Shanghai"
"""SQL 端 timezone()/AT TIME ZONE 使用的时区名。"""


def stats_today(now: datetime | None = None) -> date:
    """统计口径下的「今天」。"""
    current = now or datetime.now(STATS_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=STATS_TIMEZONE)
    return current.astimezone(STATS_TIMEZONE).date()


def stats_day_bounds(target_date: date) -> tuple[datetime, datetime]:
    """某统计日的 [start, end) 绝对时间边界（半开区间，tz-aware 统计时区）。"""
    start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=STATS_TIMEZONE)
    return start, start + timedelta(days=1)


def stats_day_bounds_utc(target_date: date) -> tuple[datetime, datetime]:
    """同 stats_day_bounds，但规范化为 UTC——SQL 时间戳比较一律用 UTC 边界。

    PostgreSQL timestamptz 比较取绝对时刻，UTC 规范化等价；SQLite 把
    aware datetime 按挂钟字符串存储，混时区比较会静默错位，统一 UTC 后
    两种后端语义一致。
    """
    start, end = stats_day_bounds(target_date)
    return start.astimezone(UTC), end.astimezone(UTC)


def stats_cutoff_dt(days_back: int, *, today: date | None = None) -> datetime:
    """「days_back 天前那天的 00:00（统计时区）」绝对时间点，用于 scan_time 等过滤。"""
    day = (today if today is not None else stats_today()) - timedelta(days=days_back)
    start, _ = stats_day_bounds(day)
    return start


def stats_cutoff_utc(days_back: int, *, today: date | None = None) -> datetime:
    """stats_cutoff_dt 的 UTC 规范化形态（SQL 比较用）。"""
    return stats_cutoff_dt(days_back, today=today).astimezone(UTC)
