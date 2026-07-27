"""yimatong-zgb1.1 验收测试 — 真实 PostgreSQL fixture。

与根 conftest 的 SQLite TestSessionLocal 完全隔离：本目录的测试只用真实 PG，
证明 RLS、迁移、首扫原子性等无法在 SQLite 上证明的事实。

默认指向运行中的 infra PostgreSQL（docker-compose.infra.yml 映射到宿主机 5433）。
每个测试会话创建并最终删除一个干净的专用数据库 ``yimatong_acceptance``，
对应 BASELINE_ACCEPTANCE_MATRIX §4「干净环境重建门禁」。

环境变量：
- ``ACCEPTANCE_PG_ADMIN_URL``：用于 CREATE/DROP DATABASE 的管理连接串。
  默认 ``postgresql://yimatong:yimatong@localhost:5433/postgres``。
- ``ACCEPTANCE_PG_DSN``：测试用 SQLAlchemy asyncpg DSN（指向 acceptance 库）。
  默认 ``postgresql+asyncpg://yimatong:yimatong@localhost:5433/yimatong_acceptance``。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BACKEND_DIR = Path(__file__).resolve().parents[2]

ACCEPTANCE_DB = os.getenv("ACCEPTANCE_DB_NAME", "yimatong_acceptance")
ADMIN_DSN = os.getenv(
    "ACCEPTANCE_PG_ADMIN_URL",
    "postgresql://yimatong:yimatong@localhost:5433/postgres",
)
ACCEPTANCE_DSN = os.getenv(
    "ACCEPTANCE_PG_DSN",
    f"postgresql+asyncpg://yimatong:yimatong@localhost:5433/{ACCEPTANCE_DB}",
)


def _pg_available() -> bool:
    """探测 infra PG 是否可达（asyncpg 同步包装）。"""

    async def _probe() -> bool:
        try:
            conn = await asyncpg.connect(ADMIN_DSN)
            try:
                await conn.fetchval("SELECT 1")
                return True
            finally:
                await conn.close()
        except Exception:
            return False

    return asyncio.get_event_loop().run_until_complete(_probe()) if False else asyncio.run(_probe())


def _run_admin(sql: str) -> None:
    """在 admin 库上执行一条无事务语句（CREATE/DROP DATABASE）。"""

    async def _go():
        conn = await asyncpg.connect(ADMIN_DSN)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(_go())


def _drop_and_create_db() -> None:
    """在 infra PG 上（重）建一个干净的 acceptance 库。"""
    _run_admin(f"DROP DATABASE IF EXISTS {ACCEPTANCE_DB} WITH (FORCE)")
    _run_admin(f"CREATE DATABASE {ACCEPTANCE_DB} OWNER yimatong")


def _drop_db() -> None:
    _run_admin(f"DROP DATABASE IF EXISTS {ACCEPTANCE_DB} WITH (FORCE)")


# ── 会话级：干净 PG + 迁移 ────────────────────────────────────────────────


@pytest.fixture(scope="session")
def migrated_pg_url() -> str:
    """创建干净 acceptance 库并跑 alembic upgrade head，返回 asyncpg DSN。"""
    if not _pg_available():
        pytest.skip("infra PostgreSQL not available on localhost:5433; run `pnpm dev:stack`")
    _drop_and_create_db()

    env = os.environ.copy()
    env["database_url"] = ACCEPTANCE_DSN
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    if result.returncode != 0:
        _drop_db()
        pytest.fail(f"alembic upgrade head failed on clean PG:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")

    # 生产配置当前以 DB owner（yimatong）作为应用连接身份，PostgreSQL 默认对 table owner
    # 不强制 RLS。为了让验收测试真正证明 RLS 策略 *逻辑* 正确（= 隔离对照租户能否读到
    # 基准数据），这里对全部 RLS 表启用 FORCE ROW LEVEL SECURITY。这是一个测试侧的强化，
    # 不修改应用代码或迁移。生产 owner-vs-app-user 的差距留给后续安全审计票。
    _force_rls_on_all_tables()

    return ACCEPTANCE_DSN


def _force_rls_on_all_tables() -> None:
    """对全部 RLS 表追加 FORCE ROW LEVEL SECURITY，并创建非超级用户测试角色。

    PostgreSQL 对 superuser 始终绕过 RLS（即使 FORCE 也无效）。infra/dev 的
    `POSTGRES_USER=yimatong` 默认是 superuser，因此以 yimatong 身份无法证明 RLS。
    这里：
    1. 创建非超级用户角色 ``acceptance_tester`` 并授予 SELECT；
    2. 对全部业务表 FORCE ROW LEVEL SECURITY；
    使 ``asyncpg_conn`` 以 acceptance_tester 身份连接时，RLS 真正生效。

    生产侧 yimatong=superuser 是一个真实的安全配置差距，但不属于 yimatong-zgb1.1
    范围（本票只建基准数据并证明策略逻辑），留给后续安全审计票。
    """

    async def _go():
        conn = await asyncpg.connect(ACCEPTANCE_DSN.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            # 创建非超级用户测试角色（幂等）
            exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname='acceptance_tester'")
            if not exists:
                await conn.execute("CREATE ROLE acceptance_tester LOGIN PASSWORD 'tester_pwd' NOSUPERUSER NOINHERIT")
            # 授予 public schema 下所有表的 SELECT（RLS 仍会作用）
            await conn.execute("GRANT USAGE ON SCHEMA public TO acceptance_tester")
            await conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO acceptance_tester")
            # FORCE RLS
            tables = await conn.fetch(
                "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relrowsecurity=true AND c.relkind='r'"
            )
            for row in tables:
                tname = row["relname"]
                if not tname.replace("_", "").isalnum():
                    continue
                await conn.execute(f'ALTER TABLE "{tname}" FORCE ROW LEVEL SECURITY')
        finally:
            await conn.close()

    asyncio.run(_go())


@pytest.fixture(scope="session", autouse=True)
def _cleanup_db(migrated_pg_url):
    """会话结束后清理 acceptance 库。"""
    yield
    try:
        _drop_db()
    except Exception:
        pass


# ── 函数级：绕过 RLS 的 AsyncSession（平台视角，跨租户只读核对）────────────


@pytest_asyncio.fixture
async def bypass_session(migrated_pg_url: str) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(migrated_pg_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        yield session
    await engine.dispose()


# ── 函数级：asyncpg 直连（用于 RLS 可见性断言）────────────────────────────


@pytest_asyncio.fixture
async def asyncpg_conn(migrated_pg_url: str) -> AsyncGenerator[asyncpg.Connection, None]:
    """直连 asyncpg（非超级用户 acceptance_tester 身份），每个测试独立连接 + 独立事务。

    以 acceptance_tester 连接才能真正验证 RLS 策略（yimatong 是 superuser 会绕过）。
    """
    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    # 替换为 acceptance_tester 身份
    tester_dsn = dsn.replace("yimatong:yimatong@", "acceptance_tester:tester_pwd@")
    conn = await asyncpg.connect(tester_dsn)
    tr = conn.transaction()
    await tr.start()
    try:
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


# ── 辅助：在指定 DSN 上构建基准数据 ──────────────────────────────────────


async def seed_baseline(database_url: str) -> dict:
    """在给定 DSN 上幂等构建基准数据，返回摘要 dict。"""
    from app.cli.baseline import _build_baseline_dataset

    return await _build_baseline_dataset(database_url)
