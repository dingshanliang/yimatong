"""yimatong-zgb1.1 验收测试 — 真实 PostgreSQL fixture。

与根 conftest 的 SQLite TestSessionLocal 完全隔离：本目录的测试只用真实 PG，
证明 RLS、迁移、首扫原子性等无法在 SQLite 上证明的事实。

默认指向运行中的 infra PostgreSQL（docker-compose.infra.yml 映射到宿主机 5433）。
每个测试会话创建并最终删除一个干净的唯一专用数据库 ``yimatong_acceptance_<run_id>``，
对应 BASELINE_ACCEPTANCE_MATRIX §4「干净环境重建门禁」。

环境变量：
- ``ACCEPTANCE_DB_NAME``：唯一专用数据库名，必须以 ``yimatong_acceptance_`` 开头；
  未提供时使用带进程号和随机后缀的本地默认名。
- ``ACCEPTANCE_PG_ADMIN_URL``：用于 CREATE/DROP DATABASE 的管理连接串。
  默认 ``postgresql://yimatong:yimatong@localhost:5433/postgres``。
- ``ACCEPTANCE_PG_DSN``：测试用 SQLAlchemy asyncpg DSN（指向 acceptance 库）。
  未提供时根据唯一专用数据库名生成；若提供则库名必须与 ``ACCEPTANCE_DB_NAME`` 一致。
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import subprocess
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import unquote, urlsplit

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BACKEND_DIR = Path(__file__).resolve().parents[2]

_ACCEPTANCE_DB_PATTERN = re.compile(r"yimatong_acceptance_[a-z0-9][a-z0-9_]{5,42}\Z")
_DROP_TOTAL_TIMEOUT_SECONDS = 15.0
_DROP_ATTEMPT_TIMEOUT_SECONDS = 3.0
_DROP_RETRY_BACKOFF_SECONDS = 0.25
_DROP_MAX_ATTEMPTS = 4


def _database_name_from_dsn(dsn: str) -> str:
    database_name = unquote(urlsplit(dsn).path.lstrip("/"))
    if not database_name or "/" in database_name:
        raise RuntimeError("ACCEPTANCE_PG_DSN must target one explicit database")
    return database_name


def _validate_acceptance_database_name(database_name: str) -> None:
    """Reject shared or broad database targets before any destructive SQL runs."""
    if not _ACCEPTANCE_DB_PATTERN.fullmatch(database_name):
        raise RuntimeError(
            "acceptance database must be a unique dedicated name matching "
            "yimatong_acceptance_<run_id> (26-63 lowercase letters, digits, or underscores)"
        )


def _resolve_acceptance_database() -> tuple[str, str]:
    explicit_name = os.getenv("ACCEPTANCE_DB_NAME")
    explicit_dsn = os.getenv("ACCEPTANCE_PG_DSN")

    if explicit_name:
        database_name = explicit_name
    elif explicit_dsn:
        database_name = _database_name_from_dsn(explicit_dsn)
    else:
        database_name = f"yimatong_acceptance_{os.getpid()}_{secrets.token_hex(4)}"

    _validate_acceptance_database_name(database_name)
    database_dsn = explicit_dsn or f"postgresql+asyncpg://yimatong:yimatong@localhost:5433/{database_name}"
    dsn_database_name = _database_name_from_dsn(database_dsn)
    if dsn_database_name != database_name:
        raise RuntimeError(
            f"ACCEPTANCE_DB_NAME ({database_name}) must match ACCEPTANCE_PG_DSN database ({dsn_database_name})"
        )
    return database_name, database_dsn


ACCEPTANCE_DB, ACCEPTANCE_DSN = _resolve_acceptance_database()
ADMIN_DSN = os.getenv(
    "ACCEPTANCE_PG_ADMIN_URL",
    "postgresql://yimatong:yimatong@localhost:5433/postgres",
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


class AcceptanceDatabaseCleanupError(RuntimeError):
    """Raised when the dedicated acceptance database cannot be proven absent."""


async def _drop_database_with_retry(
    database_name: str,
    admin_dsn: str,
    *,
    total_timeout: float = _DROP_TOTAL_TIMEOUT_SECONDS,
    attempt_timeout: float = _DROP_ATTEMPT_TIMEOUT_SECONDS,
    retry_backoff: float = _DROP_RETRY_BACKOFF_SECONDS,
    max_attempts: int = _DROP_MAX_ATTEMPTS,
    connect=asyncpg.connect,
) -> None:
    """Drop one dedicated database and verify its absence through ``pg_database``."""
    _validate_acceptance_database_name(database_name)
    if _database_name_from_dsn(admin_dsn) == database_name:
        raise RuntimeError("ACCEPTANCE_PG_ADMIN_URL must target a maintenance database, not the acceptance database")
    if total_timeout <= 0 or attempt_timeout <= 0 or max_attempts <= 0:
        raise ValueError("database cleanup timeouts and max_attempts must be positive")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + total_timeout
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        remaining = deadline - loop.time()
        if remaining <= 0:
            break

        conn = None
        try:
            conn = await asyncio.wait_for(connect(admin_dsn), timeout=min(attempt_timeout, remaining))
            operation_timeout = min(attempt_timeout, max(deadline - loop.time(), 0.001))
            await asyncio.wait_for(
                conn.execute(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'),
                timeout=operation_timeout,
            )
            operation_timeout = min(attempt_timeout, max(deadline - loop.time(), 0.001))
            still_exists = await asyncio.wait_for(
                conn.fetchval("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)", database_name),
                timeout=operation_timeout,
            )
            if not still_exists:
                return
            last_error = RuntimeError(f"database {database_name} still exists after DROP DATABASE")
        except Exception as exc:
            last_error = exc
        finally:
            if conn is not None:
                try:
                    close_timeout = min(attempt_timeout, max(deadline - loop.time(), 0.001))
                    await asyncio.wait_for(conn.close(), timeout=close_timeout)
                except Exception:
                    conn.terminate()

        if attempt < max_attempts:
            remaining = deadline - loop.time()
            if remaining > 0:
                await asyncio.sleep(min(retry_backoff, remaining))

    raise AcceptanceDatabaseCleanupError(
        f"failed to remove dedicated acceptance database {database_name} "
        f"after at most {max_attempts} attempts within {total_timeout:.1f}s"
    ) from last_error


def _drop_and_create_db() -> None:
    """在 infra PG 上（重）建一个干净的 acceptance 库。"""
    _drop_db()
    _run_admin(f'CREATE DATABASE "{ACCEPTANCE_DB}" OWNER yimatong')
    _prepare_runtime_role()


def _drop_db() -> None:
    asyncio.run(_drop_database_with_retry(ACCEPTANCE_DB, ADMIN_DSN))


def _prepare_runtime_role() -> None:
    """Create principals only; privileges are provisioned after migration from reviewed contracts."""

    async def _go():
        conn = await asyncpg.connect(ACCEPTANCE_DSN.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            await conn.execute(
                """
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'yimatong_app') THEN
                        CREATE ROLE yimatong_app LOGIN PASSWORD 'yimatong_app'
                            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
                    ELSE
                        ALTER ROLE yimatong_app WITH LOGIN PASSWORD 'yimatong_app'
                            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'acceptance_tester') THEN
                        CREATE ROLE acceptance_tester LOGIN PASSWORD 'tester_pwd'
                            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
                    ELSE
                        ALTER ROLE acceptance_tester WITH LOGIN PASSWORD 'tester_pwd'
                            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'acceptance_control') THEN
                        CREATE ROLE acceptance_control LOGIN PASSWORD 'control_pwd'
                            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
                    ELSE
                        ALTER ROLE acceptance_control WITH LOGIN PASSWORD 'control_pwd'
                            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
                    END IF;
                END $$
                """
            )
            await conn.execute("GRANT CONNECT ON DATABASE " + ACCEPTANCE_DB + " TO yimatong_app")
            await conn.execute("GRANT USAGE ON SCHEMA public TO yimatong_app")
            await conn.execute("GRANT CONNECT ON DATABASE " + ACCEPTANCE_DB + " TO acceptance_tester")
            await conn.execute("GRANT USAGE ON SCHEMA public TO acceptance_tester")
            await conn.execute("GRANT CONNECT ON DATABASE " + ACCEPTANCE_DB + " TO acceptance_control")
            await conn.execute("GRANT USAGE ON SCHEMA public TO acceptance_control")
        finally:
            await conn.close()

    asyncio.run(_go())


# ── 会话级：干净 PG + 迁移 ────────────────────────────────────────────────


@pytest.fixture(scope="session")
def migrated_pg_url() -> str:
    """创建干净 acceptance 库并跑 alembic upgrade head，返回 asyncpg DSN。"""
    if not _pg_available():
        pytest.skip("infra PostgreSQL not available on localhost:5433; run `pnpm dev:stack`")
    _drop_and_create_db()

    env = os.environ.copy()
    env["database_url"] = ACCEPTANCE_DSN
    env["migration_database_url"] = ACCEPTANCE_DSN
    env["control_database_url"] = ACCEPTANCE_DSN
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

    async def _provision_test_principals() -> None:
        conn = await asyncpg.connect(ACCEPTANCE_DSN.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            # Provision the actual runtime role from the reviewed allowlist
            # contract. Fixture defaults must never mask a required-table gap.
            await conn.execute((BACKEND_DIR / "scripts" / "init_runtime_role.sql").read_text())

            # Test-only principals are explicit and post-migration: one normal
            # tenant principal and one independently privileged control
            # principal used to exercise control-policy USING/WITH CHECK.
            await conn.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO acceptance_tester"
            )
            await conn.execute("GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO acceptance_tester")
            await conn.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO acceptance_control"
            )
            await conn.execute("GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO acceptance_control")
            await conn.execute('GRANT SET ON PARAMETER "app.bypass_rls" TO acceptance_control')
        finally:
            await conn.close()

    asyncio.run(_provision_test_principals())

    async def _assert_target_database() -> None:
        conn = await asyncpg.connect(ACCEPTANCE_DSN.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            actual = await conn.fetchval("SELECT current_database()")
            assert actual == ACCEPTANCE_DB, f"migrations ran against unexpected database: {actual}"
        finally:
            await conn.close()

    asyncio.run(_assert_target_database())

    return ACCEPTANCE_DSN


@pytest.fixture(scope="session", autouse=True)
def _cleanup_db(migrated_pg_url):
    """会话结束后清理 acceptance 库。"""
    yield
    _drop_db()


# ── 函数级：绕过 RLS 的 AsyncSession（平台视角，跨租户只读核对）────────────


@pytest_asyncio.fixture
async def bypass_session(migrated_pg_url: str) -> AsyncGenerator[AsyncSession, None]:
    control_url = migrated_pg_url.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
    engine = create_async_engine(control_url)
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
    tester_dsn = dsn.replace("yimatong:yimatong@", "acceptance_tester:tester_pwd@")
    conn = await asyncpg.connect(tester_dsn)
    tr = conn.transaction()
    await tr.start()
    try:
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


@pytest_asyncio.fixture
async def runtime_pg_conn(migrated_pg_url: str) -> AsyncGenerator[asyncpg.Connection, None]:
    """Direct connection using the actual restricted application role."""

    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    runtime_dsn = dsn.replace("yimatong:yimatong@", "yimatong_app:yimatong_app@")
    conn = await asyncpg.connect(runtime_dsn)
    tr = conn.transaction()
    await tr.start()
    try:
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


@pytest_asyncio.fixture
async def control_pg_conn(migrated_pg_url: str) -> AsyncGenerator[asyncpg.Connection, None]:
    """Explicit non-owner control principal with independently granted bypass authority."""

    dsn = migrated_pg_url.replace("postgresql+asyncpg://", "postgresql://")
    control_dsn = dsn.replace("yimatong:yimatong@", "acceptance_control:control_pwd@")
    conn = await asyncpg.connect(control_dsn)
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute("SELECT set_config('app.tenant_id', '', true)")
        await conn.execute("SELECT set_config('app.bypass_rls', 'true', true)")
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


# ── 辅助：在指定 DSN 上构建基准数据 ──────────────────────────────────────


async def seed_baseline(database_url: str) -> dict:
    """在给定 DSN 上幂等构建基准数据，返回摘要 dict。"""
    from app.cli.baseline import _build_baseline_dataset

    return await _build_baseline_dataset(database_url)
